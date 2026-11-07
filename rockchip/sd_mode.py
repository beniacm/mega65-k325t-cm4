#!/usr/bin/env python3
# sd_mode.py — runs ON the Rockchip. Switch the shared GPIO3_A1..A6 pads
# between VIDEO (vop-bt1120, func 2 -> FPGA HDMI bridge) and SD (sdmmc2m1,
# func 5 -> FPGA sd_passthru -> J12 TF Pmod), plus the FPGA sd_en gate on
# GPIO3_C0 (BCM5, sysfs global 112).
#
# The iomux is poked directly in SYS_GRF (behind pinctrl's back) because the
# rgb/DPI driver owns these pads via pinctrl at boot — switching them here
# leaves the DRM pipeline fully assembled: video comes back the instant the
# mux returns to func 2. sdmmc2 (dwmmc@fe000000, broken-cd) just sees the
# card "appear"/"vanish" on its next 1 s poll.
#
# ORDER MATTERS (the FPGA contract):
#   sd on   : pads -> func 5 FIRST, then sd_en=1
#   sd off  : sd_en=0 FIRST, then pads -> func 2
#
#   usage: sudo ./sd_mode.py sd | video | status
import mmap, os, struct, sys, time, glob

GRF        = 0xFDC60000          # RK3568 SYS_GRF
IOMUX3A_L  = 0x40                # GPIO3_A0..A3, 4-bit fields, wmask [31:16]
IOMUX3A_H  = 0x44                # GPIO3_A4..A7
SD_EN_GPIO = 112                 # GPIO3_C0 = 3*32+16
FUNC_VIDEO, FUNC_SD = 2, 5

def rd(mm, off):  return struct.unpack('<I', mm[off:off+4])[0]
def wr(mm, off, v): mm[off:off+4] = struct.pack('<I', v)

def funcs(mm):
    lo, hi = rd(mm, IOMUX3A_L), rd(mm, IOMUX3A_H)
    # A1..A3 in _L bits [7:4][11:8][15:12]; A4..A6 in _H bits [3:0][7:4][11:8]
    return [(lo >> (4*i)) & 0xF for i in (1, 2, 3)] + \
           [(hi >> (4*i)) & 0xF for i in (0, 1, 2)]

def set_funcs(mm, f):
    val_l = (f << 4) | (f << 8) | (f << 12); msk_l = 0xFFF0
    val_h = (f << 0) | (f << 4) | (f << 8);  msk_h = 0x0FFF
    wr(mm, IOMUX3A_L, (msk_l << 16) | val_l)
    wr(mm, IOMUX3A_H, (msk_h << 16) | val_h)

def sd_en(v):
    g = f"/sys/class/gpio/gpio{SD_EN_GPIO}"
    if not os.path.isdir(g):
        open("/sys/class/gpio/export", "w").write(str(SD_EN_GPIO))
        time.sleep(0.1)
    open(g + "/direction", "w").write("out")
    open(g + "/value", "w").write("1" if v else "0")

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    mm = mmap.mmap(fd, 0x1000, offset=GRF)

    cur = funcs(mm)
    print(f"GPIO3_A1..A6 iomux = {cur}  (2=vop-bt1120/video, 5=sdmmc2/SD)")

    if mode == "status":
        for b in glob.glob("/sys/block/mmcblk*"):
            print(" ", b, open(b + "/size").read().strip(), "sectors")
        return

    if mode == "sd":
        assert all(f in (FUNC_VIDEO, FUNC_SD, 0) for f in cur), \
            f"unexpected iomux {cur} — refusing (use pinctrl debugfs to inspect)"
        # sdmmc2 INPUT routing: GRF_IOFUNC_SEL2 (0x0308) bit14 = 1 selects the
        # m1 pads. Outputs fan out to any func-5 pad, but the controller's
        # INPUTS come only from the selected location — without this, responses
        # never reach it (CMD8 -110; found the hard way, 2026-07-18).
        wr(mm, 0x308, 0x4000_4000)
        set_funcs(mm, FUNC_SD)
        print("pads -> sdmmc2 (func 5):", funcs(mm))
        sd_en(1)
        print("sd_en=1 — FPGA passthrough live; waiting for card detect...")
        for _ in range(12):
            time.sleep(1)
            blks = glob.glob("/sys/block/mmcblk[1-9]")
            if blks:
                b = blks[0]
                sz = int(open(b + "/size").read()) * 512 // (1024*1024)
                print(f"CARD: /dev/{os.path.basename(b)}  {sz} MiB")
                return
        print("no card detected in 12 s — check J12 Pmod / card / dmesg")

    elif mode == "video":
        sd_en(0)
        print("sd_en=0 — FPGA passthrough released")
        time.sleep(0.05)
        set_funcs(mm, FUNC_VIDEO)
        print("pads -> vop-bt1120 (func 2):", funcs(mm))
        print("video restored (BT1120 stream resumes; HDMI relocks in ~1 s)")

    else:
        print(__doc__)

if __name__ == "__main__":
    main()
