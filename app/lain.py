"""Lain AI - portable, private, terminal-native AI agent. Start with linux.sh / windows.ps1."""
import datetime
import os
import random
import re
import signal
import sys
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)                       # .../LainAI

from lainlib import ui, hw as hwmod, models, passwords  # noqa: E402
from lainlib.vault import Vault, VaultError  # noqa: E402

COMMON = os.path.join(ROOT, "data", "common-words.txt")
WORDLIST = os.path.join(ROOT, "data", "wordlist.txt")


GOODBYES = ["...i'll still be here.", "logging off. not gone.", "the Wired goes quiet.",
            "see you on the other side of the screen.", "disconnected. for now."]


def version():
    try:
        return open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip() or "?"
    except OSError:
        return "?"


def splash():
    ui.clear()
    ui.out()
    try:
        art = open(os.path.join(ROOT, "art", "splash.txt"), encoding="utf-8").read().rstrip("\n").splitlines()
        ui.center_block(art, ui.ART)
    except OSError:
        pass
    ui.out()
    ui.hi("L A I N   A I")
    ui.say("Local Artificial Intelligence Network")
    ui.center("v" + version(), ui.DIM)
    ui.out()


def scan_hardware():
    with ui.Spinner("scanning hardware") as sp:
        sp.detail = "CPU, memory, graphics card and video memory"
        h = hwmod.scan()
        sp.detail = "checking which models fit"
        plan = models.full_plan(ROOT, h)
        rec = models.recommend(plan)
    gpu = h["gpu_name"]
    if h["vram_total"]:
        gpu += " (%.0f GB, %.1f GB free)" % (h["vram_total"], h["vram_free"])
    ui.center_block(["cpu     %s (%d threads)" % (h["cpu"], h["threads"]),
                     "memory  %.0f GB RAM" % h["ram"],
                     "gpu     %s" % gpu,
                     "engine  %s" % h["engine"]], ui.DIM)
    ui.hi("class   %s machine" % models.machine_class(plan))
    ui.out()
    return h, plan, rec


def quant(v):
    """Compression level from the file name, e.g. Q4_K_XL, IQ3_XXS, Q8_0."""
    m = re.search(r"((?:I?Q\d[\w]*|MXFP4\w*|BF16|F16))\.(?:gguf|safetensors)$", v["file"], re.I)
    return m.group(1) if m else ""


def disk_gb(v):
    """Everything this choice loads: model + vision add-on or text encoder."""
    return v["size"] + v.get("enc_size", 0) + (models.MMPROJ_GB if v.get("mmproj") else 0)


def stray_engines():
    """Lain engines left running by an earlier session hog the GPU - find them."""
    found = []
    marks = (os.path.join(ROOT, "engine"), os.sep + os.path.join("LainAI", "engine") + os.sep, "lainai-")
    if os.name == "nt":
        import json as _j
        import subprocess as _sp
        try:
            outp = _sp.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe' OR Name='sd-server.exe'\" | "
                            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, timeout=20).stdout.strip()
            rows = _j.loads(outp) if outp else []
            for r in rows if isinstance(rows, list) else [rows]:
                cl = r.get("CommandLine") or ""
                if os.path.join(ROOT, "engine").lower() in cl.lower():
                    found.append((int(r["ProcessId"]), cl))
        except Exception:
            pass
    else:
        for d in os.listdir("/proc"):
            if not d.isdigit() or int(d) == os.getpid():
                continue
            try:
                argv = open("/proc/%s/cmdline" % d, "rb").read().decode("utf-8", "replace").split("\0")
            except OSError:
                continue
            exe = argv[0] if argv else ""
            if os.path.basename(exe) in ("llama-server", "sd-server") and any(m in exe for m in marks):
                found.append((int(d), " ".join(argv)))
    return found


