"""Image studio: create pictures with stable-diffusion.cpp (sd-server), saved into the encrypted vault.
The server loads the models once and stays up, so every picture after the first is quick."""
import base64
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from . import ui
from .engine import _free_port, _IoReader

IS_WIN = os.name == "nt"

HELP = [
    ("just type", "describe the picture you want"),
    ("/size WxH", "picture size, e.g. /size 1344x768 - or /size square | wide | tall"),
    ("/n N", "pictures per prompt (each with its own seed)"),
    ("/seed N", "fix the seed to recreate or tweak a picture  (/seed random to go back)"),
    ("/steps N", "sampling steps (the model's default is tuned; more = slower)"),
    ("/neg TEXT", "things to avoid (only matters for models with cfg > 1)"),
    ("/again", "same prompt again with new seeds"),
    ("/open", "open the last picture in your image viewer"),
    ("/folder", "open the vault's images folder"),
    ("/auto", "toggle opening each picture as soon as it's done"),
    ("/settings", "show the current settings"),
    ("/exit", "disconnect (also Ctrl+D)"),
]


def _slug(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "image"


def open_file(path):
    """Show a picture or folder with the system's default app (detached, quiet)."""
    try:
        if IS_WIN:
            os.startfile(path)                     # noqa: S606 - user's own file
        else:
            opener = shutil.which("xdg-open") or shutil.which("gio")
            if not opener:
                return False
            args = [opener, path] if not opener.endswith("gio") else [opener, "open", path]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


class ImageEngine:
    def __init__(self, root, pick, scratch):
        self.root = root
        self.pick = pick
        plat = "win" if IS_WIN else "linux"
        self.flavor = "%s-%s" % (plat, "vulkan" if pick.get("img_gpu") else "cpu")
        self.dir = self._runnable(os.path.join(root, "engine", "image", self.flavor), scratch)
        self.exe = os.path.join(self.dir, "sd-server" + (".exe" if IS_WIN else ""))
        self.log_path = os.path.join(scratch, "image-engine.log")
        self.port = _free_port()
        self.url = "http://127.0.0.1:%d" % self.port
        self.proc = None
        self.loaded = True
        try:
            self.version = open(os.path.join(root, "engine", "image", "VERSION")).read().strip()
        except OSError:
            self.version = "?"

    @staticmethod
    def _runnable(d, scratch):
        exe = os.path.join(d, "sd-server")
        if IS_WIN or os.access(exe, os.X_OK):
            return d
        cp = os.path.join(scratch, "image-engine-bin")
        if not os.path.isdir(cp):
            shutil.copytree(d, cp)
            for f in os.listdir(cp):
                os.chmod(os.path.join(cp, f), 0o700)
        return cp

    def files(self):
        md = os.path.join(self.root, "models")
        return [os.path.join(md, self.pick[k]) for k in ("file", "llm", "vae")]

    def start(self):
        diff, llm, vae = self.files()
        args = [self.exe, "--diffusion-model", diff, "--llm", llm, "--vae", vae,
                "--offload-to-cpu",                     # text encoder and drawing model take turns in video memory
                "--eager-load",                         # read every weight before the server opens (no prompt until loaded)
                "--vae-tiling",                         # decode big pictures in tiles (less video memory)
                "--listen-ip", "127.0.0.1", "--listen-port", str(self.port),
                "--steps", str(self.pick["steps"]), "--cfg-scale", str(self.pick["cfg"]),
                "--sampling-method", self.pick["sampler"]]
        if not self.pick.get("img_gpu"):
            args += ["--threads", str(max(1, (os.cpu_count() or 4) - 1))]
        env = dict(os.environ)
        if not IS_WIN:
            env["LD_LIBRARY_PATH"] = self.dir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        self.log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, env=env, cwd=self.dir,
                                     creationflags=0x08000000 if IS_WIN else 0)
        self.total = sum(os.path.getsize(f) for f in (diff, llm, vae))

    def _get(self, path, timeout=5):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def _post(self, path, body, timeout=30):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def _ready(self):
        try:
            self._get("/sdcpp/v1/capabilities", 1.5)
            return True
        except Exception:
            return False

    def wait_ready(self):
        t0 = time.time()
        io = self._io = _IoReader(self.proc)
        base = self._base = io.read()
        ui.out()
        ui.hi("Loading %s (image studio)" % self.pick["name"])
        ui.say("engine stable-diffusion.cpp %s (%s)  |  models %s on the drive" % (self.version, self.flavor, ui.human(self.total)))
        ui.out()
        prog = ui.Progress(self.total)
        done_reading, sp = False, None
        try:
            while self.proc.poll() is None:
                if self._ready():
                    break
                got = max(0, io.read() - base)
                if not done_reading and got < self.total * 0.985:
                    prog.update(min(got, self.total), detail=self._last_log()[-90:])
                    time.sleep(0.2)
                    continue
                if not done_reading:
                    done_reading = True
                    prog.done("models read in %s" % ui.fmt_time(time.time() - t0))
                    sp = ui.Spinner("preparing the image engine").__enter__()
                sp.detail = self._last_log()[-100:]
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.stop()
            raise
        finally:
            if sp:
                sp.__exit__(None, None, None)
            elif not done_reading:
                if max(0, io.read() - base) >= self.total * 0.5:
                    prog.done("models read in %s" % ui.fmt_time(time.time() - t0))   # finish bar + clear log line
                else:
                    ui.out("\r\033[2K\n\033[2K\033[1A", end="")   # erase bar and the log line under it
        if self.proc.poll() is not None:
            ui.red("The image engine stopped while loading. Last log lines:")
            for l in open(self.log_path, encoding="utf-8", errors="replace").read().splitlines()[-15:]:
                ui.out("   " + ui.DIM + l + ui.R)
            return False
        ui.grn("Image studio ready  (%s)" % ui.fmt_time(time.time() - t0))
        # newer sd.cpp opens the server before reading the weights; they are read on the first picture
        self.loaded = max(0, io.read() - base) >= self.total * 0.5
        if not self.loaded:
            ui.center("The models (%s) are read from the drive when you make your first picture - "
                      "that one takes longer." % ui.human(self.total), ui.WARN)
        if not self.pick.get("img_gpu"):
            ui.center("No graphics card with enough free memory - drawing on the CPU. Expect minutes per picture.", ui.WARN)
        return True

    def _last_log(self):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(max(0, os.path.getsize(self.log_path) - 400))
                tail = f.read().decode("utf-8", "replace").replace("\r", "\n").strip().splitlines()
                return tail[-1].strip() if tail else ""
        except OSError:
            return ""

    def generate(self, prompt, width, height, seed, steps, neg=""):
        """Draw one picture; returns PNG bytes. Ctrl+C cancels it on the server."""
        sp = {"sample_method": self.pick["sampler"], "sample_steps": steps,
              "guidance": {"txt_cfg": self.pick["cfg"]}}
        if self.pick["sigmas"] and steps == self.pick["steps"]:
            sp["custom_sigmas"] = self.pick["sigmas"]       # tuned schedule (Turbo models)
        body = {"prompt": prompt, "negative_prompt": neg, "width": width, "height": height, "seed": seed,
                "batch_count": 1, "embed_image_metadata": False,     # don't hide the prompt inside the file
                "sample_params": sp}
        try:
            job = self._post("/sdcpp/v1/img_gen", body)
        except urllib.error.HTTPError as e:
            raise RuntimeError("engine refused: %s" % e.read().decode("utf-8", "replace")[:300])
        jid, t0, last = job["id"], time.time(), None
        try:
            while True:
                if self.proc.poll() is not None:
                    raise RuntimeError("the image engine stopped (see %s)" % self.log_path)
                j = self._get("/sdcpp/v1/jobs/" + jid, 30)
                st = j.get("status")
                if st == "completed":
                    imgs = (j.get("result") or {}).get("images") or []
                    if not imgs:
                        raise RuntimeError("the engine returned no picture")
                    ui.clear_line()
                    return base64.b64decode(imgs[0]["b64_json"])
                if st in ("failed", "cancelled"):
                    ui.clear_line()
                    err = j.get("error") or {}
                    raise RuntimeError(err.get("message") if isinstance(err, dict) else str(err) or st)
                pv = j.get("preview") or {}
                el = ui.fmt_time(time.time() - t0)
                if pv.get("total_steps"):
                    self.loaded = True
                got = 0 if self.loaded else max(0, self._io.read() - self._base)
                if st == "queued":
                    text = "queued"
                elif not self.loaded and got < self.total * 0.985:
                    text = "loading models from the drive %s %s / %s" % (
                        ui.bar_str(got / self.total, 20), ui.human(got), ui.human(self.total))
                elif st == "queued":
                    text = "queued"
                elif pv.get("total_steps"):
                    text = "drawing %s step %d/%d" % (ui.bar_str(pv["step"] / pv["total_steps"], 20), pv["step"], pv["total_steps"])
                else:
                    text = "reading your prompt" if last is None else "finishing the picture"
                last = pv.get("step") or last
                ui.clear_line()
                ui.out("  %s%s%s %s  %s" % (ui.ART, ui.SPIN[int(time.time() * 8) % len(ui.SPIN)], ui.R, text, ui.DIM + el + ui.R), end="")
                time.sleep(0.25)
        except KeyboardInterrupt:
            ui.clear_line()
            ui.out(ui.WARN + "  cancelling..." + ui.R)
            try:
                self._post("/sdcpp/v1/jobs/%s/cancel" % jid, {}, 10)
                for _ in range(120):
                    if self._get("/sdcpp/v1/jobs/" + jid, 10).get("status") in ("cancelled", "completed", "failed"):
                        break
                    time.sleep(0.25)
            except Exception:
                pass
            raise

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            self.log.close()
        except Exception:
            pass


