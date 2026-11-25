#!/usr/bin/env bash
# Lain AI installer for Linux.
#   bash install.sh                 interactive (recommended)
#   bash install.sh --drive /media/$USER/LAINAI --models all --yes
#   bash install.sh --format         preselect "format the drive" (you still type ERASE)
#   bash install.sh --restore-vault ~/LainAI-vault-backup-...   (put a backed-up vault onto the new drive)
# What it does:
#   1. installs what Lain needs on this computer (Vulkan loader + drivers, OpenMP, exFAT tools, curl)
#   2. finds removable drives, lets you pick one (offers to fix/format it for Windows + Linux)
#   3. downloads a portable Python and hands over to installer/lain_install.py, which downloads the
#      engines, encryption tool and models onto the drive with progress for every file.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PY_TAG=20261123; PY_VER=3.13.18      # keep in sync with installer/lain_install.py
PY_FILE="cpython-$PY_VER+$PY_TAG-x86_64-unknown-linux-gnu-install_only.tar.gz"
PY_SHA=7d42d8c2da1e5beeddc4aa086faccb7438b1676e2e14bdf65f06e9a5d0ae9aea
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/${PY_FILE//+/%2B}"
MSDATA=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7   # "Microsoft basic data" partition type: Windows mounts it, Linux too

C_A=$'\e[38;5;183m'; C_D=$'\e[38;5;245m'; C_H=$'\e[1;37m'; C_OK=$'\e[38;5;114m'; C_W=$'\e[38;5;221m'; C_E=$'\e[38;5;203m'; C_R=$'\e[0m'
STEPN=0
step() { STEPN=$((STEPN+1)); echo; echo "${C_A}━━ ${C_H}[setup $STEPN/4] $*${C_R}"; }
info() { echo "  ${C_D}$*${C_R}"; }
ok()   { echo "  ${C_OK}✓ $*${C_R}"; }
warn() { echo "  ${C_W}! $*${C_R}"; }
die()  { echo "  ${C_E}✗ $*${C_R}"; exit 1; }
ask()  { local a; read -rp "  ${C_H}$1${C_R} " a </dev/tty || a=""; echo "${a:-$2}"; }

DRIVE=""; YES=0; NO_DEPS=0; FORMAT=0; RESTORE=""; PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --drive) DRIVE="${2:?}"; shift 2 ;;
    --yes|-y) YES=1; PASS+=(--yes); shift ;;
    --no-deps) NO_DEPS=1; shift ;;
    --format) FORMAT=1; shift ;;
    --restore-vault) RESTORE="${2:?}"; shift 2 ;;
    -h|--help) sed -n "2,13p" "$0"; exit 0 ;;
    *) PASS+=("$1"); shift ;;
  esac
done
yes_no() { # prompt default(y/n)
  [ "$YES" = 1 ] && { info "$1 -> $2 (automatic)"; [ "$2" = y ]; return; }
  local a; a="$(ask "$1" "$2")"; [[ "${a,,}" == y* ]]
}

echo
echo "${C_A}   L A I N   A I   ·   installer for Linux${C_R}"
echo "${C_D}   portable · private · offline${C_R}"
[ "$(uname -m)" = x86_64 ] || die "Lain's prebuilt engines are for x86-64 PCs; this is $(uname -m)."
[ "$EUID" -eq 0 ] && warn "running as root - better to run as your normal user (sudo is used only where needed)"
SUDO=""; [ "$EUID" -ne 0 ] && SUDO="sudo"

