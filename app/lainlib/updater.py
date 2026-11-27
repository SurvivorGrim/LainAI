"""Check for and install updates: Lain herself (GitHub releases), models, the AI engine (llama.cpp) and age.
Model/engine/age downloads are checksum-verified before they replace anything; interrupted downloads resume."""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

from . import ui
from .models import load_conf, mmproj_file

IS_WIN = os.name == "nt"
GH = "https://api.github.com/repos"
HF = "https://huggingface.co"
STALE_DAYS = 14   # only offer an engine update when it's at least this much newer
UA = {"User-Agent": "LainAI-updater"}
APP_REPO = "SurvivorGrim/LainAI"   # where Lain's own releases are published (tags like v1.1.0)

ENGINE_PATS = {
    "win-cuda": [r"llama-b\d+-bin-win-cuda-(12\.\d+)-x64\.zip", r"cudart-llama-bin-win-cuda-(12\.\d+)-x64\.zip"],
    "win-vulkan": [r"llama-b\d+-bin-win-vulkan-x64\.zip"],
    "win-cpu": [r"llama-b\d+-bin-win-cpu-x64\.zip"],
    "linux-cuda": [r"llama-b\d+-bin-ubuntu-cuda-(12\.\d+)-x64\.tar\.gz", r"cudart-llama-b\d+-bin-ubuntu-cuda-(12\.\d+)-x64\.tar\.gz"],
    "linux-vulkan": [r"llama-b\d+-bin-ubuntu-vulkan-x64\.tar\.gz"],
    "linux-cpu": [r"llama-b\d+-bin-ubuntu-x64\.tar\.gz"],
}


