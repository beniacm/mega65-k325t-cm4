# MEGA65 on the QMTech XC7K325T development board (CM4 carrier)

Upstream: `mega65-core/` = github.com/MEGA65/mega65-core (`development`), local
branch **`qmtech-k325t-cm4`** adds a new target for this board (separate repo, not tracked here;
this repo holds the harness: scripts, Rockchip daemon, simulation).

> The upstream `qmtech-k325t` target is for a *different* QMTech K325T carrier
> (VGA + other Pmods). Same FPGA, clock (F22), LEDs and QSPI pins, but about 25 of its
> outputs (VGA, `jc` Pmod, PWM audio) land on this board's **CM4 GPIO bus**:
> loading it would fight the Rockchip's pin drivers. Don't use it here.

## What the new target adds

| file | role |
|---|---|
| `src/vhdl/qmtech-k325t-cm4.vhdl` | top level (from `wukong.vhdl`: HDMI + SD + QSPI + UART) |
| `src/vhdl/qmtech-k325t-cm4.xdc` | pins + clocks |
| `src/verilog/dualshock_controller.v`, `controller_ds2.v` | DS2 pad IP (from spectrumnext/snestang) |
| `vivado/qmtech-k325t-cm4_{gen,impl}.tcl` | Vivado project (part `xc7k325tffg676-1`) |
| `Makefile` | target added to `CURRENT_TARGETS` |

`machine` is built with `target => qmtechk325t` (model ID `$62`), 40.5 MHz CPU,
no attic/HyperRAM (`shadowram-no-attic`), OpenROMs embedded so it boots without a ROM.

## Wiring

Currently fitted: **J11 HDMI only**. SD card, keyboard, joysticks, reset and the serial
monitor are all **terminated on the Rockchip** (Luckfox Core3566 in the CM4 socket)
through one SPI link:

```
MEGA65 SD controller ─ sd_emu ─┐                                  ┌─ /root/m65/sd.img (sparse, FAT32)
MEGA65 UART monitor  ──────────┼─ rk_bridge ═ spi3-m1 (4 MHz) ═ m65d.py ─┼─ TCP 4510  serial monitor pipe
widget kbd matrix / joysticks ─┤   (FPGA)     CM4 BCM18-21       (RK)    └─ TCP 4511  control (type/key/joy/reset/status)
CPU reset ─────────────────────┘
```

| Connector | Use |
|---|---|
| **J11** (bank 15) | Sipeed DVI Pmod: TMDS 576p50 @ 27 MHz. Green + clock pairs inverted in RTL (board P/N swap) |
| **CM4 BCM18/19/20/21** = B15/B9/A9/A8 | `rk_bridge` SPI slave: CS/MISO/MOSI/CLK; the Rockchip's `spi3-m1` is master |
| J12 (bank 14) | optional physical SD Pmod (Digilent order), selected with `sd phys` |
| J13 (bank 14) | optional Sipeed DS2x2 Pmod: pad 1 → port 2, pad 2 → port 1 (OR'd with the Rockchip joysticks) |
| SW3 / SW2 | CPU reset / RESTORE |
| LED2/3/4 | heartbeat / SYSCTL `$D61A.4` / Rockchip SPI or SD request |

**No other CM4 line is used.** The Rockchip's boot DTB (from the ~/qmtech BT1120 work) muxes
GPIO3_A1-A7/B0-B6/C1-C3 to **BT1120 video output**, so those pads are driven by the Rockchip.
v2 had a raw UART on D13/D14, which caused contention; it was removed in v3.
`rockchip/rk_pins.py` (run by `m65d_restart.sh`) also switches GPIO3_A7/C2/C3 back to inputs.

### rk_bridge SPI protocol (mode 0, MSB first)

| cmd | frame | meaning |
|---|---|---|
| `01` | cmd, dummy, 16 status bytes | 'M' 'e' ver flags seq lba[4] rxcnt[2] txfree[2] sdcmds lastcmd ctrl |
| `10` | cmd, 512 bytes | data for the pending SD read |
| `11` | cmd, dummy, 512 bytes | captured SD write block |
| `12` | cmd | write consumed → release card busy |
| `20` | cmd, N bytes | → monitor RX (2.025 Mbaud internally) |
| `21` | cmd, dummy, N bytes | ← monitor TX FIFO (16 KiB) |
| `30` | cmd, ctrl, joyA, joyB, col0..8 | ctrl b0 reset, b1 RESTORE, b2 CAPS, b3 physical SD |

`sd_emu` is an SPI-mode SDHC card in the controller's own clock domain (CMD0/8/55/41/58/17/24/25,
FE/FC/FD tokens). Reads stall with 0xFF until the Rockchip delivers the sector; HYPPO's
0.4 s timeout is ample. Simulation: `sim/run_sim.sh` runs the real MEGA65 `SdCardCtrl`
against `sd_emu` + `rk_bridge` + a model Rockchip (`sim/tb_rk_sd.vhdl`) → `TB_RK_SD_PASS`.
It generates `sdcard_simcopy.vhdl` from upstream, only widening a counter range that underflows
harmlessly in hardware but stops GHDL.

