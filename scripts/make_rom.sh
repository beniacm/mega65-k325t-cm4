#!/usr/bin/env bash
# make_rom.sh — build MEGA65.ROM from the original C65 ROM 910828 + the official Filehost diff.
#   scripts/make_rom.sh <c65-910828.bin> <patch.zip|dir>   -> sdimage/extra/MEGA65.ROM
# 910828 sources (identical, SHA-1 b63d970727a2b8da72a0a8e234f3c30a20cbcb26):
#   * Cloanto C64 Forever 11 Express MSI: Data1.cab -> file_Shared_54 (= c-65-19910828.rom)
#   * zimmers.net .../firmware/computers/c65/910828.bin
# Patch: Filehost "C65/MEGA65 Kernal ROM diff files - Stable" (920413: files/other/920413_Sn7YEw.zip).
# The .rdf and .bdf in the zip are independent encodings of the same patch; the ROM is
# accepted only if both produce the identical file (a wrong base makes them disagree).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="$1"; SRC="$2"; T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
if [ -d "$SRC" ]; then cp -r "$SRC"/. "$T"/; else unzip -q -o "$SRC" -d "$T"; fi
RDF=$(find "$T" -iname '*.rdf' | head -1); BDF=$(find "$T" -iname '*.bdf' | head -1)
[ -n "$RDF" ] && [ -n "$BDF" ] || { echo "patch must contain .rdf and .bdf"; exit 1; }
python3 "$ROOT/scripts/rdfpatch.py" "$BASE" "$T/rdf.rom" "$RDF" >/dev/null
python3 "$ROOT/scripts/bspatch.py"  "$BASE" "$T/bdf.rom" "$BDF" >/dev/null
cmp -s "$T/rdf.rom" "$T/bdf.rom" || { echo "RDF and BDF results differ -> wrong base ROM (need C65 910828)"; exit 1; }
mkdir -p "$ROOT/sdimage/extra"; cp "$T/bdf.rom" "$ROOT/sdimage/extra/MEGA65.ROM"
"$ROOT/scripts/rom_rtcpatch.py" "$ROOT/sdimage/extra/MEGA65.ROM"     # RTC on board id $62 (soft_rtc)
echo "MEGA65.ROM $(grep -aoE 'V9[0-9]{5}' "$T/bdf.rom" | head -1)  sha1 $(sha1sum "$T/bdf.rom" | cut -c1-40)  (RDF==BDF)"
