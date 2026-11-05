#!/usr/bin/env bash
# make_sdimage.sh — build a sparse MEGA65 SD-card image for the Rockchip SD emulator.
#   scripts/make_sdimage.sh [out.img] [size]      default: sdimage/sd.img, 2G
# MBR + one FAT32 (type 0x0C) partition at 1 MiB, with the core's system files
# (BANNER/FREEZER/ONBOARD.M65) plus anything in sdimage/extra/.  Without a
# MEGA65.ROM on the card HYPPO keeps the OpenROMs built into the bitstream;
# drop your own MEGA65.ROM into sdimage/extra/ to use the original ROM.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/sdimage/sd.img}"
SIZE="${2:-2G}"
mkdir -p "$(dirname "$OUT")" "$ROOT/sdimage/extra"
rm -f "$OUT"
truncate -s "$SIZE" "$OUT"
echo 'label: dos
start=2048, type=c' | sfdisk -q "$OUT"
mkfs.vfat -F 32 -n MEGA65 --offset 2048 "$OUT" >/dev/null
export MTOOLS_SKIP_CHECK=1
for f in "$ROOT"/mega65-core/sdcard-files/*.M65 "$ROOT"/sdimage/extra/*; do
  [ -f "$f" ] && mcopy -o -i "$OUT@@1M" "$f" ::
done
mdir -i "$OUT@@1M" ::
du -h --apparent-size "$OUT"; du -h "$OUT"
