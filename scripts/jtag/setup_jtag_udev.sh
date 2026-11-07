#!/usr/bin/env bash
# setup_jtag_udev.sh — let your user drive the Xilinx JTAG cable (Platform Cable USB 03fd:0013)
# without sudo: installs Vivado's bundled udev rules (MODE=0666 for Xilinx/FTDI/Digilent cables).
# Idempotent.  Vivado: $VIVADO_DIR (default ~/Xilinx/2025.2.1/Vivado).  Asks for sudo.
set -euo pipefail
VIVADO_DIR="${VIVADO_DIR:-$HOME/Xilinx/2025.2.1/Vivado}"
DRV="$VIVADO_DIR/data/xicom/cable_drivers/lin64/install_script/install_drivers"
for r in 52-xilinx-pcusb.rules 52-xilinx-ftdi-usb.rules 52-xilinx-digilent-usb.rules; do
  sudo install -m 0644 "$DRV/$r" "/etc/udev/rules.d/$r"
  echo "installed /etc/udev/rules.d/$r"
done
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
