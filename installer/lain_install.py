"""Lain AI installer core: puts everything Lain needs onto a drive (portable, works on Windows + Linux).

Started by install.sh (Linux) or install.ps1 (Windows) with the bundled portable Python.
Run it directly only if you know what you're doing:
    python3 installer/lain_install.py --drive /media/you/LAINAI [--models all|recommended|best,mid,laptop|none] [--yes]

Safe to re-run: finished pieces are skipped (checksum-verified), partial downloads resume,
and your vault (vault/) is never touched.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))
from lainlib import hw, models, ui  # noqa: E402  (Lain's own modules: same look, same hardware logic)

IS_WIN = os.name == "nt"
HOST = "win" if IS_WIN else "linux"
UA = {"User-Agent": "LainAI-installer"}
HF = "https://huggingface.co"
GHDL = "https://github.com/%s/releases/download/%s/%s"

# ---------------------------------------------------------------- pinned versions
# Bump these (with their sha256 from the GitHub release page) to ship newer builds.
# Lain's own updater (menu "u") can move an installed drive to newer engine/age releases later.
LLAMA_TAG = "b12651"
LLAMA = {   # engine folder -> [(file, sha256, bytes)]
    "win-cuda": [("llama-b12651-bin-win-cuda-12.4-x64.zip", "1a6c090f76fab41260b5b7597c6fb1add1fad2efaac692ae2673a22366220a29", 358031654),
                 ("cudart-llama-bin-win-cuda-12.4-x64.zip", "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6", 391443627)],
    "win-vulkan": [("llama-b12651-bin-win-vulkan-x64.zip", "e58fa9a137d8046c69334005ad6fb3e904ddc7e56f2a61ad1b844335826ef3f4", 38939211)],
    "win-cpu": [("llama-b12651-bin-win-cpu-x64.zip", "b2d794fa7587aba29f654d717ff9b73e31caeabf50a7766692903008bbe0855d", 25096571)],
    "linux-cuda": [("llama-b12651-bin-ubuntu-cuda-12.8-x64.tar.gz", "cb099350c6a025e67eefaa13f2b67b2dcce53e38a0da22476e070d166ef367ea", 220195495),
                   ("cudart-llama-b12651-bin-ubuntu-cuda-12.8-x64.tar.gz", "485df8f0b0ab30c15807d25fd23a821aeaef62a3947722c683429491a5e0d027", 594371475)],
    "linux-vulkan": [("llama-b12651-bin-ubuntu-vulkan-x64.tar.gz", "0bbcf2093f15c64c8a9bf54b809c9b2c812b982992dc2fbc9b36720c1d32cf13", 36976824)],
    "linux-cpu": [("llama-b12651-bin-ubuntu-x64.tar.gz", "17b242259181d5b1b1f7719aba27929700a8b0dab39bfaef1b971a992b397b38", 23297838)],
}
# stable-diffusion.cpp: last release with Ubuntu 24.04 builds (newer Linux builds need glibc 2.43).
SD_TAG = "master-1043-3be2378"
SD = {
    "win-vulkan": ("sd-master-3be2378-bin-win-vulkan-x64.zip", "a5e1fa679561a3bb99b114a8f4a41e67239da29173ff725391ecfa5772e869ee", 29796631),
    "win-cpu": ("sd-master-3be2378-bin-win-cpu-x64.zip", "10ef877a334c97d014e3e23b4bf6c9077f6b78ff9da3bb157664d2103cb1ae0f", 18067084),
    "linux-vulkan": ("sd-master-3be2378-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip", "7608901016fb4a348bc5b75c46fc971085dadadf44e6f30daa97301844b1ce2e", 37675781),
    "linux-cpu": ("sd-master-3be2378-bin-Linux-Ubuntu-24.04-x86_64.zip", "01e1c773c1bc2a6276952f6dbd13294e58b7255c659b4a770086934c4e8fad3a", 27322680),
}
AGE_TAG = "v1.3.2"
AGE = {
    "win": ("age-v1.3.2-windows-amd64.zip", "f48d8f8f9ebe903ab5027ed067652f2cc1db94bc206976430133b905dcd8e8c7", 20250704),
    "linux": ("age-v1.3.2-linux-amd64.tar.gz", "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10", 19405817),
}
PY_TAG, PY_VER = "20261123", "3.13.18"      # keep in sync with install.sh / install.ps1
PYTHON = {
    "linux": ("cpython-3.13.18+20261123-x86_64-unknown-linux-gnu-install_only.tar.gz", "7d42d8c2da1e5beeddc4aa086faccb7438b1676e2e14bdf65f06e9a5d0ae9aea"),
    "win": ("cpython-3.13.18+20261123-x86_64-pc-windows-msvc-install_only.tar.gz", "15ed701bae38157e36fbbc9f1cfcb144dd2b49868e962a055235a3d72fa30212"),
}
# Python parts Lain never uses (and terminfo, whose case-only name clashes exFAT can't store)
PY_SKIP = ("/share/", "/include/", "/test/", "/tests/", "/idlelib/", "/tkinter/", "/turtledemo/", "/lib2to3/", "/ensurepip/")


# ---------------------------------------------------------------- output + log
class Log:
    f = None
    t0 = time.time()
    warnings = []

    @classmethod
    def open(cls, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cls.f = open(path, "a", encoding="utf-8")
        cls.write("==== Lain AI installer started %s" % datetime.datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def write(cls, msg):
        if cls.f:
            cls.f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), ui._ANSI.sub("", msg)))
            cls.f.flush()


def say(msg="", color=""):
    ui.out("  " + color + msg + (ui.R if color else ""))
    Log.write(msg)


def info(msg):
    say(msg, ui.DIM)


def ok(msg):
    say("✓ " + msg, ui.OK)


def warn(msg):
    say("! " + msg, ui.WARN)
    Log.warnings.append(msg)


def err(msg):
    say("✗ " + msg, ui.RED)


STEP = [0, 10]


def step(title):
    STEP[0] += 1
    ui.out()
    line = "[%d/%d] %s" % (STEP[0], STEP[1], title)
    ui.out(ui.ART + "━━ " + ui.HI + line + ui.R + "  " + ui.DIM + "(%s elapsed)" % ui.fmt_time(time.time() - Log.t0) + ui.R)
    Log.write("== " + line)


def ask(prompt, default="", yes=False):
    if yes:
        info("%s %s  (automatic)" % (prompt, default))
        return default
    try:
        a = input("  " + ui.HI + prompt + ui.R + " ").strip()
    except EOFError:
        a = ""
    Log.write("%s -> %r" % (prompt, a))
    return a or default


class Bar:
    """Left-aligned byte progress bar with speed and time left."""

    def __init__(self, total, verb="downloading"):
        self.total, self.verb = max(1, total), verb
        self.t, self.c, self.speed, self.drawn = time.time(), 0, 0.0, 0.0

    def update(self, cur, force=False):
        now = time.time()
        if now - self.t >= 1.0:
            inst = (cur - self.c) / (now - self.t)
            self.speed = inst if not self.speed else self.speed * 0.7 + inst * 0.3
            self.t, self.c = now, cur
        if not force and now - self.drawn < 0.25:
            return
        self.drawn = now
        frac = min(1.0, cur / self.total)
        eta = ("%s left" % ui.fmt_time((self.total - cur) / self.speed)) if self.speed > 0 and cur < self.total else ""
        ui.clear_line()
        ui.out("    %s %3d%%  %s / %s  %s/s  %s" % (ui.bar_str(frac, 24), int(frac * 100), ui.human(cur),
                                                  ui.human(self.total), ui.human(self.speed), eta), end="")

    def done(self):
        self.update(self.total, force=True)
        ui.out()


# ---------------------------------------------------------------- network + files
def get_json(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except (OSError, ValueError) as e:
            if i == tries - 1:
                raise
            info("  network hiccup (%s) - retrying in %ds" % (e, 3 * (i + 1)))
            time.sleep(3 * (i + 1))


def sha256_file(path, label="verifying"):
    size = os.path.getsize(path)
    h = hashlib.sha256()
    bar = Bar(size) if size > 200 * 2**20 else None
    if bar:
        info("  %s %s (%s) ..." % (label, os.path.basename(path), ui.human(size)))
    done = 0
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""):
            h.update(b)
            done += len(b)
            if bar:
                bar.update(done)
    if bar:
        bar.done()
    return h.hexdigest()


def read_sums(path):
    out = {}
    try:
        for l in open(path, encoding="utf-8").read().splitlines():
            p = l.split(None, 1)
            if len(p) == 2:
                out[p[1].strip()] = p[0]
    except OSError:
        pass
    return out


def record_sum(path, name, sha):
    sums = read_sums(path)
    sums[name] = sha
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write("".join("%s  %s\n" % (v, k) for k, v in sums.items()))


def download(url, dest, sha, size, label, tries=6):
    """Resumable, retrying, checksum-verified download. Returns True when dest is in place and verified."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    say("↓ %s  (%s)" % (label, ui.human(size) if size else "size unknown"), ui.HI)
    info("  from %s" % url.split("?")[0])
    part = dest + ".part"
    for attempt in range(1, tries + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if size and have > size:
            os.remove(part)
            have = 0
        free = shutil.disk_usage(os.path.dirname(dest)).free
        if size and free < size - have + 64 * 2**20:
            err("not enough free space on %s: %s free, %s needed" % (os.path.dirname(dest), ui.human(free), ui.human(size - have)))
            return False
        h = hashlib.sha256()
        if have:
            info("  resuming at %s - re-reading the part already downloaded" % ui.human(have))
            with open(part, "rb") as f:
                for b in iter(lambda: f.read(8 << 20), b""):
                    h.update(b)
        bar = Bar(size or 1)
        cur = have
        if size and have == size:          # finished earlier but not yet verified
            info("  already fully downloaded - verifying")
        else:
            try:
                hdr = dict(UA, Range="bytes=%d-" % have) if have else UA
                with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60) as r:
                    if have and r.status != 206:
                        info("  server can't resume - starting this file over")
                        h, have, cur = hashlib.sha256(), 0, 0
                        mode = "wb"
                    else:
                        mode = "ab"
                    if not size:
                        size = have + int(r.headers.get("Content-Length") or 0)
                        bar.total = max(1, size)
                    with open(part, mode) as f:
                        for b in iter(lambda: r.read(1 << 20), b""):
                            f.write(b)
                            h.update(b)
                            cur += len(b)
                            bar.update(cur)
                        f.flush()
                        os.fsync(f.fileno())
                bar.done()
            except (OSError, urllib.error.URLError) as e:
                ui.out()
                if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                    err("server said %s %s - this file may have moved or been renamed upstream" % (e.code, e.reason))
                    return False
                if attempt == tries:
                    err("download failed after %d tries: %s" % (tries, e))
                    info("  run the installer again to resume from %s" % ui.human(cur))
                    return False
                wait = min(60, 5 * attempt)
                warn("connection problem (%s) at %s - retrying in %ds (try %d of %d)" % (e, ui.human(cur), wait, attempt + 1, tries))
                time.sleep(wait)
                continue
        if size and cur != size:
            warn("got %s of %s - retrying" % (ui.human(cur), ui.human(size)))
            continue
        got = h.hexdigest()
        if sha and got != sha:
            os.remove(part)
            if attempt == tries:
                err("checksum mismatch: expected %s, got %s - file discarded" % (sha[:16], got[:16]))
                return False
            warn("checksum mismatch (corrupted transfer) - discarding and downloading again")
            continue
        os.replace(part, dest)
        ok("verified sha256 %s…" % (sha or got)[:16] if sha else "downloaded (no published checksum)")
        return True
    return False


