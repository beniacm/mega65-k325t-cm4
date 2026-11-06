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
MEGA65 SD controller ─ sd_emu ─┐                                    ┌ m65io (C):  /root/m65/card.img (SD sectors)
MEGA65 UART monitor  ──────────┼─ rk_bridge ═ spi3-m1 (25 MHz) ═ RK ─┤             TCP 4510 serial-monitor pipe
widget kbd matrix / joysticks ─┤  (162 MHz)   CM4 BCM18-21           └ m65d.py:    TCP 4511 control (type/key/joy/
CPU reset ─────────────────────┘                                                   reset/status/temps)
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
| `01` | cmd, dummy, 16 status bytes | 'M' 'e' ver flags seq lba[4] rxcnt[2] txfree[2] sdcmds lastcmd ctrl (flags b0 rd_req b1 rd_ready b2 wr_req b3 inited b4 rx overflow b5 rd_error) |
| `10` | cmd, 512 bytes | data for the pending SD read |
| `11` | cmd, dummy, 512 bytes | captured SD write block |
| `12` | cmd | write consumed → release card busy |
| `13` | cmd | pending read is past the end of the card → data-error token `0x08` (out of range) |
| `20` | cmd, N bytes | → monitor RX (2.025 Mbaud internally) |
| `21` | cmd, dummy, N bytes | ← monitor TX FIFO (16 KiB) |
| `30` | cmd, ctrl, joyA, joyB, col0..8 [, pot1x, pot1y, pot2x, pot2y] | ctrl b0 reset, b1 RESTORE, b2 CAPS, b3 physical SD; optional POT bytes = SID values (paddles / 1351 mouse, `$FF` = open) |
| `50` | cmd, len_hi, len_lo, len bytes, 2 pads | **configuration flash pass-through** (x1 SPI, ~27 MHz, CS low for exactly len bytes); the answer to data byte k comes back in frame byte k+5 |
| `60` | cmd, addr[4] (big endian), `A5` | reboot the FPGA into the bitstream at flash byte address `addr` (C64 core: `rk_iprog`; the MEGA65 core reboots via `$D6C8..$D6CF` instead) |

STATUS byte 52 = core id (1 MEGA65, 2 C64; 0 on older MEGA65 bitstreams), byte 53 bit 0 = a
flash pass-through byte arrived while the previous one was still shifting (overrun).

**Speed:** the bridge's SPI front end runs at 162 MHz (4 × cpuclock, same MMCM) and the link at
**25 MHz**. `rockchip/linktest.py`: 0 errors at 10/16.7/25 MHz, failures from 33 MHz up (MISO setup).
The hot loop is C (`m65io`): status poll → pread/pwrite → SD_PUT/GET. It spins while sectors flow and
sleeps 200 µs per poll when idle. MEGA65 FDISK "SD card read speed": 335 KB/s (v5, 4 MHz, Python)
→ 517 KB/s (25 MHz) → **951 KB/s** (25 MHz + spin-while-active). No FPGA-side caching.

`sd_emu` is an SPI-mode SDHC card in the controller's own clock domain (CMD0/8/55/41/58/17/24/25,
FE/FC/FD tokens). Reads stall with 0xFF until the Rockchip delivers the sector; HYPPO's
0.4 s timeout is ample. Simulation: `sim/run_sim.sh` runs the real MEGA65 `SdCardCtrl`
against `sd_emu` + `rk_bridge` + a model Rockchip (`sim/tb_rk_sd.vhdl`) → `TB_RK_SD_PASS`.
It generates `sdcard_simcopy.vhdl` from upstream, only widening a counter range that underflows
harmlessly in hardware but stops GHDL.

## Keyboard, gamepads

**USB keyboard and USB gamepads** plugged into the carrier's USB ports (the CM4 module's USB) are
forwarded by `m65d.py` + `rockchip/m65input.py`. It reads evdev directly (no python-evdev), grabs the
devices exclusively, hot-plugs them (rescan every 2 s), and ORs them with scripted input from the
control port.

