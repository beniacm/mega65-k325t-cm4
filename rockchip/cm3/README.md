# Radxa CM3: the Linux picture as BT.656 for the FPGA (`linux_fb`)

On the Luckfox Core3566 the Linux picture reached the FPGA as 16-bit BT1120 on GPIO3_A/B/C. The
boot DTB was edited by hand for that (`../boot_dtb/`). On the Radxa CM3 those pads drive the
module's own Ethernet PHY. The only RK3566 video output that reaches the FPGA there is
**VOP_BT656_M1**: 8 data lines plus a clock. The MEGA65 core's `qmtech-k325t-cm4-radxacm3` target
receives it (`bt656_video.v`, `linux_fb` generic `BT656`).

| signal | RK3566 pad | CM4 GPIO | FPGA |
|---|---|---|---|
| clock | GPIO4_B4 | BCM0 | C12 |
| D0..D7 | GPIO3_C6, C7, D0, D1, D2, D3, D4, D5 | BCM25, 18, 19, 21, 20, 24, 23, 4 | A13, B15, B9, A8, A9, B14, A14, E18 |

The I/O domain is VCCIO6, which runs at 3.3 V. The CM3 schematic puts GPIO3_C6 and GPIO4_B4 in the
VCCIO6 block, and dmesg on the module says `vccio6(3300000 uV)`.

## Result on hardware (2026-11-10, `bt656-576p50`, MEGA65 core `RADXACM3,20261110.21`)

- Kernel: `vp2 assign plane mask ... [0x3f], primary ... Smart0[4]`, `Update mode to 720x576p50,
  type: 17(if:BT656, flag:0x0) for vp2 dclk: 54000000`, `set dclk_vop2 to 54000000, get 54000000`.
  DPI-1 connected, `/dev/fb0` = rockchipdrmfb 720x576, debugfs summary: Video Port2 ACTIVE,
  `bus_format[2006]: UYVY8_2X8`, `real_dclk[54000 kHz]`, H 720 732 796 864, V 576 581 586 625.
- Pads: all nine `fdc60000.syscon:rgb function m65-bt656`; GRF iomux nibbles 5, drive 0x0F (level 3).
- FPGA (`m65ctl view diag`): `locked=1 lines=576 px_per_line=720 frames_per_s=50 bt_clk_mhz=54.1`,
  all 8 data lines seen high and low, start codes on the low lane, Cb first (`yc_swapped=0`).
- Picture: a test pattern (colour bars, 1-px columns, 2x2 checkerboard) arrives pixel-exact (column 0
  black, bar edges at x = 90, 180 ...); red/blue right (no Cb/Cr swap needed); fbcon login prompt on
  tty1 legible (90x36).  150 consecutive captured frames identical, on either capture edge.
- **Capture edge: falling (the default).**  With the nine pads' drive lowered to level 0 by hand (GRF
  0x2AC..0x2B8, 0x2D8) the rising edge broke (672 "lines", every frame different) while falling stayed
  exact; at level 1 both were exact.  `pixelclk-active = <0>` stays.
- The 576i fallback was not needed and is untested.
- Install/check: `rockchip/deploy/setup_rockchip.sh install|check root@HOST` handles the overlay
  (compiled on the PC; `M65_BT656=bt656-576i50` selects the fallback).  Survives a reboot.

## Files

- `bt656-576p50.dts`: the overlay to use first. 720x576 **progressive** at 50 Hz with CEA 576p50
  timing. The pixel clock is 27 MHz, so the pads carry a **54 MHz** byte clock.
- `bt656-576i50.dts`: fallback if progressive fails. 720x576 **interlaced** (BT.656 PAL timing,
  13.5 MHz pixel clock, so a **27 MHz** byte clock). Install only one of the two files.

Build both on the PC or on the module (`dtc` is installed on both):

    dtc -@ -I dts -O dtb -o bt656-576p50.dtbo bt656-576p50.dts

Both overlays were checked on the PC. They compile, and `fdtoverlay` applies them to the
module's `rk3566-radxa-cm3-rpi-cm4-io.dtb` without errors. Every label they reference is present
in the running kernel's `/sys/firmware/devicetree/base/__symbols__`: `rgb`, `rgb_in_vp2`, `vp0`,
`vp2`, `hdmi`, `hdmi_in_vp0`, `hdmi_sound`, `route_hdmi`, `pinctrl`, `pcfg_pull_none_drv_level_3`.

## How it works

The overlay targets kernel `6.1.84-18-rk2410-nocsf`. The source is
github.com/radxa/kernel, branch `linux-6.1-stan-rkr4.1` (SUBLEVEL 84).

1. **Encoder.** The `rgb` node (`compatible = "rockchip,rk3568-rgb"`, under the SYS_GRF syscon
   at fdc60000) is the DPI/BT.656/BT.1120 encoder. Its only VOP input in the DT is `rgb_in_vp2`
   (from `vp2_out_rgb`). The overlay enables both and adds `port@1` pointing to a panel.