def extract(archive, dest, skip=()):
    """Extract zip/tar.gz; links become real files (exFAT can't store links); 'skip' drops matching paths."""
    os.makedirs(dest, exist_ok=True)
    real = os.path.realpath(dest)
    n = 0
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for m in z.infolist():
                p = "/" + m.filename.replace("\\", "/")
                if any(s in p for s in skip):
                    continue
                target = os.path.realpath(os.path.join(dest, m.filename))
                if not target.startswith(real + os.sep):
                    continue
                if m.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                n += 1
        return n
    with tarfile.open(archive) as t:
        for m in t.getmembers():
            p = "/" + m.name
            if any(s in p for s in skip):
                continue
            target = os.path.realpath(os.path.join(dest, m.name))
            if not target.startswith(real + os.sep):
                continue
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isfile() or m.issym() or m.islnk():
                try:
                    src = t.extractfile(m)        # follows links to their target's data
                except KeyError:
                    src = None
                if src is None:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as f:
                    shutil.copyfileobj(src, f)
                if not IS_WIN:
                    os.chmod(target, 0o755 if (m.mode & 0o111 or m.issym()) else 0o644)
                n += 1
    return n


def copy_tree(src, dst):
    """Copy a folder onto the drive with a file counter (exFAT: no permissions/links)."""
    files = [os.path.join(dp, f) for dp, _, fs in os.walk(src) for f in fs]
    total = sum(os.path.getsize(f) for f in files) or 1
    bar = Bar(total, "copying")
    done = 0
    for f in files:
        rel = os.path.relpath(f, src)
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(f, out)
        try:
            os.chmod(out, 0o755)
        except OSError:
            pass
        done += os.path.getsize(f)
        bar.update(done)
    bar.done()
    return len(files)


