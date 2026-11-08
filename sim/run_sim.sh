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
ghdl -a --std=08 -frelaxed $V/cputypes.vhdl $V/ddr3_attic.vhdl tb_ddr3_attic.vhdl
ghdl -e --std=08 -frelaxed tb_ddr3_attic
ghdl -r --std=08 -frelaxed tb_ddr3_attic --stop-time=10ms 2>&1 | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'
# Ethernet: RMII MAC model <-> eth_vphy <-> rk_bridge <-> model Rockchip (60/100/1514-byte frames).
ghdl -a --std=08 -frelaxed $V/eth_vphy.vhdl $V/rk_bridge.vhdl tb_eth.vhdl
ghdl -e --std=08 -frelaxed tb_eth
ghdl -r --std=08 -frelaxed tb_eth --stop-time=20ms 2>&1 | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'
# ---------------- C64 for MEGA65 (M2M) port: c64mega65/K325T ----------------
C=../c64mega65
Q=$C/M2M/QNICE/vhdl
mkdir -p c64work
# SD: QNICE sdcard (SimpleSDHC; CRC on) <-> sd_emu (SYNC_IN) <-> rk_bridge <-> model Rockchip.
# Sim copies: SimpleSDHC's byte_counter underflows and byte_bram is indexed with 512 by design
# (harmless in hardware, range errors in GHDL).
sed -E 's/(signal byte_counter, new_byte_counter : )integer range 0 to 512/\1integer/' $Q/sd_spi.vhd > sd_spi_simcopy.vhd
sed -e 's/ram(to_integer(address_i))/ram(to_integer(address_i) mod SIZE_BYTES)/' \
    -e 's/ram(to_integer(address_o))/ram(to_integer(address_o) mod SIZE_BYTES)/' $Q/byte_bram.vhd > byte_bram_simcopy.vhd
ghdl -a --std=08 -frelaxed --workdir=c64work byte_bram_simcopy.vhd sd_spi_simcopy.vhd $Q/sdcard.vhd \
  $V/sd_emu.vhdl $V/rk_bridge.vhdl tb_c64_sd.vhdl
ghdl -e --std=08 -frelaxed --workdir=c64work tb_c64_sd
ghdl -r --std=08 -frelaxed --workdir=c64work tb_c64_sd --stop-time=300ms 2>&1 | grep -v "simulation stopped" | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'
# DDR3: avm_mig (M2M Avalon bus -> MIG) against a pipelined MIG model, random bursts.
ghdl -a --std=08 -frelaxed --workdir=c64work $C/K325T/avm_mig.vhd tb_avm_mig.vhdl
ghdl -e --std=08 -frelaxed --workdir=c64work tb_avm_mig
ghdl -r --std=08 -frelaxed --workdir=c64work tb_avm_mig --stop-time=200ms 2>&1 | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'
# Keyboard: M2M's mega65kbd_to_matrix (sim copy: column index range widened to its RAM's) <-> m65kbd_emu.
sed 's/matrix_col_idx : in integer range 0 to 9;/matrix_col_idx : in integer range 0 to 15;/' \
  $C/M2M/vhdl/controllers/M65/mega65kbd_to_matrix.vhdl > mega65kbd_to_matrix_simcopy.vhdl
ghdl -a --std=08 -frelaxed --workdir=c64work $C/M2M/vhdl/controllers/M65/kb_matrix_ram.vhdl \
  mega65kbd_to_matrix_simcopy.vhdl $C/K325T/m65kbd_emu.vhd tb_kbd_emu.vhdl
ghdl -e --std=08 -frelaxed --workdir=c64work tb_kbd_emu
ghdl -r --std=08 -frelaxed --workdir=c64work tb_kbd_emu --stop-time=50ms 2>&1 | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'

# C64 core: REU + cartridge memory paths end to end (reu_mapper, avm_cache, avm_fifo, both
# arbiters, avm_mig, MIG model incl. the UG586 write-data rule), with scaler-like traffic
ghdl -a --std=08 -frelaxed --workdir=c64work axi_fifo_sim.vhdl $C/M2M/vhdl/memory/avm_fifo.vhd \
    $C/M2M/vhdl/memory/avm_cache.vhd $C/M2M/vhdl/memory/avm_arbit.vhd \
    $C/M2M/vhdl/memory/avm_arbit_general.vhd $C/CORE/vhdl/reu_mapper.vhd tb_c64_reu.vhdl
ghdl -e --std=08 -frelaxed --workdir=c64work tb_c64_reu
ghdl -r --std=08 -frelaxed --workdir=c64work tb_c64_reu --stop-time=40ms --ieee-asserts=disable 2>&1 \
    | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'