class Studio:
    def __init__(self, engine, pick, out_dir):
        self.engine = engine
        self.pick = pick
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.w, self.h = pick["width"], pick["height"]
        self.n, self.seed, self.steps, self.neg = 1, None, pick["steps"], ""
        self.auto_open = False
        self.last_prompt, self.last_file = "", None

    def settings(self):
        ui.out(ui.DIM + "  %s  ·  %dx%d  ·  %d per prompt  ·  seed %s  ·  %d steps%s  ·  auto-open %s" % (
            self.pick["name"], self.w, self.h, self.n, self.seed if self.seed is not None else "random", self.steps,
            "  ·  avoid: " + self.neg if self.neg else "", "on" if self.auto_open else "off") + ui.R)

    def _size(self, arg):
        base = max(self.pick["width"], self.pick["height"])
        presets = {"square": (base, base), "wide": (base * 4 // 3, base * 3 // 4), "tall": (base * 3 // 4, base * 4 // 3)}
        if arg in presets:
            w, h = presets[arg]
        else:
            m = re.fullmatch(r"(\d+)\s*[x*]\s*(\d+)", arg)
            if not m:
                ui.out(ui.DIM + "  usage: /size 1024x768  or  /size square | wide | tall" + ui.R)
                return
            w, h = int(m.group(1)), int(m.group(2))
        w, h = max(256, min(2048, w // 32 * 32)), max(256, min(2048, h // 32 * 32))   # models want multiples of 32
        self.w, self.h = w, h
        ui.out(ui.DIM + "  size %dx%d" % (w, h) + ui.R)

    def command(self, line):
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("/exit", "/quit", "/q"):
            return False
        if cmd == "/help":
            for k, v in HELP:
                ui.out("  " + ui.ART + "%-12s" % k + ui.R + " " + v)
            ui.out(ui.DIM + "  Ctrl+C while drawing cancels that picture." + ui.R)
        elif cmd == "/size":
            self._size(arg.lower())
        elif cmd == "/n":
            self.n = max(1, min(16, int(arg))) if arg.isdigit() else self.n
            ui.out(ui.DIM + "  %d per prompt" % self.n + ui.R)
        elif cmd == "/seed":
            self.seed = int(arg) if arg.isdigit() else None
            ui.out(ui.DIM + "  seed %s" % (self.seed if self.seed is not None else "random") + ui.R)
        elif cmd == "/steps":
            self.steps = max(1, min(100, int(arg))) if arg.isdigit() else self.pick["steps"]
            ui.out(ui.DIM + "  %d steps%s" % (self.steps, " (model default)" if self.steps == self.pick["steps"] else "") + ui.R)
        elif cmd == "/neg":
            self.neg = arg
            ui.out(ui.DIM + ("  avoid: " + arg if arg else "  nothing to avoid") + ui.R)
            if arg and self.pick["cfg"] <= 1.0:
                ui.out(ui.DIM + "  (this model runs with cfg 1, so it mostly ignores the avoid list)" + ui.R)
        elif cmd == "/again":
            if self.last_prompt:
                self.make(self.last_prompt)
            else:
                ui.out(ui.DIM + "  nothing to repeat yet" + ui.R)
        elif cmd == "/open":
            if not self.last_file or not open_file(self.last_file):
                ui.out(ui.DIM + "  nothing to open yet" + ui.R if not self.last_file else ui.RED + "  couldn't open an image viewer" + ui.R)
        elif cmd == "/folder":
            open_file(self.out_dir) or ui.out(ui.RED + "  couldn't open a file manager" + ui.R)
        elif cmd == "/auto":
            self.auto_open = not self.auto_open
            ui.out(ui.DIM + "  auto-open %s" % ("on" if self.auto_open else "off") + ui.R)
        elif cmd == "/settings":
            self.settings()
        else:
            ui.out(ui.DIM + "  unknown command - /help lists them" + ui.R)
        return True

    def make(self, prompt):
        self.last_prompt = prompt
        for i in range(self.n):
            seed = (self.seed + i) if self.seed is not None else random.randrange(1, 2**31 - 1)
            if self.n > 1:
                ui.out(ui.DIM + "  picture %d of %d" % (i + 1, self.n) + ui.R)
            t0 = time.time()
            try:
                png = self.engine.generate(prompt, self.w, self.h, seed, self.steps, self.neg)
            except KeyboardInterrupt:
                ui.out(ui.WARN + "  cancelled" + ui.R)
                return
            except (RuntimeError, OSError, ValueError) as e:
                ui.out(ui.RED + "  couldn't make the picture: %s" % e + ui.R)
                return
            name = "%s-%s-%d.png" % (datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), _slug(prompt), seed)
            path = os.path.join(self.out_dir, name)
            with open(path, "wb") as f:
                f.write(png)
            self.last_file = path
            ui.out(ui.OK + "  ✓ " + ui.R + "%s  %s" % (name, ui.DIM + "%dx%d · seed %d · %s" % (
                self.w, self.h, seed, ui.fmt_time(time.time() - t0)) + ui.R))
            if self.auto_open:
                open_file(path)

    def loop(self):
        ui.out()
        ui.say("Describe a picture and press Enter. Pictures are saved in your encrypted vault (images/).")
        ui.say("/help for options  ·  /open shows the last picture  ·  Ctrl+C cancels  ·  /exit to disconnect")
        self.settings()
        while True:
            try:
                line = input("\n" + ui.ART + "image ❯ " + ui.R)
            except EOFError:
                ui.out()
                return
            except KeyboardInterrupt:
                ui.out(ui.DIM + "\n  (type /exit to disconnect)" + ui.R)
                continue
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    return
                continue
            self.make(line)
