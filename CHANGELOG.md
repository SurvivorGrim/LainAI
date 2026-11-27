# Changelog

Lain AI uses [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
The version lives in the `VERSION` file. Each release is a GitHub release tagged `vX.Y.Z`.
Lain's update menu (`u`) checks the latest release and offers to install it.

## 1.2.0 (2026-11-27)

From a real SysAdmin test: "my RGB isn't on the Breath profile". The breathing service had been dropped at
boot by a dependency loop. Lain blamed the (intended) GPU temperature script instead, because an `&&` chain
hid the check that would have found it.

- **New tool `service_health`** (Linux + Windows, read-only): what's supposed to run but isn't.
  - Linux: failed services, services enabled for boot/login that are NOT running (and whether they started
    at all this boot), and this boot's warnings from systemd: ordering cycles, crashes, failed starts. With
    a name (e.g. `rgb`) it also shows each matching service's status, unit file ordering, recent log lines
    and every log line of this boot that mentions it.
  - Windows: automatic services that aren't running, your own scheduled tasks whose last run failed,
    errors since boot, and with a name: matching services, tasks, startup entries, processes and log events.
  - SysAdmin mode uses it first whenever something stopped working or doesn't come back after a reboot.
- **A search that finds nothing isn't a failure.** `grep`, `pgrep`, `rg` and `findstr` exiting with 1 is
  reported as "no match - that's an answer". This stops false "3 failed steps in a row" warnings.
- **`&&` chains explained.** When a command line with `&&` fails, Lain is told the commands after the failing
  one never ran: their results are missing, not empty.
- **New SysAdmin rules:** look for what's missing, not only what's running; don't chain independent checks
  with `&&`; before blaming or removing something, check whether the user set it up on purpose.

## 1.1.0 (2026-11-27)

Fixes from real test sessions (a Windows security review and a Windows install fix).

- **Lain waits for your answer.** The finishing check used to tell Lain to "finish the plan" even
  right after she'd asked you a question, so she acted on choices you hadn't made yet (in testing
  she deleted saved credentials before you'd picked which ones). Now:
  - when her reply asks you something and she hasn't changed anything, there is no check: your
    answer comes first;
  - when she changed something *and* asks you something, she only verifies what's already done and
    repeats her question;
  - the check never pushes her to start unapproved work, and it no longer repeats every message
    about the same open plan steps (e.g. a step that's blocked waiting for admin rights).
- **Pasting works.** A multi-line paste (an error message, a screen of output) now arrives as one
  message instead of one message per line. The help text and README say so.
- **Pasted text can't answer prompts.** Keys typed or pasted earlier are thrown away before every
  "allow?", "keep going?" and "command stopped" prompt, so stray input can never approve a command.
- **Loop breaker catches repeated look-ups, not just failures.** If Lain runs the same read-only
  command or file read again and gets the same output, she's told it's nothing new. A third identical
  run isn't run at all: she gets the saved output back. After 4 such repeats Lain pauses and asks you
  whether to continue. Anything that changes the system resets this, so checking a result after a
  change still works, and so do commands that wait or watch (sleep, tail -f).
- **Clearer instructions** for every job:
  - when Lain offers choices, she stops and waits;
  - short replies like "yes on 1,3" or "keep X" get restated item by item (number + name) before she
    acts, and anything ambiguous gets a question instead of a guess;
  - reviews, audits and scans are read-only until you pick fixes, and reports use one numbered list
    so a number can only mean one thing;
  - credentials, accounts, user data and security software (antivirus, firewall, VPN) are only
    removed or disabled item by item, as you name them;
  - no re-running look-ups she already has the output of;
  - to restart her as administrator / with sudo, she tells you to start Lain again and type
    `/resume` (your conversation is already saved in the vault).
- **Updates appear as soon as you push.** Lain used to look only at GitHub *releases* and *tags*, so a
  push with a new `VERSION` showed "up to date". Now the start-up check and menu `u` read `VERSION` on
  the `main` branch first: if it's newer than the drive, Lain asks whether to update (the notes come
  from this changelog). Releases/tags are still used if `main` can't be read.
- **Search tips:** when a file search finds nothing, Lain is told why (the name filter matched no
  files in that folder, or the pattern is case-sensitive) so she widens the search instead of giving up.

## 1.0.0 (2026-11-26)

First versioned release.

- **New SysAdmin job** (menu `s`): Linux and Windows administration with a careful workflow.
  Lain identifies the system first, checks what already controls a device, backs up configs,
  changes one thing at a time, and verifies each change.
  Best = Qwen3.6-35B-A3B (4-bit, 64K), Mid = Qwen3.6-35B-A3B (3-bit), Laptop = Qwen3.5-9B / 4B.
  Optional **Alt** model: Gemma 4 26B-A4B as a second opinion (17 GB, download with `d`).
- **Per-job instructions** for Coding, SysAdmin, Research and Vision.
- **Loop breaker:** Lain won't rerun a command that already failed unless something changed
  in between. After 3 failures in a row she's told to step back and change approach. After 6
  failures in a row, or 25 steps on one request, she pauses and asks you whether to continue,
  stop, or take a hint.
- **Self-edit lock:** Lain can't change her own running copy on the drive unless you type
  `/selfedit`. When you talk about "the project" she works in your development copy (the git repo).
- **A subtle Lain voice:** quiet, brief, calm. It never gets in the way of exact commands or warnings.
- Coding Best/Mid now use a 64K context, so long tasks don't lose earlier output.
- **Updates from GitHub:** menu `u` also checks this repo's releases and installs a newer Lain.
  Your vault, models, quotes and splash art are kept.
- **Update check at start:** one quick request to GitHub while the hardware is scanned (silent when
  offline). If a newer Lain exists you're asked before the vault opens: update now (Lain restarts into
  it), not now, or skip this version. Create `LainAI/data/no-update-check` to switch it off.
- A few more quiet Lain touches: a short line when you disconnect, quiet greetings, lowercase asides.
- The version is shown on the splash screen, in the menu and in the installer.
- Menu shows each model's full name with its compression level (e.g. `Q4_K_XL`), its size,
  a short description and a plain "recommended" tag.
- Menu shows this machine's fast graphics memory (total + free) and slower system RAM (total + usable)
  under "What would you like to do today?", to help pick a model size.
- The online question is now "Connect to the Wired for online access? [y/n]" for every chat job
  and needs an explicit y or n.
- **Ctrl+C no longer unloads the model.** The engines run apart from the terminal's Ctrl+C, so stopping
  something never costs a multi-minute reload. They still shut down when Lain exits.
- **Check in on running commands:** a live line shows how long a command has run and how long since
  its last output. Ctrl+C stops just that command, then Enter lets Lain carry on, `b` brakes, or you
  type a hint. Commands get a 2-minute limit by default (Lain asks for longer on installs and builds).
- **SysAdmin primer rewritten as a method:** survey everything that already controls the thing
  (processes, services, autostart, scripts, logs), explain the real cause and any hard limits,
  design one owner per device using the tool's own persistent mechanisms, build small and
  **measure against the goal** (timing, CPU, log growth), make it survive reboot, clean up
  what it replaced, then report each requirement with evidence and how to undo.
- SysAdmin troubleshooting habits (learned from a real repair): check suspicions before acting on them,
  restore a known-good baseline first, read live state through the program's own API, validate the
  measurement itself, no permanent-memory writes in loops, stable IDs instead of boot-numbered paths,
  leave a sane state on stop, disable things properly and prove it, confirm dependents come back
  after a disruptive test, back up removals to one dated folder, never `pkill -f` your own command.
- **Plan tool:** for multi-step work Lain keeps a checklist (`update_plan`) that you can view any time
  with `/plan`. It's never lost to context trimming.
- **Finishing check:** in Coding and SysAdmin, when Lain has changed something, she gets one check before
  her final answer: verify each request with evidence, clean up test leftovers, make sure it survives a
  restart, finish the plan, then report changes and how to undo them.
- SysAdmin primer also: read official docs when online, keep continuous jobs to one long-running
  process (not a shell loop that launches programs), test long-running things under `timeout`,
  and propose installing missing tools instead of working around them.
- Context size falls back to the configured value when the engine log doesn't report it
  (Lain showed 32K for a 64K model).
- Menu fix: keys typed before the prompt appears are discarded, and stray arrow-key or control
  characters are removed. Input the menu doesn't understand now shows a message instead of
  quietly loading the recommended model (only Enter picks the recommended one).
