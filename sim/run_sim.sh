#!/usr/bin/env bash
# run_sim.sh — GHDL: real MEGA65 SdCardCtrl <-> sd_emu <-> rk_bridge <-> model Rockchip.
# Expects "TB_RK_SD_PASS".  sdcard_simcopy.vhdl = upstream sdcard.vhdl with byteCnt_v's
# range widened (it underflows by design after a write; harmless in hardware, fatal in GHDL).
set -euo pipefail
cd "$(dirname "$0")"
V=../mega65-core/src/vhdl
sed -E 's/(variable byteCnt_v *: *)natural range 0 to ([A-Za-z_0-9]+)/\1integer range -2 to \2/' \
  $V/sdcard.vhdl > sdcard_simcopy.vhdl
ghdl -a --std=08 -frelaxed sdcard_simcopy.vhdl $V/sd_emu.vhdl $V/rk_bridge.vhdl tb_rk_sd.vhdl
ghdl -e --std=08 -frelaxed tb_rk_sd
ghdl -r --std=08 -frelaxed tb_rk_sd --stop-time=300ms 2>&1 | grep -v "st\[" | sed 's/.*(report note): //'
# Monitor UART burst: the real UART_TX_CTRL (debug report line stripped: its to_hstring
# clashes with VHDL-2008 numeric_std in GHDL) sends 64 back-to-back bytes, all must arrive.
sed -e '/report "UART_TX: Sending byte/d' -e '/use work.debugtools.all;/d' $V/UART_TX_CTRL.vhdl > uart_tx_simcopy.vhdl
ghdl -a --std=08 -frelaxed uart_tx_simcopy.vhdl tb_uart_burst.vhdl
ghdl -e --std=08 -frelaxed tb_uart_burst
ghdl -r --std=08 -frelaxed tb_uart_burst --stop-time=5ms 2>&1 | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /' 
# DDR3 attic RAM controller against a MIG native-UI model (independent app_rdy/app_wdf_rdy).
ghdl -a --std=08 -frelaxed $V/ddr3_attic.vhdl tb_ddr3_attic.vhdl
ghdl -e --std=08 -frelaxed tb_ddr3_attic
ghdl -r --std=08 -frelaxed tb_ddr3_attic --stop-time=10ms 2>&1 | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'
