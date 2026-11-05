#!/usr/bin/env bash
# build.sh — build the MEGA65 bitstream for the QMTech XC7K325T CM4-carrier board.
#   scripts/build.sh            -> mega65-core/bin/qmtech-k325t-cm4.bit (+ timing report)
# Host quirks handled here:
#   * linuxbrew's pkg-config shadows the system one and cannot see libpng.pc
#   * exomizer comes from Homebrew (brew install exomizer)
#   * Vivado 2025.2.1 at ~/Xilinx (the repo's vivado_wrapper honours VIVADODIR)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PKG_CONFIG_PATH=/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig
export VIVADODIR="${VIVADODIR:-/home/me/Xilinx/2025.2.1/Vivado}"
command -v exomizer >/dev/null || { echo "exomizer missing: brew install exomizer"; exit 1; }
mkdir -p "$ROOT/logs"
cd "$ROOT/mega65-core"
make bin/qmtech-k325t-cm4.bit 2>&1 | tee "$ROOT/logs/build_k325t_cm4.log"
ls -l bin/qmtech-k325t-cm4.bit
grep -E "WNS|TNS|Timing constraints are" qmtech-k325t-cm4.timing.txt 2>/dev/null | head -5 || true