| PC key | MEGA65 | | PC key | MEGA65 |
|---|---|---|---|---|
| Esc | RUN/STOP | | PgUp | RESTORE |
| Backspace, Del | INST/DEL | | Insert | INST (SHIFT+DEL) |
| Home | CLR/HOME | | End | ↑ (up-arrow) |
| PgDn | £ | | PrtSc | HELP |
| CapsLock | CAPS LOCK (latching) | | ScrollLock | NO SCROLL |
| Ctrl / Alt / Win | CTRL / ALT / MEGA | | Tab | TAB |
| F1/3/5/7/9/11 | same | | F2/4/6/8/10/12 | SHIFT + odd F-key |
| cursor keys | cursor (up/left = SHIFT+down/right) | | keypad | digits, + - * / . RETURN |
| `` ` `` - = [ ] ; ' \ | ← + - @ * : ; = | | letters, digits, , . / | positional |

Gamepads: **first pad → joystick port 2** (most C64 games), second → port 1. D-pad, hat or left stick
(30 % dead zone) give directions; A/B/X/Y, shoulders and triggers fire. Start = SPACE,
Select = RETURN, Mode/Home = RUN/STOP. The optional DS2x2 Pmod on J13 and the `joy` control
command are OR'd in as well.

Test without hardware: `rockchip/uinput_test.py` creates a virtual keyboard or gamepad via
`/dev/uinput` (run it with a host-side `adb shell` in the background; processes backgrounded
inside an adb command die with the session). Verified: `print 6*7` typed on the virtual
keyboard → ` 42`; pad fire → `$DC00=$6F`, D-pad up → `$7E`, released → `$7F`.

Scripted input (control port): `type`, `typecase`, `key`, `keys`, `joy`, `restore`, `capslock`.

## Scripts

```
scripts/build.sh        # make bin/qmtech-k325t-cm4.bit (handles pkg-config / exomizer / Vivado quirks)
scripts/program.sh      # JTAG → SRAM via ~/qmtech/scripts/03_program.sh
scripts/capture.sh      # MS2130 HDMI capture → captures/
scripts/smoke_test.sh   # plug → program → m65d → RUN/STOP → SD boot → type → joystick → capture
scripts/make_sdimage.sh # sparse 2 GiB MBR+FAT32 image with the core's system files (+ sdimage/extra/*)
scripts/m65ctl.py       # control client: status | type <text> | typecase <Text> | key <K> | joy a b |
                        #   reset | restore 0/1 | mon <cmd>   (typecase = capitals with SHIFT, for ASCII apps)
scripts/sd_populate.sh  # copy system files + ROM + extra dirs into an existing (FDISK-made) card image
rockchip/m65d.py        # Rockchip daemon (root): SD server, monitor pipe :4510, control :4511
rockchip/m65d_restart.sh, rockchip/m65d_stop.sh, rockchip/rk_pins.py
rockchip/m65input.py    # USB keyboard/gamepad/1351-mouse bridge (imported by m65d.py); uinput_test.py = virtual devices
rockchip/m65flash.py    # configuration flash: id | backup | write <slot> <bit> | verify | boot <slot>
rockchip/m65d.service   # systemd unit (enabled): m65io + m65d at boot, then m65_bootreset.py
scripts/c64_run.sh      # C64 core: load, mount k325t.d64, LOAD+RUN a program, capture
scripts/mkd64.py        # BASIC V2 text / PRG files -> D64;  scripts/crt_banksum.py: expected CRT bank sums
# m65d ignores any status without the bridge magic: while the FPGA is reprogrammed, MISO floats
# and every field reads 0xFF (an old daemon wrote a garbage sector at LBA 0xFFFFFFFF that way).
```

Host prerequisites already installed: `brew install exomizer`; libpng is found via
`PKG_CONFIG_PATH` (linuxbrew's pkg-config shadows the system one).

## SD card and ROM

**Card = image file on the Rockchip** (`/root/m65/card.img`, the default for `m65d`, sparse).
Card capacity = file size: reads past the end get an SD "out of range" error token, so FDISK
and HYPPO see the real size.

**Factory-style card, as used on the board (4 GiB):**
1. `truncate -s 4G card.img` on the Rockchip, then boot: HYPPO finds no partitions and shows the
   utility menu. Press `2` (SDCARD FDISK+FORMAT), select card `0`, then
   `m65ctl.py typecase 'DELETE EVERYTHING\n'`. FDISK writes MBR + FAT32 (2 GiB at `$800`) + MEGA65
   System partition (type `41`, 1022 freeze/service slots) in about 10k sector writes.
   A copy of that blank card is kept as `sdimage/card_fdisk_blank.img`.
2. Pull the image back (`tar -cS` on the Rockchip + `adb pull`) and run
   `scripts/sd_populate.sh card.img ext/filehost/intros/sdcard-files`. That copies the 13 system
   `.M65` files built from this tree, `MEGA65.ROM`, and the public "ALL INTRO DISKS" collection
   (Filehost, 701 MB, MEGA65.D81 menu + intro disks 1-4 + about 100 programs).
3. Push it back, restart `m65d`, reload the FPGA ("power-cycle"). First boot runs ONBOARD
   (time, video mode, CRT emulation). Choose **Enhanced (with sound), PAL 50Hz** to match the
   core's fixed 576p50 HDMI output, then RETURN to save to the system partition.

Verified on hardware: the settings persist across a power-cycle; the card autoboots the MEGA65.D81
menu; Intro Disk #1 mounts; MAND65 renders; the Freezer (hold RESTORE) freezes to a slot
(about 900 sector writes), shows thumbnail/ROM/drive info, and F3 resumes the program intact.

`scripts/make_sdimage.sh` (mtools-formatted, no system partition) is kept for quick tests only.

**MEGA65.ROM (BASIC 65) from public sources**: `scripts/make_rom.sh <910828.bin> <patch.zip>`
- Base: original C65 ROM **910828** (SHA-1 `b63d9707…`). It's in Cloanto's free C64 Forever 11 Express
  MSI (`Data1.cab` → `file_Shared_54` = `c-65-19910828.rom`) and is byte-identical to zimmers.net
  `firmware/computers/c65/910828.bin`. **911001 does not work as a base.**
- Patch: Filehost "C65/MEGA65 Kernal ROM diff files - Stable", currently **920413**
  (`https://files.mega65.org/files/other/920413_Sn7YEw.zip`, public). The search API is
  `POST /php/readpagesearch.php search=…`, and details come from `POST /php/readfiledetail.php fileid=…`.
