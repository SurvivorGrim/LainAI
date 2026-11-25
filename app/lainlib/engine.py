"""Start llama-server and narrate model loading with real progress."""
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request

from . import ui

IS_WIN = os.name == "nt"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _IoReader:
    """Bytes read so far by a process (how much of the model has come off the drive)."""

    def __init__(self, proc):
        self.proc = proc

    def read(self):
        try:
            if IS_WIN:
                import ctypes

                class IOC(ctypes.Structure):
                    _fields_ = [(n, ctypes.c_ulonglong) for n in (
                        "ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
                c = IOC()
                ctypes.windll.kernel32.GetProcessIoCounters(int(self.proc._handle), ctypes.byref(c))
                return c.ReadBytes
            for l in open("/proc/%d/io" % self.proc.pid):
                if l.startswith("rchar:"):
                    return int(l.split()[1])
        except Exception:
            pass
        return 0


# interesting llama.cpp log lines -> friendlier status text
_STAGES = [
    (re.compile(r"ggml_cuda_init|found \d+ CUDA devices|ggml_vulkan: Found"), "initialising GPU"),
    (re.compile(r"llama_model_loader: loaded meta data"), "reading model metadata"),
    (re.compile(r"llama_params_fit|fitting params"), "fitting model to GPU + RAM"),
    (re.compile(r"load_tensors: loading model tensors"), "loading model weights"),
    (re.compile(r"llama_context|llama_kv_cache|kv_cache"), "allocating context memory"),
    (re.compile(r"warming up|warmup"), "warming up"),
    (re.compile(r"server is listening|model loaded"), "ready"),
]
_OFFLOAD = re.compile(r"offloaded (\d+)/(\d+) layers to GPU")
_BUF = re.compile(r"(\S+) (?:model )?buffer size\s*=\s*([\d.]+) MiB")
_NCTX = re.compile(r"n_ctx\s*=\s*(\d+)")
_DEV = re.compile(r"Device \d+: ([^,]+), compute capability")
_ERR = re.compile(r"error|failed|out of memory|unable to|cannot", re.I)


class Engine:
    def __init__(self, root, engine_name, scratch):
        self.root = root
        self.name = engine_name
        self.dir = self._runnable(os.path.join(root, "engine", engine_name), scratch)
        self.exe = os.path.join(self.dir, "llama-server" + (".exe" if IS_WIN else ""))
        self.log_path = os.path.join(scratch, "engine.log")
        self.proc = None
        self.port = _free_port()
        self.key = secrets.token_urlsafe(24)    # only Lain can talk to this engine
        self.url = "http://127.0.0.1:%d" % self.port
        self.stats = {"offload": None, "buffers": {}, "n_ctx": None, "device": None, "load_s": 0}
        try:
            self.version = open(os.path.join(root, "engine", "VERSION")).read().strip()
        except OSError:
            self.version = "?"

    @staticmethod
    def _runnable(d, scratch):
        exe = os.path.join(d, "llama-server")
        if IS_WIN or os.access(exe, os.X_OK):
            return d
        cp = os.path.join(scratch, "engine-bin")
        if not os.path.isdir(cp):
            shutil.copytree(d, cp)
            for f in os.listdir(cp):
                os.chmod(os.path.join(cp, f), 0o700)
        return cp

    def start(self, model_path, sampling, ctx=32768, mmproj=""):
        args = [self.exe, "-m", model_path, "-c", str(ctx),
                "--host", "127.0.0.1", "--port", str(self.port), "--api-key", self.key,
                "--no-webui",                    # terminal only
                "-lm", "none",                   # read the whole model into RAM/VRAM once (no paging off USB)
                "-fa", "auto", "-ctk", "q8_0", "-ctv", "q8_0",   # smaller context cache -> more layers on GPU
                "-ub", "1024", "--cache-reuse", "256", "-np", "1",
                "--cache-ram", "0",              # one chat slot already keeps its cache; the extra RAM prompt cache only eats system RAM
                "--reasoning-format", "deepseek"] + list(sampling)
        if mmproj:
            args += ["--mmproj", mmproj]         # vision add-on: lets the model see images
        env = dict(os.environ)
        if not IS_WIN:
            env["LD_LIBRARY_PATH"] = self.dir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        self.log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, env=env, cwd=self.dir,
                                     creationflags=0x08000000 if IS_WIN else 0)
        self.model_path = model_path
        self.model_size = os.path.getsize(model_path)
        self.model_size += os.path.getsize(mmproj) if mmproj else 0
        self.sampling, self.ctx, self.mmproj = list(sampling), ctx, mmproj

    def alive(self):
        return bool(self.proc) and self.proc.poll() is None

    def restart(self, label):
        """Bring the engine back after it died (same model, port and key)."""
        self.stop()
        self.stats["offload"], self.stats["buffers"] = None, {}
        self.start(self.model_path, self.sampling, self.ctx, self.mmproj)
        return self.wait_ready(label)

    def _healthy(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=1.5) as r:
                return r.status == 200 and json.loads(r.read() or b"{}").get("status", "ok") == "ok"
        except Exception:
            return False

    def _scan_log(self, pos):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                new = f.read()
                return new, f.tell()
        except OSError:
            return "", pos

    def wait_ready(self, label):
        """Narrated load: bytes-off-the-drive bar, then stage spinner, then summary."""
        t0 = time.time()
        io = _IoReader(self.proc)
        base_io = io.read()
        pos, stage, last_line, errors = 0, "starting engine", "", []
        ui.out()
        ui.hi("Loading %s" % label)
        ui.say("engine llama.cpp %s (%s)  |  model %s on the drive" % (self.version, self.name, ui.human(self.model_size)))
        ui.out()
        prog = ui.Progress(self.model_size)
        reading_done = False
        sp = None
        try:
            while True:
                if self.proc.poll() is not None:
                    break
                new, pos = self._scan_log(pos)
                for line in new.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    last_line = s
                    for rx, name in _STAGES:
                        if rx.search(s):
                            stage = name
                    m = _OFFLOAD.search(s)
                    if m:
                        self.stats["offload"] = (int(m.group(1)), int(m.group(2)))
                    m = _BUF.search(s)
                    if m and "load_tensors" in s:
                        self.stats["buffers"][m.group(1)] = float(m.group(2))
                    m = _NCTX.search(s)
                    if m:
                        self.stats["n_ctx"] = int(m.group(1))
                    m = _DEV.search(s)
                    if m:
                        self.stats["device"] = m.group(1).strip()
                    if _ERR.search(s) and "no error" not in s.lower():
                        errors.append(s)
                if self._healthy():
                    break
                got = max(0, io.read() - base_io)
                if not reading_done and got < self.model_size * 0.985:
                    prog.update(min(got, self.model_size), detail="%s  -  %s" % (stage, last_line[-90:]))
                    time.sleep(0.15)
                    continue
                if not reading_done:
                    reading_done = True
                    prog.done("model read into memory in %s" % ui.fmt_time(time.time() - t0))
                    sp = ui.Spinner(stage).__enter__()
                sp.text = stage
                sp.detail = last_line[-100:]
                time.sleep(0.15)
        except KeyboardInterrupt:
            self.stop()
            raise
        finally:
            if sp:
                sp.__exit__(None, None, None)
            elif not reading_done:
                ui.out("\n\033[2K", end="")
        if self.proc.poll() is not None:
            ui.red("The engine stopped while loading. Last log lines:")
            tail = open(self.log_path, encoding="utf-8", errors="replace").read().splitlines()[-15:]
            for l in tail:
                ui.out("   " + ui.DIM + l + ui.R)
            return False
        self.stats["load_s"] = time.time() - t0
        self.summary()
        return True

    def summary(self):
        st = self.stats
        ui.grn("Lain is awake  (ready in %s)" % ui.fmt_time(st["load_s"]))
        bits = []
        if st["offload"]:
            on, tot = st["offload"]
            pct = int(100 * on / tot) if tot else 0
            where = st["device"] or "GPU"
            bits.append("%s: %d/%d layers on GPU (%d%%)" % (where, on, tot, pct))
        gpu_mib = sum(v for k, v in st["buffers"].items() if not k.upper().startswith("CPU"))
        cpu_mib = sum(v for k, v in st["buffers"].items() if k.upper().startswith("CPU"))
        if gpu_mib:
            bits.append("%.1f GB in VRAM" % (gpu_mib / 1024))
        if cpu_mib:
            bits.append("%.1f GB in RAM" % (cpu_mib / 1024))
        if st["n_ctx"]:
            bits.append("context %dK tokens" % (st["n_ctx"] // 1024))
        if bits:
            ui.say("  |  ".join(bits))
        if st["offload"] and st["offload"][0] < st["offload"][1] and self.name.endswith(("cuda", "vulkan")):
            ui.center("Part of this model runs on the CPU, so the GPU will wait on it. A smaller mode runs faster.", ui.WARN)

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
