#!/usr/bin/env bash
# c64_run.sh -- C64 core on the K325T: fresh JTAG load, mount /c64/k325t.d64 on drive 8,
# optionally enable the simulated 1750 REU, LOAD + RUN one program, capture frames.
#   scripts/c64_run.sh <PROGRAM> [reu] [seconds-to-watch]
# Assumes m65d serves the C64 card image (m65d_restart.sh -i /root/m65/c64.img) and the
# menu file browser starts in /c64; it lists disk images only, sorted, so k325t.d64 is
# entry 5 ("..", tests, Disk-Write-Test.d64, eobsave.d64, k325t.d64): K325T_POS=4 DOWNs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROG="$1"; REU="${2:-}"; WATCH="${3:-12}"
POS="${K325T_POS:-4}"     # entries above k325t.d64 in the drive browser
C="python3 $ROOT/scripts/m65ctl.py"
"$ROOT/scripts/program.sh" "$ROOT/c64mega65/K325T/bin/c64_k325t_cm4.bit" | grep -E "PROGRAM_BIT_DONE|ERROR:"
sleep 4
$C key HELP >/dev/null; sleep 1
$C key RETURN >/dev/null; sleep 2                       # 8: mount -> file browser
for ((i = 0; i < POS; i++)); do $C key DOWN >/dev/null; sleep 0.3; done
$C key RETURN >/dev/null; sleep 6                       # k325t.d64, back in the menu
if [ "$REU" = reu ]; then
  for i in 1 2 3; do $C key DOWN >/dev/null; sleep 0.3; done
  $C key RETURN >/dev/null; sleep 1                     # Simulate 1750 REU 512KB
fi
$C key HELP >/dev/null; sleep 1
$C type "LOAD\"$PROG\",8\n" >/dev/null; sleep 7
$C type 'RUN\n' >/dev/null
for ((t = 0; t < WATCH; t += 2)); do
  sleep 2
  "$ROOT/scripts/capture.sh" -n 2 2>&1 | grep -o 'frame_[0-9_]*\.png' | head -1
done