- The zip carries the same patch twice (`.rdf` romdiff and `.bdf` bsdiff). `make_rom.sh` applies both
  (`scripts/rdfpatch.py`, `scripts/bspatch.py`) and only accepts the ROM if they agree byte-for-byte.
  Result: `MEGA65.ROM` V920413, SHA-1 `4214ba46…`.
- ROM images are never committed (`.gitignore`).

This board has no I²C RTC, so the ROM reports `NO-RTC` and calibrates its clock with the
CIA2 32-bit timer. With the core's old CIA power-on latch values (`$0001`) that loop never
ended. `cia6526.vhdl` now powers up with `$FFFF`, as the MOS 6526 does.

## 8 MB attic RAM (DDR3)

The board's 256 MB DDR3 (MT41K128M16, bank 34) provides the MEGA65's **8 MB attic RAM**
(`$8000000-$87FFFFF`, mirrored above, like the HyperRAM of a real MEGA65).
- `ddr3_attic.vhdl` implements the `slow_devices` expansion-RAM byte interface (the one the
  hyperram/sdram controllers provide on real boards). Its request side runs on **pixelclock**
  (81 MHz, slow_devices' clock). Each byte access is one masked 16-byte MIG write or one
  16-byte read; the clock crossing uses toggle handshakes. `busy` stays high until DDR3
  calibration completes.
- MIG 7-series from `src/vhdl/qmtech-k325t-cm4_mig_ddr3.prj`: the board's proven DDR3-800 setup
  (from the ~/qmtech TM project) with the MIG's own XADC disabled (`fpgatemp` owns the only
  XADC and feeds `device_temp_i`) and its debug ports off. clock200 comes from the core's
  (otherwise unused) eth MMCM. The project script generates the IP.
- Synthesis for this target is **not timing driven** (`-no_timing_driven` in the impl tcl):
  with the MIG and the live expansion-RAM path, synth_design's Timing Optimization phase never
  finished (twice, >20 min). Placement and routing stay timing driven; timing is met.
- Verified on hardware: HYPPO detects it (its probe pattern is at `$8000000` after boot).
  `scripts/attic_test.py` (monitor spot tests, boundaries, mirror) passes, and the BASIC 65
  memtest typed in by `scripts/attic_memtest_basic.sh` (DMA-fill/verify all 128 × 64 KB blocks,
  23 address lines, 8 MB mirror, 4 patterns at 500 random addresses) prints **ATTIC RAM OK**.
