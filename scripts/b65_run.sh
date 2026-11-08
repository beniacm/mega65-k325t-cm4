#!/usr/bin/env bash
# b65_run.sh — start B65 BASIC (https://github.com/beniacm/b65-basic) on the rig.
#   scripts/b65_run.sh                 # build if needed, load build/b65.prg, fresh reset, RUN
#   scripts/b65_run.sh demo FILE.bas   # the same, then type the program (lower case: B65 swaps
#                                      #   the case of letters like a BBC Micro) and RUN it
# B65_DIR = the checkout (default ~/projects/bbcbasic65); M65_HOST = the Rockchip (192.168.1.143).
# The loader is the B65 repo's tests/board.py (scp + m65run --reset --run on the Rockchip).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
B65_DIR="${B65_DIR:-$HOME/projects/bbcbasic65}"
[ -d "$B65_DIR" ] || { echo "B65_DIR=$B65_DIR not found (git clone https://github.com/beniacm/b65-basic)"; exit 1; }
[ -f "$B65_DIR/build/b65.prg" ] || make -C "$B65_DIR"
python3 "$B65_DIR/tests/board.py" load
if [ "${1:-}" = demo ]; then
  sleep 3
  tr 'A-Z' 'a-z' < "$2" | while IFS= read -r line; do
    "$ROOT/scripts/m65ctl.py" type "$line\\n" >/dev/null
    sleep 7                                   # B65 tokenises and stores each line as it arrives
  done
  "$ROOT/scripts/m65ctl.py" type "run\\n"
fi
