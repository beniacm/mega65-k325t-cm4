# Rockchip boot image (Luckfox Core3566, partition p3)

**For this project:** `rockchip/deploy/setup_rockchip.sh` checks for / installs
**`boot_sd.img.xz`** — the stock Luckfox boot image with a modified kernel DTB, carried over
from the board bring-up (BT1120 video out on GPIO3, then `sdmmc2` enabled: `edit_dts.py`
→ `fdt_new.dts`, `edit_dts_sd.py` → `fdt_sd.dts`, `make_boot.py` repacks).  The MEGA65 work
does not use the video or SD-passthrough paths; what matters is that this DTB muxes
GPIO3_A1-A7/B0-B6/C1-C3 to video **outputs** at boot, so `rockchip/rk_pins.py` releases the
lines it shares with the FPGA (run by `m65d_restart.sh`).  `sd_mode.py` (../) is the pad-mux
switcher from that work, kept as the reference for poking SYS_GRF behind pinctrl.
**`p3_boot_backup.img.xz`** is the untouched factory p3 dump for recovery (below).
Both images contain the vendor (Luckfox, Linux 4.19) kernel; its source is in the Luckfox SDK.

---

# Boot-DTB surgery — BT1120 (vp2 → DPI-1) enablement, VERIFIED E2E 2026-07-10

The Luckfox Core3566 boots a FIT image on p3 (`/dev/block/by-name/boot`) with
external data: a 1.5 KiB FIT header (a plain DTB) + fdt/kernel/resource blobs at
`data-position` offsets. **u-boot loads the kernel DTB from the RSCE `resource`
image's `rk-kernel.dtb` entry — NOT from the FIT `fdt` node** (patching only the
fdt node boots the old DTB; found the hard way).

## What was changed (vs the stock DTB) — see `edit_dts.py` (run against the
decompiled stock DTB, `dtc -I dtb -O dts`):
1. `/syscon@fdc60000/rgb`: `status="okay"`, `pinctrl-0=<0x1b2>` (the vendor's own
   `/pinctrl/bt1120/bt1120-pins` group — clk GPIO3_A6 + 16 data, mux 2).
2. `rgb/ports/port@0/endpoint@2` (rgb_in_vp2): `status="okay"`.
3. `rgb/ports/port@1/endpoint@0` (new): remote → panel.
4. New root node `bt1120-panel`: `compatible="simple-panel"`,
   `bus-format=<0x2011>` (MEDIA_BUS_FMT_YUYV8_1X16), display-timings
   1280x720@60 CEA (74.25 MHz).
4b. New pinctrl group `bt1120hd` (phandle 0x2b5): same 17 pins but
   `pcfg-pull-none-drv-level-3` (12 mA, phandle 0x103) — **REQUIRED**: with the
   vendor group's default drive the link is dead above ~25 MHz on the CM4
   jumpers (640x480 worked, 37/74 MHz pure black until the drive bump).
5. VOP2 plane masks (`/vop@fe040000/ports`): vp1 `0x0a→0x02` (primary 3→1),
   vp2 gets `rockchip,plane-mask=<0x08>` + `rockchip,primary-plane=<0x03>`
   (Esmart1) — without this vp2 has mask 0x0 and the CRTC is unusable.

## Repack recipe (all offsets from the stock image):
- resource at `0x1615400` (3049 blocks): append new DTB at block 3049, update
  the `rk-kernel.dtb` ENTR block (sha1 @+224, `<hash_size,off_blk,size>` @+256).
- FIT header: `images/fdt` data-position → `0x1792600` (shares the same bytes),
  data-size + sha256; `images/resource` data-size + sha256; root `totalsize`.
- dd header (padded to 0x800) at 0, resource at 0x1615400, flash via
  `dd of=/dev/block/by-name/boot conv=fsync`.

## Recovery
The pristine factory p3 boot image is committed compressed as
**`p3_boot_backup.img.xz`** (6.6 MB; the raw 64 MB `.img` is gitignored,
local-only). It is the exact pre-modification dump — stock hashes fdt
`6cd79228…`, kernel `b4cc79fa…`, resource `c6726e8a…`; whole-image
sha256 `bc8d2529492bab7dbf18632f6b1653acfbcc5e8632fb3da702e6c17fb9ea9531`.

Restore (verify first, then flash):
```
xz -dc p3_boot_backup.img.xz | sha256sum   # expect bc8d2529…
# A) board still boots + adb:
xz -dc p3_boot_backup.img.xz > /tmp/p3.img
adb push /tmp/p3.img /data/p3.img
adb shell 'dd if=/data/p3.img of=/dev/block/by-name/boot bs=1M conv=fsync; sync; reboot'
# B) board bricked -> maskrom/loader (USB 2207:0006):
adb reboot loader              # or hold the recovery/maskrom button on power-up
rkdeveloptool wl <p3_start_lba> /tmp/p3.img   # p3 = /dev/block/mmcblk0p3
```
Signature in the FIT is a template only (`verifiedbootstate=orange`) — u-boot
checks the sha256 image hashes, not RSA, which is why the repack works.

## Result
Boot OK; `card0-DPI-1` connected **1280x720@60** (74.25 MHz × 16 bit =
1.19 Gbit/s over the jumpers); fbcon clones the console to it. Full path
verified pixel-clean: fb0 color bars captured intact through the FPGA
(a BT1120-to-HDMI test bitstream, 720p60) → Sipeed DVI Pmod on J11 → MS2130.
`make_boot.py` = one-command boot-image rebuild from `fdt_new.dtb`.
Note: i2c3 loses GPIO3_B5/B6 to the bt1120 pinmux (kernel log line is expected).

## Deployed image: `boot_sd.img.xz` (BT1120 + sdmmc2) — on the board since 2026-07-18
`edit_dts_sd.py` (fdt_new.dts → `fdt_sd.dts`: sdmmc2 `dwmmc@fe000000` okay, 4-bit, broken-cd,
supports-sd, 8 MHz) on top of the BT1120 DTB, repacked like above into `boot_sd.img`
(24,863,744 bytes, sha256 `a34f418cacd9a595b6f1876c0fe43b235d6f210ff1268d1c3aace4c96db766b7`).
This is the boot image the MEGA65/C64 deployment (github.com/beniacm/mega65-k325t-cm4,
`rockchip/deploy/`) was built and tested on; `setup_rockchip.sh check` there verifies it.
```
xz -dc rockchip/boot_dtb/boot_sd.img.xz | sha256sum            # expect a34f418c…
xz -dc rockchip/boot_dtb/boot_sd.img.xz | ssh rock-root 'dd of=/dev/block/by-name/boot bs=1M conv=fsync && sync'
```