def runnable(exe, tmp):
    """Path we can execute: the drive copy, or (if the drive is mounted noexec) a temp copy of its folder."""
    if IS_WIN or os.access(exe, os.X_OK):
        return exe
    d = os.path.join(tmp, "selftest-" + os.path.basename(os.path.dirname(exe)))
    if not os.path.isdir(d):
        shutil.copytree(os.path.dirname(exe), d)
        for f in os.listdir(d):
            os.chmod(os.path.join(d, f), 0o755)
    return os.path.join(d, os.path.basename(exe))


def selftest(exe, tmp, args=("--version",)):
    try:
        exe = runnable(exe, tmp)
        env = dict(os.environ)
        if not IS_WIN:
            env["LD_LIBRARY_PATH"] = os.path.dirname(exe)
        p = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=60, env=env)
        out = (p.stdout + p.stderr).strip().splitlines()
        return p.returncode == 0, (out[-1] if out else "")
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- install steps
def install_python(lain, tmp, host_archive, force):
    for plat in ("linux", "win"):
        dst = os.path.join(lain, "runtime", plat)
        name, sha = PYTHON[plat]
        if not force and open_text(os.path.join(dst, "VERSION")) == "%s+%s" % (PY_VER, PY_TAG):
            ok("Python %s for %s already on the drive" % (PY_VER, "Windows" if plat == "win" else "Linux"))
            continue
        arch = os.path.join(tmp, name)
        if host_archive and os.path.basename(host_archive) == name and os.path.exists(host_archive):
            info("reusing the copy the bootstrap already downloaded: %s" % name)
            if sha256_file(host_archive) != sha:
                err("bootstrap copy failed its checksum")
                return False
            shutil.copyfile(host_archive, arch)
        elif not download(GHDL % ("astral-sh/python-build-standalone", PY_TAG, name.replace("+", "%2B")), arch, sha, 0,
                          "Python %s (%s)" % (PY_VER, "Windows" if plat == "win" else "Linux")):
            return False
        info("unpacking (skipping tests, headers, Tk and other parts Lain doesn't use)...")
        stage = os.path.join(tmp, "py-" + plat)
        n = extract(arch, stage, PY_SKIP)
        os.remove(arch)
        info("%d files unpacked - copying to the drive" % n)
        shutil.rmtree(dst, ignore_errors=True)
        copy_tree(os.path.join(stage, "python"), dst)
        write_text(os.path.join(dst, "VERSION"), "%s+%s" % (PY_VER, PY_TAG))
        shutil.rmtree(stage, ignore_errors=True)
        ok("Python %s for %s installed" % (PY_VER, "Windows" if plat == "win" else "Linux"))
    return True