2. **Panel and bus format.** The panel is a `simple-panel` with `bus-format = <0x2006>`
   (MEDIA_BUS_FMT_UYVY8_2X8) and one `display-timings` mode. In `rockchip_rgb.c`,
   `rockchip_rgb_encoder_atomic_check()` maps all four `*_2X8` YUV formats to
   `VOP_OUTPUT_IF_BT656` with BT.601 limited range. VOP2 then does the following
   (`rockchip_drm_vop2.c`):
   - It doubles the dclk for BT.656: 27 MHz becomes 54 MHz (`adj_mode->crtc_clock *= 2`).
   - It sets `rgb_en`, `bt656_en` and `rgb_mux = vp2`.
   - `bt656_yc_swap` is set only for YUYV/YVYU (`vop2_output_yc_swap()`). UYVY therefore gives
     the standard order Cb Y Cr Y.
   - It writes `grf_bt656_clk_inv = !dclk_inv`, where `dclk_inv` comes from the panel's
     `pixelclk-active`.
   - The driver does not reject progressive modes for BT.656. `rk3568_rgb` sets no
     `max_dclk_rate`, and the rgb connector has `interlace_allowed = true`.
3. **Pins.** The vendor DT already has `bt656m1_pins` (`/pinctrl/bt656/bt656m1-pins`, function
   5). Those pins use plain `pcfg_pull_none` and reset-default drive. The overlay adds its own
   copy, `m65-bt656m1-pins`, with **drive level 3**. The Luckfox BT1120 link was dead above about
   25 MHz until its drive was raised.
4. **VOP2 windows and HDMI.** The RK3566 has six window IDs. Cluster1, Esmart1 and Smart1 are
   "mirror" windows (`WIN_FEATURE_MIRROR`, `RK3566_MIRROR_PLANE_MASK`). The driver registers them
   only when two CRTCs exist, and they cannot show their own content.
   - **The current behaviour:** the base DT gives `vp0` the mask 0x15 and gives `vp1`/`vp2`
     nothing. That fails `vop2_plane_mask_check()`, because every window must be assigned once.
     The kernel falls back to the default policy: today's boot log shows "vp0 assign plane mask
     … [0x3f], primary … Smart0[4]".
   - **What the overlay does:** it disables the module's HDMI (`hdmi`, `hdmi_in_vp0`, the HDMI
     sound card and `route_hdmi`). Radxa's own display overlays do the same, for example
     `radxa-cm3-rpi-cm4-7inch-touchscreen-disp0`. The overlay then gives `vp2` all six windows
     (mask 0x3f, primary Smart0) and `vp0` mask 0. The check passes, and VP2 gets every real
     window.
   - **Coexistence with HDMI:** this would need split masks, for example vp0 = 0x15 (real) and
     vp2 = 0x2a (mirror only). VP2 would then be limited to RK3566 mirror windows, which only
     clone the main windows. That is untested and probably black when HDMI is idle. The
     carrier's HDMI output comes from the FPGA, and `card0-HDMI-A-1` reads "disconnected" today,
     so turning the module's HDMI off costs nothing.
5. **Console.** With an always-"connected" panel, DRM fbdev creates `/dev/fb0` at 720x576 when
   the driver binds. Without the overlay, the boot log says "Cannot find any crtc or sizes" and
   `/proc/fb` is empty. fbcon then takes over tty1 (`console=tty1` is on the command line), and
   the getty shows on DPI-1. This is the same layout as on the Luckfox: 90x36 characters with
   the 8x16 font.
   - Plymouth is installed and `splash` is on the command line. Expect the Radxa splash during
     boot, then the console.
   - To keep kernel messages off the console, reuse the Luckfox setting
     (`/etc/sysctl.d/90-m65-console.conf`, level 4).
   - The MEGA65 shows the picture when bridge VIDEO 0x70 bit 0 is set (`m65ctl view linux`, or
     Ctrl+Alt+Insert).

## Install / remove (on the module, as root)

Radxa's image builds `/boot/extlinux/extlinux.conf` with `u-boot-update`. Every `*.dtbo` in
`/boot/dtbo/` goes onto the default entry's `fdtoverlays` line. Files ending in
`.dtbo.disabled` are skipped. `rsetup` does the same renaming, and the `metadata` node lets
`rsetup` list this overlay. The file is not in `managed.list`, so package updates leave it alone.
The rescue entry `l0r` gets **no** overlays.

    scp bt656-576p50.dtbo root@192.168.1.215:/boot/dtbo/          # from this directory
    ssh root@192.168.1.215 'u-boot-update && grep -n fdtoverlays /boot/extlinux/extlinux.conf && reboot'

To remove it, or to switch to the 576i file:

    mv /boot/dtbo/bt656-576p50.dtbo /boot/dtbo/bt656-576p50.dtbo.disabled   # or rm
    u-boot-update && reboot

