"""What Lain can do on the host machine. Anything that changes the machine asks first
(unless you switch on /auto). Command output streams live so you see what's happening."""
import difflib
import fnmatch
import getpass
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time

from . import ui, web

IS_WIN = os.name == "nt"
MAX_RESULT = 16000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".mypy_cache", "target", "dist", "build"}


def clip(text, limit=MAX_RESULT):
    if len(text) <= limit:
        return text
    head, tail = text[: int(limit * 0.7)], text[-int(limit * 0.25):]
    return head + "\n\n[... %d characters omitted ...]\n\n" % (len(text) - len(head) - len(tail)) + tail


def kill_tree(p):
    """Stop a command and everything it started (bash -c children would otherwise keep running)."""
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        return
    kids, todo = [], [p.pid]
    try:
        procs = {}
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    procs[int(d)] = int(open("/proc/%s/stat" % d).read().rsplit(")", 1)[1].split()[1])
                except (OSError, ValueError, IndexError):
                    pass
        while todo:
            pid = todo.pop()
            for c, pp in procs.items():
                if pp == pid:
                    kids.append(c)
                    todo.append(c)
    except OSError:
        pass
    for pid in [p.pid] + kids:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".bmp": "image/bmp"}
MAX_IMAGE = 20 * 2**20


def load_image(path):
    """Read an image file for the model. Raises ValueError with a readable reason."""
    import base64
    path = os.path.normpath(os.path.expanduser(path))
    mime = IMAGE_TYPES.get(os.path.splitext(path)[1].lower())
    if not mime:
        raise ValueError("not a supported image type (use %s): %s" % (", ".join(sorted(IMAGE_TYPES)), path))
    if not os.path.isfile(path):
        raise ValueError("no such file: %s" % path)
    n = os.path.getsize(path)
    if n > MAX_IMAGE:
        raise ValueError("image is too big (%s, max %s)" % (ui.human(n), ui.human(MAX_IMAGE)))
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return {"path": path, "bytes": n, "url": "data:%s;base64,%s" % (mime, data)}


def image_message(text, imgs):
    """A user message carrying text plus images (OpenAI format; llama-server shows them to the model)."""
    parts = [{"type": "text", "text": text}]
    for im in imgs:
        parts.append({"type": "image_url", "image_url": {"url": im["url"]}})
    return {"role": "user", "content": parts}


