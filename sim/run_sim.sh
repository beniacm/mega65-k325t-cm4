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
