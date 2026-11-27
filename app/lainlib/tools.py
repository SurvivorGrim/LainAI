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
DEFAULT_TIMEOUT = 120      # seconds; Lain passes a bigger timeout for installs/builds
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".mypy_cache", "target", "dist", "build"}


def clip(text, limit=MAX_RESULT):
    if len(text) <= limit:
        return text
    head, tail = text[: int(limit * 0.7)], text[-int(limit * 0.25):]
    return head + "\n\n[... %d characters omitted ...]\n\n" % (len(text) - len(head) - len(tail)) + tail


_SEARCHERS = {"grep", "egrep", "fgrep", "zgrep", "rg", "pgrep", "findstr", "findstr.exe"}


def _search_last(command):
    """Is the command's last step a search tool (grep, pgrep, rg, findstr) whose exit code 1 just means "no match"?
    Not when && / || decide what ran: then a 1 could come from any step."""
    if "&&" in command or "||" in command:
        return False
    last = re.split(r"[|;\n]", command.strip())[-1].split()
    while last and ("=" in last[0] or last[0] in ("sudo", "command", "env", "nice")):
        last = last[1:]
    return bool(last) and os.path.basename(last[0].strip("'\"")).lower() in _SEARCHERS


def _capture(argv, timeout=20):
    """Run a short read-only helper command and return its output ("" if it isn't available)."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.stdout + (r.stderr if not r.stdout else "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _count_lines(lines, limit):
    """Duplicate lines collapsed into 'Nx line', first-seen order kept (order matters for e.g. dependency cycles)."""
    seen = {}
    for l in lines:
        l = l.strip()
        if l:
            seen[l] = seen.get(l, 0) + 1
    out = [("%dx " % n if n > 1 else "") + l[:300] for l, n in seen.items()]
    return out[:limit] + (["... %d more different lines" % (len(out) - limit)] if len(out) > limit else [])


_BOOT_TARGETS = {"default.target", "multi-user.target", "graphical.target"}


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
        self.selfedit = False      # /selfedit: may write_file/edit_file change Lain's own running code?
        self.halt = False          # Ctrl+C pressed: the agent stops the whole request (emergency brake)
        self.shell = "PowerShell" if IS_WIN else "bash"
        self.vision = False        # set by lain.py when the model has its vision add-on loaded
        self.pending_images = []   # images view_image loaded; the agent shows them to the model next
        self.plan = []             # update_plan checklist: [{"step", "status"}], shown with /plan
        self.plan_note = ""

    # ------------------------------------------------------------------ schemas
    def schemas(self):
        s = [
            _schema("run_command",
                    "Run a %s command on the user's computer and return its output and exit code. Each call is a "
                    "fresh shell started in the working directory (use the cwd argument to run elsewhere). "
                    "Commands that need sudo/admin will prompt the user for their password in the terminal. "
                    "Every command must finish on its own: no interactive programs (editors, pagers, top/watch), "
                    "and put limits on anything that can wait (curl --max-time 10, ping -c 3, nc -w 3, head)." % self.shell,
                    {"command": {"type": "string", "description": "the command line to run"},
                     "cwd": {"type": "string", "description": "directory to run in (default: working directory)"},
                     "timeout": {"type": "integer", "description": "seconds before it is stopped (default %d; raise it, up to 3600, for installs, builds or big downloads)" % DEFAULT_TIMEOUT}},
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
            _schema("service_health", "What's supposed to run but isn't: failed services, services set to start at "
                    "boot/login that are NOT running (and whether they started at all), and this boot's warnings from "
                    "the service manager (dependency/ordering cycles, crashes, failed starts). With name, also the full "
                    "status, unit file and recent log lines of every service/task matching it. Use it early whenever "
                    "something stopped working, isn't there after a reboot, or 'used to work'. Read-only.",
                    {"name": {"type": "string", "description": "optional word to focus on, e.g. rgb, docker, nginx"}}),
            _schema("update_plan", "Keep a short checklist for any task that needs more than a couple of steps. "
                    "Call it before you start and whenever a step changes status; always send the whole list. "
                    "Exactly one step should be in_progress while you work. The user sees it (and can view it any "
                    "time with /plan). Use note for the key finding so far (e.g. the root cause).",
                    {"steps": {"type": "array", "items": {"type": "object", "properties": {
                        "step": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
                        "required": ["step", "status"]}},
                     "note": {"type": "string", "description": "optional: key finding or current understanding"}},
                    ["steps"]),
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

    def _self_locked(self, p):
        """write_file/edit_file on Lain's own running install while self-editing is locked -> refusal text."""
        if self.selfedit or not self._touches_self(path=p):
            return ""
        ui.out("    " + ui.WARN + "refused: that's Lain's own running code - self-edit is locked (/selfedit unlocks it)" + ui.R)
        return ("Refused: %s is part of your own LIVE install on the drive, and self-editing is locked. Don't modify "
                "yourself. If the user wants the Lain AI project changed, work in their development copy (the git repo) "
                "instead. Only if they really mean this running copy, tell them to type /selfedit first." % p)

    def _approve(self, what, force=False):
        if force:
            ui.out("    " + ui.WARN + "this touches Lain's own engine or files - asking even in auto mode" + ui.R)
            if not self.selfedit:
                ui.out("    " + ui.WARN + "(self-edit is locked: say no unless you want Lain to change her running copy)" + ui.R)
        if self.auto and not force:
            ui.out("    " + ui.DIM + "(auto-approved)" + ui.R)
            return True, ""
        ui.flush_input()             # keys typed or pasted earlier must never answer this prompt
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

    def plan_lines(self):
        marks = {"done": (ui.OK, "✓"), "in_progress": (ui.WARN, "▸"), "pending": (ui.DIM, "○")}
        out = []
        for st in self.plan:
            c, m = marks.get(st["status"], marks["pending"])
            out.append("    " + c + m + " " + st["step"] + ui.R)
        if self.plan_note:
            out.append("    " + ui.DIM + "note: " + self.plan_note + ui.R)
        return out

    def plan_text(self):
        marks = {"done": "[x]", "in_progress": "[>]", "pending": "[ ]"}
        t = "\n".join("%s %s" % (marks.get(st["status"], "[ ]"), st["step"]) for st in self.plan)
        return t + ("\nnote: " + self.plan_note if self.plan_note else "")

    def unfinished(self):
        return [st["step"] for st in self.plan if st["status"] != "done"]

    def t_update_plan(self, steps, note=""):
        if not isinstance(steps, list) or not steps:
            return "Error: steps must be a non-empty list of {step, status}."
        plan = []
        for st in steps[:30]:
            if not isinstance(st, dict) or not str(st.get("step", "")).strip():
                return "Error: each step needs a 'step' text and a 'status' (pending, in_progress, done)."
            status = st.get("status") if st.get("status") in ("pending", "in_progress", "done") else "pending"
            plan.append({"step": str(st["step"]).strip()[:160], "status": status})
        self.plan, self.plan_note = plan, str(note or "").strip()[:300]
        for l in self.plan_lines():
            ui.out(l)
        return "[plan] Plan updated:\n" + self.plan_text()

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
        if name == "update_plan":
            st = args.get("steps") if isinstance(args.get("steps"), list) else []
            return "%d steps, %d done" % (len(st), sum(1 for x in st if isinstance(x, dict) and x.get("status") == "done"))
        return ""

    # ------------------------------------------------------------------ tools
    def t_run_command(self, command, cwd=None, timeout=DEFAULT_TIMEOUT):
        try:
            timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), 3600))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
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
        last = [time.time()]            # when the command last printed something
        lock = threading.Lock()
        status_on = [False]

        def show(text):
            with lock:
                if status_on[0]:
                    ui.clear_line()
                    status_on[0] = False
                ui.out(text)

        def pump():
            for raw in iter(p.stdout.readline, b""):
                last[0] = time.time()
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if size[0] < 2_000_000:
                    chunks.append(line)
                    size[0] += len(line) + 1
                if shown[0] < 80:
                    show("    " + ui.DIM + "│ " + line[: ui.cols() - 8] + ui.R)
                    shown[0] += 1
                else:
                    hidden[0] += 1
                    if hidden[0] % 200 == 1:
                        show("    " + ui.DIM + "│ ... (output continues, %d more lines so far)" % hidden[0] + ui.R)
        th = threading.Thread(target=pump, daemon=True)
        th.start()
        status, stopped_by_user = "", False
        try:
            while True:                 # wait, and keep a live "still running" line so the user can check in
                try:
                    p.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = time.time()
                if now - t0 >= timeout:
                    kill_tree(p)
                    status = "stopped after %ds time limit" % timeout
                    break
                quiet = now - last[0]
                if quiet >= 5:
                    with lock:
                        ui.clear_line()
                        ui.out("    %s%s running %s · no output for %s · limit %s · Ctrl+C stops it%s" % (
                            ui.DIM, ui.SPIN[int(now) % len(ui.SPIN)], ui.fmt_time(now - t0), ui.fmt_time(quiet),
                            ui.fmt_time(timeout), ui.R), end="")
                        status_on[0] = True
        except KeyboardInterrupt:
            kill_tree(p)
            stopped_by_user = True
            status = "stopped by the user"
        with lock:
            if status_on[0]:
                ui.clear_line()
                status_on[0] = False
        th.join(2)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            p.wait()
        el = time.time() - t0
        code = p.returncode
        nomatch = code == 1 and not status and _search_last(command)
        if nomatch:
            status = "no match - the search found nothing; that's an answer, not an error"
        color = ui.OK if (code == 0 and not status) or nomatch else ui.RED
        ui.out("    " + color + "└ %s · %s · %d lines" % (status or "exit %s" % code, ui.fmt_time(el) if el >= 1 else "%.1fs" % el,
                                                         len(chunks)) + ui.R)
        note = ""
        if stopped_by_user:
            # Ctrl+C on a command stops just that command; the user decides whether Lain carries on
            ui.flush_input()
            try:
                a = input("    " + ui.WARN + "command stopped." + ui.R +
                          " [Enter] let Lain carry on  ·  b = emergency brake  ·  or type a hint > ").strip()
            except (EOFError, KeyboardInterrupt):
                ui.out()
                a = "b"
            if a.lower() in ("b", "brake", "stop", "s"):
                self.halt = True
            else:
                note = ("\nThe user stopped this command because it was taking too long or looked stuck. Don't run "
                        "it again as it was; if you need something like it, make sure it finishes on its own "
                        "(time limits, non-interactive flags).")
                if a:
                    note += " The user says: " + a
        elif status.startswith("stopped after"):
            note = ("\nIt hit the time limit. If it can legitimately take longer (install, build, download), run it "
                    "again with a bigger timeout; if it was waiting on something (network, input), fix that instead.")
        if code not in (0, None) and not nomatch and "&&" in command:
            note += ("\nNote: this command line uses &&, so every command after the one that failed did NOT run - "
                     "their results are missing, not empty. Run independent checks separately (or join them with ;).")
        res = "exit code: %s%s\n" % (code, (" (" + status + ")") if status else "")
        return (res + clip("\n".join(chunks)) if chunks else res + "(no output)") + note

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
        locked = self._self_locked(p)
        if locked:
            return locked
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
        locked = self._self_locked(p)
        if locked:
            return locked
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
        if hits:
            return clip("\n".join(hits))
        hint = (" No file in %s matched the name filter %r - search the parent folder or drop the filter." % (root, glob)
                if scanned == 0 and glob else
                " The pattern is a case-sensitive regex: try (?i) for any case, a shorter word, or a wider path/filter.")
        return "No matches (%d files scanned)." % scanned + hint

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

    def t_service_health(self, name=""):
        name = str(name or "").strip()
        with ui.Spinner("checking services and this boot's logs"):
            text = self._health_win(name) if IS_WIN else self._health_linux(name)
        ui.out("    " + ui.DIM + "└ done" + ui.R)
        return text

    def _health_linux(self, name):
        if not shutil.which("systemctl"):
            return "This system has no systemctl (not systemd) - check its init system's own tools instead."
        rx = re.compile(re.escape(name), re.I) if name else None
        out = []
        props = ("Id,Description,ActiveState,SubState,Type,Result,WantedBy,ExecMainStartTimestampMonotonic,FragmentPath,"
                 "ConditionResult,ConditionTimestampMonotonic")
        for scope, flag in (("user services (systemctl --user, start at login)", ["--user"]),
                            ("system services (systemctl, start at boot)", ["--system"])):
            sc = ["systemctl"] + flag + ["--no-pager"]
            out.append("== %s ==" % scope)
            failed = [l.split()[0] for l in _capture(sc + ["--failed", "--no-legend", "--plain"]).splitlines() if l.split()]
            out.append("failed: " + (", ".join(failed) if failed else "none"))
            enabled = [l.split()[0] for l in _capture(sc + ["list-unit-files", "--state=enabled", "--type=service",
                                                            "--no-legend"]).splitlines() if l.split()]
            enabled = [u for u in enabled if not u.endswith("@.service")]
            down, exited = [], []
            for block in _capture(sc + ["show", "-p", props, "--"] + enabled).split("\n\n") if enabled else []:
                d = dict(l.split("=", 1) for l in block.splitlines() if "=" in l)
                if not d.get("Id") or d.get("ActiveState") in ("active", "activating", "reloading"):
                    continue
                if not set(d.get("WantedBy", "").split()) & _BOOT_TARGETS:
                    continue                    # started on demand (socket, D-Bus, timer...), not at boot/login
                ran = d.get("ExecMainStartTimestampMonotonic", "0") not in ("", "0")
                if d.get("Type") == "oneshot" and d.get("Result") == "success" and ran:
                    continue                    # a one-time job that ran fine
                if d.get("ConditionResult") == "no" and d.get("ConditionTimestampMonotonic", "0") not in ("", "0"):
                    continue                    # skipped on purpose: its start condition isn't met on this machine
                if ran and d.get("Result") == "success":
                    exited.append(d["Id"])      # ran and exited cleanly (often by design)
                    continue
                down.append("  %s - %s (%s/%s, result %s) - %s  [%s]" % (
                    d["Id"], "NEVER STARTED this boot" if not ran else "started, then stopped",
                    d.get("ActiveState"), d.get("SubState"), d.get("Result"), d.get("Description", ""),
                    d.get("FragmentPath", "")))
            out.append("enabled to start at boot/login but NOT running:" + ("\n" + "\n".join(down) if down else " none"))
            if exited:
                out.append("ran this boot and exited normally (usually fine): " + ", ".join(exited))
            jr = ["journalctl"] + flag + ["-b", "--no-pager", "-o", "cat"]
            warn = _capture(jr + ["-p", "warning", "-t", "systemd"]).splitlines()
            warn = [l for l in warn if not l.startswith("-- ")]
            out.append("this boot's warnings from the service manager:" +
                       ("\n  " + "\n  ".join(_count_lines(warn, 30)) if warn else " none"))
            if rx:
                units = set()
                for args in (["list-units", "--all", "--no-legend", "--plain"], ["list-unit-files", "--no-legend"]):
                    for l in _capture(sc + args).splitlines():
                        if l.split() and rx.search(l.split()[0]):
                            units.add(l.split()[0])
                units = sorted(u for u in units if not u.endswith(".mount"))[:8]
                for u in units:
                    out.append("-- %s --" % u)
                    st = _capture(sc + ["status", "-n", "0", "--", u]).strip()
                    out.append(st[:1500] or "(no status)")
                    out.append("unit file: " + (_capture(sc + ["show", "-p", "FragmentPath,UnitFileState,WantedBy,After,"
                                                                "Before,Requires", "--", u]).replace("\n", "; ").strip()))
                    logs = _capture(jr + ["-n", "15", "-u", u]).splitlines()
                    out.append("recent log lines:" + ("\n  " + "\n  ".join(_count_lines(logs, 15)) if logs else " none"))
                lines = [l for l in _capture(jr + ["-p", "info"]).splitlines() if rx.search(l)]
                out.append("this boot's log lines mentioning %r:" % name +
                           ("\n  " + "\n  ".join(_count_lines(lines[-60:], 30)) if lines else " none"))
            out.append("")
        if not name:
            out.append("(Tip: call again with name=... to see full status, unit files and logs of the services involved.)")
        return "\n".join(out)

    def _health_win(self, name):
        flt = name.replace("'", "''")
        ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