def _schema(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


class Tools:
    def __init__(self, cwd, files_dir, online, hw):
        self.cwd = cwd
        self.files_dir = files_dir
        self.online = online
        self.hw = hw
        self.auto = False
        self.engine_pid = None     # set by lain.py: Lain's own engine
        self.protected = []        # set by lain.py: Lain's own app/engine/model folders
        self.halt = False          # Ctrl+C pressed: the agent stops the whole request (emergency brake)
        self.shell = "PowerShell" if IS_WIN else "bash"
        self.vision = False        # set by lain.py when the model has its vision add-on loaded
        self.pending_images = []   # images view_image loaded; the agent shows them to the model next

    # ------------------------------------------------------------------ schemas
    def schemas(self):
        s = [
            _schema("run_command",
                    "Run a %s command on the user's computer and return its output and exit code. Each call is a "
                    "fresh shell started in the working directory (use the cwd argument to run elsewhere). "
                    "Commands that need sudo/admin will prompt the user for their password in the terminal. "
                    "Avoid commands that wait for interactive input." % self.shell,
                    {"command": {"type": "string", "description": "the command line to run"},
                     "cwd": {"type": "string", "description": "directory to run in (default: working directory)"},
                     "timeout": {"type": "integer", "description": "seconds before it is stopped (default 300)"}},
                    ["command"]),
            _schema("read_file", "Read a text file. Returns numbered lines. Use offset/limit for big files.",
                    {"path": {"type": "string"}, "offset": {"type": "integer", "description": "first line, 1-based"},
                     "limit": {"type": "integer", "description": "max lines (default 400)"}}, ["path"]),
            _schema("write_file", "Create or overwrite a file with the given content (parent folders are created).",
                    {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            _schema("edit_file", "Replace an exact piece of text in a file. old_text must match exactly (including "
                    "indentation) and be unique unless replace_all is true. Read the file first.",
                    {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
                     "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"]),
            _schema("list_dir", "List a directory (names, types, sizes). depth 1-3.",
                    {"path": {"type": "string"}, "depth": {"type": "integer"}}),
            _schema("search_files", "Search file contents with a regular expression (like grep -rn). "
                    "Optionally limit to file names matching a glob such as *.py.",
                    {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"},
                     "max_results": {"type": "integer"}}, ["pattern"]),
            _schema("system_info", "Details about this computer: OS, CPU, RAM, GPU, disks, user, working directory.", {}),
        ]
        if self.vision:
            s.append(_schema("view_image", "Look at an image file (PNG, JPEG, WebP, GIF, BMP): screenshots, photos, "
                             "charts, scanned documents. The image is shown to you right after this call. To see the "
                             "screen, first save a screenshot with run_command, then view it.",
                             {"path": {"type": "string"}}, ["path"]))
        if self.online:
            s += [
                _schema("web_search", "Search the web (DuckDuckGo). Returns titles, URLs and snippets.",
                        {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"]),
                _schema("fetch_url", "Download a web page and return its readable text.",
                        {"url": {"type": "string"}, "max_chars": {"type": "integer"}}, ["url"]),
            ]
        return s

    # ------------------------------------------------------------------ helpers
    def path(self, p):
        p = os.path.expandvars(os.path.expanduser(p or "."))
        return os.path.normpath(p if os.path.isabs(p) else os.path.join(self.cwd, p))

    def _touches_self(self, command=None, path=None):
        """Would this kill Lain's engine or change Lain's own files? Then always ask, even in /auto."""
        norm = lambda x: os.path.normcase(os.path.realpath(x))
        if path:
            rp = norm(path)
            return any(rp == norm(d) or rp.startswith(norm(d) + os.sep) for d in self.protected)
        c = command or ""
        if re.search(r"llama-server|\b(pkill|killall|taskkill|Stop-Process)\b", c, re.I):
            return True
        if self.engine_pid and re.search(r"\bkill\b.*\b%d\b" % self.engine_pid, c):
            return True
        return any(os.path.normcase(d) in os.path.normcase(c) for d in self.protected)

    def _approve(self, what, force=False):
        if force:
            ui.out("    " + ui.WARN + "this touches Lain's own engine or files - asking even in auto mode" + ui.R)
        if self.auto and not force:
            ui.out("    " + ui.DIM + "(auto-approved)" + ui.R)
            return True, ""
        while True:
            try:
                a = input("    " + ui.WARN + "allow?" + ui.R + " [y]es / [n]o / [a]lways this session > ").strip().lower()
            except EOFError:
                ui.out()
                a = "n"
            except KeyboardInterrupt:
                ui.out()
                self.halt = True
                return False, "The user hit the emergency brake (Ctrl+C). Stop and wait for their next message."
            if force and a == "":
                a = "n"              # protected actions need an explicit yes
            if a in ("y", "yes", ""):
                return True, ""
            if a in ("a", "always"):
                if force:
                    return True, ""  # yes once; protected actions never switch on auto mode
                self.auto = True
                ui.out("    " + ui.WARN + "auto-approve is ON for this session (/auto to turn it off)" + ui.R)
                return True, ""
            if a in ("n", "no"):
                try:
                    why = input("    " + ui.DIM + "tell Lain why / what to do instead (optional) > " + ui.R).strip()
                except (EOFError, KeyboardInterrupt):
                    why = ""
                return False, why

    @staticmethod
    def _show_diff(old, new, path):
        d = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "a/" + os.path.basename(path),
                                      "b/" + os.path.basename(path), lineterm="", n=2))
        shown = 0
        for line in d[2:]:
            if shown >= 40:
                ui.out("    " + ui.DIM + "... (%d more diff lines)" % (len(d) - 2 - shown) + ui.R)
                break
            color = ui.OK if line.startswith("+") else ui.RED if line.startswith("-") else ui.DIM
            ui.out("    " + color + line[:ui.cols() - 6] + ui.R)
            shown += 1
        added = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
        return added, removed

    # ------------------------------------------------------------------ dispatch
    def run(self, name, args):
        fn = getattr(self, "t_" + name, None)
        if not fn or (name in ("web_search", "fetch_url") and not self.online):
            return "Error: unknown tool %r" % name
        try:
            res = fn(**args)
            return clip(res) if isinstance(res, str) else res
        except TypeError as e:
            return "Error: bad arguments for %s: %s" % (name, e)
        except Exception as e:
            return "Error: %s: %s" % (type(e).__name__, e)

    def t_view_image(self, path):
        if not self.vision:
            return "Error: this model has no vision add-on loaded. Ask the user to start Lain in a Vision mode."
        img = load_image(self.path(path))
        self.pending_images.append(img)
        return "Loaded %s (%s). It is attached in the next message." % (img["path"], ui.human(img["bytes"]))

    def headline(self, name, args):
        if name == "run_command":
            return args.get("command", "")
        if name in ("read_file", "write_file", "edit_file", "list_dir"):
            return args.get("path", ".")
        if name == "search_files":
            return "%r in %s" % (args.get("pattern", ""), args.get("path", "."))
        if name == "web_search":
            return args.get("query", "")
        if name == "fetch_url":
            return args.get("url", "")
        if name == "view_image":
            return args.get("path", "")
        return ""

    # ------------------------------------------------------------------ tools
    def t_run_command(self, command, cwd=None, timeout=300):
        try:
            timeout = max(1, min(int(timeout or 300), 3600))
        except (TypeError, ValueError):
            timeout = 300
        where = self.path(cwd) if cwd else self.cwd
        if where != self.cwd:
            ui.out("    " + ui.DIM + "in " + where + ui.R)
        ok, why = self._approve("run", self._touches_self(command=command + " " + where))
        if not ok:
            return "The user declined to run this command." + (" They said: " + why if why else "")
        if IS_WIN:
            argv = ["powershell.exe", "-NoProfile", "-Command",
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
        else:
            argv = ["bash", "-c", command]
        t0 = time.time()
        p = subprocess.Popen(argv, cwd=where, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        chunks, shown, hidden = [], [0], [0]
        size = [0]

        def pump():
            for raw in iter(p.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if size[0] < 2_000_000:
                    chunks.append(line)
                    size[0] += len(line) + 1
                if shown[0] < 80:
                    ui.out("    " + ui.DIM + "│ " + line[: ui.cols() - 8] + ui.R)
                    shown[0] += 1
                else:
                    hidden[0] += 1
                    if hidden[0] % 200 == 1:
                        ui.out("    " + ui.DIM + "│ ... (output continues, %d more lines so far)" % hidden[0] + ui.R)
        th = threading.Thread(target=pump, daemon=True)
        th.start()
        status = ""
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            status = "stopped after %ds timeout" % timeout
        except KeyboardInterrupt:
            kill_tree(p)
            self.halt = True
            status = "stopped by the user (Ctrl+C)"
        th.join(2)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            p.wait()
        el = time.time() - t0
        code = p.returncode
        color = ui.OK if code == 0 and not status else ui.RED
        ui.out("    " + color + "└ %s · %s · %d lines" % (status or "exit %s" % code, ui.fmt_time(el) if el >= 1 else "%.1fs" % el,
                                                         len(chunks)) + ui.R)
        res = "exit code: %s%s\n" % (code, (" (" + status + ")") if status else "")
        return res + clip("\n".join(chunks)) if chunks else res + "(no output)"

    def t_read_file(self, path, offset=1, limit=400):
        p = self.path(path)
        if os.path.isdir(p):
            return "Error: %s is a directory (use list_dir)" % p
        with open(p, "rb") as f:
            raw = f.read(5_000_000)
        if b"\0" in raw[:8000]:
            return "%s is a binary file (%s)" % (p, ui.human(os.path.getsize(p)))
        lines = raw.decode("utf-8", "replace").splitlines()
        offset = max(1, int(offset or 1))
        limit = max(1, min(int(limit or 400), 2000))
        sel = lines[offset - 1: offset - 1 + limit]
        if not sel:
            return "%s has %d lines; offset %d is past the end" % (p, len(lines), offset)
        ui.out("    " + ui.DIM + "└ lines %d-%d of %d" % (offset, offset + len(sel) - 1, len(lines)) + ui.R)
        body = "\n".join("%6d  %s" % (i, l) for i, l in enumerate(sel, offset))
        more = "\n[file has %d lines; continue with offset=%d]" % (len(lines), offset + len(sel)) \
            if offset - 1 + len(sel) < len(lines) else ""
        return clip(body) + more

    def t_write_file(self, path, content):
        p = self.path(path)
        old = None
        if os.path.exists(p):
            try:
                old = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                pass
        if old is not None:
            ui.out("    " + ui.DIM + "overwrite existing file:" + ui.R)
            self._show_diff(old, content, p)
        else:
            ui.out("    " + ui.DIM + "new file, %d lines, %s:" % (content.count("\n") + 1, ui.human(len(content.encode()))) + ui.R)
            for l in content.splitlines()[:12]:
                ui.out("    " + ui.OK + "+ " + l[: ui.cols() - 8] + ui.R)
            if content.count("\n") > 12:
                ui.out("    " + ui.DIM + "  ..." + ui.R)
        ok, why = self._approve("write", self._touches_self(path=p))
        if not ok:
            return "The user declined this write." + (" They said: " + why if why else "")
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        ui.out("    " + ui.OK + "└ wrote " + p + ui.R)
        return "Wrote %d bytes to %s" % (len(content.encode()), p)

    def t_edit_file(self, path, old_text, new_text, replace_all=False):
        p = self.path(path)
        text = open(p, encoding="utf-8", errors="replace").read()
        n = text.count(old_text)
        if n == 0:
            return "Error: old_text not found in %s. Read the file and copy the text exactly." % p
        if n > 1 and not replace_all:
            return "Error: old_text appears %d times in %s. Add surrounding lines to make it unique, or set replace_all." % (n, p)
        new = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
        a, r = self._show_diff(text, new, p)
        ok, why = self._approve("edit", self._touches_self(path=p))
        if not ok:
            return "The user declined this edit." + (" They said: " + why if why else "")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        ui.out("    " + ui.OK + "└ edited %s (+%d -%d)" % (p, a, r) + ui.R)
        return "Edited %s (%d replacement%s)" % (p, n if replace_all else 1, "s" if replace_all and n > 1 else "")

    def t_list_dir(self, path=".", depth=1):
        root = self.path(path)
        depth = max(1, min(int(depth or 1), 3))
        out, count = [], 0
        for dp, dns, fns in os.walk(root):
            lvl = dp[len(root):].count(os.sep)
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS) if lvl + 1 < depth else []
            ind = "  " * lvl
            if lvl:
                out.append(ind[:-2] + os.path.basename(dp) + "/")
            for d in (dns if lvl + 1 >= depth else []):
                out.append(ind + d + "/")
            for fn in sorted(fns):
                try:
                    sz = ui.human(os.path.getsize(os.path.join(dp, fn)))
                except OSError:
                    sz = "?"
                out.append("%s%s  (%s)" % (ind, fn, sz))
                count += 1
            if count > 400:
                out.append("... (truncated)")
                break
        if depth == 1:
            out = sorted([d + "/" for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]) + \
                [l for l in out if not l.endswith("/")]
        ui.out("    " + ui.DIM + "└ %d entries" % len(out) + ui.R)
        return root + "\n" + clip("\n".join(out))

    def t_search_files(self, pattern, path=".", glob=None, max_results=100):
        rx = re.compile(pattern)
        root = self.path(path)
        max_results = max(1, min(int(max_results or 100), 500))
        hits, scanned = [], 0
        targets = [(os.path.dirname(root), [], [os.path.basename(root)])] if os.path.isfile(root) else os.walk(root)
        for dp, dns, fns in targets:
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                if glob and not fnmatch.fnmatch(fn, glob):
                    continue
                fp = os.path.join(dp, fn)
                try:
                    if os.path.getsize(fp) > 3_000_000:
                        continue
                    with open(fp, "rb") as f:
                        data = f.read()
                except OSError:
                    continue
                if b"\0" in data[:4000]:
                    continue
                scanned += 1
                for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append("%s:%d: %s" % (os.path.relpath(fp, root if os.path.isdir(root) else dp), i, line.strip()[:200]))
                        if len(hits) >= max_results:
                            break
                if len(hits) >= max_results:
                    break
            if len(hits) >= max_results:
                break
        ui.out("    " + ui.DIM + "└ %d matches in %d files scanned" % (len(hits), scanned) + ui.R)
        return clip("\n".join(hits)) if hits else "No matches (%d files scanned)." % scanned

    def t_system_info(self):
        h = self.hw
        lines = [
            "OS: %s (%s)" % (h["os"], platform.machine()),
            "Host: %s   User: %s" % (socket.gethostname(), getpass.getuser()),
            "CPU: %s (%d threads)" % (h["cpu"], h["threads"]),
            "RAM: %.0f GB" % h["ram"],
            "GPU: %s%s" % (h["gpu_name"], " (%.0f GB VRAM)" % h["vram_total"] if h["vram_total"] else ""),
            "Shell for run_command: %s" % self.shell,
            "Working directory: %s" % self.cwd,
            "Encrypted vault files folder: %s" % self.files_dir,
        ]
        for label, p in (("home", os.path.expanduser("~")), ("working dir", self.cwd)):
            try:
                du = shutil.disk_usage(p)
                lines.append("Disk (%s): %s free of %s" % (label, ui.human(du.free), ui.human(du.total)))
            except OSError:
                pass
        ui.out("    " + ui.DIM + "└ done" + ui.R)
        return "\n".join(lines)

    def t_web_search(self, query, max_results=8):
        r = web.web_search(query, max_results)
        ui.out("    " + ui.DIM + "└ %d results" % r.count("\n   http") + ui.R)
        return r

    def t_fetch_url(self, url, max_chars=15000):
        r = web.fetch_url(url, max_chars)
        ui.out("    " + ui.DIM + "└ %s of text" % ui.human(len(r)) + ui.R)
        return r