# C64 core: software-cartridge loader (real sw_cartridge_wrapper) writing the first 8 KB of a
# real EasyFlash .crt byte by byte through avm_mig + framework arbiter (with scaler traffic),
# then parsing it.  Needs eob_head.hex (head -c 8192 file.crt | xxd -p -c1 > eob_head.hex).
sed 's/variable char_index_v  : natural range 1 to 32;/variable char_index_v  : natural range 0 to 32;/' \
  $C/M2M/vhdl/qnice_csr.vhd > qnice_csr_simcopy.vhd
if [ -f eob_head.hex ]; then
ghdl -a --std=08 -frelaxed --workdir=c64work $C/M2M/vhdl/cdc_stable.vhd $C/M2M/vhdl/cdc_slow.vhd \
  qnice_csr_simcopy.vhd $C/M2M/vhdl/qnice2hyperram.vhd $C/M2M/vhdl/tdp_ram.vhd \
  $C/CORE/vhdl/crt_parser.vhd $C/CORE/vhdl/crt_cacher.vhd $C/CORE/vhdl/crt_loader.vhd \
  $C/CORE/vhdl/sw_cartridge_csr.vhd $C/CORE/vhdl/sw_cartridge_wrapper.vhd mig_model.vhdl tb_c64_crt.vhdl
ghdl -e --std=08 -frelaxed --workdir=c64work tb_c64_crt
ghdl -r --std=08 -frelaxed --workdir=c64work tb_c64_crt --stop-time=80ms --ieee-asserts=disable 2>&1 \
  | sed 's/.*(report note): //;s/.*(report failure): /FAIL: /;s/.*(assertion failure): /FAIL: /' | grep -v readback
fi

# rk_bridge: configuration-flash pass-through (0x50), IPROG request (0x60), core id
ghdl -a --std=08 -frelaxed --workdir=c64work $V/rk_bridge.vhdl tb_rk_flash.vhdl
ghdl -e --std=08 -frelaxed --workdir=c64work tb_rk_flash
ghdl -r --std=08 -frelaxed --workdir=c64work tb_rk_flash --stop-time=5ms 2>&1 | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'

# K325T RRB list mode: rrb_list_expander, v1 (entries per row; buffer-full case at LIMIT 24) and
# v2 (global object table: copy, scan, counts, aborts, overflow, mfree stall), MLAT 3 and 4;
# v2 collisions (tb_rrb_collide)
ghdl -a --std=08 -frelaxed --workdir=k325work $V/rrb_list_expander.vhdl tb_rrb_list_expander.vhdl tb_rrb_list_v2.vhdl
ghdl -e --std=08 -frelaxed --workdir=k325work tb_rrb_list_expander
ghdl -e --std=08 -frelaxed --workdir=k325work tb_rrb_list_v2
for g in "-gMLAT=3" "-gMLAT=4" "-gMLAT=4 -gLIMIT=24"; do
  ghdl -r --std=08 -frelaxed --workdir=k325work tb_rrb_list_expander $g --ieee-asserts=disable 2>&1 \
    | grep -v "simulation stopped" | sed 's/.*(report note): //;s/.*(report failure): /FAIL: /;s/.*(assertion failure): /FAIL: /'
done
for m in 3 4; do
  ghdl -r --std=08 -frelaxed --workdir=k325work tb_rrb_list_v2 -gMLAT=$m --ieee-asserts=disable 2>&1 \
    | grep -v "simulation stopped" | sed 's/.*(report note): //;s/.*(report failure): /FAIL: /;s/.*(assertion failure): /FAIL: /'
done
# list mode v2 collisions: the per-frame pass over the snapshot against a model (designed and
# random boxes, groups, REL, negative Y, 255 objects, double buffering, held snapshot)
ghdl -a --std=08 -frelaxed --workdir=k325work tb_rrb_collide.vhdl
ghdl -e --std=08 -frelaxed --workdir=k325work tb_rrb_collide
for m in 3 4; do
  ghdl -r --std=08 -frelaxed --workdir=k325work tb_rrb_collide -gMLAT=$m --ieee-asserts=disable 2>&1 \
    | grep -v "simulation stopped" | grep "PASS\|FAIL\|failure\|error" | sed 's/.*(report note): //;s/.*(report failure): /FAIL: /;s/.*(assertion failure): /FAIL: /'
done

# soft_rtc: calendar, and the list mode v2 collision registers ($FFD71A0-C3: copy on the
# VIC-IV's toggle, partner / $FF, status latch, IRQ line, acknowledge, reset)
mkdir -p rtcwork
ghdl -a --std=08 -frelaxed --workdir=rtcwork $V/soft_rtc.vhdl tb_soft_rtc.vhdl
ghdl -e --std=08 -frelaxed --workdir=rtcwork tb_soft_rtc
ghdl -r --std=08 -frelaxed --workdir=rtcwork tb_soft_rtc 2>&1 | grep "PASS\|FAIL\|failure" | sed 's/.*(report note): //;s/.*(assertion failure): /FAIL: /'