'== services set to start automatically but NOT running (delayed/trigger-start ones are often normal) =='
Get-CimInstance Win32_Service | Where-Object { $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' } |
  ForEach-Object { '  {0} - {1} ({2}, last exit code {3})' -f $_.Name, $_.DisplayName, $_.State, $_.ExitCode }
'== your own scheduled tasks whose last run failed =='
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' -and $_.State -ne 'Disabled' } | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo
  if ($i.LastTaskResult -ne 0 -and $i.LastTaskResult -ne 267011) { '  {0}{1} - last result 0x{2:X} at {3}' -f $_.TaskPath, $_.TaskName, $i.LastTaskResult, $i.LastRunTime } }
'== errors since this boot (System + Application, newest first) =='
Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=1,2; StartTime=$boot} -MaxEvents 300 |
  Group-Object ProviderName, Id | Select-Object -First 25 | ForEach-Object {
    $e = $_.Group[0]; $m = ($e.Message -split "`n")[0]; if ($m.Length -gt 200) { $m = $m.Substring(0,200) }
    '  {0}x {1} {2}: {3}' -f $_.Count, $e.ProviderName, $e.Id, $m.Trim() }
$f = '__NAME__'
if ($f) {
  "== everything matching '$f' =="
  Get-Service "*$f*" | ForEach-Object { $c = Get-CimInstance Win32_Service -Filter "Name='$($_.Name)'"
    '  service {0} - {1}: {2}, start {3}, runs {4}' -f $_.Name, $_.DisplayName, $_.Status, $c.StartMode, $c.PathName }
  Get-ScheduledTask | Where-Object { $_.TaskName -like "*$f*" } | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo
    '  task {0}{1}: {2}, last result 0x{3:X} at {4}' -f $_.TaskPath, $_.TaskName, $_.State, $i.LastTaskResult, $i.LastRunTime }
  Get-CimInstance Win32_StartupCommand | Where-Object { $_.Name -like "*$f*" -or $_.Command -like "*$f*" } |
    ForEach-Object { '  startup entry {0}: {1} ({2})' -f $_.Name, $_.Command, $_.Location }
  Get-Process | Where-Object { $_.Name -like "*$f*" } | ForEach-Object { '  running process {0} (pid {1}) {2}' -f $_.Name, $_.Id, $_.Path }
  Get-WinEvent -FilterHashtable @{LogName='System','Application'; StartTime=$boot} -MaxEvents 3000 |
    Where-Object { $_.Message -like "*$f*" -or $_.ProviderName -like "*$f*" } | Select-Object -First 20 | ForEach-Object {
      $m = ($_.Message -split "`n")[0]; if ($m.Length -gt 200) { $m = $m.Substring(0,200) }
      '  log {0:HH:mm:ss} {1} {2}: {3}' -f $_.TimeCreated, $_.LevelDisplayName, $_.ProviderName, $m.Trim() }
}
""".replace("__NAME__", flt)
        return _capture(["powershell.exe", "-NoProfile", "-Command",
                         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + ps], timeout=90).strip() \
            or "Couldn't read services/event logs (PowerShell gave no output)."

    def t_web_search(self, query, max_results=8):
        r = web.web_search(query, max_results)
        ui.out("    " + ui.DIM + "└ %d results" % r.count("\n   http") + ui.R)
        return r

    def t_fetch_url(self, url, max_chars=15000):
        r = web.fetch_url(url, max_chars)
        ui.out("    " + ui.DIM + "└ %s of text" % ui.human(len(r)) + ui.R)
        return r
