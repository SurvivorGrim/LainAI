#!/usr/bin/env bash
# Lain AI - start on Linux:  bash linux.sh   (or double-click > Run as a Program)
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
L="$DIR/LainAI"
if [ ! -t 0 ]; then   # opened from a file manager: reopen in a terminal window
  HOLD='bash "$1"; echo; read -rp "Press Enter to close " _'
  SELF="$DIR/linux.sh"
  for t in gnome-terminal kgx cosmic-term konsole xfce4-terminal alacritty kitty xterm x-terminal-emulator; do
    command -v "$t" >/dev/null || continue
    case "$t" in
      gnome-terminal|kgx) exec "$t" -- bash -c "$HOLD" _ "$SELF" ;;
      xfce4-terminal)     exec "$t" -x bash -c "$HOLD" _ "$SELF" ;;
      kitty)              exec "$t" bash -c "$HOLD" _ "$SELF" ;;
      *)                  exec "$t" -e bash -c "$HOLD" _ "$SELF" ;;
    esac
  done
fi
PY="$L/runtime/linux/bin/python3"
if ! "$PY" -c pass 2>/dev/null; then
  # the drive may be mounted without execute permission: run the bundled Python from RAM instead
  RAMPY="/dev/shm/lainai-py-$(id -u)"
  if [ -x "$RAMPY/bin/python3" ] || { cp -r "$L/runtime/linux" "$RAMPY" 2>/dev/null && chmod -R u+x "$RAMPY/bin"; }; then
    PY="$RAMPY/bin/python3"
  elif command -v python3 >/dev/null; then
    PY=python3
  else
    echo "Lain needs Python: the bundled copy couldn't run and none is installed."; exit 1
  fi
fi
exec "$PY" -I -X utf8 "$L/app/lain.py" "$@"
