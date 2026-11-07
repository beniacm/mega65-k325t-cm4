#!/usr/bin/env bash
# program.sh — JTAG-load a bitstream into the K325T's SRAM (volatile; lost at power-off).
#   scripts/program.sh [path.bit]     default: mega65-core/bin/qmtech-k325t-cm4.bit
# Xilinx Platform Cable USB (03fd:0013) on the board's JTAG header; run
# scripts/jtag/setup_jtag_udev.sh once.  For the configuration flash use rockchip/m65flash.py.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIT="$(realpath "${1:-$ROOT/mega65-core/bin/qmtech-k325t-cm4.bit}")"
"$ROOT/scripts/jtag/run_vivado.sh" scan_jtag.tcl 2>&1 | grep -iE 'DEVICE=|PART|IDCODE|DEVICES_FOUND'
"$ROOT/scripts/jtag/run_vivado.sh" program_bit.tcl "$BIT" 2>&1 | grep -iE 'PROGRAMMED=|DONE|ERROR'
