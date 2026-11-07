#!/usr/bin/env bash
# smoke_test.sh — unattended MEGA65 bring-up on the QMTech K325T (only J11 HDMI fitted;
# SD card, keyboard, joysticks and serial monitor are all terminated on the Rockchip).
#   scripts/smoke_test.sh [path.bit] [--no-power]
#  1. power via the Matter plug          5. RUN/STOP past MEGAFLASH (the QSPI holds non-MEGA65 slots)
#  2. JTAG-program the bitstream         6. wait for HYPPO to boot from the served SD image
#  3. (re)start m65d on the Rockchip     7. type a BASIC line, check a joystick via CIA1, capture HDMI
#  4. check the rk_bridge answers
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIT="${1:-$ROOT/mega65-core/bin/qmtech-k325t-cm4.bit}"
CTL="$ROOT/scripts/m65ctl.py"
fail(){ echo "SMOKE FAIL: $*"; exit 1; }

if [ "${2:-}" != "--no-power" ]; then
  PLUG="${PLUG:-$HOME/matter-plug/plug}"   # smart-plug client (optional; --no-power skips)
  "$PLUG" status | grep -q ON || { "$PLUG" on; sleep 90; }
fi
adb get-state >/dev/null 2>&1 || fail "Rockchip not reachable over adb"
"$ROOT/scripts/program.sh" "$BIT" | grep -q "PROGRAMMED=" || fail "JTAG programming"

adb push "$ROOT"/rockchip/{m65io.c,m65d.py,m65input.py,m65d_restart.sh,m65d_stop.sh,rk_pins.py} /root/m65/ >/dev/null
adb shell sh /root/m65/m65d_restart.sh | grep -q "bridge found" || fail "rk_bridge not answering on spidev3.0"

# MEGAFLASH: "slot messed up, press any key" + slot menu; RUN/STOP answers both and
# then HYPPO boots from SD.  Keep tapping until SD reads start (harmless afterwards).
sleep 3
for i in $(seq 1 40); do
  rd=$("$CTL" status | sed -n 's/.* rd=\([0-9]*\).*/\1/p')
  [ "${rd:-0}" -gt 500 ] && break
  [ "${rd:-0}" -eq 0 ] && [ $((i % 3)) -eq 1 ] && "$CTL" key RUNSTOP 150 >/dev/null
  sleep 1
done
echo "SD sectors served: $rd"
[ "${rd:-0}" -gt 500 ] || fail "HYPPO did not boot from the SD image"
sleep 4

# The CPU must be running, not parked in a known hang (e.g. the ROM's RTC-less clock
# calibration loop at $56E6-$56ED when CIA latches power up as $0001).
pcs=$(for i in 1 2 3; do "$CTL" mon r | grep -oE '^[0-9A-F]{4} ' ; sleep 0.3; done | tr -d ' ' | tr '\n' ' ')
echo "CPU PC samples: $pcs"
hung=1; for pc in $pcs; do [ $((16#$pc)) -lt $((16#56E6)) ] || [ $((16#$pc)) -gt $((16#56ED)) ] && hung=0; done
[ -n "$pcs" ] && [ $hung -eq 0 ] || fail "CPU stuck in the ROM clock-calibration loop (\$56E6)"

# A full card autoboots MEGA65.D81 into the intro menu: '/' = "exit to BASIC 65"
# (at a plain READY. prompt it is just a harmless syntax error).
"$CTL" key / 150 >/dev/null; sleep 4
"$CTL" type 'PRINT 6*7\n' >/dev/null; sleep 2
"$CTL" joy 0 16 >/dev/null; sleep 0.3                   # fire on port 2
pa=$("$CTL" mon 'mffd3c00' | grep -o ':0FFD3C00:..' | cut -c11-12)
"$CTL" joy 0 0 >/dev/null
echo "CIA1 \$DC00 with port-2 fire held: \$$pa (bit 4 must be 0)"
[ -n "$pa" ] && [ $(( 0x$pa & 0x10 )) -eq 0 ] || fail "joystick port 2 fire not seen"

"$ROOT/scripts/capture.sh" -n 20 | grep -E "saved=|file:"
echo "SMOKE PASS"
