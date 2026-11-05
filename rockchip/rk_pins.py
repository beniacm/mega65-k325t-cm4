#!/usr/bin/env python3
"""rk_pins.py -- release the CM4-bus pads the MEGA65 bitstream uses as FPGA inputs/outputs
from the leftover BT1120 video mux (boot DTB of the ~/qmtech bridge work).
GPIO3_A7 (BCM26/C9), GPIO3_C2 (BCM9/D14), GPIO3_C3 (BCM11/D13) -> GPIO function, input.
Poked in SYS_GRF / GPIO3 behind pinctrl's back (same method as ~/qmtech/rockchip/sd_mode.py).
Usage: rk_pins.py [show]"""
import mmap, os, struct, sys
GRF, GPIO3 = 0xFDC60000, 0xFE760000
PADS = {  # name: (iomux reg, field shift, gpio3 pin)
    "GPIO3_A7": (0x44, 12, 7),
    "GPIO3_C2": (0x50, 8, 18),
    "GPIO3_C3": (0x50, 12, 19),
}
def rd(mm, o): return struct.unpack("<I", mm[o:o+4])[0]
def wr(mm, o, v): mm[o:o+4] = struct.pack("<I", v)
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
grf = mmap.mmap(fd, 0x1000, offset=GRF)
gp = mmap.mmap(fd, 0x1000, offset=GPIO3)
def show():
    for n, (reg, sh, pin) in PADS.items():
        f = (rd(grf, reg) >> sh) & 7
        ddr = (rd(gp, 0x08 if pin < 16 else 0x0C) >> (pin % 16)) & 1
        ext = (rd(gp, 0x70) >> pin) & 1
        print("%s func=%d dir=%s level=%d" % (n, f, "out" if ddr else "in", ext))
if len(sys.argv) > 1 and sys.argv[1] == "show":
    show(); sys.exit()
for n, (reg, sh, pin) in PADS.items():
    wr(gp, 0x08 if pin < 16 else 0x0C, (1 << (16 + pin % 16)))      # DDR bit = 0 (input)
    wr(grf, reg, (7 << (16 + sh)))                                    # func = 0 (GPIO)
show()