def install_llama(lain, tmp, force):
    eng = os.path.join(lain, "engine")
    have = all(os.path.exists(os.path.join(eng, e, "llama-server" + (".exe" if e.startswith("win") else ""))) for e in LLAMA)
    if not force and have and open_text(os.path.join(eng, "VERSION")) == LLAMA_TAG:
        ok("llama.cpp %s already on the drive (all %d builds)" % (LLAMA_TAG, len(LLAMA)))
        return True
    info("6 builds so the drive runs anywhere: NVIDIA (CUDA), AMD/Intel (Vulkan) and CPU-only, for Windows and Linux")
    for i, (name, files) in enumerate(LLAMA.items(), 1):
        say("· build %d/%d: %s" % (i, len(LLAMA), name), ui.HI)
        stage = os.path.join(tmp, "eng-" + name)
        for f, sha, size in files:
            arch = os.path.join(tmp, f)
            if not download(GHDL % ("ggml-org/llama.cpp", LLAMA_TAG, f), arch, sha, size, f):
                return False
            info("  unpacking %s ..." % f)
            info("  %d files" % extract(arch, stage))
            os.remove(arch)
        srv = next((os.path.join(dp, x) for dp, _, fs in os.walk(stage) for x in fs if x in ("llama-server", "llama-server.exe")), None)
        if not srv:
            err("llama-server not found inside the %s download" % name)
            return False
        dst = os.path.join(eng, name)
        shutil.rmtree(dst, ignore_errors=True)
        info("  copying to engine/%s ..." % name)
        copy_tree(os.path.dirname(srv), dst)
        extra = 0
        for dp, _, fs in os.walk(stage):              # CUDA runtime libs ship in a separate archive/folder
            for x in fs:
                if (x.startswith(("cudart", "cublas")) and x.endswith(".dll")) or x.startswith(("libcudart", "libcublas")):
                    if os.path.dirname(os.path.join(dp, x)) != os.path.dirname(srv):
                        shutil.copyfile(os.path.join(dp, x), os.path.join(dst, x))
                        extra += 1
        if extra:
            info("  + %d CUDA runtime libraries" % extra)
        shutil.rmtree(stage, ignore_errors=True)
        ok("engine/%s ready" % name)
    write_text(os.path.join(eng, "VERSION"), LLAMA_TAG)
    return True