# ------------------------------------------------------------------ 1. system packages
step "Software this computer needs"
. /etc/os-release 2>/dev/null || true
info "system: ${PRETTY_NAME:-unknown Linux}  ·  kernel $(uname -r)"
PM=""; for p in apt-get dnf zypper pacman; do command -v $p >/dev/null && { PM=$p; break; }; done
case "$PM" in
  apt-get) PKGS=(curl ca-certificates tar util-linux exfatprogs libgomp1 libvulkan1 mesa-vulkan-drivers pciutils udisks2)
           have_pkg() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "ok installed"; }
           install_pkgs() { $SUDO apt-get update && $SUDO apt-get install -y "$@"; } ;;
  dnf)     PKGS=(curl ca-certificates tar util-linux exfatprogs libgomp vulkan-loader mesa-vulkan-drivers pciutils udisks2)
           have_pkg() { rpm -q "$1" >/dev/null 2>&1; }
           install_pkgs() { $SUDO dnf install -y "$@"; } ;;
  zypper)  PKGS=(curl ca-certificates tar util-linux exfatprogs libgomp1 libvulkan1 libvulkan_radeon libvulkan_intel pciutils udisks2)
           have_pkg() { rpm -q "$1" >/dev/null 2>&1; }
           install_pkgs() { $SUDO zypper --non-interactive install "$@"; } ;;
  pacman)  PKGS=(curl ca-certificates tar util-linux exfatprogs gcc-libs vulkan-icd-loader vulkan-radeon vulkan-intel pciutils udisks2)
           have_pkg() { pacman -Qi "$1" >/dev/null 2>&1; }
           install_pkgs() { $SUDO pacman -S --needed --noconfirm "$@"; } ;;
esac
if [ "$NO_DEPS" = 1 ]; then
  info "skipped (--no-deps)"
elif [ -z "$PM" ]; then
  warn "unknown package manager - make sure these are installed: curl, tar, exfatprogs, OpenMP (libgomp), Vulkan loader + Mesa Vulkan drivers"
else
  MISSING=(); for p in "${PKGS[@]}"; do if have_pkg "$p"; then info "have  $p"; else MISSING+=("$p"); fi; done
  if [ ${#MISSING[@]} -eq 0 ]; then ok "everything needed is already installed"
  else
    info "need: ${MISSING[*]}"
    info "  curl/tar: downloads  ·  exfatprogs: cross-OS drive format  ·  libgomp: CPU engine"
    info "  vulkan + mesa: GPU engine for AMD/Intel  ·  pciutils: GPU detection  ·  udisks2: mounting drives"
    if yes_no "Install them now with $PM (asks for your password)? [Y/n]" y; then
      install_pkgs "${MISSING[@]}" && ok "installed: ${MISSING[*]}" || warn "some packages failed to install - continuing; see messages above"
    else warn "skipped - Lain may fall back to slower engines"; fi
  fi
fi
for c in curl tar sha256sum lsblk; do command -v $c >/dev/null || die "'$c' is required but missing"; done
# NVIDIA: the CUDA engine needs the proprietary driver (not installed automatically: it needs a reboot)
if lspci 2>/dev/null | grep -Eqi '(VGA|3D).*NVIDIA'; then
  if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    ok "NVIDIA driver: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
  else
    warn "NVIDIA graphics card found but no working NVIDIA driver - Lain would run much slower."
    case "${ID:-}" in
      pop)    info "  install it with:  sudo apt install system76-driver-nvidia   then reboot" ;;
      ubuntu|linuxmint) info "  install it with:  sudo ubuntu-drivers install   then reboot" ;;
      fedora) info "  enable RPM Fusion, then:  sudo dnf install akmod-nvidia   then reboot" ;;
      arch|endeavouros|manjaro) info "  install it with:  sudo pacman -S nvidia   (or nvidia-open) then reboot" ;;
      *)      info "  install your distribution's NVIDIA driver package, then reboot" ;;
    esac
  fi
fi
GLIBC="$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || true)"
if [ -n "$GLIBC" ] && [ "$(printf '%s\n2.39\n' "$GLIBC" | sort -V | head -1)" != 2.39 ]; then
  warn "glibc $GLIBC is older than 2.39: the image engine (stable-diffusion.cpp) may not start on this computer"
fi

