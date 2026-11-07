#!/usr/bin/env bash
# attic_memtest_basic.sh -- type a BASIC 65 attic-RAM test into the MEGA65 (via the Rockchip
# keyboard bridge), RUN it, and capture the result.  Needs BASIC 65 at READY.
#   scripts/attic_memtest_basic.sh [size_mb [wait_s]]    default 64 MB (K325T), 8 = real MEGA65
#   0. (> 8 MB) unlock: POKE $BFFFFFC,size, PEEK must read it back; the top 16 bytes are the
#      controller registers, so the last block is filled 16 bytes short
#   1. DMA-fill size/64K blocks with the block number (low byte), verify samples + each last byte
#   2. address lines: POKE $8000000+2^k (k=0..log2(size)-1), base must stay 0
#   3. mirror: $8000001+size must equal $8000001
#   4. $55/$AA/$00/$FF at 500 random addresses
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTL="$ROOT/scripts/m65ctl.py"
MB=${1:-64}
NB=$(( MB * 16 ))                                  # 64 KB blocks
BITS=$(( $(echo "l($MB)/l(2)" | bc -l | cut -d. -f1) + 19 ))   # highest address line
[ $(( 1 << (BITS + 1) )) -eq $(( MB << 20 )) ] || { echo "size must be a power of two MB"; exit 1; }
PROG=(
'NEW'
"10 REM ATTIC RAM TEST ${MB}MB"
'20 B=$8000000:E=0'
"25 IF $MB>8 THEN POKE \$BFFFFFC,$MB:IF PEEK(\$BFFFFFC)<>$MB THEN PRINT \"NO ${MB}MB UNLOCK\":END"
"30 PRINT \"FILL $NB BLOCKS\":FOR I=0 TO $((NB - 1)):A=B+I*65536:V=I AND 255:GOSUB 200"
'40 EDMA 3,L,V,A:POKE A+L,V:NEXT'
"50 PRINT \"VERIFY\":FOR I=0 TO $((NB - 1)):A=B+I*65536:V=I AND 255:GOSUB 200"
'60 FOR J=0 TO L STEP 4099:IF PEEK(A+J)<>V THEN E=E+1'
'70 NEXT:IF PEEK(A+L)<>V THEN E=E+1'
'80 NEXT:PRINT "ERRORS";E'
'90 PRINT "ADDRESS LINES":POKE B,0:C=1'
"100 FOR K=0 TO $BITS:POKE B+C,K+1"
'110 IF PEEK(B+C)<>K+1 OR PEEK(B)<>0 THEN E=E+1:PRINT "BIT";K'
'120 C=C*2:NEXT'
"130 IF PEEK(B+$((MB << 20))+1)<>1 THEN E=E+1:PRINT \"NO ${MB}MB MIRROR\""
"140 PRINT \"PATTERNS\":FOR R=1 TO 500:A=B+INT(RND(1)*$(( (MB << 20) - 16 )))"
'150 FOR V=0 TO 3:D=VAL(MID$("085170000255",V*3+1,3))'
'160 POKE A,D:IF PEEK(A)<>D THEN E=E+1'
'170 NEXT:NEXT'
'180 PRINT "ERRORS";E:IF E=0 THEN PRINT "ATTIC RAM OK"'
'190 END'
"200 L=65535:IF $MB>8 AND I=$((NB - 1)) THEN L=65519"
'210 RETURN'
'SCNCLR:LIST'
)
for l in "${PROG[@]}"; do
  "$CTL" type "$l\\n" >/dev/null
  sleep $(( (${#l} + 9) / 10 + 1 ))      # ~0.1 s per key + margin
done
sleep 3
"$CTL" type 'RUN\n' >/dev/null
sleep "${2:-$(( 40 + MB * 4 ))}"
"$ROOT/scripts/capture.sh" -n 10 | grep file:
