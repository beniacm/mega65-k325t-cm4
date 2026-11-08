# MEGA65 (+ C64) on the QMTech XC7K325T development board (CM4 carrier)

The [MEGA65](https://mega65.org) core and the [C64MEGA65](https://github.com/MJoergen/C64MEGA65)
core running on a **QMTech XC7K325T "CM4" carrier** with a **Luckfox Core3566** (Rockchip)
module in its CM4 socket.  The Rockchip replaces the MEGA65's missing peripherals: it serves
the SD card, the keyboard (USB), gamepads, the mouse, the serial monitor, Ethernet and the
real-time clock over one SPI link, writes the FPGA's configuration flash and switches cores.

Not an official MEGA65 product and not affiliated with the MEGA e.V. or the C64MEGA65 authors.

**Highlights**
- Both cores standalone from flash: MEGA65 (slot 0) and C64 (slot 1), switched with
  Ctrl+Alt+F12 on a USB keyboard; USB gamepads as joysticks, USB mouse as 1351.
- The board's DDR3 as **64 MB attic RAM** (8 MB + mirrors after reset, like a real MEGA65;
  `POKE $BFFFFFC,64` unlocks it) and as a **16 MB REU** for the C64 core.
- **K325T extensions**, off after every reset (so the machine is a real MEGA65 until a
  program asks): **1 MB chip RAM**, a **raster DMA list** (DMAgic jobs that wait for a raster
  line: split screens / colour bars without CPU timing code) and **64 KB colour RAM** — see
  "K325T extensions" below.
- Battery-less **real-time clock** set from the Rockchip's NTP time; **safe power-off**
  (Ctrl+Alt+End, optionally cutting a smart plug afterwards); Ethernet through the Rockchip.
- Simulation testbenches (GHDL) for the bridge, the SD emulation, the DDR3 paths, the REU.

**Repositories** (github.com/beniacm):

| repo | content | branch |
|---|---|---|
| [`mega65-k325t-cm4`](https://github.com/beniacm/mega65-k325t-cm4) | this harness: build/test scripts, Rockchip daemons and tools, Rockchip boot image, simulation, docs | `main` |
| [`mega65-core-k325t-cm4`](https://github.com/beniacm/mega65-core-k325t-cm4) | fork of MEGA65/mega65-core with the `qmtech-k325t-cm4` target | `qmtech-k325t-cm4` |
| [`c64mega65-k325t-cm4`](https://github.com/beniacm/c64mega65-k325t-cm4) | fork of MJoergen/C64MEGA65 (V5.2) with the `K325T/` port | `qmtech-k325t-cm4` |

```
git clone https://github.com/beniacm/mega65-k325t-cm4.git mega65 && cd mega65
git clone -b qmtech-k325t-cm4 https://github.com/beniacm/mega65-core-k325t-cm4.git mega65-core
git clone -b qmtech-k325t-cm4 https://github.com/beniacm/c64mega65-k325t-cm4.git c64mega65
```
(`mega65-core/` and `c64mega65/` are separate repos, git-ignored here.)

**You need:** the QMTech XC7K325T CM4 carrier, a Luckfox Core3566, a Sipeed DVI Pmod on J11
(HDMI out), a Xilinx JTAG cable for the first bitstream, Vivado (2025.2 used; the free edition
covers the XC7K325T), and a Linux PC.  **Not included** (copyright): the MEGA65 ROM (build it
with `scripts/make_rom.sh` from a C65 ROM you own, see "SD card and ROM"), C64/1541 ROMs beyond
what the cores ship, and any games.  The MEGA65 core boots without a ROM (OpenROMs).

**Keeping up with upstream** (the forks never push upstream; their `upstream` remote is
fetch-only):
```
cd mega65-core && git fetch upstream && git merge upstream/development   # then build + test
cd c64mega65   && git fetch upstream && git merge <upstream tag>          # e.g. a V5.x release
```
Our changes to shared upstream files are small and gated by the target/generics, so merges
are mostly conflict-free; the K325T description lives in `.github/README.md` of each fork so
upstream's `README.md` is untouched.

Licence: this repository GPL-3.0 (`LICENSE`); mega65-core LGPL-3.0, C64MEGA65 GPL-3.0.
Credits: the MEGA65 team (mega65-core, OpenROMs), sy2002 and MJoergen (C64MEGA65, MiSTer2MEGA65),
the MiSTer C64 core authors, Luckfox and QMTech for the board files.

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

**BT1120 video from the Rockchip:** the boot DTB (`rockchip/boot_dtb/`, from the earlier BT1120 work) muxes
GPIO3_A1-A7/B0-B6/C1-C3 to **BT1120 video output** (DPI-1, 16 bit + clock). The MEGA65 bitstream receives it
on 17 FPGA inputs (`linux_fb`, see "One HDMI output: Linux and MEGA65"); no other CM4 line is used.
(v2 had a raw UART on D13/D14 against those outputs; until linux_fb a script switched three of the pads back
to GPIO, which cut data bits 5, 14 and 15 of the video. Both are gone.)

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

**The carrier's four USB-A ports only work with no PC cable in the mini-USB port.** The module's
one USB pair is switched by `USBOTG_ID` (FSUSB42 mux, schematic p.2): PC cable in → the Rockchip is
a USB device (adb) and the USB2514 hub gets no host, so it never switches port power on (keyboards
stay dark); cable out → host mode, hub, ports powered. Use SSH / `m65ctl` while the cable is out.

**USB keyboard and USB gamepads** plugged into the carrier's USB ports (the CM4 module's USB) are
forwarded by `m65d.py` + `rockchip/m65input.py`. It reads evdev directly (no python-evdev), grabs the
devices exclusively and ORs them with scripted input from the control port. Keyboards, mice and pads
can all be plugged at once (4 ports; more with a hub).

- **Hot-plug** via inotify on `/dev/input`: a new device is used ~30 ms after the kernel creates it
  (was: rescan every 2 s); the 2 s rescan stays as a fallback (it only re-parses
  `/proc/bus/input/devices` when that changed: parsing costs ~2 ms of input latency).
- Devices are classified by capability and **one node can have several roles** (`kbd+mouse` for
  keyboard/touchpad combos and dongles); events are dispatched by type.
- **State is re-read from the kernel** at plug-in (a key held while plugging works) and after an
  evdev buffer overrun (`SYN_DROPPED`), so no key or button stays stuck; keys are tracked per device
  (unplugging one keyboard does not release keys held on another).
- `status` lists each input with its role, its effective **USB polling interval**, and its
  joystick port, e.g. `kbd:Primax_HP_Wired_Desktop_320K_Keyboard@8ms`.

**Latency** (`m65d latency`: USB event kernel timestamp → INPUT register written over SPI,
`rockchip/input_bench.py`): p50 0.46 ms, p99 0.7 ms, max ~1–2 ms, unchanged under heavy SD traffic.
That needs the `performance` cpufreq governor (p50 0.86 ms with `ondemand`; no measurable
temperature difference, ~57 °C) and SCHED_FIFO (removes 5–10 ms spikes); `m65d.service` sets both.
The FPGA side adds < 0.3 ms (MEGA65: widget matrix; C64: one keyboard-emulator scan).
**The dominant term is the device's USB polling interval**: xHCI schedules a low/full-speed
interrupt endpoint at its descriptor interval rounded down to a power of two and ignores usbhid's
`kbpoll`/`mousepoll`/`jspoll` (verified in the xHCI endpoint context: the HP office keyboard stays at
8000 µs). So an office keyboard/mouse adds up to 8 ms, gaming keyboards/mice and most pads 1–4 ms —
pick devices accordingly. Then the program itself reads the keyboard/joystick once per frame.

**Gamepad support depends on the vendor kernel**: generic USB HID pads (DirectInput / "Android" /
"PC" modes, DS4 over USB) work; there is no `xpad` (Xbox 360/One pads, 8BitDo in X-input mode),
no `hid-sony` (DS3) and no `hid-nintendo` (Switch Pro) — such pads are not seen at all.

| PC key | MEGA65 | | PC key | MEGA65 |
|---|---|---|---|---|
| Esc | RUN/STOP | | PgUp | RESTORE |
| Backspace, Del | INST/DEL | | Insert | INST (SHIFT+DEL) |
| Home | CLR/HOME | | End | ↑ (up-arrow) |
| PgDn | £ | | PrtSc | HELP |
| CapsLock | CAPS LOCK (latching) | | ScrollLock | NO SCROLL |
| Ctrl / Alt / Win | CTRL / ALT / MEGA | | Tab | TAB |
| F1/3/5/7/9/11 | same | | F2/4/6/8/10/12 | SHIFT + odd F-key |
| cursor keys | cursor; up/left = the MEGA65's dedicated keys (INPUT ctrl b4/b5) | | keypad | digits, + - * / . RETURN |
| `` ` `` - = [ ] ; ' \ | ← + - @ * : ; = | | letters, digits, , . / | positional |

Gamepads: **first pad → joystick port 2** (most C64 games), second → port 1; a pad keeps its port
while connected (unplugging pad 1 does not move pad 2). **Ctrl+Alt+J swaps ports 1 ↔ 2** (pads and
mouse; also the `joyswap` command). D-pad, hat or left stick (30 % dead zone) give directions;
A/B/X/Y, shoulders and triggers fire. Start = SPACE, Select = RETURN, Mode/Home = RUN/STOP. Generic
twin-stick pads that report as a "joystick" (12 buttons BTN_TRIGGER..BTN_BASE6, e.g. DragonRise
0079:0006 "PC TWIN SHOCK") get their usual layout: button 9 = Select, 10 = Start; the other
buttons fire. The optional DS2x2 Pmod on J13 and the `joy` control command are OR'd in as well.

Tested with real devices (2026-11-06): HP 320K keyboard, HP 320M optical mouse (1351: movement,
left = fire, right = up), DragonRise twin-shock pad (D-pad as X/Y axes, 8 buttons; its D-pad itself
reports short diagonals when pressed off-centre, verified in the raw HID reports). All three poll at
8 ms. `m65ctl.py inputs` shows the live state: joystick bits per port, per-pad hat/stick/buttons,
POT values, keys down, devices with polling interval and port.

Mouse: Commodore 1351 (proportional, POT X/Y = position mod 64) on port 2, left = fire, right = up.
A 1351 driver reads the position once per frame and takes a change of more than 31 counts as the
other direction, which a modern mouse moved fast exceeded (pointer jumping back). The POT values
now move at most 1100 counts/s with a burst of 8 (≤ 30 per PAL frame); normal movement passes at
once, a fling is queued up to 40 counts and the rest dropped, like a real 1351 at its limit.

Tests: `rockchip/input_selftest.py` (on the Rockchip, virtual devices; works with real devices
plugged in) checks combo devices, `SYN_DROPPED` recovery, port stability + Ctrl+Alt+J, the 1351 rate
limit, keys held at plug-in and the twin-stick Select/Start layout. A device that fails to register
is logged and skipped; it cannot stop the others.

Test without hardware: `rockchip/uinput_test.py` creates a virtual keyboard or gamepad via
`/dev/uinput` (run it with a host-side `adb shell` in the background; processes backgrounded
inside an adb command die with the session). Verified: `print 6*7` typed on the virtual
keyboard → ` 42`; pad fire → `$DC00=$6F`, D-pad up → `$7E`, released → `$7F`.

Scripted input (control port): `type`, `typecase`, `key`, `keys`, `joy`, `restore`, `capslock`,
`mouse`, `pots`; `keylog` shows the last 20 USB key events and the resulting matrix.

Cursor up/left are sent like a real MEGA65 keyboard sends them: as the dedicated keys (MEGA65
core `keyup`/`keyleft`, C64 core keyboard keys 73/74) **and** as RIGHT SHIFT + DOWN/RIGHT in the
matrix. The hardware ASCII decoder (e.g. the Intro Disk menus) uses the former, games scanning
the CIA matrix (e.g. Roguecraft DX) the latter; either alone failed in one of them. Other PC keys mapped to a
modifier + key (Insert, F2…) get the modifier 40 ms first and a 100 ms minimum hold.

## One HDMI output: Linux and MEGA65

The Rockchip's own Linux display (console, or anything drawn to `/dev/fb0`) can replace the
MEGA65's picture on the same HDMI output, and the USB keyboard follows the picture:

| | |
|---|---|
| **Ctrl+Alt+Insert** | toggle Linux / MEGA65 (picture and keyboard) |
| `m65ctl view linux\|mega65\|toggle` | the same from a script (TCP 4511 on the Rockchip) |
| **F5 in vim** (`make run` → `m65run`) | build, load, start on the MEGA65, then switch to it |

Typical loop: edit in vim on the Linux picture, F5, the program runs on the MEGA65 picture with
the keyboard on the MEGA65, Ctrl+Alt+Insert back to vim.

**How.**  The Rockchip outputs its DPI-1 display as BT1120 (16 bit YCbCr 4:2:2 + clock,
720x576@50, 27 MHz) on the GPIO3 lines of the CM4 bus (boot DTB, `rockchip/boot_dtb/`).
`linux_fb.vhdl` receives it (`bt1120_rx.v`, `bt1120_video.v` from the board bring-up), keeps it
in its **own video buffer** (720x576 RGB332 = 405 KB of block RAM; MEGA65 memory is never
touched) and, when selected, puts it in place of the core's picture at the input of the HDMI
encoder.  The output timing is always the core's 576p50, so switching needs no resync; the core
keeps running underneath.  The input is cropped per line to 720x576, so other Rockchip modes
still show their top-left part.  The switch is rk_bridge command `0x70 VIDEO`; with the Linux
picture shown, m65input releases its keyboard/mouse grab (Linux gets them); the MEGA65 then
sees no key, no mouse, no RESTORE (Page Up) or CAPS LOCK -- gamepads stay on the MEGA65.  Only
Ctrl+Alt+Insert and Ctrl+Alt+End (power-off) remain hotkeys; the other Ctrl+Alt keys belong to
Linux (e.g. Ctrl+Alt+F2 switches the console to tty2, not the core).  Linux sees the hotkey too:
m65d voids Ctrl+Alt+Insert in the console keymap (`loadkeys`), and Ctrl+Alt+Del is masked
(`ctrl-alt-del.target`).  A core switch gives the keyboard back to the MEGA65 (the reload resets
the picture to the core's).  Tested with a virtual keyboard: `uinput_test.py keys 29,56,110`.

Diagnosis: `m65ctl view diag` (frames, lines, pixels per line, lock, the BT1120 clock, which data
lines toggle); `m65ctl view rise|fall` picks the capture edge (falling is the default and works).
All 17 BT1120 pads must stay in their video function on the Rockchip -- a pad switched to GPIO
behind pinctrl still shows "bt1120hd" in debugfs (read the GRF iomux registers).  Kernel messages
are kept off the console (`/etc/sysctl.d/90-m65-console.conf`, level 4).  `sim/tb_linux_fb.vhdl`
tests placement, cropping, the RGB332 expansion and the switch (GHDL, `sim/unisim_iddr_sim.vhdl`
models the IDDR).

## Cross-development on the Rockchip

The Rockchip is the development machine: its Linux console (or an ssh session) on the HDMI
output via the Linux picture (see "One HDMI output: Linux and MEGA65"), vim, assembler and C
compiler; **F5** in vim builds, loads and starts the program on the MEGA65 and switches picture
and keyboard to it, **Ctrl+Alt+Insert** comes back to vim.  (Until the linux_fb bitstream a
separate terminal emulator drew a shell into the MEGA65's own screen memory through the serial
monitor, Ctrl+Alt+T; it is gone, the Linux picture replaces it.)

`m65run prog.prg [--sys N | --run | --load | --reset]` (in `/usr/local/bin`): loads the PRG
through the monitor at its load address, types `SYS <address from the BASIC stub>` (or RUN) on
the MEGA65, which should be at the READY prompt, and switches to the MEGA65 picture.

**Toolchain:** cc65 V2.19 (ca65 assembler, ld65 linker, cc65 C compiler; `--cpu 45GS02`
verified, Q instructions encode correctly), built from the official github.com/cc65/cc65 on the
Rockchip by `rockchip/deploy/build_tools.sh` (Debian buster's package predates the 45GS02).

**C: llvm-mos** in `/opt/llvm-mos` (clang 24, SDK v23.4.0, `mos-mega65-clang`; on PATH via
`/etc/profile.d/llvm-mos.sh`). The SDK is only released for x86_64, so
`rockchip/deploy/build_llvm_mos.sh` (run on the PC) cross-builds clang/lld/llvm tools for aarch64
from the exact compiler commit of the official SDK, against a sysroot made of the board's own
libraries (+ Debian buster's libstdc++-8-dev headers), and swaps them into the official SDK (its MOS
runtime, headers and `mos-platform/mega65` libraries are host-independent). Needs ≤ GLIBC 2.27.
`m65new -c <name>` makes a C project (`<mega65.h>`: `VICIV.bordercol` etc.). llvm-mos programs do
not return to BASIC (the start code maps the BASIC ROM out; the CPU ends in `BRA *`):
`m65run` detects that end loop in the monitor's disassembly and then loads and starts the next
build directly (stop CPU, `L`, set PC to the stub's SYS address, run). A BASIC-style program after
a C program (kind of the last run in `/tmp/m65run.last`, or CPU parked) cannot use BASIC/KERNAL
any more, so `m65run` resets the MEGA65 core first and waits for `READY.` in screen RAM (leaving
the start menu with `/`); `--reset` forces that. Tested: C program, then `m65new` asm project →
reset → `HELLO MEGA65` + `READY.` Tested: hello world with
printf and `VICIV.bordercol` run twice in a row without a reset.

**Assembler IDE in vim** (`rockchip/ide/`, installed by `setup_rockchip.sh`):
- `m65new <name>`: project with `main.s` (BASIC 65 stub `10 SYS 8205`), `mega65.cfg`, `Makefile`
  (`make` → main.prg + listing + labels, `make run`, `make clean`).
- `*.s`/`*.asm`/`*.inc` open as filetype `ca65`: highlighting of all 45GS02 instructions (6502,
  65CE02/4510, 32-bit Q), ca65 directives, labels, numbers, MEGA65 I/O addresses, strings, comments.
- `m65new -c <name>`: the same for C (llvm-mos); F5/F7/F8/F9 also work in C files of such projects
  (clang's `file:line:col: error:` lands in the quickfix list).
- **F5** build + run on the MEGA65 (`make run` → `m65run`; Ctrl+Alt+Insert comes back to vim), **F7**
  build, **F8/F9** next/previous error (quickfix from ca65's `file:line: Error:` messages),
  **K** on an instruction: `m65doc` (45GS02 notes, `rockchip/ide/45gs02.txt`).
- **Symbolic names, no magic addresses** (`jsr CHROUT`, `inc VIC_BORDERCOLOR`, `tsb VIC4_CHR16`): the
  template includes cc65's `cbm_kernal.inc` (KERNAL entry points) and `mega65.inc` (zero page,
  C64-compatible VIC/SID/CIA) plus `mega65_regs.inc`: ~990 MEGA65 register names (VIC4_*, DMA_*,
  MATH_*, CPU_*, SD_*, ETH_*, ...) with `_MASK` constants for bit fields, generated by
  `rockchip/ide/gen_mega65_regs.py` from mega65-core's `iomap.txt` (the VHDL's `@IO`
  annotations, so the hardware's own names and descriptions). The Makefile passes both include
  directories (this ca65 has no built-in asminc path).

## Scripts

```
scripts/build.sh        # make bin/qmtech-k325t-cm4.bit (handles pkg-config / exomizer / Vivado quirks)
scripts/program.sh      # JTAG → SRAM (scripts/jtag/: Vivado batch tcl, udev setup)
scripts/jtag/run_vivado.sh sysmon.tcl   # XADC over JTAG: die temperature, VCCINT/VCCAUX/VCCBRAM, DONE (read-only)
scripts/jtag/run_vivado.sh boot_from_flash.tcl   # reconfigure from flash slot 0 (after a JTAG-loaded design without the bridge)
scripts/capture.sh      # MS2130 HDMI capture → captures/
scripts/m65shot.py      # screenshot WITHOUT the dongle: VIC-IV registers + screen/colour RAM + font +
                        #   palette read over the serial monitor (~0.4 s), text modes rendered as the
                        #   720x576 HDMI picture (x 40..760 / y 17..593) → captures/shot_*.png; --text.
                        #   Not rendered: bitmap modes, sprites, full-colour characters.
scripts/smoke_test.sh   # plug → program → m65d → RUN/STOP → SD boot → type → joystick → capture
scripts/make_sdimage.sh # sparse 2 GiB MBR+FAT32 image with the core's system files (+ sdimage/extra/*)
scripts/m65ctl.py       # control client: status | type <text> | typecase <Text> | key <K> | joy a b |
                        #   reset | restore 0/1 | mon <cmd>   (typecase = capitals with SHIFT, for ASCII apps)
scripts/sd_populate.sh  # copy system files + ROM + extra dirs into an existing (FDISK-made) card image
rockchip/m65d.py        # Rockchip daemon (root): SD server, monitor pipe :4510, control :4511
rockchip/m65d_restart.sh, rockchip/m65d_stop.sh
rockchip/m65input.py    # USB keyboard/gamepad/1351-mouse bridge (imported by m65d.py); uinput_test.py = virtual devices
rockchip/m65flash.py    # configuration flash: id | backup | write <slot> <bit> | verify | boot <slot>
rockchip/m65d.service   # systemd (enabled): m65d, SCHED_FIFO, performance governor, Restart=always;
rockchip/m65io.service  #   pulls in m65io (Restart=always; m65d_stop/m65d_restart.sh handle both)
rockchip/m65-bootreset.service  # once per boot: reset the core when the card is served (never on restarts)
rockchip/input_bench.py, rockchip/input_selftest.py   # input latency / functional tests (virtual devices)
rockchip/deploy/        # setup_rockchip.sh check|install + the board's /boot/config.txt
scripts/c64_run.sh      # C64 core: load, mount k325t.d64, LOAD+RUN a program, capture
scripts/mkd64.py        # BASIC V2 text / PRG files -> D64;  scripts/crt_banksum.py: expected CRT bank sums
scripts/b65_run.sh      # B65 BASIC: build/load/start it; `demo FILE.bas` also types a program
scripts/bas65_run.sh    # BASIC 65 program from a text file: tok65 (b65-basic repo) -> PRG -> m65run, seconds
scripts/rrb_list_test.py, scripts/rrb_diag.py   # RRB list mode: pixel-exact board test, hardware counters
scripts/demos/          # BASIC 65 / B65 demos (type them in; see docs/rrb_list_mode.md)
# m65d ignores any status without the bridge magic: while the FPGA is reprogrammed, MISO floats
# and every field reads 0xFF (an old daemon wrote a garbage sector at LBA 0xFFFFFFFF that way).
```

Host prerequisites: exomizer (`brew install exomizer` or your distro's package), libpng
development files, GHDL for the testbenches; `scripts/build.sh` sets `PKG_CONFIG_PATH` for the
system libpng (linuxbrew's pkg-config shadows it) and `VIVADODIR` (default
`~/Xilinx/2025.2.1/Vivado`).  Addresses: the scripts default to the Rockchip at `192.168.1.143`
(`M65_HOST` overrides it).

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

## SD read cache in the bridge (v4) and 40 MHz SPI

Streaming demos (Hyperballad: "needs an SD card with a read speed of 10 MB/s or more"; MEGApple:
"audio-playback issues with a slow card") stuttered: every sector was one Rockchip round trip.
Measured per sector ~585 us = ~300 us on our side (notice the request by polling + transfer) +
~300 us on the MEGA65 side (its SdCardCtrl runs the SD bus at cpuclock / 2 = 20 MHz). 40 MHz SPI
alone gained nothing (854 KB/s); disabling the SPI controller's runtime PM gave +34 % SPI
transactions/s (+5 % SD rate). A fast real card helps a real MEGA65 by its short access time per
CMD17 -- the cache gives us the same.

- **`rk_bridge` v4**: 16 prefetch slots + the SD_PUT slot in one 16 KB block RAM, a 32-bit LBA tag
  + valid bit per slot. A pending read is compared with all 16 tags; on a hit the emulator is
  switched to that slot and `rd_ready` rises < 1 us later, no Rockchip involved. `0x14 SD_PREF`
  (slot, count 1..7, LBA, data; spidev moves <= 4096 bytes per transaction), `0x15 SD_INVAL`.
  The slot being read is never overwritten; slots tagged in the last two cycles are not used yet
  (the tag compare is registered); writes stay synchronous and invalidate a slot holding that LBA
  (a sector the MEGA65 is writing is not validated by a prefetch). STATUS 54-55 valid bitmap,
  56-57 hit counter, 58 active slot. `sd_emu` is unchanged.
- **`m65io`**: SPI at 40 MHz (linktest clean up to 40, fails at 50; `scripts/sdlinktest.py`:
  0 mismatches); up to 4 interleaved sequential streams (a stream starts with two consecutive
  sectors; 1 stream: all 16 slots, 2: 8 each, 3-4: 4 each; window = part - 2); misses always
  first; `SD_INVAL` at start; `pf`, `pf_hits`, `pf_valid` in m65io.stats. `m65io.service` keeps
  the SPI controller's clock on (runtime PM off).
- Simulation `sim/tb_rk_sd.vhdl` (real MEGA65 SdCardCtrl + sd_emu + bridge + Rockchip model):
  hits while the prefetch transaction is still running, skip protection of the slot being read,
  write invalidation, SD_INVAL, hit counter -- TB_RK_SD_PASS.
- Hardware (`scripts/sdlinktest.py`: the MEGA65's SD controller reads sectors, compared with
  card.img): 200 random + 2 x 300 consecutive sectors, 0 mismatches, 298/300 hits.

| | before | v4 cache |
|---|---|---|
| Hyperballad: sectors to the MEGA65 | ~1,700/s | **3,745/s** (1.87 MB/s), ~100 % hits |
| Hyperballad: new data the demo reads | ~690 KB/s | **917 KB/s**, steady |
| MEGApple (video + music file in turns), 1 stream / 4 streams | 90 % hits | **100 %** hits |

Audio recorded through the capture dongle: no gaps or repeated buffers in the music. The C64 core
uses the same bridge and needs its own build to get the cache.

## Real-time clock and safe power-off

The board has no RTC chip and no battery.  **`soft_rtc.vhdl`** (MEGA65 core, `qmtechk325t` only)
puts the MEGA65 RTC registers at `$FFD7110-$FFD7116` (seconds, minutes, hours with bit 7 = 24 h,
day, month, year 20yy, day of week; BCD, the R3..R6 layout) and counts by itself from the CPU
clock with month lengths and leap years; until set it reads day 0.  **m65d** sets it from the
Rockchip's NTP-synchronised **local** time (time zone `Europe/Bratislava`, set by
`setup_rockchip.sh`, `M65_TZ=` to change) at a full second, with one monitor command that starts
with the seconds (a seconds write restarts the 1 Hz divider): when the MEGA65 core appears, when
m65io saw the bridge go away (the FPGA was reconfigured), hourly, and on `m65ctl rtc`.
`sim/tb_soft_rtc.vhdl`: leap day, month ends, 2099 → 2000 -- TB_SOFT_RTC_PASS.

**ROM patch**: BASIC 65 reads the RTC (banner, `DT$`, `TI$`) only on board ids below `$0A` and
prints `NO-RTC-2000` on ours (`$D629 = $62`).  `scripts/rom_rtcpatch.py` raises both checks
(`LDA $D629 / CMP #$0A / BCS`) to `#$63`; `make_rom.sh` applies it.  Result: `BASIC 65 V920413
07-NOV-2026 00:00:01`, `DT$` / `TI$` equal to the Rockchip's clock.  Changing the board id instead
would make HYPPO and the tools treat the board as an R3.

**Power-off** (the Rockchip's root file system is on eMMC): **Ctrl+Alt+End** on the USB
keyboard (or `m65ctl poweroff`) → m65d asks **plugd** on the PC with the smart plug to cut the
power → red screen "SHUTTING DOWN - POWER OFF FOLLOWS" → `systemctl poweroff` →
`/lib/systemd/system-shutdown/m65-safe.shutdown` runs once every file system is unmounted or
read-only: it writes the prepared monitor commands over SPI (green "ROCKCHIP HALTED - POWER OFF
NOW") and sends plugd UDP "halted" → plugd cuts the plug 2 s later.  Measured: halted (green)
3 s after the key, power off after 6 s.  m65d builds both messages before sending the red one
(reading the VIC registers right after the red commands collided with their replies: empty
green message), and the green one starts with two CRs (otherwise its first command, the
border, merged with a half line left in the monitor).
- `scripts/plugd.py` (port 4530, accepts only the Rockchip's address): cuts after "halted", or,
  if that never comes, once the Rockchip's kernel has not answered a TCP connect at all for 15 s
  (a reset still counts as alive, so this means halted, not just "sshd stopped"); if the
  Rockchip still answers after 5 min it does NOT cut.  Install once on the PC (user service):
  `ln -sf ~/projects/mega65/scripts/plugd.service ~/.config/systemd/user/ && systemctl --user
  daemon-reload && systemctl --user enable --now plugd`.
- m65d `--plug-host` (default `$M65_PLUG_HOST`, set in the untracked `/root/m65/m65d.env`, e.g.
  `M65_PLUG_HOST=192.168.1.133:4530`; empty = none).  Without
  plugd the screens read "WAIT FOR: SAFE TO SWITCH OFF" / "SAFE TO SWITCH OFF" and you switch
  off by hand.
- The FPGA keeps running until the plug goes; the MEGA65 card writes are on the eMMC within 0.1 s
  anyway (`m65io` `fdatasync`s once writes pause).
- The RTC is only set once the Rockchip's clock is NTP-synchronised (no battery: right after boot
  it is stale -- once it set the MEGA65 to March).

**SPI clock per bitstream**: how fast MISO is clean depends on the build -- one with the MISO
register placed far from its pin failed every status read above 25 MHz, and m65io used to hang
(no monitor, no SD, no reset).  Now `m65io` checks the link (200 status reads) at start, while the
bridge is down and after 3 dropouts in 10 s, and uses the fastest clean rate up to `-s`
(40 → 33 → 25 → 16.7 → 10 MHz; the log says "SPI link at ... (requested rate unreliable)"); the
sockets and stats stay alive while the bridge is away.  While the link runs below the requested
rate, m65io re-tests the faster rates every 5 s ("SPI link back up to ..."; the interval grows
×4 after each case of repeated dropouts): a probe during a bitstream's start-up or a C64-core
session (25 MHz) no longer leaves the MEGA65 slow -- tested: C64 25 MHz → MEGA65 40 MHz.  The XDC now packs the first SCK/MOSI/CS
sampling flip-flops and the MISO register into the I/O cells (`IOB TRUE`, MISO `SLEW FAST`), so
the pad delays no longer depend on placement.  That build: linktest 0 errors in 3000 at 10..40 MHz
(50 fails, as before), `sdlinktest.py` 200 random + 300 consecutive sectors at 40 MHz, 0 mismatches,
298/300 cache hits.

## Attic RAM (DDR3): 8 MB compatible, 64 MB unlocked

The board's 256 MB DDR3 (MT41K128M16, bank 34) provides the MEGA65's attic RAM.
- **After any reset: 8 MB** (`$8000000-$87FFFFF`, mirrored above), exactly like the HyperRAM
  of a real MEGA65, so unmodified software (size probes, mirror tricks) behaves as there.
- **64 MB on request**: `POKE $BFFFFFC,64` (BASIC 65) or `lpoke(0xbfffffc,64)` →
  `$8000000-$BFFFFEF` is RAM, mirrored from `$C000000`. `PEEK($BFFFFFC)` reads the current
  size in MB (8 or 64); writing any other value returns to 8 MB, and so does every CPU reset.
  Software can detect the board by writing 64 and reading 64 back.
- `$BFFFFF0-$BFFFFFF` are controller registers in both sizes, as on a real MEGA65 (its HyperRAM
  controller's registers sit there; it ignores writes to `$BFFFFFC`). The other 15 read 0.
  So in 64 MB mode the top 16 bytes are not RAM; a DMA fill of the last 64 KB block must stop
  16 bytes short, or it rewrites the size register.
- From BASIC 65: `PEEK`/`POKE` with 28-bit addresses and `EDMA` copies/fills (as the memtest
  does); in machine code `LDA [zp],Z` with a 32-bit pointer, or the DMAgic.
- `ddr3_attic.vhdl` implements the `slow_devices` expansion-RAM byte interface (the one the
  hyperram/sdram controllers provide on real boards). Its request side runs on **pixelclock**
  (81 MHz, slow_devices' clock). Each byte access is one masked 16-byte MIG write or one
  16-byte read; the clock crossing uses toggle handshakes. `busy` stays high until DDR3
  calibration completes.
- MIG 7-series from `src/vhdl/qmtech-k325t-cm4_mig_ddr3.prj`: the board's proven DDR3-800 setup
  (from an earlier DDR3 project on this board) with the MIG's own XADC disabled (`fpgatemp` owns the only
  XADC and feeds `device_temp_i`) and its debug ports off. clock200 comes from the core's
  (otherwise unused) eth MMCM. The project script generates the IP.
- Synthesis for this target is **not timing driven** (`-no_timing_driven` in the impl tcl):
  with the MIG and the live expansion-RAM path, synth_design's Timing Optimization phase never
  finished (twice, >20 min). Placement and routing stay timing driven; timing is met.
- Verified on hardware: HYPPO detects it (its probe pattern is at `$8000000` after boot).
  `scripts/attic_test.py [mb]` (8 MB mode + register, unlock, monitor spot tests, every address
  line, boundaries, mirror, back to 8 MB) and the BASIC 65 memtest typed in by
  `scripts/attic_memtest_basic.sh [mb]` (unlock, DMA-fill/verify all 64 KB blocks, every address
  line, mirror, 4 patterns at 500 random addresses; prints **ATTIC RAM OK**). Both default to
  64 MB; `8` tests the compatible mode only. (8 MB build: both passed.)
- **CPU fast path** (as R6's SDRAM controller): every read publishes its 8-byte line to the CPU
  (`slowram_cache_line*`). The CPU's inc/dec toggles trigger silent next/previous-line reads, and
  writes update the published line in place (matched on the physical address, so mirror aliases
  stay coherent). `$D7FE.2` = cache (default on), `.3` = advance prefetch.
  `scripts/attic_bench.py` (8 MB sequential `LDA [$FB],Z` at 40 MHz in C64 mode, A/B on one bitstream):
  cache off 1171 KB/s → **cache on 2084 KB/s (1.8×)** → cache + advance 2395 KB/s (2.05×).
  (C64 mode runs the CPU at 1 MHz until `POKE 0,65`; the benchmark sets it.)
- Simulation: `sim/tb_ddr3_attic.vhdl` (MIG native-UI model over the full address with
  independent `app_rdy`/`app_wdf_rdy`, 81 MHz request side): 8 MB mirrors after reset, size
  register, unlock, all 26 address lines, 64 MB mirror, registers are not RAM and never
  prefetched, reset → 8 MB; worst read 27 cycles against slow_devices' 128-cycle limit.

## K325T extensions: bigger limits, same MEGA65 model

Direction: raise limits the MEGA65 already has, through mechanisms it already has (same
registers, data formats and BASIC idioms), instead of adding new subsystems.  Everything is
**off after every reset**, so the machine is a real MEGA65 until a program asks for more.

**XCTL `$FFD71F0`** (K325T extension control, read/write, cleared by reset; the CPU snoops the
write, `soft_rtc.vhdl` keeps the copy you read back):

| bit | extension | after reset (= real MEGA65) | when set |
|---|---|---|---|
| 0 | **1 MB chip RAM** | CPU/DMA see 384 KB; writes to `$60000-$FFFFF` are dropped | CPU/DMA see `$00000-$FFFFF` |
| 1 | **raster DMA list** | off | DMA list at `$FFD71F4-7` runs every frame |
| 2 | **64 KB colour RAM** | 32 KB, `$FF88000-$FF8FFFF` mirrors `$FF80000-` | 64 KB, separate halves |
| 3 | (24 extra sprites: branch `k325t-extra-sprites`, not in this build) | | |
| 4 | **fast RRB painting** | VIC-IV paints 1 pixel per cycle | 2 pixels per cycle: about twice the RRB layers per line |

**1 MB chip RAM.**  The CPU (`gs4510`: `chipram_size`, `chipram_1mb`) and the VIC-IV
(`chipram_size`, a 20-bit fetch port) were already written for up to 1 MB; no board had the
block RAM.  `chipram_k325t.vhdl` keeps the generated, pre-loaded 384 KB (`shadowram-k325t-lo.vhdl`,
made from `shadowram-no-attic.vhdl` by the Makefile) and adds 640 KB as two plain blocks indexed by
raw address bits.  The VIC-IV always sees all 1 MB (its pointers already take 28 bits), so e.g.
a full-raster full-colour screen (720x576, 414,720 bytes of unique tiles) now fits.  From BASIC:
`POKE $FFD71F0,1`, then `PEEK`/`POKE`/`EDMA` up to `$FFFFF`.

**Raster DMA list** (a "copper" in DMAgic terms).  With bit 1 set, the DMAgic starts the enhanced
DMA list at `$FFD71F4-$FFD71F7` (28-bit physical address) at VIC-II raster line 0 of every frame.
A job whose option list carries **`$D0 lo` / `$D1 hi`** (new options: wait until raster >= line,
the `$D011.7/$D012` numbering) **suspends** the list and gives the CPU back; at that line the job
resumes at the next instruction boundary.  So split screens, colour bars and per-line scroll or
palette changes are plain DMA jobs (e.g. a 1-byte fill of `$FFD3020`), without timed CPU code,
and BASIC keeps running.  Jobs start only at instruction boundaries, never in the hypervisor; the
CPU's own DMA list address is saved/restored around them; DMA options do not carry over a wait.
`scripts/rasterdma_test.py` builds a 3-band border demo.

**64 KB colour RAM.**  The VIC-IV already decodes `$FF80000-$FF8FFFF` and fetches with 16-bit
colour RAM addresses; real boards have 32 KB (the top half mirrors).  `colourram_k325t.vhdl` adds
the second 32 KB; with bit 2 clear, address bit 15 is forced to 0 on both ports (the mirror).

**Fast RRB painting.**  The VIC-IV draws each raster line's characters (and every raster-rewrite
layer: GOTOX objects, the MEGA65's "sprites from characters") into a line buffer, one pixel per
pixel clock; what does not fit into the line time is cut off.  With bit 4 the buffer is split
into even/odd banks (`viciv.vhdl`, two `ram18x2k`) and the four paint paths (full colour, 4-bit
NCM, mono, multicolour) write two pixels per cycle.  Same picture (0 pixel difference on the
BASIC screen), more layers: `scripts/rrb_stress.py` finds 2 full-width 40-character layers at 1
px/cycle and 3 at 2 px/cycle.  With the bit clear the paint timing is the original one
(the first 47 intro-disk demos compared with the previous build: same pictures; Roguecraft DX
checked with the bit set in-game).

**RRB list mode** (bits 5 and 6, `docs/rrb_list_mode.md`; after upstream issue #747 "Make RRB
even more amazing" and #597).  Raster-rewrite objects moved like sprites: with bit 5 a CHR16
row may end with a LIST token (`$FFFC`) followed by 8-byte entries (X, Y offset, draw mask,
FLIPX/FLIPY, transparent/behind, the address of the object's tokens); the VIC-IV expands them
into the row buffer at the badline (`rrb_list_expander.vhdl`, with a colour row buffer).  With
bit 6 one table of up to 255 16-byte objects with X **and** Y (`$FFD71F8-FC`: address, count,
HOLD) is snapshotted once per frame and scanned for every row, so a program writes only X/Y and
objects can never tear.  Verified pixel-exact against plain GOTOX rows (`scripts/rrb_list_test.py`,
cases A-F); hardware counters at `$FFD71E0-E3` (`scripts/rrb_diag.py`) count aborts, plain rows,
port collisions and copy/expansion times.  Demos: `scripts/demos/rrblist65*.bas` (BASIC 65) and
`rrblist_b65*.bas` (B65 BASIC).  Known limit (step 1 in the design note): objects at the left
edge can lose the first raster line of a row in V400 because the expansion runs in the badline's
time; the fix (expanding the next row one line early) is in progress.

Tests: `sim/tb_chipram_k325t.vhdl`, `sim/tb_colourram_k325t.vhdl`, `sim/tb_rrb_list_expander.vhdl`,
`sim/tb_rrb_list_v2.vhdl` (GHDL, all in `sim/run_sim.sh`); on the board `scripts/chipram_test.py`,
`scripts/rasterdma_test.py`, `scripts/rrb_stress.py`, `scripts/rrb_list_test.py`.

## B65 BASIC (BBC BASIC v5 for the MEGA65)

[B65 BASIC](https://github.com/beniacm/b65-basic) is R.T. Russell's BBC BASIC (Z80) v5
re-implemented for the 45GS02: immediate mode, line editor, the BBC program format, 40-bit floats
on the math unit, PROC/FN, the inline 45GS02 assembler, files on the card (incl. its own FAT32
writer), and MEGA65 extensions -- raster-interrupt TIME with EVERY/AFTER timers, SOUND/ENVELOPE
on the SIDs, MODE 1 graphics (320x200x256) with FILL/ELLIPSE, sprites, MOUSE, 28-bit `?`/`!`/`|`
indirection, 60 KB programs in bank 1, arrays in attic RAM.  On a confirmed K325T it enables the
1 MB chip RAM and detects the attic size; a standard MEGA65 is never touched beyond the standard
registers (the standard MEGA65 path is tested in Xemu only).  Everything above was verified on
this rig (its `tests/hw` transcripts equal the Xemu ones; the card is fsck-checked before and after
each file test).  It is the second BASIC for the list-mode demos: `rrblist_b65.bas` /
`rrblist_b65v2.bas` are the B65 twins of the BASIC 65 ones, written with its far-memory `?`/`!`
operators (one 4-byte `!` write moves an object).

`scripts/b65_run.sh` builds it if needed, loads `build/b65.prg` through the monitor and starts it
(fresh reset); `scripts/b65_run.sh demo scripts/demos/rrblist_b65v2.bas` also types a demo (B65
swaps letter case like a BBC Micro, so programs are typed in lower case).  `B65_DIR` points to the
checkout (default `~/projects/bbcbasic65`).

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

**REU: 16 MB** (menu "Simulate REU 16MB"; upstream: 1750 with 512 KB). MiSTer's `reu.v` already
supports 16 MB (`cfg = "11"`); `reu_mapper` no longer wraps at the MEGA65's 8 MB HyperRAM and
puts REU bytes 0-$FFFFFF at DDR3 4-20 MB (the M2M framework uses 0-4 MB; the CRT buffer shares
the 4 MB start, but CRT and REU are alternative expansion-port modes). `sim/tb_c64_reu.vhdl` uses
a full-address MIG model and checks blocks that would alias at 512 KB / 4 MB / 8 MB plus the top
block (fails with the old mapper, as it should).

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
| Ctrl+Alt+End | `poweroff` | shut the Rockchip down safely, then plugd cuts the smart plug (see below) |

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

## Rebuilding the Rockchip from scratch

Everything the Luckfox Core3566 needs beyond its stock image; `rockchip/deploy/setup_rockchip.sh
check` compares a board with this list (read-only) and passed on the current board (2026-11-06).

1. **Base OS**: the stock Luckfox Core3566 Debian 10 (buster) eMMC image (rootfs of 2023-09-13,
   vendor kernel 4.19.232, hostname `linaro-alip`, login `linaro`/`linaro`, passwordless sudo).
   It already has python3 `spidev` 3.6, gcc and python3-dev; nothing else is installed for this
   project (`dosfstools` came with the qmtech SD work, not needed here).
2. **Boot partition (p3)**: `rockchip/boot_dtb/boot_sd.img.xz` (raw sha256 `35f646ee…`; flash
   recipe and recovery in `rockchip/boot_dtb/README.md`): the stock DTB plus the BT1120 video and
   sdmmc2 edits of the earlier bring-up, BT1120 mode 720x576@50. The BT1120 output carries the
   Linux picture into the MEGA65 bitstream (linux_fb); the sdmmc2 edit is unused. With the factory
   image (`p3_boot_backup.img.xz`, same directory) everything except the Linux picture should work,
   untested.
3. **Access**: a fixed DHCP address (the scripts default to `192.168.1.143`, `M65_HOST` overrides);
   install the PC's SSH key for `root` (`ssh-copy-id linaro@…`, then
   `sudo cp ~linaro/.ssh/authorized_keys /root/.ssh/`) and **change the default `linaro` password**.
   The image's sshd is socket-activated (one `ssh@.service` per connection) and each instance
   deleted `/run/sshd` when it ended, so about half of all back-to-back ssh/scp calls failed with
   `kex_exchange_identification: Connection reset by peer`; `setup_rockchip.sh install` adds a
   drop-in (`RuntimeDirectoryPreserve=yes`). Measured: 20 of 40 failed before, 0 of 90 after.
   Optional: a smart plug driven from the PC for power-off (`scripts/plugd.py`; `PLUG=` is the
   command that switches it, e.g. a Matter or Tasmota client).
4. **This repo's files**: `rockchip/deploy/setup_rockchip.sh install` copies the scripts and
   `m65io.c` to `/root/m65`, builds `m65io`, installs `/boot/config.txt` (`deploy/config.txt`:
   `spi3cs1=on` enables SPI3 → `/dev/spidev3.0`, the rest switches stock overlays off; the old file is
   kept as `config.txt.pre-m65`), installs `m65d.service`, `m65io.service` and
   `m65-bootreset.service` (mode 644) and enables them. Then reboot: both daemons run ~6 s after
   the kernel starts, the core is reset once the card is served.
5. **SD card**: create `/root/m65/card.img` as in "SD card and ROM" above (format with HYPPO, then
   `scripts/sd_populate.sh`), add `/c64` for the C64 core and the games. Not in git (copyrighted
   ROMs and games); back it up separately.
6. **FPGA flash**: slot 0 / slot 1 with `m65flash.py write` ("Standalone" above). Back up the
   original flash contents first (`m65flash.py backup`).
7. `rockchip/deploy/setup_rockchip.sh check` → `== root@192.168.1.143 matches the repo`.

## Build status / hardware results

- **v1** (2026-11-05): HDMI-only test. **First-light OK**: MEGAFLASH screen, clean 720x576 capture via J11.
- **v2**: adds `rk_bridge` + `sd_emu` (Rockchip termination), raw UART on D13/D14 and PS/2 on D10/C9.
  Found on hardware: those CM4 pads are **BT1120 outputs on the Rockchip** → contention on D13
  and a jammed monitor RX. Fixed at runtime with a pad script, removed from the design in v3.
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
- **2026-11-06 games / sound**: Roguecraft DX (MEGA65) and Eye of the Beholder / Metal Warrior
  (C64) play with USB keyboard and gamepad; MEGA65 HDMI sound confirmed by ear.