- **CPU fast path** (as R6's SDRAM controller): every read publishes its 8-byte line to the CPU
  (`slowram_cache_line*`). The CPU's inc/dec toggles trigger silent next/previous-line reads, and
  writes update the published line in place (matched on the physical address, so mirror aliases
  stay coherent). `$D7FE.2` = cache (default on), `.3` = advance prefetch.
  `scripts/attic_bench.py` (8 MB sequential `LDA [$FB],Z` at 40 MHz in C64 mode, A/B on one bitstream):
  cache off 1171 KB/s → **cache on 2084 KB/s (1.8×)** → cache + advance 2395 KB/s (2.05×).
  (C64 mode runs the CPU at 1 MHz until `POKE 0,65`; the benchmark sets it.)
- Simulation: `sim/tb_ddr3_attic.vhdl` (MIG native-UI model with independent
  `app_rdy`/`app_wdf_rdy`, 81 MHz request side): worst read 26 cycles against slow_devices'
  128-cycle limit.

## Ethernet (MEGA65 MAC → LAN through the Rockchip)

```
MEGA65 MAC ─RMII─ eth_vphy ═ rk_bridge ═ SPI ═ m65io ─AF_PACKET─ eth0 ─ LAN (own MAC / DHCP)
(ethernet.vhdl)  (FPGA, 8×2 KB slots/dir)    (C, L2 bridge)
```
- The core's Ethernet MAC was excluded for `qmtechk325t` in `iomapper.vhdl`; it is enabled now.
  `machine` gets `clock50` (RMII) and `clock200` (MAC phase logic) from the eth MMCM, both
  through explicit `BUFG`s (the MIG's IDELAYCTRL needs a buffered clock200, DRC PLIDC-9).
  `{clock50, clock200}` are asynchronous to the core clocks, as in the R6 constraints.
- `eth_vphy.vhdl` is a virtual RMII PHY: it captures MAC TX frames (preamble/SFD stripped, FCS
  kept) into 2 KB slots and sends Rockchip frames to the MAC with preamble/SFD/96-bit gap.
  Bridge v3 commands: `0x40 ETH_TXGET`, `0x41 ETH_RXPUT`, STATUS bytes 16-19 (next frame
  length, free RX slots, drops). The bridge byte index grew to 12 bits for 1518-byte frames.
- The module's vendor kernel has **no TUN/TAP, bridge or NAT**, so `m65io` bridges at layer 2 over an
  AF_PACKET socket on `eth0` (promiscuous; GRO switched off via SIOCETHTOOL). MEGA65 frames go
  out unchanged (FCS checked and stripped). Frames for the MEGA65's MAC (learned from its
  traffic) and broadcasts/multicasts come in, with the FCS appended. Limitation: the MEGA65 and
  the module itself can't talk directly. The x86 host, the LAN and the internet can.
- MEGA65 side: `$D6E0` 0→3 starts the MAC, `$D6E5=$10` accepts broadcasts, MAC at `$D6E9-$D6EE`.
  TX: frame at `$FFDE800`, length `$D6E2/3`, `$D6E4=1`. RX: `$D6E1.5` = frame waiting, a
  **0→1 edge** on `$D6E1.1` releases/advances the buffer (the MAC blocks RX while all 4 are full).
- Verified on hardware: `scripts/eth_test.py` → **ETH PASS**. A UDP broadcast built by the MEGA65
  reaches the x86 host over the LAN, and a host broadcast lands in the MEGA65 RX buffer.
  Simulation: `sim/tb_eth.vhdl` (RMII MAC model, 60/100/1514-byte frames both ways).

## C64 core (C64MEGA65 V5.2 port)

`c64mega65/` = github.com/MJoergen/C64MEGA65 (tag V5.2-release), local branch
**`qmtech-k325t-cm4`** (separate repo, not tracked here). Build: `c64mega65/K325T/build.sh`
(~8 min) → `K325T/bin/c64_k325t_cm4.bit`. The QNICE firmware needs the assembler built once
(`M2M/QNICE/assembler/qasm*`) and `dist_kit/sysdef.asm` (from `M2M/QNICE/monitor`).

| piece | role |
|---|---|
| `K325T/avm_mig.vhd` | M2M Avalon bus (HyperRAM interface) → MIG DDR3 native UI |
| `K325T/m65kbd_emu.vhd` | MEGA65 keyboard serial link, answered from the bridge's matrix |
| `K325T/gen_top.py` + `top_qmtech_k325t_cm4.tpl.vhd` | top generated from the R6 top: clocks, MIG, bridge, SD emulator, flash, IPROG |
| M2M `framework.vhd` | generics `G_EXT_HR` (external memory), `G_TMDS_INVERT` (J11), `G_NO_I2C` |

Works on hardware: BASIC, keyboard, menu (HELP), SD/D64 load + save, REU, EasyFlash cartridges
(Eye of the Beholder, Metal Warrior Trilogy), disk games (Metal Warrior), joysticks and the
**1351 mouse** (USB mouse → evdev → SID POT values, port 2; `m65ctl mouse dx dy [btn]`).
Files live in `/c64` on the card (the MEGA65 card's FAT32 partition serves both cores).

DDR3 bridge bugs found on the way (both in `avm_mig.vhd`, simulated with `sim/tb_c64_reu.vhdl`):
- **MIG write order** (UG586): write data may come at most 2 cycles after its command. Under
  `app_wdf_rdy` back-pressure it came later. Data now goes first. `ddr3_attic` had the same bug.
- **No new command in the cycle of a read's last beat**: M2M's `avm_arbit` miscounts and read
  beats reach the wrong master. The HDMI scaler then waited forever (frozen picture while the
  C64 ran on), and the CRT parser read garbage ("Wrong CRT/CHIP header"). Afterwards 20/20
  cartridge loads OK.

## Standalone: both cores in flash, switched from the Rockchip

No PC or JTAG is needed in normal use:

1. Power on → the FPGA loads the **MEGA65 from flash slot 0**.
2. The Rockchip boots and `m65d.service` starts m65io + m65d, then resets the core once
   (`m65_bootreset.py`): the FPGA boots in ~1 s, long before the card is served, and HYPPO
   would otherwise stop with "SD CARD IS NOT SDHC".
3. The MEGA65 comes up in its menu system.

| USB keyboard (not passed to the core) | `m65ctl` | |
|---|---|---|
| Ctrl+Alt+F12 | `core toggle` | MEGA65 ⇄ C64 |
| Ctrl+Alt+F1 / F2 | `core mega65` / `core c64` | |
| Ctrl+Alt+Del | `reset` | reset the running core |

Flash (S25FL128L, 16 MB), written from the Rockchip with `rockchip/m65flash.py` while either core
runs (16 MB read 15 s, a 6 MB core about 1 min including verify):

| slot | address | content |
|---|---|---|
| 0 | `0x000000` | MEGA65 core (boots at power-on) |
| 1 | `0x800000` | 4 KB `$FF`, then the C64 core |

- The 4 KB `$FF` in front make HYPPO's flash check see slot 1 as *empty*. A valid `.cor` there
  would be auto-started at power-on; anything else gives "slot 1 messed up". The FPGA skips the
  `$FF` bytes before the sync word.
- **WBSTAR for SPI is the byte address >> 8.** An unshifted address left the FPGA unconfigured
  (DONE low, no fallback). Both bitstreams set `CONFIGFALLBACK`.
- Upstream megaflash bug fixed (`mf_utility.c`): `$D629=$62` was split into "minor 6, model 2"
  = MEGA65 R2 with 4 MB slots. Ids ≥ `$20` are now whole bytes, so this board gets 8 MB slots.
- The original flash content is backed up in `flashbackup/flash_backup_20261106.bin` (git-ignored).
- Core switching: MEGA65 → C64 through the serial monitor (`$D6C8..$D6CB` = address >> 8,
  `$D6CF`←`$42`); C64 → MEGA65 through bridge command `60` (`rk_iprog`, own ICAPE2).

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
- **2026-11-06 standalone**: MEGA65 (slot 0) and C64 (slot 1) in flash. A cold power cycle via
  the smart plug boots the MEGA65 menu system without the PC; Ctrl+Alt+F12 switches to the C64
  and back; Ctrl+Alt+Del resets. MEGA65 build WNS +0.988 ns, C64 build WNS +0.187 ns.