def install_sd(lain, tmp, force):
    eng = os.path.join(lain, "engine", "image")
    have = all(os.path.exists(os.path.join(eng, e, "sd-server" + (".exe" if e.startswith("win") else ""))) for e in SD)
    if not force and have and open_text(os.path.join(eng, "VERSION")) == SD_TAG:
        ok("stable-diffusion.cpp %s already on the drive" % SD_TAG)
        return True
    info("4 builds: GPU (Vulkan - NVIDIA, AMD, Intel) and CPU, for Windows and Linux")
    for i, (name, (f, sha, size)) in enumerate(SD.items(), 1):
        say("· build %d/%d: %s" % (i, len(SD), name), ui.HI)
        arch = os.path.join(tmp, f)
        if not download(GHDL % ("leejet/stable-diffusion.cpp", SD_TAG, f), arch, sha, size, f):
            return False
        stage = os.path.join(tmp, "sd-" + name)
        info("  unpacking: %d files" % extract(arch, stage))
        os.remove(arch)
        srv = next((os.path.join(dp, x) for dp, _, fs in os.walk(stage) for x in fs if x in ("sd-server", "sd-server.exe")), None)
        if not srv:
            err("sd-server not found inside %s" % f)
            return False
        dst = os.path.join(eng, name)
        shutil.rmtree(dst, ignore_errors=True)
        copy_tree(os.path.dirname(srv), dst)
        shutil.rmtree(stage, ignore_errors=True)
        ok("engine/image/%s ready" % name)
    write_text(os.path.join(eng, "VERSION"), SD_TAG)
    return True


def install_age(lain, tmp, force):
    b = os.path.join(lain, "bin")
    if not force and open_text(os.path.join(b, "VERSION")) == AGE_TAG and \
            os.path.exists(os.path.join(b, "win", "age.exe")) and os.path.exists(os.path.join(b, "linux", "age")):
        ok("age %s already on the drive" % AGE_TAG)
        return True
    for plat, (f, sha, size) in AGE.items():
        arch = os.path.join(tmp, f)
        if not download(GHDL % ("FiloSottile/age", AGE_TAG, f), arch, sha, size, "age %s (%s)" % (AGE_TAG, plat)):
            return False
        stage = os.path.join(tmp, "age-" + plat)
        extract(arch, stage)
        os.remove(arch)
        os.makedirs(os.path.join(b, plat), exist_ok=True)
        n = 0
        for dp, _, fs in os.walk(stage):
            for x in fs:
                if x.startswith("age") and not x.endswith(".txt"):
                    shutil.copyfile(os.path.join(dp, x), os.path.join(b, plat, x))
                    if not IS_WIN:
                        os.chmod(os.path.join(b, plat, x), 0o755)
                    n += 1
        shutil.rmtree(stage, ignore_errors=True)
        ok("bin/%s: %d age tools" % (plat, n))
    write_text(os.path.join(b, "VERSION"), AGE_TAG)
    return True


def install_app(drive, lain):
    app = os.path.join(lain, "app")
    if os.path.isdir(app):
        info("replacing the old app/ folder (your vault, models and settings are not in it)")
        shutil.rmtree(app, ignore_errors=True)
    os.makedirs(os.path.join(app, "lainlib"))
    files = [("app/lain.py", "app/lain.py")]
    files += [("app/lainlib/" + f, "app/lainlib/" + f) for f in sorted(os.listdir(os.path.join(REPO, "app", "lainlib"))) if f.endswith(".py")]
    files += [(f, f) for f in ("models.conf", "images.conf", "README.txt", "Lain.bat")]
    files += [("data/wordlist.txt", "data/wordlist.txt"), ("data/common-words.txt", "data/common-words.txt")]
    keep = [("quotes.txt", "quotes.txt"), ("art/splash.txt", "art/splash.txt")]
    for src, dst in files:
        os.makedirs(os.path.dirname(os.path.join(lain, dst)), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, src), os.path.join(lain, dst))
    info("%d program files copied" % len(files))
    for src, dst in keep:
        if os.path.exists(os.path.join(lain, dst)):
            info("kept your own %s" % dst)
        else:
            os.makedirs(os.path.dirname(os.path.join(lain, dst)), exist_ok=True)
            shutil.copyfile(os.path.join(REPO, src), os.path.join(lain, dst))
    for f in ("linux.sh", "windows.ps1"):
        shutil.copyfile(os.path.join(REPO, "root", f), os.path.join(drive, f))
        if not IS_WIN:
            os.chmod(os.path.join(drive, f), 0o755)
    ok("launchers on the drive root: linux.sh and windows.ps1")


# ---------------------------------------------------------------- models
def model_catalog(lain):
    """Every model file in models.conf + images.conf, with which job/size slots use it."""
    rows = models.load_conf(os.path.join(REPO, "models.conf")) + models.load_images(os.path.join(REPO, "images.conf"))
    files = {}
    for r in rows:
        for repo, remote, local in r["downloads"]:
            e = files.setdefault(local, {"repo": repo, "remote": remote, "local": local, "slots": [], "rows": []})
            e["slots"].append(r["mode"])
            e["rows"].append(r)
    return rows, files