# ------------------------------------------------------------------ 2. pick the drive
step "Choosing the drive"
ROOTDISK="$(lsblk -lnso NAME,TYPE "$(findmnt -no SOURCE / | sed 's/\[.*//')" 2>/dev/null | awk '$2=="disk"{print $1; exit}' || true)"
decode() { printf '%b' "$1"; }   # lsblk -P escapes spaces etc. as \xHH
parse() { # KEY="v" ... -> assoc array F
  local line="$1"; F=()
  while [[ $line =~ ^([A-Z0-9_:-]+)=\"([^\"]*)\"[[:space:]]?(.*)$ ]]; do F[${BASH_REMATCH[1]}]="$(decode "${BASH_REMATCH[2]}")"; line="${BASH_REMATCH[3]}"; done
}
declare -A F
scan() {
  C_DEV=(); C_DISK=(); C_MNT=(); C_FS=(); C_LINE=(); C_PTYPE=()
  while IFS= read -r d; do
    parse "$d"; local disk="${F[NAME]}"
    [ "${F[TYPE]}" = disk ] || continue
    [[ "${F[TRAN]}" == usb || "${F[RM]}" == 1 || "${F[HOTPLUG]}" == 1 ]] || continue
    [ -n "$ROOTDISK" ] && [ "$disk" = "$ROOTDISK" ] && continue
    local model="${F[MODEL]:-drive}" bus="${F[TRAN]:-removable}" dsize="${F[SIZE]}" n=0
    while IFS= read -r p; do
      parse "$p"; [ "${F[TYPE]}" = part ] || [ "${F[TYPE]}" = disk ] || continue
      [ "${F[TYPE]}" = disk ] && [ -z "${F[FSTYPE]}" ] && continue
      n=$((n+1)); C_DEV+=("${F[PATH]}"); C_DISK+=("/dev/$disk"); C_MNT+=("${F[MOUNTPOINT]}"); C_FS+=("${F[FSTYPE]}"); C_PTYPE+=("${F[PARTTYPE]}")
      C_LINE+=("$(printf '%-11s %7s  %-6s %-12s %s (%s)  %s' "${F[PATH]}" "${F[SIZE]}" "${F[FSTYPE]:-none}" "${F[LABEL]:-}" "$model" "$bus" "${F[MOUNTPOINT]:+mounted at ${F[MOUNTPOINT]}}")")
    done < <(lsblk -nP -o PATH,SIZE,FSTYPE,LABEL,MOUNTPOINT,TYPE,PARTTYPE "/dev/$disk")
    if [ $n -eq 0 ]; then  # blank disk
      C_DEV+=(""); C_DISK+=("/dev/$disk"); C_MNT+=(""); C_FS+=(""); C_PTYPE+=("")
      C_LINE+=("$(printf '%-11s %7s  %-6s %-12s %s (%s)  %s' "/dev/$disk" "$dsize" "none" "" "$model" "$bus" "empty - needs formatting")")
    fi
  done < <(lsblk -dnP -o NAME,TYPE,TRAN,RM,HOTPLUG,SIZE,MODEL)
}

backup_vault() { # mountpoint -> sets VAULT_BACKUP
  local v="$1/LainAI/vault"; [ -d "$v" ] || return 0
  VAULT_BACKUP="$HOME/LainAI-vault-backup-$(date +%Y%m%d-%H%M%S)"
  info "this drive has a Lain vault - backing it up to $VAULT_BACKUP first"
  cp -r "$v" "$VAULT_BACKUP"
  diff -rq "$v" "$VAULT_BACKUP" >/dev/null || die "vault backup didn't verify - stopping, nothing was erased"
  ok "vault backed up and verified ($(du -sh "$VAULT_BACKUP" | cut -f1))"
}

mount_dev() { # dev -> prints mountpoint
  local m; m="$(lsblk -no MOUNTPOINT "$1" | head -1)"
  if [ -z "$m" ]; then
    info "mounting $1 ..." >&2
    if ! udisksctl mount -b "$1" >/dev/null 2>&1; then
      { $SUDO mkdir -p /mnt/lainai && $SUDO mount -o uid="$(id -u)",gid="$(id -g)" "$1" /mnt/lainai; } >&2 || true
    fi
    m="$(lsblk -no MOUNTPOINT "$1" | head -1)"
  fi
  echo "$m"
}

show_contents() { # disk -> warning listing of what would be lost
  local p m used n
  echo
  echo "  ${C_E}┌──────────────────────────────────────────────────────────────┐${C_R}"
  echo "  ${C_E}│  WARNING: formatting ERASES EVERYTHING on this drive.        │${C_R}"
  echo "  ${C_E}│  It can't be undone. Back up anything you want to keep.      │${C_R}"
  echo "  ${C_E}└──────────────────────────────────────────────────────────────┘${C_R}"
  echo "  ${C_H}$1  ·  $(lsblk -dno SIZE "$1" | tr -d ' ')  ·  $(lsblk -dno MODEL "$1")${C_R}"
  for p in $(lsblk -lnpo PATH,TYPE "$1" | awk '$2=="part"{print $1}'); do
    m="$(lsblk -no MOUNTPOINT "$p" | head -1)"
    echo "    $p  $(lsblk -no FSTYPE,LABEL "$p" | head -1)"
    [ -n "$m" ] || continue
    used="$(df -h --output=used "$m" | tail -1 | tr -d ' ')"
    n="$(find "$m" -mindepth 1 -maxdepth 1 ! -name '.Trash-*' ! -name 'System Volume Information' 2>/dev/null | wc -l)"
    echo "      ${C_W}$used in use, $n item(s) at the top level:${C_R}"
    find "$m" -mindepth 1 -maxdepth 1 ! -name '.Trash-*' ! -name 'System Volume Information' -printf '        %f\n' 2>/dev/null | head -8
    [ "$n" -gt 8 ] && echo "        … and $((n-8)) more"
    [ -d "$m/LainAI/vault" ] && echo "      ${C_W}contains a Lain vault - it will be backed up to your home folder first${C_R}"
    [ -d "$m/LainAI/models" ] && echo "      ${C_W}contains Lain models ($(du -sh "$m/LainAI/models" 2>/dev/null | cut -f1)) - they'd have to be downloaded again${C_R}"
  done
  return 0
}

format_disk() { # disk
  local disk="$1"
  [ -n "$ROOTDISK" ] && [ "$disk" = "/dev/$ROOTDISK" ] && die "refusing: that's the disk this system runs from"
  local p m
  for p in $(lsblk -lnpo PATH,TYPE "$disk" | awk '$2=="part"{print $1}'); do   # mount so we can show + back up contents
    [ -n "$(lsblk -no FSTYPE "$p")" ] && [ -z "$(lsblk -no MOUNTPOINT "$p" | head -1)" ] && udisksctl mount -b "$p" >/dev/null 2>&1 || true
  done
  show_contents "$disk"
  echo
  local a; a="$(ask "Type ERASE (capitals) to format $disk as exFAT, anything else cancels:" "")"
  [ "$a" = ERASE ] || { info "cancelled - nothing was changed"; return 1; }
  for p in $(lsblk -lnpo PATH,TYPE "$disk" | awk '$2=="part"{print $1}'); do
    m="$(lsblk -no MOUNTPOINT "$p" | head -1)"
    [ -n "$m" ] || continue
    backup_vault "$m"
    info "unmounting $p ..."
    udisksctl unmount -b "$p" >/dev/null 2>&1 || $SUDO umount "$p" || die "couldn't unmount $p - close any windows/terminals using it and run again"
  done
  command -v mkfs.exfat >/dev/null || die "mkfs.exfat missing - install exfatprogs and run again"
  info "[1/4] wiping old filesystem signatures..."; $SUDO wipefs -a "$disk" >/dev/null
  info "[2/4] new GPT partition table, one 'Microsoft basic data' partition (Windows + Linux both mount it)..."
  printf 'label: gpt\ntype=%s, name="LAINAI"\n' "$MSDATA" | $SUDO sfdisk --quiet --wipe always "$disk"
  $SUDO partprobe "$disk" 2>/dev/null || true; udevadm settle 2>/dev/null || sleep 2
  local part; part="$(lsblk -lnpo PATH,TYPE "$disk" | awk '$2=="part"{print $1; exit}')"
  [ -n "$part" ] || die "partition didn't appear - unplug/replug the drive and run again"
  # the desktop may auto-mount the old filesystem signature for a moment; make sure it's free
  udisksctl unmount -b "$part" >/dev/null 2>&1 || true
  info "[3/4] creating exFAT filesystem on $part (label LAINAI)..."; $SUDO mkfs.exfat -L LAINAI "$part" >/dev/null
  udevadm settle 2>/dev/null || sleep 2
  info "[4/4] checking the result..."
  local fs pt
  fs="$(lsblk -no FSTYPE "$part" | head -1)"; pt="$(lsblk -no PARTTYPE "$part" | head -1)"
  [ "$fs" = exfat ] || die "format check failed: filesystem is '${fs:-none}', expected exfat"
  [ "${pt,,}" = "${MSDATA,,}" ] || die "format check failed: partition type is '$pt'"
  ok "formatted and verified: $part  exFAT  'LAINAI'  GPT / Microsoft basic data - readable on Windows, Linux and macOS"
  SEL_DEV="$part"; SEL_FS=exfat
}

fix_tag() { # disk part
  local pn; pn="$(cat "/sys/class/block/$(basename "$2")/partition")"
  $SUDO sfdisk --no-reread --part-type "$1" "$pn" "$MSDATA" >/dev/null && ok "partition is now 'Microsoft basic data' - Windows will show it (files untouched)" \
    || warn "couldn't change it (drive busy?) - Windows may not see this drive"
}

SEL_DEV=""; SEL_FS=""; VAULT_BACKUP=""
if [ -n "$DRIVE" ]; then
  [ -d "$DRIVE" ] || die "not a folder: $DRIVE"
  SEL_FS="$(findmnt -no FSTYPE -T "$DRIVE" || true)"
else
  while :; do
    scan
    if [ ${#C_LINE[@]} -eq 0 ]; then
      warn "no removable drives found. Plug one in and press Enter (or type a mount folder, q to quit)."
    else
      info "removable drives:"
      for i in "${!C_LINE[@]}"; do echo "   ${C_H}$((i+1)))${C_R} ${C_LINE[$i]}"; done
      info "   r) rescan   ·   a folder path) use that folder   ·   q) quit"
    fi
    a="$(ask "Install Lain on which drive? [1]:" "$([ ${#C_LINE[@]} -gt 0 ] && echo 1 || echo r)")"
    case "$a" in
      q) exit 0 ;;
      r|"") continue ;;
      /*) [ -d "$a" ] && { DRIVE="$a"; SEL_FS="$(findmnt -no FSTYPE -T "$a" || true)"; break; }; warn "not a folder: $a"; continue ;;
    esac
    [[ "$a" =~ ^[0-9]+$ ]] && [ "$a" -ge 1 ] && [ "$a" -le ${#C_LINE[@]} ] || { warn "pick a number from the list"; continue; }
    i=$((a-1)); SEL_DEV="${C_DEV[$i]}"; SEL_FS="${C_FS[$i]}"; disk="${C_DISK[$i]}"
    badtag=0; [ -n "${C_PTYPE[$i]}" ] && [ "${C_PTYPE[$i],,}" != "${MSDATA,,}" ] && badtag=1
    # what's wrong with it as-is, and can it be kept?
    keep=1
    case "$SEL_FS" in
      exfat) if [ $badtag = 1 ]; then warn "exFAT, but the partition is tagged '$(lsblk -no PARTTYPENAME "$SEL_DEV")' - Windows won't show it"
             else ok "exFAT with the right partition type - already works on Windows, Linux and macOS"; fi ;;
      ntfs)  warn "NTFS - works on Windows and most Linux, but exFAT is the safer cross-OS choice" ;;
      vfat)  warn "FAT32 - can't store files over 4 GB and the models are bigger: it has to be formatted"; keep=0 ;;
      "")    warn "no filesystem - it has to be formatted"; keep=0 ;;
      *)     warn "$SEL_FS - Windows can't read it: it has to be formatted to work everywhere"; keep=0 ;;
    esac
    m=""
    if [ -n "$SEL_DEV" ] && [ $keep = 1 ]; then
      m="$(lsblk -no MOUNTPOINT "$SEL_DEV" | head -1)"
      [ -n "$m" ] || { udisksctl mount -b "$SEL_DEV" >/dev/null 2>&1 || true; m="$(lsblk -no MOUNTPOINT "$SEL_DEV" | head -1)"; }
    fi
    echo
    echo "   ${C_H}1)${C_R} Format as exFAT for Windows + Linux  ${C_E}(ERASES everything on the drive)${C_R}"
    if [ $keep = 1 ]; then
      echo "   ${C_H}2)${C_R} Keep the files and install onto it as it is$([ $badtag = 1 ] && echo " (and fix the partition tag - files stay)")"
      def=2; [ "$FORMAT" = 1 ] && def=1                       # usable as it is: never default to erasing it
      echo "   ${C_H}b)${C_R} back to the drive list"
      c="$(ask "Choose [$def]:" "$def")"
    else
      echo "   ${C_H}b)${C_R} back to the drive list"
      c="$(ask "Choose [1]:" 1)"
    fi
    case "$c" in
      1) format_disk "$disk" || continue ;;
      2) [ $keep = 1 ] || { warn "this drive has to be formatted"; continue; }
         [ $badtag = 1 ] && fix_tag "$disk" "$SEL_DEV" ;;
      *) continue ;;
    esac
    DRIVE="$(mount_dev "$SEL_DEV")"
    [ -n "$DRIVE" ] && [ -d "$DRIVE" ] || { warn "couldn't mount $SEL_DEV - open it in your file manager, then rescan"; continue; }
    break
  done
fi
ok "installing to: $DRIVE  ($(df -h --output=avail "$DRIVE" | tail -1 | tr -d ' ') free, ${SEL_FS:-unknown fs})"

# ------------------------------------------------------------------ 3. portable Python for the installer
step "Installer runtime (portable Python $PY_VER, nothing installed system-wide)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/lainai-installer"; mkdir -p "$CACHE"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [ -f "$CACHE/$PY_FILE" ] && [ "$(sha256sum "$CACHE/$PY_FILE" | cut -d' ' -f1)" = "$PY_SHA" ]; then
  ok "using cached copy ($CACHE)"
else
  info "downloading $PY_FILE"
  curl -fL --retry 5 --progress-bar -o "$CACHE/$PY_FILE.part" "$PY_URL"
  [ "$(sha256sum "$CACHE/$PY_FILE.part" | cut -d' ' -f1)" = "$PY_SHA" ] || { rm -f "$CACHE/$PY_FILE.part"; die "checksum mismatch on the Python download"; }
  mv "$CACHE/$PY_FILE.part" "$CACHE/$PY_FILE"; ok "downloaded and checksum verified"
fi
tar xzf "$CACHE/$PY_FILE" -C "$TMP"
PY="$TMP/python/bin/python3"; "$PY" -c pass || die "portable Python didn't start"
ok "ready"

# ------------------------------------------------------------------ 4. hand over
step "Installing Lain onto the drive"
set +e
"$PY" -I -X utf8 "$HERE/installer/lain_install.py" --drive "$DRIVE" --fs "${SEL_FS:-}" --python-archive "$CACHE/$PY_FILE" "${PASS[@]}"
RC=$?
set -e

RESTORE="${RESTORE:-$VAULT_BACKUP}"
if [ -n "$RESTORE" ]; then
  echo
  if [ -e "$DRIVE/LainAI/vault/vault.age" ]; then
    info "the drive already has a vault - not restoring $RESTORE"
  elif [ -f "$RESTORE/vault.age" ]; then
    if yes_no "Put your backed-up vault ($RESTORE) onto the drive? [Y/n]" y; then
      mkdir -p "$DRIVE/LainAI/vault"; cp -r "$RESTORE"/. "$DRIVE/LainAI/vault/"; sync
      diff -rq "$RESTORE" "$DRIVE/LainAI/vault" >/dev/null && ok "vault restored and verified (same passphrase as before)" || warn "vault copy differs - check $DRIVE/LainAI/vault"
      info "the backup stays in $RESTORE until you delete it"
    fi
  else warn "$RESTORE has no vault.age - nothing restored"; fi
fi
[ $RC -eq 0 ] && info "Eject the drive from your file manager before unplugging it."
exit $RC
