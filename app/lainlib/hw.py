"""Hardware scan: CPU, RAM, GPU/VRAM, and which llama.cpp engine build to use."""
import glob
import os
import platform
import re
import shutil
import subprocess

IS_WIN = os.name == "nt"


def _run(cmd, timeout=8):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def _nvidia_smi():
    exe = shutil.which("nvidia-smi")
    if not exe and IS_WIN:
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        exe = cand if os.path.exists(cand) else None
    if not exe:
        return []
    q = _run([exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"])
    gpus = []
    for line in q.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) == 3:
            try:
                gpus.append({"name": p[0], "total": float(p[1]) / 1024, "free": float(p[2]) / 1024})
            except ValueError:
                pass
    return gpus


def _linux():
    info = {"os": "Linux " + platform.release(), "cpu": "", "threads": os.cpu_count() or 1, "ram": 0.0}
    try:
        for l in open("/proc/cpuinfo"):
            if l.startswith("model name"):
                info["cpu"] = re.sub(r"\s+", " ", l.split(":", 1)[1]).strip()
                break
        for l in open("/proc/meminfo"):
            if l.startswith("MemTotal"):
                info["ram"] = int(l.split()[1]) / 1048576
    except OSError:
        pass
    gpus, kind = _nvidia_smi(), "nvidia"
    if not gpus:
        kind = "none"
        for f in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
            try:
                tot = int(open(f).read()) / 2**30
                used_f = f.replace("vram_total", "vram_used")
                used = int(open(used_f).read()) / 2**30 if os.path.exists(used_f) else 0
            except (OSError, ValueError):
                continue
            if tot >= 2:
                name = ""
                for l in _run(["lspci"]).splitlines():
                    if re.search(r"(VGA|3D|Display).*(AMD|ATI)", l, re.I):
                        name = l.split(": ", 1)[-1]
                        break
                gpus.append({"name": name or "AMD GPU", "total": tot, "free": tot - used})
                kind = "amd"
    if not gpus:
        name = ""
        for l in _run(["lspci"]).splitlines():
            if re.search(r"VGA|3D", l):
                name = l.split(": ", 1)[-1]
                break
        info["igpu"] = name
    vulkan = bool(glob.glob("/usr/share/vulkan/icd.d/*.json") or glob.glob("/etc/vulkan/icd.d/*.json"))
    return info, gpus, kind, vulkan


def _windows():
    import ctypes
    import winreg

    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEM()
    m.dwLength = ctypes.sizeof(MEM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    info = {"os": "Windows " + platform.release(), "cpu": platform.processor(),
            "threads": os.cpu_count() or 1, "ram": m.ullTotalPhys / 2**30}
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        info["cpu"] = re.sub(r"\s+", " ", winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    gpus, kind = _nvidia_smi(), "nvidia"
    if not gpus:
        kind = "none"
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        best = None
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not re.fullmatch(r"\d{4}", sub):
                    continue
                try:
                    k = winreg.OpenKey(root, sub)
                    mem = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0]
                    desc = winreg.QueryValueEx(k, "DriverDesc")[0]
                except OSError:
                    continue
                gb = int(mem) / 2**30
                if gb >= 2 and (not best or gb > best["total"]):
                    best = {"name": desc, "total": gb, "free": gb - 0.5}
        except OSError:
            pass
        if best:
            gpus, kind = [best], "other"
    if not gpus:
        info["igpu"] = _run(["powershell", "-NoProfile", "-Command",
                             "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"]).strip()
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    vulkan = os.path.exists(os.path.join(sysroot, "System32", "vulkan-1.dll"))
    return info, gpus, kind, vulkan


def scan():
    info, gpus, kind, vulkan = _windows() if IS_WIN else _linux()
    plat = "win" if IS_WIN else "linux"
    if kind == "nvidia":
        engine = plat + "-cuda"
    elif kind in ("amd", "other") and vulkan:
        engine = plat + "-vulkan"
    else:
        engine = plat + "-cpu"
        if kind != "nvidia" and not vulkan:
            gpus, kind = gpus if kind == "none" else [], kind if kind == "none" else "none"
    vram_total = sum(g["total"] for g in gpus)
    vram_free = sum(g["free"] for g in gpus)
    name = gpus[0]["name"] + (" x%d" % len(gpus) if len(gpus) > 1 else "") if gpus else \
        (info.get("igpu") or "none") + " (integrated/shared)"
    reserve = 4.0 if IS_WIN else 3.0
    info.update({
        "gpus": gpus, "gpu_kind": kind, "gpu_name": name, "engine": engine,
        "vram_total": vram_total, "vram_free": vram_free,
        "gpu_usable": max(0.0, vram_free - 0.4) if gpus else 0.0,   # keep a little for the desktop
        "ram_usable": max(0.0, info["ram"] - reserve),             # keep some for the OS
    })
    return info