def lookup_files(files):
    """Ask Hugging Face for each file's size + sha256 (folder listings are cached per repo/folder)."""
    trees = {}
    n = len(files)
    for i, f in enumerate(files.values(), 1):
        ui.clear_line()
        ui.out("    %s looking up %d/%d: %s" % (ui.SPIN[i % len(ui.SPIN)], i, n, f["local"]), end="")
        folder = f["remote"].rsplit("/", 1)[0] if "/" in f["remote"] else ""
        key = (f["repo"], folder)
        if key not in trees:
            trees[key] = get_json("%s/api/models/%s/tree/main%s" % (HF, f["repo"], "/" + folder if folder else ""))
        e = next((x for x in trees[key] if x.get("path") == f["remote"]), None)
        if not e or not e.get("lfs"):
            f["missing"] = True
            continue
        f["sha"] = e["lfs"]["oid"]
        f["bytes"] = int(e.get("size") or e["lfs"].get("size") or 0)
    ui.clear_line()


def choose_models(args, rows, files, lain, h):
    md = os.path.join(lain, "models")
    gb = lambda sel: sum(files[k].get("bytes", 0) for k in sel)
    todo = lambda sel: [k for k in sel if not os.path.exists(os.path.join(md, k))]
    every = [k for k in files if not files[k].get("missing")]
    by_tier = {t: [k for k in every if any(r["tier"] == t for r in files[k]["rows"])] for t in models.SIZES}
    hh = dict(h, gpu_usable=max(0.0, h["vram_total"] - 0.8)) if h["gpus"] else h   # judge by the card, not what's free right now
    p = models.plan(models.load_conf(os.path.join(REPO, "models.conf")), hh, md) + \
        models.plan_images(models.load_images(os.path.join(REPO, "images.conf")), hh, md)
    rec = []
    for job, _ in models.JOBS:
        r = models.recommend_job(p, job, include_missing=True)
        if r:
            for _, _, local in r["downloads"]:
                if local not in rec and local in every:
                    rec.append(local)

    say("Models Lain can use (Best = 12 GB+ graphics card, Mid = 6-8 GB card or 16 GB RAM, Laptop = anything):")
    for t in models.SIZES:
        names = sorted({"%s %s" % (r["job"], r["name"]) for k in by_tier[t] for r in files[k]["rows"] if r["tier"] == t})
        info("  %-7s %8s  %s" % (t, ui.human(gb(by_tier[t])), ", ".join(names)))
    ui.out()
    free = shutil.disk_usage(lain).free
    opts = [("1", "Everything (all jobs, all sizes)", every),
            ("2", "Recommended for THIS computer (%s)" % h["gpu_name"], rec),
            ("3", "Pick sizes (Best / Mid / Laptop)", None),
            ("4", "None for now (download later from Lain's menu: d)", [])]
    for key, label, sel in opts:
        extra = "" if sel is None else "  %s to download" % ui.human(gb(todo(sel)))
        say("  %s) %s%s" % (key, label, ui.DIM + extra + ui.R))
    info("  free space on the drive: %s" % ui.human(free))
    choice = {"all": "1", "everything": "1", "recommended": "2", "none": "4"}.get(args.models or "", "")
    tiers = None
    if args.models and not choice:
        tiers = [t.strip().capitalize() for t in args.models.split(",")]
        choice = "3"
    while True:
        c = choice or ask("Choose 1-4 [1]:", "1", args.yes)
        choice = ""
        if c == "1":
            sel = every
        elif c == "2":
            sel = rec
        elif c == "4":
            sel = []
        elif c == "3":
            if tiers is None:
                a = ask("Which sizes? e.g.  best mid  or  laptop  [best mid laptop]:", "best mid laptop", args.yes)
                tiers = [x.capitalize() for x in a.replace(",", " ").split()]
            bad = [t for t in tiers if t not in models.SIZES]
            if bad:
                warn("unknown size %s - use best, mid, laptop" % ", ".join(bad))
                tiers = None
                continue
            sel = [k for t in tiers for k in by_tier[t]]
            sel = list(dict.fromkeys(sel))
            tiers = None
        else:
            warn("type 1, 2, 3 or 4")
            continue
        need = gb(todo(sel))
        if need > free - 2**30:
            warn("that needs %s but the drive has %s free" % (ui.human(need), ui.human(free)))
            if args.yes or ask("Continue anyway (downloads stop when the drive is full)? [y/N]:", "n").lower() != "y":
                if args.yes:
                    return []
                continue
        return sel


