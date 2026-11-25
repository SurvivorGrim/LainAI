# Lain AI

A private AI agent that lives on a USB drive and runs in your terminal. Plug the drive into
a Windows or Linux PC and start it. Nothing gets installed on the PC, nothing goes to the
cloud, and your chats and files stay in an encrypted vault on the drive.

- **Coding**: writes, fixes and runs code. It asks before changing anything on the machine.
- **Research**: works through hard questions and can search the web if you allow it.
- **Vision**: reads screenshots, photos, charts and documents.
- **Image**: draws pictures from a description.

Lain checks the computer's CPU, RAM and graphics card, then picks the best model size for
each job. That's **Best** for a 12 GB+ graphics card, **Mid** for a 6–8 GB card or 16 GB of
RAM, and **Laptop** for anything else.

## Install

You need a USB drive or portable SSD (**128 GB or more** for everything, ~16 GB for the
Laptop models only) and an internet connection while installing.

**Linux**
```bash
git clone https://github.com/<you>/lain-ai.git
cd lain-ai
bash install.sh
```

**Windows**: download the ZIP (green **Code** button → Download ZIP), extract it, then
double-click **`install.bat`**.
(Or from PowerShell: `powershell -ExecutionPolicy Bypass -File .\install.ps1`)

The installer:

1. **Installs what the computer needs.**
   Linux: Vulkan loader and Mesa drivers, OpenMP, exFAT tools, curl (via apt, dnf, zypper or pacman, after asking).
   Windows: the Microsoft Visual C++ runtime (via winget, or Microsoft's signed installer).
   It also checks your graphics driver and tells you how to fix it if it's missing.
2. **Finds removable drives** and lets you pick one, then offers to **format it correctly**:
   exFAT on a GPT "Microsoft basic data" partition, which Windows, Linux and macOS can all read and write.
   - Before formatting, a red warning shows what's on the drive (space used, top-level files,
     any Lain vault or models). Nothing is erased until you type `ERASE`.
   - A Lain vault on the drive is backed up to your home folder first and offered back after the install.
   - After formatting, it checks that the filesystem and partition type came out right.
   - Or keep the files: an exFAT/NTFS drive can be installed onto as it is. If the partition is tagged
     "Linux filesystem" (Windows hides those), the installer can fix the tag without erasing anything.
   - FAT32 (4 GB file limit), ext4 and blank drives have to be formatted.
3. **Downloads everything onto the drive** and shows progress, speed and time left for each file:
   portable Python, the llama.cpp and stable-diffusion.cpp engines (NVIDIA CUDA, Vulkan and CPU builds for both OSes),
   `age` for encryption, and the models you choose:

   | Choice | What | Download |
   |---|---|---|
   | 1 Everything | all jobs, all sizes | ~100 GB |
   | 2 Recommended | the best fit for *this* computer | varies |
   | 3 Pick sizes | e.g. only `mid laptop` | varies |
   | 4 None | download later from Lain's menu (`d`) | 0 |

Every file is checked against its SHA-256 checksum. If a download gets interrupted, run the
installer again and it picks up where it stopped. Finished parts are skipped, and **your vault
is never touched**. A log is saved to `LainAI/logs/` on the drive.

Options (Linux / Windows):

```
--drive PATH          / -Drive E:\           skip the drive picker
--models all|recommended|none|best,mid,laptop / -Models ...
--yes                 / -Yes                 accept defaults (formatting still needs typed ERASE)
--format              / -Format              preselect "format the drive" (still asks for ERASE)
--no-deps             / -NoDeps              don't install system packages
--force               / -Force               reinstall engines/runtimes
--restore-vault DIR   / -RestoreVault DIR    copy a backed-up vault onto the drive
```

## Start Lain

From the drive's top folder:

- **Linux:** `bash linux.sh` (or double-click → Run as a Program)
- **Windows:** right-click `windows.ps1` → Run with PowerShell (or double-click `LainAI\Lain.bat`)

The first time, you create a vault passphrase. Press Enter to get a strong generated one.
See `LainAI/README.txt` on the drive for menus and chat commands.

## Privacy & security

- Fully offline unless you choose to connect for research. Web access blocks local-network addresses. No telemetry, no accounts.
- The engines only listen on `127.0.0.1`, and the chat engine needs a random key for each session.
- Vault: [age](https://github.com/FiloSottile/age) encryption (scrypt + X25519 + ChaCha20-Poly1305). The passphrase is never stored, so a lost passphrase can't be recovered.
- Lain asks before running commands or changing files. `/auto` turns the prompts off; Ctrl+C is the emergency brake.

## Supported systems

- Windows 10 1809+ / 11, x86-64
- Linux x86-64 with glibc 2.35+ (Ubuntu 22.04+, Pop!_OS, Fedora, Debian 12+, Arch …). The image engine needs glibc 2.39+ (Ubuntu 24.04+).
- macOS and ARM aren't supported yet.

## Credits & licenses

Lain AI bundles or downloads the following. Each keeps its own license:
[llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT),
[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) (MIT),
[age](https://github.com/FiloSottile/age) (BSD-3-Clause),
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) (Python license),
the [EFF long wordlist](https://www.eff.org/dice) (CC BY 3.0) for passphrases.
The models come from their Hugging Face pages (Qwen, FLUX, Z-Image and others, listed in `models.conf` / `images.conf`).
Check each model's license before using it commercially.
