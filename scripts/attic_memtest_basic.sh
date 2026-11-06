#!/usr/bin/env bash
# attic_memtest_basic.sh -- type a BASIC 65 attic-RAM test into the MEGA65 (via the Rockchip
# keyboard bridge), RUN it, and capture the result.  Needs BASIC 65 at READY.
#   1. DMA-fill 128 x 64 KB blocks with the block number, verify samples + each last byte
#   2. address lines: POKE $8000000+2^k (k=0..22), base must stay 0
#   3. 8 MB mirror: $8800001 must equal $8000001
#   4. $55/$AA/$00/$FF at 500 random addresses
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTL="$ROOT/scripts/m65ctl.py"
PROG=(
'NEW'
'10 REM ATTIC RAM TEST 8MB'
'20 B=$8000000:E=0'
'30 PRINT "FILL 128 BLOCKS":FOR I=0 TO 127:A=B+I*65536'
'40 EDMA 3,65535,I,A:POKE A+65535,I:NEXT'
'50 PRINT "VERIFY":FOR I=0 TO 127:A=B+I*65536'
'60 FOR J=0 TO 65535 STEP 4099:IF PEEK(A+J)<>I THEN E=E+1'
'70 NEXT:IF PEEK(A+65535)<>I THEN E=E+1'
'80 NEXT:PRINT "ERRORS";E'
'90 PRINT "ADDRESS LINES":POKE B,0:C=1'
'100 FOR K=0 TO 22:POKE B+C,K+1'
'110 IF PEEK(B+C)<>K+1 OR PEEK(B)<>0 THEN E=E+1:PRINT "BIT";K'
'120 C=C*2:NEXT'
'130 IF PEEK($8800001)<>1 THEN E=E+1:PRINT "NO 8MB MIRROR"'
'140 PRINT "PATTERNS":FOR R=1 TO 500:A=B+INT(RND(1)*8388608)'
'150 FOR V=0 TO 3:D=VAL(MID$("085170000255",V*3+1,3))'
'160 POKE A,D:IF PEEK(A)<>D THEN E=E+1'
'170 NEXT:NEXT'
'180 PRINT "ERRORS";E:IF E=0 THEN PRINT "ATTIC RAM OK"'
'SCNCLR:LIST'
)
for l in "${PROG[@]}"; do
  "$CTL" type "$l\\n" >/dev/null
  sleep $(( (${#l} + 9) / 10 + 1 ))      # ~0.1 s per key + margin
done
sleep 3
"$CTL" type 'RUN\n' >/dev/null
sleep "${1:-60}"
"$ROOT/scripts/capture.sh" -n 10 | grep file:
