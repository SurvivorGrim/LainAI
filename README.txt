LAIN AI - portable, private AI agent for the terminal
=====================================================
Start (from the root of the drive)
  Linux:   bash linux.sh            (or double-click > Run as a Program)
  Windows: right-click windows.ps1 > Run with PowerShell   (or double-click LainAI\Lain.bat)

What happens
  1. Lain stops any engine left running by an earlier session, then scans the
     machine (CPU, RAM, GPU + free video memory) and recommends, for each job, the
     best model size that runs well on it.
  2. Enter your vault passphrase (first run: create one; Enter = generate a strong one).
     Forgot it? Type  reset  at the prompt to wipe the vault and start fresh.
  3. Pick a job and size: a number, or c / s / r / v / i for the recommended Coding / SysAdmin /
     Research / Vision / Image model (Enter = recommended Coding). Then answer y or n to "Connect to the Wired for online access?".
     d downloads anything this machine needs that isn't on the drive yet.
  4. Lain loads the model with live progress (reading from the drive, GPU placement,
     context memory) and tells you how much of it runs on the GPU.
  5. Chat in the terminal. Lain can run commands, read/write/edit files, search, and
     (if connected) research the web. Anything that changes the machine asks first.
  6. /exit (or Ctrl+D): the engine stops and the vault is sealed again.

Chat commands
  /help  /auto (stop asking)  /think (show reasoning)  /cd PATH  /files (vault folder)
  /clear  /resume (previous conversation)  /stats (GPU + speed)  /exit
  /image PATH [question] (Vision: show Lain a picture; Lain can also open image files herself)
  /btw QUESTION (side question, not kept)  /reboot (reload Lain's code, vault stays open)
  /selfedit (unlock/lock Lain changing her own running code - locked by default)
  /plan (show Lain's checklist for the current task)
  /clean (delete leftover backups/cache in app/)
  Multi-line: just paste it (it arrives as one message), end a line with \  or wrap text in """ lines.
  Ctrl+C while a command runs: stops just that command (a live line shows how long it has
    been running and how long since it last printed anything). Then Enter = Lain carries on,
    b = emergency brake, or type a hint.
  Ctrl+C while Lain is thinking/answering = emergency brake: stops the request, turns /auto off.
  The model stays loaded either way - Ctrl+C never unloads it.
  Commands stop by themselves after 2 minutes unless Lain gives them longer (installs, builds).

How Lain works (every job)
  - Does only what you asked, looks before changing, one change at a time, and checks it worked.
  - Loop breaker: an identical failed command isn't run again unless something changed in between;
    after 3 failures in a row she must step back and change approach; after 6 failures in a row
    or 25 steps on one request she pauses and asks: continue, stop, or type a hint.
  - Your direction wins: decline an action and say what to do instead, and she drops her old plan.
  - Bigger tasks get a visible checklist (/plan). In Coding and SysAdmin, before her final answer
    she double-checks once: every request verified with evidence, test leftovers cleaned up,
    survives a restart - then reports what changed and how to undo it.
  - SysAdmin works in phases: survey everything involved, explain the cause, design one clean
    fix, build small and measure, make it last, clean up what it replaced, report.
  - Self-edit lock: she won't modify this drive's running copy of herself. When you talk about
    "the Lain AI project" she works in your development copy (the git repo, e.g. ~/LainAI).

Jobs x machine sizes (models.conf; Lain picks the best that fits each machine)
              Best (12 GB+ GPU)            Mid (6-8 GB GPU / 16 GB RAM)   Laptop
  Coding      Qwen3.8-27B (64K ctx)        Qwen3.6-35B-A3B (3-bit, 64K)   Qwen3.5-9B / 4B
  SysAdmin    Qwen3.6-35B-A3B (64K ctx)    Qwen3.6-35B-A3B (3-bit, 64K)   Qwen3.5-9B / 4B
              Alt: Gemma-4-26B-A4B (second opinion, 17 GB, not downloaded by default)
  Research    Qwen3.6-35B-A3B (64K ctx)    Qwen3.6-35B-A3B (3-bit)        Qwen3.5-9B / 4B
  Vision      Qwen3.6-35B-A3B + add-on     Qwen3.6-35B-A3B (3-bit) + add-on   Qwen3.5-9B / 4B + add-on
  Vision uses the same models plus their "mmproj" vision add-on (~0.9 GB each, in models/).
  Image       Qwen-Image-2.1-Turbo         FLUX.3-Klein                   Z-Image-Turbo
              (images.conf, models/image/, engine/image/ = stable-diffusion.cpp)

Image studio (menu i)
  Type a description, press Enter. Pictures go to images/ inside your encrypted vault
  (no prompt is embedded in the file). /size 1344x768 or /size wide|tall|square, /n 4,
  /seed 42 (recreate/tweak), /again, /open (show last), /folder, /auto (open each one).
  Ctrl+C cancels the picture being drawn. The image studio never goes online.

Privacy & security
  - Nothing leaves the machine unless you connect to the Wired (web search/fetch only;
    local network addresses are blocked). No telemetry, no accounts.
  - The engine only listens on 127.0.0.1 and requires a random per-session key.
  - Vault (vault/vault.age, age: scrypt + X25519 + ChaCha20-Poly1305) holds your files
    and conversations. While unlocked it lives in RAM on Linux (/dev/shm) and in
    %TEMP%\LainAI on Windows. The passphrase is never stored; lost = unrecoverable.
  - At start Lain makes one quick request to GitHub for a newer Lain (while it scans the hardware;
    silent when offline) and asks: update now / not now / skip this version. To switch the check
    off, create an empty file LainAI/data/no-update-check.
  - Menu "u" checks for updates: a new Lain release on GitHub (github.com/SurvivorGrim/LainAI),
    models, engine and age (checksum-verified). Version: LainAI/VERSION.

Layout
  app/        Lain itself (Python)          runtime/   portable Python (linux, win)
  engine/     llama.cpp builds              bin/       age (encryption)
  models/     AI models + SHA256SUMS        data/      wordlists for passphrases
  art/splash.txt, quotes.txt                customize freely