def get_json(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def download(url, dest, want_sha, total, label):
    """Resumable download with the happy bar; verifies sha256 before moving into place."""
    ui.out()
    ui.hi(label)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    free = shutil.disk_usage(os.path.dirname(dest)).free
    if free < total - have + 256 * 2**20:
        ui.red("Not enough free space (%s free, need %s)." % (ui.human(free), ui.human(total - have)))
        return False
    h = hashlib.sha256()
    if have:
        with ui.Spinner("resuming: checking the partial download"):
            with open(part, "rb") as f:
                for b in iter(lambda: f.read(4 << 20), b""):
                    h.update(b)
    prog = ui.Progress(total)
    if have < total:
        req = urllib.request.Request(url, headers=dict(UA, Range="bytes=%d-" % have) if have else UA)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                mode = "ab"
                if have and r.status != 206:      # server ignored resume: start over
                    mode, have, h = "wb", 0, hashlib.sha256()
                cur = have
                with open(part, mode) as f:
                    for b in iter(lambda: r.read(1 << 20), b""):
                        f.write(b)
                        h.update(b)
                        cur += len(b)
                        prog.update(cur)
        except (OSError, KeyboardInterrupt) as e:
            ui.out()
            ui.red("Download stopped: %s" % (e if not isinstance(e, KeyboardInterrupt) else "cancelled"))
            ui.say("Run the update again to resume where it stopped.")
            return False
    prog.done(msg="")
    if want_sha and h.hexdigest() != want_sha:
        ui.red("Checksum mismatch - download discarded, nothing was changed.")
        os.remove(part)
        return False
    os.replace(part, dest)
    ui.grn("\\(^o^)/  done")
    return True


def extract_flat(archive, dest):
    """Extract; symlinks become real files (exFAT can't store links)."""
    os.makedirs(dest, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    with tarfile.open(archive) as t:
        for m in t.getmembers():
            target = os.path.realpath(os.path.join(dest, m.name))
            if not (target + os.sep).startswith(os.path.realpath(dest) + os.sep):
                continue
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isfile() or m.issym() or m.islnk():
                src = t.extractfile(m)        # follows links to their target's data
                if src is None:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as f:
                    shutil.copyfileobj(src, f)
                os.chmod(target, 0o755)


def record_sum(sums_path, name, sha):
    lines = []
    if os.path.exists(sums_path):
        lines = [l for l in open(sums_path).read().splitlines() if l and not l.endswith("  " + name)]
    lines.append("%s  %s" % (sha, name))
    with open(sums_path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def read_sums(sums_path):
    out = {}
    if os.path.exists(sums_path):
        for l in open(sums_path).read().splitlines():
            p = l.split(None, 1)
            if len(p) == 2:
                out[p[1].strip()] = p[0]
    return out


def parse_version(v):
    """'v1.2.3' / '1.2' -> (1, 2, 3) / (1, 2, 0); anything else -> None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (v or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def app_version(root):
    try:
        return open(os.path.join(root, "VERSION"), encoding="utf-8").read().strip() or "none"
    except OSError:
        return "none"


APP_BRANCH = "main"                  # pushing a bumped VERSION to this branch is a new Lain version


def _gh_file(path, timeout):
    """A text file from the repo's main branch (via the API: fresh right after a push, unlike the raw CDN)."""
    import base64
    d = get_json("%s/%s/contents/%s?ref=%s" % (GH, APP_REPO, path, APP_BRANCH), timeout)
    return base64.b64decode(d.get("content") or "").decode("utf-8", "replace")


def _changelog_notes(text, ver):
    """The CHANGELOG.md section for version `ver` (a '## X.Y.Z ...' heading up to the next '## ')."""
    out, on = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if on:
                break
            on = parse_version(line[3:].split()[0] if line[3:].split() else "") == ver
            continue
        if on:
            out.append(line)
    return "\n".join(out).strip().replace("**", "").replace("`", "")     # plain text for the terminal


def notes_summary(notes, width=88, most=6):
    """The short summary at the top of a version's notes (the lines before the first bullet), wrapped to fit.
    Without one, the first words of each bullet."""
    import textwrap
    head = []
    for line in (notes or "").splitlines():
        if line.lstrip().startswith(("- ", "* ")):
            break
        if line.strip():
            head.append(line.strip())
    if not head:
        head = [re.sub(r"\s+", " ", l.strip()[2:]).split(". ")[0] for l in (notes or "").splitlines()
                if l.startswith(("- ", "* "))]
    out = []
    for h in head:
        out += textwrap.wrap(h, width) or [""]
    return out[:most]


def check_app(root, timeout=30):
    """A newer Lain on GitHub, if there is one: the VERSION on the main branch first (a push with a bumped
    VERSION is an update), then published releases/tags. None when this drive is up to date."""
    cur = parse_version(app_version(root))
    try:
        main_ver = parse_version(_gh_file("VERSION", timeout).strip())
    except (OSError, ValueError, KeyError) as e:
        main_ver, main_err = None, e
    else:
        main_err = None
    if main_ver and (not cur or main_ver > cur):
        tag = "v%d.%d.%d" % main_ver
        try:
            notes = _changelog_notes(_gh_file("CHANGELOG.md", timeout), main_ver)
        except (OSError, ValueError, KeyError):
            notes = ""
        return {"kind": "app", "label": "Lain    app", "detail": "%s -> %s" % (app_version(root), tag[1:]),
                "bytes": 0, "tag": tag, "notes": notes,
                "url": "https://github.com/%s/archive/refs/heads/%s.zip" % (APP_REPO, APP_BRANCH)}
    if main_ver:
        return None                     # main's VERSION is the newest there is: up to date
    try:                                # main unreadable: fall back to published releases / tags
        try:
            rel = get_json("%s/%s/releases/latest" % (GH, APP_REPO), timeout)
            tag, notes = rel["tag_name"], (rel.get("body") or "").strip()
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            tags = get_json("%s/%s/tags?per_page=100" % (GH, APP_REPO), timeout)   # no releases: plain tags
            tags = [t["name"] for t in tags if parse_version(t["name"])]
            if not tags:
                if main_err:
                    raise main_err
                return None
            tag, notes = max(tags, key=parse_version), ""
    except (OSError, ValueError, KeyError):
        if main_err:
            raise main_err
        raise
    new = parse_version(tag)
    if not new or (cur and new <= cur):
        return None
    return {"kind": "app", "label": "Lain    app", "detail": "%s -> %s" % (app_version(root), tag.lstrip("v")), "bytes": 0,
            "tag": tag, "notes": notes, "url": "https://github.com/%s/archive/refs/tags/%s.zip" % (APP_REPO, tag)}


def _install_app(root, it, tmp):
    """Download the release, check it, then let ITS installer copy the program files (--app-only)."""
    ui.out()
    ui.hi("Lain %s" % it["tag"])
    for l in notes_summary(it.get("notes"), 100):
        ui.say(l)
    arch = os.path.join(tmp, "lain-%s.zip" % it["tag"])
    try:
        with ui.Spinner("downloading Lain %s from GitHub" % it["tag"]) as sp:
            with urllib.request.urlopen(urllib.request.Request(it["url"], headers=UA), timeout=60) as r, open(arch, "wb") as f:
                n = 0
                for b in iter(lambda: r.read(1 << 16), b""):
                    f.write(b)
                    n += len(b)
                    sp.detail = ui.human(n)
        src = os.path.join(tmp, "lain-src")
        with zipfile.ZipFile(arch) as z:
            for m in z.namelist():
                if m.startswith("/") or ".." in m.replace("\\", "/").split("/"):
                    raise ValueError("unsafe path in archive: %s" % m)
            z.extractall(src)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        ui.red("Lain update failed to download: %s - nothing was changed." % e)
        return False
    tops = os.listdir(src)
    top = os.path.join(src, tops[0]) if len(tops) == 1 else src
    inst = os.path.join(top, "installer", "lain_install.py")
    got = parse_version(open(os.path.join(top, "VERSION"), encoding="utf-8").read()) \
        if os.path.isfile(os.path.join(top, "VERSION")) else None
    if not os.path.isfile(inst) or not os.path.isfile(os.path.join(top, "app", "lain.py")) or got != parse_version(it["tag"]):
        ui.red("That release doesn't look like a complete Lain %s - nothing was changed." % it["tag"])
        return False
    for dp, _, fs in os.walk(top):          # every Python file must at least compile before anything is replaced
        for fn in fs:
            if fn.endswith(".py"):
                try:
                    compile(open(os.path.join(dp, fn), encoding="utf-8").read(), fn, "exec")
                except (SyntaxError, ValueError, UnicodeDecodeError) as e:
                    ui.red("Release file %s is broken (%s) - nothing was changed." % (fn, e))
                    return False
    drive = os.path.dirname(os.path.abspath(root))
    r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", inst, "--drive", drive, "--app-only"])
    if r.returncode != 0:
        ui.red("Installing Lain %s failed (code %s)." % (it["tag"], r.returncode))
        return False
    ui.grn("Lain %s installed. Restart Lain to use it." % it["tag"].lstrip("v"))
    return True


def check(root):
    items = []
    try:
        with ui.Spinner("checking for a new Lain version on GitHub"):
            app = check_app(root)
        if app:
            items.append(app)
    except (OSError, ValueError, KeyError) as e:
        ui.say("Lain app: couldn't check GitHub right now (%s)." % e)
    models_dir = os.path.join(root, "models")
    sums_path = os.path.join(models_dir, "SHA256SUMS")
    sums = read_sums(sums_path)
    trees = {}
    with ui.Spinner("checking models on Hugging Face") as sp:
        for row in load_conf(os.path.join(root, "models.conf")):
            sp.detail = row["name"] + " - " + row["file"]
            try:
                tree = trees.get(row["repo"]) or get_json("%s/api/models/%s/tree/main" % (HF, row["repo"]))
                trees[row["repo"]] = tree
            except OSError:
                continue
            e = next((x for x in tree if x.get("path") == row["file"]), None)
            if not e or not e.get("lfs"):
                continue
            sha, size = e["lfs"]["oid"], int(e.get("size") or e["lfs"].get("size") or 0)
            local = os.path.join(models_dir, row["file"])
            if not os.path.exists(local):
                continue        # not installed on purpose (e.g. a size variant for other GPUs)
            if row["file"] not in sums:
                sp.text = "fingerprinting %s (first check only)" % row["name"]
                hh = hashlib.sha256()
                with open(local, "rb") as f:
                    for b in iter(lambda: f.read(8 << 20), b""):
                        hh.update(b)
                sums[row["file"]] = hh.hexdigest()
                record_sum(sums_path, row["file"], sums[row["file"]])
                sp.text = "checking models on Hugging Face"
            if sums[row["file"]] != sha:
                if any(i.get("file") == row["file"] for i in items):
                    continue       # same file is used by several jobs
                items.append({"kind": "model", "label": "%s  %s" % (row["mode"], row["name"]),
                              "detail": "newer version upstream", "bytes": size, "file": row["file"],
                              "sha": sha, "repo": row["repo"]})
    # engine
    try:
        cur = open(os.path.join(root, "engine", "VERSION")).read().strip()
    except OSError:
        cur = "none"
    try:
        with ui.Spinner("checking the AI engine (llama.cpp) on GitHub"):
            rels = [r for r in get_json(GH + "/ggml-org/llama.cpp/releases?per_page=30")
                    if re.fullmatch(r"b\d+", r["tag_name"]) and not r.get("draft")]
            new = max(rels, key=lambda r: int(r["tag_name"][1:])) if rels else None
            when = lambda r: datetime.datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
            offer = False
            if new and (not re.fullmatch(r"b\d+", cur) or int(new["tag_name"][1:]) > int(cur[1:])):
                offer = True
                if re.fullmatch(r"b\d+", cur):
                    try:
                        offer = (when(new) - when(get_json(GH + "/ggml-org/llama.cpp/releases/tags/" + cur))).days >= STALE_DAYS
                    except OSError:
                        pass
            plan = []
            if offer:
                for eng, pats in ENGINE_PATS.items():
                    cver = None
                    for p in pats:
                        cands = []
                        for a in new["assets"]:
                            m = re.fullmatch(p, a["name"])
                            if m and (cver is None or not m.groups() or m.group(1) == cver):
                                cands.append((tuple(int(x) for x in m.group(1).split(".")) if m.groups() else (0,), a, m))
                        if not cands:
                            plan = None
                            break
                        _, a, m = max(cands, key=lambda c: c[0])
                        if m.groups():
                            cver = m.group(1)
                        plan.append({"eng": eng, "name": a["name"], "url": a["browser_download_url"],
                                     "sha": (a.get("digest") or "").replace("sha256:", ""), "size": a["size"]})
                    if plan is None:
                        break
            if offer and plan:
                items.append({"kind": "engine", "label": "Engine  llama.cpp", "detail": "%s -> %s" % (cur, new["tag_name"]),
                              "bytes": sum(p["size"] for p in plan), "tag": new["tag_name"], "plan": plan})
    except (OSError, ValueError):
        ui.say("Engine: couldn't check GitHub right now.")
    # age
    try:
        cur_age = open(os.path.join(root, "bin", "VERSION")).read().strip()
    except OSError:
        cur_age = "none"
    try:
        r = get_json(GH + "/FiloSottile/age/releases/latest")
        t = r["tag_name"]
        if t != cur_age:
            want = {"age-%s-windows-amd64.zip" % t: "win", "age-%s-linux-amd64.tar.gz" % t: "linux"}
            plan = [{"plat": want[a["name"]], "name": a["name"], "url": a["browser_download_url"],
                     "sha": (a.get("digest") or "").replace("sha256:", ""), "size": a["size"]}
                    for a in r["assets"] if a["name"] in want]
            if len(plan) == 2:
                items.append({"kind": "age", "label": "age     encryption", "detail": "%s -> %s" % (cur_age, t),
                              "bytes": sum(p["size"] for p in plan), "tag": t, "plan": plan})
    except (OSError, ValueError, KeyError):
        pass
    return items, cur, cur_age


def run(root):
    ui.out()
    ui.hi("Checking for updates")
    try:
        urllib.request.urlopen(urllib.request.Request(HF + "/api/models?limit=1", headers=UA), timeout=10).close()
    except OSError:
        ui.red("Can't reach the update servers. Connect to the internet and try again.")
        return
    items, cur, cur_age = check(root)
    ui.out()
    if not items:
        ui.grn("Everything is up to date.  (^_^)")
        ui.say("Lain %s  |  engine %s  |  age %s  |  all installed models match upstream" % (app_version(root), cur, cur_age))
        return
    ui.hi("Updates available:")
    ui.center_block(["  %-26s %-30s %9s" % (i["label"], i["detail"], ui.human(i["bytes"]) if i["bytes"] else "")
                     for i in items])
    ui.out()
    ui.say("total " + ui.human(sum(i["bytes"] for i in items)))
    ans = ui.ask("Download all [Y], choose one by one [c], or skip [n]? ", 70).strip().lower()
    if ans.startswith("n"):
        ui.say("Skipped.")
        return
    choose = ans.startswith("c")
    tmp = tempfile.mkdtemp(prefix="lainai-update-")
    failed = app_done = False
    try:
        for it in items:
            if choose and ui.ask("Update %s (%s%s)? [Y/n] " % (" ".join(it["label"].split()), it["detail"],
                                                                ", " + ui.human(it["bytes"]) if it["bytes"] else ""), 70).strip().lower().startswith("n"):
                continue
            if it["kind"] == "app":
                ok = _install_app(root, it, tmp)
                app_done |= ok
                failed |= not ok
            elif it["kind"] == "model":
                ok = download("%s/%s/resolve/main/%s" % (HF, it["repo"], it["file"]), os.path.join(root, "models", it["file"]),
                              it["sha"], it["bytes"], "%s - %s" % (" ".join(it["label"].split()), it["file"]))
                if ok:
                    record_sum(os.path.join(root, "models", "SHA256SUMS"), it["file"], it["sha"])
                failed |= not ok
            elif it["kind"] == "engine":
                failed |= not _install_engine(root, it, tmp)
            elif it["kind"] == "age":
                failed |= not _install_age(root, it, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ui.out()
    if failed:
        ui.red("Some updates didn't finish - run the update again to retry.")
    else:
        ui.grn("All done. Lain is up to date.  \\(^o^)/")
    if app_done:
        ui.say("The new version may list new models: after restarting, d downloads them and u checks again.")


def get_models(root, wanted):
    """Download recommended models (and their vision add-ons) that aren't on the drive yet."""
    ui.out()
    if not wanted:
        ui.grn("This drive already has everything this machine needs.  (^_^)")
        return
    models_dir = os.path.join(root, "models")
    files, trees = [], {}
    with ui.Spinner("looking up files on Hugging Face") as sp:
        for row in wanted:
            for repo, remote, local in row["downloads"]:
                sp.detail = local
                if os.path.exists(os.path.join(models_dir, local)) or any(f["local"] == local for f in files):
                    continue
                folder = remote.rsplit("/", 1)[0] if "/" in remote else ""
                tkey = (repo, folder)
                try:
                    tree = trees.get(tkey) or get_json("%s/api/models/%s/tree/main%s" % (HF, repo, "/" + folder if folder else ""))
                except OSError:
                    ui.red("Can't reach Hugging Face. Connect to the internet and try again.")
                    return
                trees[tkey] = tree
                e = next((x for x in tree if x.get("path") == remote), None)
                if not e or not e.get("lfs"):
                    ui.red("%s isn't in %s any more - skipping." % (remote, repo))
                    continue
                files.append({"repo": repo, "remote": remote, "local": local, "sha": e["lfs"]["oid"],
                              "bytes": int(e.get("size") or e["lfs"].get("size") or 0)})
    if not files:
        ui.grn("Nothing to download.")
        return
    ui.hi("To download:")
    ui.center_block(["  %-50s %9s" % (f["local"], ui.human(f["bytes"])) for f in files])
    ui.out()
    ui.say("total " + ui.human(sum(f["bytes"] for f in files)))
    if ui.ask("Download now? [Y/n] ", 70).strip().lower().startswith("n"):
        ui.say("Skipped.")
        return
    failed = False
    for f in files:
        dest = os.path.join(models_dir, f["local"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        ok = download("%s/%s/resolve/main/%s" % (HF, f["repo"], f["remote"]), dest, f["sha"], f["bytes"], f["local"])
        if ok:
            record_sum(os.path.join(os.path.dirname(dest), "SHA256SUMS"), os.path.basename(dest), f["sha"])
        failed |= not ok
    ui.out()
    if failed:
        ui.red("Some downloads didn't finish - choose d again to resume.")
    else:
        ui.grn("All downloaded and verified.  \\(^o^)/")


def _selftest(exe):
    try:
        env = dict(os.environ)
        if not IS_WIN:
            env["LD_LIBRARY_PATH"] = os.path.dirname(exe)
        return subprocess.run([exe, "--version"], capture_output=True, timeout=30, env=env).returncode == 0
    except Exception:
        return False


def _install_engine(root, it, tmp):
    eng_root = os.path.join(root, "engine")
    for a in it["plan"]:
        dl = os.path.join(tmp, a["name"])
        if not download(a["url"], dl, a["sha"], a["size"], "Engine %s - %s" % (it["tag"], a["eng"])):
            return False
        with ui.Spinner("unpacking " + a["name"]):
            extract_flat(dl, os.path.join(tmp, "eng", a["eng"]))
        os.remove(dl)
    with ui.Spinner("installing engine %s onto the drive" % it["tag"]) as sp:
        for eng in os.listdir(os.path.join(tmp, "eng")):
            src = os.path.join(tmp, "eng", eng)
            srv = None
            for dp, _, fs in os.walk(src):
                for f in fs:
                    if f in ("llama-server", "llama-server.exe"):
                        srv = os.path.join(dp, f)
            if not srv:
                ui.red("llama-server missing in " + eng)
                return False
            dst = os.path.join(eng_root, ".new-" + eng)
            shutil.rmtree(dst, ignore_errors=True)
            sp.detail = "copying " + eng
            shutil.copytree(os.path.dirname(srv), dst)
            for dp, _, fs in os.walk(src):      # CUDA runtime libs may live elsewhere in the archive
                for f in fs:
                    if re.match(r"(cudart|cublas).*\.dll$|lib(cudart|cublas).*\.so", f):
                        shutil.copy2(os.path.join(dp, f), dst)
    test = os.path.join(eng_root, ".new-" + ("win-cpu" if IS_WIN else "linux-cpu"), "llama-server" + (".exe" if IS_WIN else ""))
    if not _selftest(test):
        ui.red("The new engine failed its self-test - keeping the current one.")
        for d in os.listdir(eng_root):
            if d.startswith(".new-"):
                shutil.rmtree(os.path.join(eng_root, d), ignore_errors=True)
        return False
    for d in os.listdir(eng_root):
        if d.startswith(".new-"):
            eng = d[5:]
            live = os.path.join(eng_root, eng)
            shutil.rmtree(live + ".prev", ignore_errors=True)
            if os.path.isdir(live):
                os.rename(live, live + ".prev")
            os.rename(os.path.join(eng_root, d), live)
    with open(os.path.join(eng_root, "VERSION"), "w", newline="\n") as f:
        f.write(it["tag"] + "\n")
    ui.grn("\\(^o^)/  engine updated to %s (previous kept as engine/*.prev)" % it["tag"])
    return True


def _install_age(root, it, tmp):
    for a in it["plan"]:
        dl = os.path.join(tmp, a["name"])
        if not download(a["url"], dl, a["sha"], a["size"], "age %s (%s)" % (it["tag"], a["plat"])):
            return False
        extract_flat(dl, os.path.join(tmp, "age-" + a["plat"]))
    me = "win" if IS_WIN else "linux"
    exe = None
    for dp, _, fs in os.walk(os.path.join(tmp, "age-" + me)):
        if ("age.exe" if IS_WIN else "age") in fs:
            exe = os.path.join(dp, "age.exe" if IS_WIN else "age")
    if not exe or not _selftest(exe):
        ui.red("age update failed its self-test - keeping the current version.")
        return False
    for plat in ("win", "linux"):
        for dp, _, fs in os.walk(os.path.join(tmp, "age-" + plat)):
            for f in fs:
                if f.startswith("age") and not f.endswith(".txt"):
                    shutil.copy2(os.path.join(dp, f), os.path.join(root, "bin", plat, f))
    with open(os.path.join(root, "bin", "VERSION"), "w", newline="\n") as f:
        f.write(it["tag"] + "\n")
    ui.grn("\\(^o^)/  age updated to " + it["tag"])
    return True


# ---------------------------------------------------------------- check at start
SKIP_FILE = os.path.join("data", "skip-version")          # "s" at the start-up prompt: don't ask about it again
OFF_FILE = os.path.join("data", "no-update-check")        # create this file to switch the start-up check off


def startup_check(root, result):
    """Runs in a background thread while Lain scans the hardware: one request to GitHub, a few seconds at most.
    Puts the update item (or nothing) into result["item"]. Silent when offline."""
    try:
        if os.path.exists(os.path.join(root, OFF_FILE)):
            return
        it = check_app(root, timeout=4)
        if not it:
            return
        try:
            skipped = open(os.path.join(root, SKIP_FILE), encoding="utf-8").read().strip()
        except OSError:
            skipped = ""
        if skipped and parse_version(skipped) == parse_version(it["tag"]):
            return
        result["item"] = it
    except Exception:
        pass


def offer_update(root, it):
    """Ask before the vault opens. Returns True when a new version was installed (caller restarts Lain)."""
    ui.out()
    ui.center("A newer Lain is waiting in the Wired:  v%s  ->  v%s" % (app_version(root), it["tag"].lstrip("v")), ui.HI)
    for l in notes_summary(it.get("notes")):
        ui.center(l, ui.DIM)
    ui.out()
    a = ui.ask("Update now? [y]es / [n]ot now / [s]kip this version ", 70, fresh=True).strip().lower()
    if a.startswith("s"):
        try:
            with open(os.path.join(root, SKIP_FILE), "w", encoding="utf-8", newline="\n") as f:
                f.write(it["tag"] + "\n")
        except OSError:
            pass
        ui.say("Skipped. Menu u still offers it any time.")
        return False
    if not a.startswith("y"):
        return False
    tmp = tempfile.mkdtemp(prefix="lainai-update-")
    try:
        return _install_app(root, it, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
