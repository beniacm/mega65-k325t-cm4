#!/usr/bin/env bash
# sd_populate.sh — copy the MEGA65 system files (+ROM, + extras) into an existing card image,
# e.g. one partitioned/formatted by the MEGA65's own FDISK on the board.
#   scripts/sd_populate.sh <card.img> [extra-dir ...]
# Finds the FAT32 partition (MBR type 0x0B/0x0C) and copies, in this order (fresh copies are
# contiguous, which HYPPO needs for MEGA65.ROM and the .M65 files):
#   mega65-core/sdcard-files/*.M65   (built from this tree, matches the core's HYPPO)
#   sdimage/extra/*                  (MEGA65.ROM from make_rom.sh, alternate ROMs, ...)
#   each extra-dir given on the command line (recursively, e.g. unpacked intro disks)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMG="$1"; shift
OFF=$(python3 - "$IMG" <<'PY'
import sys, struct
mbr = open(sys.argv[1], "rb").read(512)
assert mbr[510:512] == b"\x55\xaa", "no MBR signature"
for i in range(4):
    e = mbr[446 + 16 * i: 446 + 16 * (i + 1)]
    if e[4] in (0x0B, 0x0C):
        print(struct.unpack("<I", e[8:12])[0]); break
else:
    sys.exit("no FAT32 partition")
PY
)
echo "FAT32 partition at sector $OFF"
export MTOOLS_SKIP_CHECK=1
M="$IMG@@$((OFF * 512))"
for f in "$ROOT"/mega65-core/sdcard-files/*.M65 "$ROOT"/sdimage/extra/*; do
  [ -f "$f" ] && mcopy -o -i "$M" "$f" "::$(basename "$f" | tr a-z A-Z)"
done
for d in "$@"; do mcopy -o -s -i "$M" "$d"/* :: ; done
mdir -i "$M" -w ::
mdir -i "$M" :: | tail -2