If U-Boot cannot apply an overlay, its extlinux code prints an error and boots without it. The
overlay only touches display nodes, so ssh and Ethernet stay up either way, and you can remove it
over ssh. The module's serial console (UART2 = BCM14/15) goes to the FPGA, not to a PC, so there
is no serial fallback. The last resort is reflashing the eMMC in maskrom mode.

## What to check after boot

    dmesg | grep -i -E 'vop2|rgb|panel|dclk|plane mask'
    # expect: vp2 assign plane mask: ...[0x3f], primary plane phy id: Smart0[4]
    #         Update mode to 720x576p50, type: 17(if:BT656, ...) for vp2 dclk: 54000000
    #         set dclk_vp2 to 54000000, get 54000000      (exact names may differ)
    cat /sys/class/drm/card0-DPI-1/status /sys/class/drm/card0-DPI-1/modes   # connected, 720x576
    cat /sys/kernel/debug/dri/0/summary      # Video Port2: ACTIVE, bus_format[2006], 720x576p50
    cat /proc/fb; fbset 2>/dev/null          # 0 rockchipdrmfb
    grep -E 'dclk_vop2|gpll ' /sys/kernel/debug/clk/clk_summary   # dclk_vop2 = 54000000 (gpll 1188 MHz / 22)
    P=/sys/kernel/debug/pinctrl/pinctrl-rockchip-pinctrl
    grep -E '^pin (118|119|120|121|122|123|124|125|140) ' $P/pinmux-pins
    # all nine: device fdc60000.syscon:rgb function m65-bt656 group m65-bt656m1-pins
    grep -E '^pin (118|125|140) ' $P/pinconf-pins

`modetest` is not installed (`apt install libdrm-tests`, then `modetest -M rockchip -c`). A quick
picture test: `cat /dev/urandom > /dev/fb0`, or a colour bar written with Python.

Behind pinctrl, the SYS_GRF registers at 0xFDC60000 show the pad setup. Read them with Python
`mmap` on `/dev/mem`, as `../sd_mode.py` does:

- **Iomux** (4 bits per pad, each nibble should read 5):
  - GPIO3_C6/C7: offset 0x54, bits 8..15
  - GPIO3_D0..D3: offset 0x58
  - GPIO3_D4/D5: offset 0x5C, bits 0..7
  - GPIO4_B4: offset 0x6C, bits 0..3
- **Drive** (8 bits per pad; level n reads as `(1 << (n+1)) - 1`, so level 3 = 0x0F):
  - GPIO3_C6/C7: 0x2AC
  - D0/D1: 0x2B0
  - D2/D3: 0x2B4
  - D4/D5: 0x2B8
  - GPIO4_B4: 0x2D8, low byte

These offsets were worked out from the pinctrl driver's rk3568 tables, not read from the TRM.
On first-revision silicon the driver places the drive bits of pin 23 (GPIO3_C7) differently.

On the FPGA side (MEGA65 radxacm3 core), `m65ctl view diag` shows frames, lines, the lock and the
clock.

## Known unknowns and fallbacks

- **Progressive BT.656:** the driver allows it (see above). Rockchip documents BT.656 only as
  PAL/NTSC, and nobody has checked whether the RK3566 encoder sends sensible V/F bits in
  progressive mode. `bt656_video.v` locks on SAV/EAV, F=0 throughout. If the FPGA sees no lock,
  or a broken picture with the clock present, install `bt656-576i50.dtbo`. The FPGA handles
  interlace by placing field 1's lines between field 0's, and its 54 MHz clock constraint covers
  27 MHz.
- **Clock edge:** `pixelclk-active = <0>` matches the Luckfox panel. Try the capture edge on the
  FPGA first (`m65ctl view rise|fall`, VIDEO bit 1). Only after that, flip `pixelclk-active` to 1.
- **Byte order:** UYVY gives the standard Cb Y Cr Y.
  - `bt656_video.v` detects luma-first vs chroma-first by itself, from the blanking fill
    (80 10 vs 10 80).
  - The VOP cannot swap Cb and Cr: `VYUY8_2X8` (0x2007) is handled exactly like UYVY. If red
    and blue look swapped, use VIDEO bit 2 on the FPGA.
  - `YUYV8_2X8` (0x2008) sets `bt656_yc_swap` (luma first) if ever needed.
- **Signal integrity at 54 MHz:** if the picture has sparkles or no lock, raise the drive to
  `&pcfg_pull_none_drv_level_5`, the highest level that has a symbol. Or use 576i, which halves
  the byte clock.
- **Clock source:** 54 MHz and 27 MHz come exactly from GPLL (1188 MHz). The live tree shows
  `dclk_vop2` under `hpll` (24 MHz) right now; the VOP2 code reparents it. Check that the rate
  reads 54000000.
- **HDMI and the module:** the module's HDMI is off while the overlay is installed (see
  "How it works"). The U-Boot logo is not involved: the logo memory is not reserved
  ("failed to parse loader memory").
- Tested on hardware 2026-11-10 (576p50): see "Result on hardware" at the top.