def install_models(lain, sel, files):
    md = os.path.join(lain, "models")
    failed = []
    total = sum(files[k].get("bytes", 0) for k in sel)
    done_bytes = 0
    t0 = time.time()
    for i, k in enumerate(sel, 1):
        f = files[k]
        dest = os.path.join(md, k)
        sums_path = os.path.join(os.path.dirname(dest), "SHA256SUMS")
        name = os.path.basename(dest)
        ui.out()
        say("Model file %d of %d  ·  used by: %s" % (i, len(sel), ", ".join(sorted(set(f["slots"])))), ui.ART)
        if done_bytes and time.time() - t0 > 5:
            rate = done_bytes / (time.time() - t0)
            info("  overall: %s of %s  ·  about %s left at %s/s" % (ui.human(done_bytes), ui.human(total),
                                                                    ui.fmt_time((total - done_bytes) / rate), ui.human(rate)))
        if os.path.exists(dest):
            sums = read_sums(sums_path)
            if sums.get(name) == f["sha"] and os.path.getsize(dest) == f["bytes"]:
                ok("%s already on the drive (verified earlier)" % k)
                done_bytes += f["bytes"]
                continue
            if os.path.getsize(dest) == f["bytes"] and sha256_file(dest, "checking existing") == f["sha"]:
                record_sum(sums_path, name, f["sha"])
                ok("%s already on the drive (checksum OK)" % k)
                done_bytes += f["bytes"]
                continue
            warn("%s on the drive is outdated or damaged - downloading a fresh copy" % k)
        url = "%s/%s/resolve/main/%s" % (HF, f["repo"], f["remote"])
        if download(url, dest, f["sha"], f["bytes"], k):
            record_sum(sums_path, name, f["sha"])
        else:
            failed.append(k)
        done_bytes += f["bytes"]
    return failed


# ---------------------------------------------------------------- helpers
def open_text(path):
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return ""


def write_text(path, s):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write(s + "\n")


def banner():
    ui.out()
    try:
        art = open(os.path.join(REPO, "art", "splash.txt"), encoding="utf-8").read().splitlines()
        if ui.cols() >= max(len(l) for l in art) + 4:
            ui.center_block(art, ui.ART)
    except (OSError, ValueError):
        pass
    ui.out()
    ui.center("L A I N   A I   ·   installer", ui.HI)
    ui.center("portable · private · offline", ui.DIM)
    ui.out()


