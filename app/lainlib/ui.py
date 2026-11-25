"""Terminal look & feel: colors, centering, spinners, progress bars, prompts."""
import getpass
import os
import re
import shutil
import sys
import threading
import time

IS_WIN = os.name == "nt"


def _enable_vt():
    if not IS_WIN:
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        for std in (-11, -12):  # stdout, stderr
            h = k.GetStdHandle(std)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


_enable_vt()
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ART = "\033[38;5;183m"
DIM = "\033[38;5;245m"
HI = "\033[1;37m"
QUOTE = "\033[3;38;5;117m"
RED = "\033[38;5;203m"
OK = "\033[38;5;114m"
WARN = "\033[38;5;221m"
CODE = "\033[38;5;151m"
TOOL = "\033[38;5;111m"
R = "\033[0m"
_ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]|\033\][^\a]*\a")


def vis_len(s):
    return len(_ANSI.sub("", s))


def cols():
    return max(40, shutil.get_terminal_size((100, 30)).columns)


def out(s="", end="\n"):
    sys.stdout.write(s + end)
    sys.stdout.flush()


def pad(width):
    return " " * max(0, (cols() - width) // 2)


def center(text, color=""):
    out(pad(vis_len(text)) + color + text + (R if color else ""))


def center_block(lines, color=""):
    w = max((vis_len(l) for l in lines), default=0)
    p = pad(w)
    for l in lines:
        out(p + color + l + (R if color else ""))


def say(t):
    center(t, DIM)


def red(t):
    center(t, RED)


def grn(t):
    center(t, OK)


def hi(t):
    center(t, HI)


def clear():
    out("\033[2J\033[3J\033[H", end="")


def clear_line():
    out("\r\033[2K", end="")


def title(t):
    out("\033]0;" + t + "\a", end="")


def ask(prompt, width=64):
    try:
        return input(pad(width) + prompt)
    except EOFError:
        return ""


def ask_secret(prompt, width=44):
    try:
        return getpass.getpass(pad(width) + prompt + " ")
    except EOFError:
        return ""


def human(b):
    b = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or u == "TB":
            return ("%d %s" if u in ("B", "KB") else "%.1f %s") % (b, u)
        b /= 1024


def fmt_time(s):
    s = int(max(0, s))
    if s >= 3600:
        return "%dh%02dm" % (s // 3600, s % 3600 // 60)
    if s >= 60:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%ds" % s


FACES = ("(^_^)", "(^o^)", "(^_^)/", "\\(^-^)")
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def bar_str(frac, width=28, color=ART):
    frac = min(1.0, max(0.0, frac))
    n = int(frac * width)
    return color + "█" * n + DIM + "░" * (width - n) + R


class Progress:
    """Byte progress bar with speed + ETA and a happy face."""

    def __init__(self, total, label=""):
        self.total = max(1, int(total))
        self.label = label
        self.cur = 0
        self.speed = 0.0
        self._last = (time.time(), 0)
        self._drawn = 0.0

    def update(self, cur, detail="", force=False):
        now = time.time()
        self.cur = cur
        t0, c0 = self._last
        if now - t0 >= 1.0:
            inst = (cur - c0) / (now - t0)
            self.speed = inst if not self.speed else self.speed * 0.7 + inst * 0.3
            self._last = (now, cur)
        if not force and now - self._drawn < 0.2:
            return
        self._drawn = now
        frac = cur / self.total
        eta = ""
        if self.speed > 0 and cur < self.total:
            eta = fmt_time((self.total - cur) / self.speed) + " left"
        face = FACES[int(now) % 4]
        line = "%-7s %s %3d%%  %s / %s  %s/s  %s" % (
            face, bar_str(frac), int(frac * 100), human(cur), human(self.total), human(self.speed), eta)
        clear_line()
        out(pad(76) + line, end="")
        if detail:
            out("\n\033[2K" + pad(76) + DIM + detail[: max(10, cols() - len(pad(76)) - 2)] + R + "\033[1A", end="")

    def done(self, msg="\\(^o^)/  done"):
        self.update(self.total, force=True)
        out("\n\033[2K", end="")
        if msg:
            grn(msg)


class Spinner:
    """Animated status line: `with Spinner("unlocking vault") as sp: ...; sp.text = "..."`."""

    def __init__(self, text, indent=None):
        self.text = text
        self.detail = ""
        self.indent = indent
        self._stop = threading.Event()
        self._t = None
        self.start_time = time.time()

    def _run(self):
        i = 0
        while not self._stop.is_set():
            el = time.time() - self.start_time
            s = "%s %s  %s" % (SPIN[i % len(SPIN)], self.text, DIM + fmt_time(el) + R)
            p = pad(60) if self.indent is None else " " * self.indent
            clear_line()
            out(p + ART + s + R, end="")
            if self.detail:
                d = self.detail[: max(10, cols() - len(p) - 2)]
                out("\n\033[2K" + p + DIM + d + R + "\033[1A", end="")
            i += 1
            self._stop.wait(0.1)

    def __enter__(self):
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join()
        clear_line()
        out("\n\033[2K\033[1A", end="")
        clear_line()