## Keyboard options

Implemented: the **Rockchip types through the MEGA65 keyboard matrix** (widget interface).
For example, `scripts/m65ctl.py type 'LOAD "$",8\n'` or `key RUNSTOP`. Next step: an evdev reader
in `m65d.py`, so a USB keyboard and gamepads on the carrier's USB hub feed the same matrix
and joystick registers.
Alternatives: a real PS/2 keyboard or a MEGA65 MK-II keyboard on JP5, or USB host in the FPGA (see git history).

## Scripts

```
scripts/build.sh        # make bin/qmtech-k325t-cm4.bit (handles pkg-config / exomizer / Vivado quirks)
scripts/program.sh      # JTAG → SRAM via ~/qmtech/scripts/03_program.sh
scripts/capture.sh      # MS2130 HDMI capture → captures/
scripts/smoke_test.sh   # plug → program → m65d → RUN/STOP → SD boot → type → joystick → capture
scripts/make_sdimage.sh # sparse 2 GiB MBR+FAT32 image with the core's system files (+ sdimage/extra/*)
scripts/m65ctl.py       # control client: status | type <text> | key <K> | joy a b | reset | mon <cmd>
rockchip/m65d.py        # Rockchip daemon (root): SD server, monitor pipe :4510, control :4511
rockchip/m65d_restart.sh, rockchip/rk_pins.py
```

Host prerequisites already installed: `brew install exomizer`; libpng is found via
`PKG_CONFIG_PATH` (linuxbrew's pkg-config shadows the system one).

## SD card

FAT32 card in the J12 Pmod. Without a card, or without `MEGA65.ROM` on it, the core boots
the embedded OpenROMs. For the full experience, put the MEGA65 system files and your own
`MEGA65.ROM` on the card (or run the built-in FDISK utility from the HYPPO menu).

## Build status / hardware results

- **v1** (2026-11-05): HDMI-only test. **First-light OK**: MEGAFLASH screen, clean 720x576 capture via J11.
- **v2**: adds `rk_bridge` + `sd_emu` (Rockchip termination), raw UART on D13/D14 and PS/2 on D10/C9.
  Found on hardware: those CM4 pads are **BT1120 outputs on the Rockchip** → contention on D13
  and a jammed monitor RX. Fixed at runtime with `rk_pins.py`, removed from the design in v3.
- **v3** (current, `bin/qmtech-k325t-cm4.bit`): CM4 bus = 4 spi3 lines only. WNS +1.006 ns,
  about 22% LUTs, 30% BRAM. **`scripts/smoke_test.sh` → SMOKE PASS**, verified on hardware:
  - JTAG program → MEGAFLASH (QSPI holds non-MEGA65 slots; two RUN/STOPs get past it)
  - HYPPO boots from the Rockchip-served SD image (about 1000 sectors) → OpenROMs `READY.`
  - keyboard matrix typing correct (`PRINT "HELLO FROM THE ROCKCHIP!"`; OpenROMs answers
    `?NOT IMPLEMENTED ERROR` for `*`/`+`, which is an OpenROMs limitation)
  - joystick: CIA1 `$DC00` = `$6F` with port-2 fire held, `$7F` released
  - serial monitor over TCP 4510: `m800` dump, `sffd3020 2` turns the border red (HDMI-verified)
  - SD **write**: `$D680`←`$57` (write gate), `$D680`←`$03` → CMD24 → sector 100 of `sd.img` = `MEGA65!K325T`
- Bugs found and fixed on the way: widget column must be **combinational** (registered → every key one
  column off); CMD25 has 3 extra SCLK pulses between blocks (found in simulation); m65d crashed on a
  client reset.
- Upstream "timing loop" synth warnings (`neotrng` ring oscillators, `gs4510` divider) are expected.
