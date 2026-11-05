#!/usr/bin/env bash
# program.sh — JTAG-load a MEGA65 bitstream into the K325T's SRAM (volatile).
#   scripts/program.sh [path.bit]     default: mega65-core/bin/qmtech-k325t-cm4.bit
# Reuses the proven ~/qmtech JTAG flow (Xilinx Platform Cable 03fd:0013).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIT="$(realpath "${1:-$ROOT/mega65-core/bin/qmtech-k325t-cm4.bit}")"
exec /home/me/qmtech/scripts/03_program.sh "$BIT"