def main():
    ap = argparse.ArgumentParser(description="Install Lain AI onto a drive.")
    ap.add_argument("--drive", required=True, help="mount folder (Linux) or drive root like E:\\ (Windows)")
    ap.add_argument("--models", help="all | recommended | none | comma list of sizes: best,mid,laptop")
    ap.add_argument("--yes", action="store_true", help="don't ask; use defaults")
    ap.add_argument("--force", action="store_true", help="reinstall engines/runtimes even if already present")
    ap.add_argument("--python-archive", help="(bootstrap) host Python archive already downloaded")
    ap.add_argument("--fs", default="", help="(bootstrap) filesystem type of the drive")
    args = ap.parse_args()

    drive = os.path.abspath(args.drive)
    lain = os.path.join(drive, "LainAI")
    if sys.version_info < (3, 9):
        print("Python 3.9+ needed")
        return 1
    banner()
    if not os.path.isdir(drive):
        err("drive folder not found: %s" % drive)
        return 1

    step("Checking the drive")
    try:
        os.makedirs(lain, exist_ok=True)
        probe = os.path.join(lain, ".write-test")
        with open(probe, "wb") as f:
            f.write(os.urandom(4096))
            f.flush()
            os.fsync(f.fileno())
        os.remove(probe)
    except OSError as e:
        err("can't write to %s: %s" % (drive, e))
        info("is it mounted read-only, or full? On Linux, re-plug it and let the file manager mount it.")
        return 1
    Log.open(os.path.join(lain, "logs", "install-%s.log" % time.strftime("%Y%m%d-%H%M%S")))
    du = shutil.disk_usage(drive)
    ok("writable: %s" % drive)
    info("size %s  ·  free %s  ·  filesystem %s" % (ui.human(du.total), ui.human(du.free), args.fs or "unknown"))
    if args.fs.lower() in ("vfat", "fat32", "fat"):
        err("FAT32 can't hold files over 4 GB and most models are bigger. Reformat as exFAT (the installer scripts can do it).")
        return 1
    if args.fs and args.fs.lower() != "exfat":
        warn("%s isn't readable on every OS - exFAT is the cross-platform choice (Windows + Linux + macOS)" % args.fs)
    if os.path.isdir(os.path.join(lain, "vault")):
        ok("existing vault found - it will not be touched")
    info("install log: %s" % Log.f.name)

    step("Scanning this computer")
    h = hw.scan()
    ok("%s  ·  %s" % (h["os"], h.get("cpu") or "CPU"))
    info("CPU threads %s  ·  RAM %.0f GB" % (h["threads"], h["ram"]))
    info("graphics: %s  ·  video memory %.1f GB (%.1f GB free)" % (h["gpu_name"], h["vram_total"], h["vram_free"]))
    info("Lain will use the %s engine here" % h["engine"])
    if h["gpu_kind"] == "none":
        warn("no usable GPU found - Lain will run on the CPU (slower). GPU drivers installed? Vulkan runtime present?")

    step("Choosing models")
    rows, files = model_catalog(lain)
    info("asking Hugging Face for file sizes and checksums (%d files)..." % len(files))
    try:
        lookup_files(files)
    except OSError as e:
        err("can't reach Hugging Face: %s" % e)
        return 1
    for k, f in files.items():
        if f.get("missing"):
            warn("%s is no longer in %s - skipped" % (f["remote"], f["repo"]))
    sel = choose_models(args, rows, files, lain, h)
    info("%d model files selected" % len(sel))

    tmp = tempfile.mkdtemp(prefix="lainai-install-")
    results = []
    try:
        step("Portable Python (runs Lain with nothing installed on the computer)")
        results.append(("Python runtimes", install_python(lain, tmp, args.python_archive, args.force)))
        step("AI engine: llama.cpp %s" % LLAMA_TAG)
        results.append(("Chat engine (llama.cpp)", install_llama(lain, tmp, args.force)))
        step("Image engine: stable-diffusion.cpp")
        results.append(("Image engine (sd.cpp)", install_sd(lain, tmp, args.force)))
        step("Vault encryption: age %s" % AGE_TAG)
        results.append(("Encryption (age)", install_age(lain, tmp, args.force)))
        step("Lain herself")
        install_app(drive, lain)
        results.append(("Lain app + launchers", True))
        step("Downloading models (%d files, %s)" % (len(sel), ui.human(sum(files[k].get("bytes", 0) for k in sel))))
        failed = install_models(lain, sel, files) if sel else []
        if not sel:
            info("skipped - choose d in Lain's menu any time to download what this computer needs")
        results.append(("Models (%d)" % len(sel), not failed))

        step("Checking the install")
        ext = ".exe" if IS_WIN else ""
        tests = [("Python", os.path.join(lain, "runtime", HOST, "python.exe" if IS_WIN else "bin/python3"), ("-c", "import ssl,sqlite3;print('ok')")),
                 ("age", os.path.join(lain, "bin", HOST, "age" + ext), ("--version",)),
                 ("engine %s-cpu" % HOST, os.path.join(lain, "engine", HOST + "-cpu", "llama-server" + ext), ("--version",))]
        if not h["engine"].endswith("-cpu"):
            tests.append(("engine " + h["engine"], os.path.join(lain, "engine", h["engine"], "llama-server" + ext), ("--version",)))
        for label, exe, a in tests:
            if not os.path.exists(exe):
                warn("%s: not installed" % label)
                continue
            if label == "Python" and not os.access(exe, os.X_OK):
                info("Python: drive is mounted without run permission here - linux.sh handles that (runs it from RAM)")
                continue
            good, line = selftest(exe, tmp, a)
            if good:
                ok("%s runs on this computer%s" % (label, "  (%s)" % line[:70] if line else ""))
            elif "cuda" in label:
                warn("%s didn't start: %s - NVIDIA driver missing/too old? Lain falls back to Vulkan/CPU" % (label, line[:100]))
            else:
                warn("%s didn't start here: %s" % (label, line[:120] or "unknown error"))
        for k in failed:
            warn("not finished: %s" % k)
        if not IS_WIN:
            info("flushing writes to the drive...")
            os.sync()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ui.out()
    ui.out(ui.ART + "━" * 60 + ui.R)
    for label, good in results:
        say(("✓ " if good else "✗ ") + label, ui.OK if good else ui.RED)
    du = shutil.disk_usage(drive)
    info("drive: %s used · %s free  ·  took %s" % (ui.human(du.total - du.free), ui.human(du.free), ui.fmt_time(time.time() - Log.t0)))
    if Log.warnings:
        info("%d warning(s) - see the log: %s" % (len(Log.warnings), Log.f.name))
    ui.out()
    if all(g for _, g in results):
        ui.grn("Lain is ready.  \\(^o^)/")
        say("Start from the drive:   Linux: bash linux.sh    Windows: right-click windows.ps1 > Run with PowerShell")
        say("First run asks you to create a vault passphrase (Enter = generate a strong one).")
        Log.write("==== finished OK")
        return 0
    ui.red("Some parts didn't finish. Run the installer again - it picks up where it stopped.")
    Log.write("==== finished with errors")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        ui.out()
        err("Stopped. Run the installer again any time: finished parts are kept and downloads resume.")
        sys.exit(130)
