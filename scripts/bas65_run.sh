#!/usr/bin/env bash
# bas65_run.sh — load and run a BASIC 65 program from a text file on the rig in seconds.
#   scripts/bas65_run.sh prog.bas [--no-reset]
# Tokenises the text with the B65 repository's tools/tok65.py (load address $2001, BASIC 65
# keyword table), copies the PRG to the Rockchip and lets m65run load it through the serial
# monitor and type RUN (with --reset first: a fresh BASIC, the start menu left with '/').
# B65_DIR = the b65-basic checkout (default ~/projects/bbcbasic65); M65_HOST = the Rockchip.
# Type-in with m65ctl (7 s per line) is only for programs that must go through the editor.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
B65_DIR="${B65_DIR:-$HOME/projects/bbcbasic65}"
HOST="${M65_HOST:-192.168.1.143}"
BAS="${1:?usage: bas65_run.sh prog.bas [--no-reset]}"
[ -f "$B65_DIR/tools/tok65.py" ] || { echo "tok65.py not found: set B65_DIR (git clone https://github.com/beniacm/b65-basic)"; exit 1; }
PRG="$(mktemp /tmp/bas65_XXXXXX.prg)"
python3 "$B65_DIR/tools/tok65.py" "$BAS" "$PRG"
NAME="$(basename "${BAS%.*}").prg"
scp -q "$PRG" "root@$HOST:/tmp/$NAME"
rm -f "$PRG"
if [ "${2:-}" = "--no-reset" ]; then
  ssh "root@$HOST" "m65run /tmp/$NAME --run"
else
  ssh "root@$HOST" "m65run /tmp/$NAME --run --reset"
fi
"$ROOT/scripts/m65ctl.py" view mega65 >/dev/null 2>&1 || true