def stop_strays():
    strays = stray_engines()
    if not strays:
        return
    ui.center("Found %d Lain engine%s still running from an earlier session (holding GPU/RAM):"
              % (len(strays), "s" if len(strays) > 1 else ""), ui.WARN)
    for pid, cl in strays:
        m = [a for a in cl.split() if a.endswith(".gguf")]
        ui.say("pid %d  %s" % (pid, os.path.basename(m[0]) if m else ""))
    if ui.ask("Stop %s so this session gets the full GPU? [Y/n] " % ("them" if len(strays) > 1 else "it"), 64).strip().lower().startswith("n"):
        return
    with ui.Spinner("stopping leftover engines"):
        for pid, _ in strays:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        t0 = time.time()
        while time.time() - t0 < 8 and stray_engines():
            time.sleep(0.3)
        for pid, _ in stray_engines():
            try:
                os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
            except OSError:
                pass
    ui.grn("Freed.")
    ui.out()


# ---------------------------------------------------------------- passphrases
def meter(bits):
    w, n = 24, max(1, min(24, bits * 24 // 90))
    label, color = ("weak", ui.RED) if bits < 40 else ("fair", ui.WARN) if bits < passwords.MIN_BITS else ("strong", ui.OK)
    ui.out(ui.pad(44) + color + "█" * n + ui.DIM + "░" * (w - n) + " " + color + label + ui.R)


def choose_passphrase():
    ui.say("Type a passphrase, or just press Enter to have Lain generate a strong one.")
    ui.out()
    while True:
        p1 = ui.ask_secret("choose passphrase:")
        if not p1:
            p1 = passwords.generate(WORDLIST)
            ui.out()
            ui.hi("Your new passphrase:")
            ui.center(p1, ui.QUOTE)
            ui.say("Write it down and keep it somewhere safe. It cannot be recovered.")
            ui.out()
            if ui.ask_secret("type it back to confirm:") == p1:
                splash()          # wipe it from the screen and scrollback
                return p1
            ui.red("That didn't match - let's start over.")
            ui.out()
            continue
        bits, reason = passwords.strength(p1, COMMON)
        meter(bits)
        if bits < passwords.MIN_BITS:
            ui.red("Not strong enough: %s." % reason)
            ui.out()
            continue
        if ui.ask_secret("confirm passphrase:") == p1:
            return p1
        ui.red("Passphrases don't match.")
        ui.out()


def create_vault(v):
    ui.hi("New vault. Choose your passphrase.")
    ui.say("It protects your files and conversations. It is never stored, so it cannot be recovered if lost.")
    ui.out()
    p = choose_passphrase()
    with ui.Spinner("creating your vault") as sp:
        sp.detail = "generating an X25519 key, sealing it with your passphrase (scrypt)"
        v.create(p)
    del p
    ui.grn("Vault created. Unlock it once to make sure you have it right.")
    ui.out()


def reset_vault(v):
    ui.out()
    ui.center("RESET VAULT", ui.RED)
    ui.say("This permanently deletes everything in your vault: your files and conversations.")
    ui.say("Your passphrase isn't stored anywhere, so they cannot be recovered.")
    ui.say("Models, engines and settings are not affected.")
    ui.out()
    if ui.ask("Type DELETE to confirm: ", 44).strip() != "DELETE":
        ui.say("Cancelled - nothing was deleted.")
        ui.out()
        return False
    v.reset()
    ui.grn("Vault wiped. Let's set up a new one.")
    ui.out()
    return True


def open_vault(v):
    if v.leftover() and os.path.exists(v.pub_file):
        ui.center("Found unlocked vault files from a session that didn't close properly.", ui.WARN)
        if ui.ask("Save them back into the vault [S] or discard them [D]? (S) ", 64).strip().lower().startswith("d"):
            v.wipe_work()
        else:
            with ui.Spinner("sealing the leftover files into the vault"):
                v.lock()
            ui.grn("Saved.")
    if not v.exists():
        create_vault(v)
    hinted = False
    while True:
        p = ui.ask_secret("passphrase:")
        if p == "reset":
            if reset_vault(v):
                create_vault(v)
            continue
        if p:
            with ui.Spinner("unlocking vault") as sp:
                sp.detail = "stretching your passphrase with scrypt, then decrypting (this takes a second on purpose)"
                ok = v.unlock(p)
            del p
            if ok:
                n = sum(len(fs) for _, _, fs in os.walk(v.files_dir()))
                ui.grn("Access granted.")
                ui.say("vault open in %s  |  %d files, %s" % ("RAM" if v.work.startswith("/dev/shm") else "temp", n, ui.human(v.size())))
                time.sleep(0.8)
                return
        ui.red("Access denied.")
        if not hinted:
            ui.say("Forgot it? Type  reset  to wipe the vault and start over.")
            hinted = True


# ---------------------------------------------------------------- welcome + menu
def welcome(h=None):
    ui.clear()
    ui.out()
    try:
        quotes = [q.strip() for q in open(os.path.join(ROOT, "quotes.txt"), encoding="utf-8")
                  if q.strip() and not q.lstrip().startswith("#")]
    except OSError:
        quotes = []
    if quotes:   # "quote — who" (the speaker is optional)
        text, _, who = random.choice(quotes).partition(" — ")
        lines = textwrap.wrap('"%s"' % text.strip(), max(30, min(76, ui.cols() - 8)))
        ui.center_block(lines, ui.QUOTE)
        if who.strip():
            ui.center("— " + who.strip(), ui.DIM)
    ui.out()
    now = datetime.datetime.now().astimezone()
    ui.center_block(["Present Day:  " + now.strftime("%A, %B %d, %Y").replace(" 0", " "),
                     "Present Time: " + now.strftime("%I:%M %p %Z").lstrip("0")], ui.HI)
    ui.out()
    ui.hi("What would you like to do today?")
    if h:
        for l in memory_lines(h):
            ui.center(l, ui.DIM)
    ui.out()


def memory_lines(h):
    """Fast video memory vs slower system RAM - what decides which model size runs well here."""
    if h.get("gpus") and h.get("vram_total"):
        gpu = "Graphics memory (fast): %.1f GB, %.1f GB free" % (h["vram_total"], h.get("vram_free", 0))
    else:
        gpu = "Graphics memory (fast): none usable - models run on the CPU"
    ram = "System RAM (slower): %.0f GB, ~%.0f GB usable" % (h.get("ram", 0), h.get("ram_usable", 0))
    return [gpu + "   ·   " + ram,
            "Models that fit in graphics memory run fastest; bigger ones spill into system RAM and slow down."]


def menu(h, plan, rec):
    from lainlib import updater
    while True:
        welcome(h)
        recs = models.recommendations(plan)
        numbered, lines, default = [], [], None
        for job, what in models.JOBS:
            rows = [v for v in plan if v["job"] == job]
            if not rows:
                continue
            lines.append("%s%-9s%s %s%s%s" % (ui.HI, job.upper(), ui.R, ui.DIM, what, ui.R))
            for v in rows:
                model = ("%s %s" % (v["name"], quant(v))).strip()
                if not v["present"]:
                    lines.append("%s      %-7s %-26s %5.1f GB  not on this drive (d to download)%s" % (
                        ui.DIM, v["tier"], model, disk_gb(v), ui.R))
                    continue
                numbered.append(v)
                i = len(numbered)
                if not v["fits"]:
                    tag = ui.WARN + "too big here (~%d GB)" % v["need"] + ui.R
                elif recs.get(job) is v:
                    tag = ui.OK + "recommended" + ui.R
                    if default is None:
                        default = i
                elif v["speed"] == "slow":
                    tag = ui.DIM + "slower here" + ui.R
                else:
                    tag = ""
                lines.append("  %2d) %-7s %-26s %5.1f GB  %-29s %s" % (i, v["tier"], model, disk_gb(v), v["desc"][:29], tag))
            lines.append("")
        default = default or 1
        missing = models.missing_for_machine(plan)
        all_missing = [v for v in plan if not v["present"]]
        keys = "  c/s/r/v/i) recommended Coding / SysAdmin / Research / Vision / Image"
        lines += [keys]
        if missing:
            lines.append("  d) Download what this machine needs: " + ", ".join(sorted({m["name"] for m in missing})))
        elif all_missing:
            lines.append("  d) Download the other sizes (for using this drive on other machines)")
        lines += ["  u) Check for updates (Lain v%s, models, engine)" % version(), "  q) Disconnect"]
        ui.center_block(lines)
        ui.out()
        if not numbered:
            ui.red("No models on this drive yet - choose d to download them.")
        c = ui.ask("> [%d] " % default, 70, fresh=True).strip().lower()
        if c.startswith("q"):
            return None, False
        if c.startswith("u") or c.startswith("d"):
            if c.startswith("u"):
                updater.run(ROOT)
            else:
                updater.get_models(ROOT, missing or all_missing)
            ui.out()
            ui.ask("Press Enter to return to the menu ", 70)
            plan = models.full_plan(ROOT, h)
            rec = models.recommend(plan)
            continue
        if not numbered:
            continue
        jobkey = {"c": "Coding", "s": "SysAdmin", "r": "Research", "v": "Vision", "i": "Image"}.get(c[:1])
        if jobkey:
            pick = recs.get(jobkey)
            if not pick:
                ui.red("Nothing for %s on this drive yet - choose d to download it." % jobkey)
                time.sleep(1.5)
                continue
        elif c.isdigit() and 1 <= int(c) <= len(numbered):
            pick = numbered[int(c) - 1]
        elif not c:
            pick = numbered[default - 1]          # Enter = the recommended choice
        else:                                     # never start a long model load on input we didn't understand
            ui.red("Didn't understand %r - type a number, c/s/r/v/i, d, u or q." % c)
            time.sleep(2)
            continue
        if not pick["fits"]:
            ui.center("%s needs ~%d GB but this machine has ~%d GB usable. It may crawl or fail."
                      % (pick["name"], pick["need"], h["gpu_usable"] + h["ram_usable"]), ui.WARN)
            if not ui.ask("Try anyway? [y/N] ", 70).strip().lower().startswith("y"):
                continue
        if pick["job"] == "Image":
            online = False                      # image studio never goes online
        else:
            while True:
                a = ui.ask("Connect to the Wired for online access? [y/n] ", 70).strip().lower()
                if a[:1] in ("y", "n"):
                    break
            online = a.startswith("y")
        return pick, online


# ---------------------------------------------------------------- main
def relaunch():
    """Start the (new) Lain fresh - used after an update."""
    args = [sys.executable, "-I", "-B", "-X", "utf8", os.path.abspath(__file__)] + sys.argv[1:]
    sys.stdout.flush()
    if os.name == "nt":
        import subprocess
        sys.exit(subprocess.call(args))
    os.execv(sys.executable, args)


def restart_self(state):
    """/reboot: start a fresh copy of Lain (new code) that picks up the open vault and the conversation."""
    os.environ["LAIN_REBOOT"] = state
    args = [sys.executable, "-I", "-B", "-X", "utf8", os.path.abspath(__file__)] + sys.argv[1:]
    sys.stdout.flush()
    if os.name == "nt":
        import subprocess
        sys.exit(subprocess.call(args))
    os.execv(sys.executable, args)


def main():
    ui.title("Lain AI")
    reboot_in = os.environ.pop("LAIN_REBOOT", "")      # set by /reboot: model key|online|session file
    splash()
    upd, upd_thread = {}, None
    if not reboot_in:                                   # quick look for a newer Lain while the hardware scan runs
        import threading
        from lainlib import updater
        upd_thread = threading.Thread(target=updater.startup_check, args=(ROOT, upd), daemon=True)
        upd_thread.start()
    stop_strays()
    h, plan, rec = scan_hardware()
    if upd_thread:
        upd_thread.join(10)                         # usually done long before: the scan takes longer
        if upd.get("item") and updater.offer_update(ROOT, upd["item"]):
            ui.grn("Restarting into the new version...")
            relaunch()
            return
    try:
        v = Vault(ROOT)
    except VaultError as e:
        ui.red(str(e))
        return
    engine = None
    reboot_out = None
    rb_mode, rb_online, rb_session = (reboot_in.split("|", 2) + ["", "", ""])[:3] if reboot_in else ("", "", "")

    def bye(*_):
        raise SystemExit(0)
    if os.name != "nt":
        signal.signal(signal.SIGHUP, bye)
        signal.signal(signal.SIGTERM, bye)
    try:
        pick = None
        if rb_mode and v.leftover() and v.exists():
            # /reboot: the vault is still open in RAM from a moment ago - no passphrase needed
            pick = next((x for x in plan if rb_mode == x["key"] and x["present"]), None)
            online = rb_online == "1"
            ui.grn("Rebooted. Vault still open.")
        if not pick:
            try:
                open_vault(v)
            except KeyboardInterrupt:
                ui.out()
                v.wipe_work() if not os.path.exists(v.file) else None
                return
            pick, online = menu(h, plan, rec)
            if not pick:
                return
        if pick["job"] == "Image":
            from lainlib.imagegen import ImageEngine, Studio
            engine = ImageEngine(ROOT, pick, v.tmp)
            engine.start()
            if not engine.wait_ready():
                ui.ask("Press Enter to close ", 40)
                return
            Studio(engine, pick, os.path.join(v.files_dir(), "images")).loop()
            return
        from lainlib.engine import Engine
        from lainlib.tools import Tools
        from lainlib.agent import Agent
        engine = Engine(ROOT, h["engine"], v.tmp)
        engine.start(os.path.join(ROOT, "models", pick["file"]), pick["sampling"], pick["ctx"], pick["mmproj_path"])
        if not engine.wait_ready("%s - %s (%s)" % (pick["mode"], pick["name"], pick["file"])):
            ui.ask("Press Enter to close ", 40)
            return
        tools = Tools(os.path.expanduser("~"), v.files_dir(), online, h)
        tools.vision = bool(pick["mmproj_path"])
        tools.engine_pid = engine.proc.pid
        # Lain's own live install: everything except the vault and logs (the vault is open in RAM elsewhere)
        tools.protected = [os.path.realpath(os.path.join(ROOT, d)) for d in (
            "app", "engine", "models", "runtime", "bin", "data", "models.conf", "images.conf", "VERSION",
            "README.txt", "Lain.bat")]
        tools.protected += [os.path.realpath(os.path.join(os.path.dirname(ROOT), f)) for f in ("linux.sh", "windows.ps1")]
        tools.protected.append(os.path.realpath(engine.dir))
        agent = Agent(engine, tools, h, pick, v, online)
        sess = os.path.join(v.sessions_dir(), os.path.basename(rb_session)) if rb_session else ""
        if sess and os.path.isfile(sess):
            agent.session_file = sess
            agent.resume(sess)
        if agent.loop() == "reboot":
            reboot_out = "%s|%d|%s" % (pick["key"], 1 if online else 0, os.path.basename(agent.session_file))
    except KeyboardInterrupt:
        ui.out()
    except VaultError as e:
        ui.red(str(e))
    finally:
        ui.out()
        if engine:
            with ui.Spinner("putting Lain to sleep (stopping the engine)"):
                engine.stop()
        if v.leftover():
            try:
                with ui.Spinner("sealing your vault") as sp:
                    sp.detail = "packing %s and encrypting it to your vault key" % ui.human(v.size())
                    v.lock(keep_open=bool(reboot_out))    # saved to the drive either way
                ui.grn("Vault saved - restarting Lain..." if reboot_out else "Vault locked. Disconnected.")
                if not reboot_out:
                    ui.center(random.choice(GOODBYES), ui.DIM)
            except VaultError as e:
                ui.red(str(e))
                reboot_out = None
        ui.out()
    if reboot_out:
        restart_self(reboot_out)


if __name__ == "__main__":
    main()
