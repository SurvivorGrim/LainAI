"""Model table (models.conf): jobs x machine sizes, and choosing what runs best here."""
import os

SPEED_RANK = {"fast": 2, "ok": 1, "slow": 0}
JOBS = [("Coding", "write, fix and run code"),
        ("SysAdmin", "set up, fix and look after your machines"),
        ("Research", "dig into questions, search the web, reason it out"),
        ("Vision", "look at screenshots, photos, charts and documents"),
        ("Image", "create pictures from a description")]
SIZES = ["Best", "Mid", "Laptop"]
TIERS = SIZES + ["Alt"]  # Alt = optional second-opinion model: listed, never auto-recommended
MMPROJ_GB = 0.9          # vision add-on, loaded next to the model


def mmproj_file(row):
    """Local file name of a row's vision add-on (repo files are all called mmproj-*.gguf, so prefix the model)."""
    return "%s-%s" % (row["name"], row["mmproj"]) if row.get("mmproj") else ""


def load_conf(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split("|")
        if len(f) < 9:
            continue
        f += [""] * (11 - len(f))
        rows.append({"job": f[0], "tier": f[1], "name": f[2], "file": f[3], "size": float(f[4]), "type": f[5],
                     "sampling": f[6].split(), "desc": f[7], "repo": f[8], "mmproj": f[9].strip(),
                     "ctx": int(f[10]) if f[10].strip().isdigit() else 32768})
    for r in rows:
        r["key"] = "%s/%s" % (r["job"], r["tier"])          # one slot per job + machine size
        r["mode"] = "%s %s" % (r["job"], r["tier"])          # short label, e.g. "Coding Best"
        r["downloads"] = [(r["repo"], r["file"], r["file"])]  # (repo, file in repo, file under models/)
        if r["mmproj"]:
            r["downloads"].append((r["repo"], r["mmproj"], mmproj_file(r)))
    return rows


def load_images(path):
    """images.conf: image generation models (stable-diffusion.cpp), one row per machine size."""
    rows = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split("|")
        if len(f) < 14:
            continue
        w, _, h = f[11].lower().partition("x")
        dl = []
        for item in f[13].split(";"):
            src, _, local = item.strip().partition(">")
            repo, _, remote = src.partition(":")
            if repo and remote and local:
                dl.append((repo, remote, os.path.join("image", local)))
        rows.append({"job": "Image", "tier": f[0], "name": f[1], "file": os.path.join("image", f[2]),
                     "llm": os.path.join("image", f[3]), "vae": os.path.join("image", f[4]),
                     "size": float(f[5]), "enc_size": float(f[6]), "type": "diffusion",
                     "steps": int(f[7]), "cfg": float(f[8]), "sampler": f[9].strip() or "euler",
                     "sigmas": [float(x) for x in f[10].split(",") if x.strip()],
                     "width": int(w or 1024), "height": int(h or 1024), "desc": f[12],
                     "downloads": dl, "mmproj": "", "sampling": [], "ctx": 0,
                     "key": "Image/" + f[0], "mode": "Image " + f[0]})
    return rows


def assess_image(v, hw):
    """Diffusion: the whole diffusion model must sit in video memory while it draws
    (the text encoder is swapped in and out), otherwise it runs on the CPU - minutes per picture."""
    gpu = hw["gpu_usable"] if hw["gpus"] else 0.0
    need = v["size"] + 1.0                             # + working memory for a ~1 megapixel picture
    on_gpu = gpu >= need
    speed = "fast" if on_gpu else "slow"
    fits = v["size"] + v["enc_size"] + 1.0 <= gpu + hw["ram_usable"]
    return dict(v, need=v["size"] + v["enc_size"] + 1.0, fits=fits, speed=speed,
                gpu_frac=1.0 if on_gpu else 0.0, img_gpu=on_gpu)


def plan_images(rows, hw, models_dir):
    out = []
    for r in rows:
        present = all(os.path.exists(os.path.join(models_dir, local)) for _, _, local in r["downloads"])
        out.append(dict(assess_image(r, hw), present=present, mmproj_path=""))
    return out


def assess(v, hw):
    """Estimate whether a model fits and how fast it will run here."""
    size = v["size"] + (MMPROJ_GB if v.get("mmproj") else 0)
    need = size + (2.0 if size > 10 else 1.5)          # + context cache and compute buffers
    if v.get("ctx", 32768) > 32768:
        need += 1.0
    gpu = hw["gpu_usable"] if hw["gpus"] else 0.0
    budget = gpu + hw["ram_usable"]
    frac = min(1.0, gpu / need) if need else 0.0
    if v["type"] == "moe":
        speed = "fast" if gpu >= 4 else "ok"
    elif frac >= 0.93:
        speed = "fast"
    elif frac >= 0.75 or size <= 6:
        speed = "ok"
    else:
        speed = "slow"
    return dict(v, need=need, fits=need <= budget, speed=speed, gpu_frac=frac)


def full_plan(root, hw):
    """Everything the menu shows: chat models (models.conf) + image models (images.conf)."""
    md = os.path.join(root, "models")
    return plan(load_conf(os.path.join(root, "models.conf")), hw, md) + \
        plan_images(load_images(os.path.join(root, "images.conf")), hw, md)


def plan(conf, hw, models_dir):
    """One entry per job + machine size (in table order), with the best present variant chosen."""
    groups, order = {}, []
    for row in conf:
        if row["key"] not in groups:
            groups[row["key"]] = []
            order.append(row["key"])
        mm = mmproj_file(row)
        row = dict(row, mmproj_path=os.path.join(models_dir, mm) if mm else "",
                   present=os.path.exists(os.path.join(models_dir, row["file"]))
                   and (not mm or os.path.exists(os.path.join(models_dir, mm))))
        groups[row["key"]].append(assess(row, hw))
    out = []
    for k in order:
        vs = groups[k]
        present = [v for v in vs if v["present"]]
        pool = present or vs                 # if nothing is downloaded, show what would be downloaded
        fitting = [v for v in pool if v["fits"]]
        if fitting:
            pick = max(fitting, key=lambda v: (SPEED_RANK[v["speed"]], v["size"]))
        else:
            pick = min(pool, key=lambda v: v["size"])
        out.append(dict(pick, present=bool(present)))
    return out


def _best(cands):
    for v in cands:
        if v["fits"] and v["speed"] != "slow":
            return v
    for v in cands:
        if v["fits"]:
            return v
    return cands[-1] if cands else None


def recommend_job(p, job, include_missing=False):
    """Best size for this job on this machine (Best before Mid before Laptop)."""
    rows = [v for v in p if v["job"] == job and v["tier"] in SIZES and (include_missing or v["present"])]
    rows.sort(key=lambda v: SIZES.index(v["tier"]))
    if job == "Image" and rows and not any(v["fits"] and v["speed"] != "slow" for v in rows):
        fitting = [v for v in rows if v["fits"]] or rows
        return min(fitting, key=lambda v: v["size"])      # CPU drawing: the lightest model is the only sane choice
    return _best(rows)


def recommendations(p):
    return {job: recommend_job(p, job) for job, _ in JOBS}


def recommend(p):
    """The overall default (the first job that has something usable)."""
    for job, _ in JOBS:
        r = recommend_job(p, job)
        if r:
            return r
    return None


def machine_class(p):
    """Which column of the table this machine belongs to, judged by what would run well (downloaded or not)."""
    r = recommend_job(p, "Coding", include_missing=True)
    return r["tier"] if r and r["fits"] else "Too small"


def missing_for_machine(p):
    """Recommended models (one per job) that aren't on the drive yet."""
    out, seen = [], set()
    for job, _ in JOBS:
        r = recommend_job(p, job, include_missing=True)
        if r and not r["present"] and r["key"] not in seen:
            seen.add(r["key"])
            out.append(r)
    return out
