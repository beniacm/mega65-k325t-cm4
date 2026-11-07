#!/usr/bin/env bash
# run_vivado.sh — run one of the JTAG tcl scripts in this directory with Vivado in batch mode.
#   scripts/jtag/run_vivado.sh scan_jtag.tcl
#   scripts/jtag/run_vivado.sh program_bit.tcl <file.bit>
# Vivado: $VIVADO_SETTINGS (default ~/Xilinx/2025.2.1/Vivado/settings64.sh).  Logs go to logs/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TCL="${1:?usage: run_vivado.sh <script.tcl> [args...]}"; shift
[ -f "$HERE/$TCL" ] || { echo "no such script: $HERE/$TCL"; exit 1; }
# shellcheck disable=SC1090
source "${VIVADO_SETTINGS:-$HOME/Xilinx/2025.2.1/Vivado/settings64.sh}"
mkdir -p "$ROOT/logs"
cd "$ROOT/logs"
vivado -mode batch -nojournal -log "jtag_${TCL%.tcl}.log" -source "$HERE/$TCL" ${1:+-tclargs "$@"}
