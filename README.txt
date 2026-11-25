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
  3. Pick a job and size: a number, or c / r / v / i for the recommended Coding / Research /
     Vision / Image model (Enter = recommended Coding). Research asks to connect to the web (default yes).
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
  /clean (delete leftover backups/cache in app/)
  Multi-line: end a line with \  or wrap text in """ lines.
  Ctrl+C = emergency brake: stops the answer and any running command, turns /auto off.

Jobs x machine sizes (models.conf; Lain picks the best that fits each machine)
              Best (12 GB+ GPU)            Mid (6-8 GB GPU / 16 GB RAM)   Laptop
  Coding      Qwen3.8-27B                  Qwen3.6-35B-A3B (3-bit)        Qwen3.5-9B / 4B
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
  - Menu "u" checks for updates (models, engine, age) with checksum-verified downloads.

Layout
  app/        Lain itself (Python)          runtime/   portable Python (linux, win)
  engine/     llama.cpp builds              bin/       age (encryption)
  models/     AI models + SHA256SUMS        data/      wordlists for passphrases
  art/splash.txt, quotes.txt                customize freely
