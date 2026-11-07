#!/usr/bin/env python3
"""xsprite_test.py -- K325T extra sprites (XCTL bit 3, registers $FFD7120-$FFD71E1).

Sets up, through the serial monitor (BASIC 65 at READY, screen cleared):
  VIC sprite 0   yellow 24x21 block at X=100,Y=100 (16-bit sprite pointers, table at $4F000)
  extra sprite 0 red block at X=100, Y=140   -> measures the X alignment against sprite 0
  extra sprite 1 green block at X=160, Y=100 -> measures the Y alignment
then captures the HDMI picture, measures the offsets, writes them to $FFD71E0/E1 (signed),
re-captures and checks.  --demo additionally shows all 24 extra sprites (mono, multicolour,
expanded, 16-colour, 64 px); --off disables them again.

    scripts/xsprite_test.py [--demo] [--keep-adjust] [--off]
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rasterdma_test import peek, poke                       # monitor helpers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XCTL = 0xFFD71F0
XS = 0xFFD7120
PTRTAB, DATA = 0x4F000, 0x4E000                              # 16-bit pointer table, sprite data


def capture():
    out = subprocess.run([os.path.join(ROOT, "scripts/capture.sh"), "-n", "3"], capture_output=True, text=True).stdout
    path = [l.split("file:")[1].strip() for l in out.splitlines() if "file:" in l][-1]
    from PIL import Image
    return path, Image.open(path).convert("RGB")


def bbox(img, pred):
    w, h = img.size
    px = img.load()
    xs, ys = [], []
    for y in range(0, h):
        for x in range(0, w):
            if pred(px[x, y]):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


is_yellow = lambda p: p[0] > 180 and p[1] > 180 and p[2] < 120
is_red = lambda p: p[0] > 110 and p[1] < 70 and p[2] < 70
is_green = lambda p: p[1] > 150 and p[0] < 150 and p[2] < 150


def xsprite(n, x, y, ptr, colour, flags):
    poke(XS + 8 * n, [x & 0xFF, x >> 8 & 1, y & 0xFF, y >> 8 & 1, ptr & 0xFF, ptr >> 8, colour, flags])


def main():
    x = peek(XCTL)[0]
    if "--off" in sys.argv:
        poke(XCTL, [x & ~8])
        print("extra sprites off")
        return
    # sprite data: block (ptr $1380), block (ptr $1381), multicolour stripes ($1382),
    # 16-colour gradient 8 bytes/row ($1383, 168 bytes -> next free $1386), 64 px frame ($1386)
    poke(DATA, [0xFF] * 63 + [0])
    poke(DATA + 0x40, [0xFF] * 63 + [0])
    poke(DATA + 0x80, [0x55, 0xAA, 0xFF] * 21 + [0])
    poke(DATA + 0xC0, [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF1] * 21)
    poke(DATA + 0x180, ([0xFF] * 8) + ([0x80, 0, 0, 0, 0, 0, 0, 0x01] * 19) + ([0xFF] * 8))
    # VIC sprite 0 with 16-bit pointers from $4F000
    poke(PTRTAB, [0x80, 0x13])
    poke(0xFFD306C, [PTRTAB & 0xFF, PTRTAB >> 8 & 0xFF, (PTRTAB >> 16 & 0x7F) | 0x80])
    poke(0xFFD3000, [100, 100])
    d010 = peek(0xFFD3010)[0]
    poke(0xFFD3010, [d010 & 0xFE])
    poke(0xFFD3027, [7])
    for r in (0xFFD3017, 0xFFD301B, 0xFFD301C, 0xFFD301D):
        poke(r, [peek(r)[0] & 0xFE])
    poke(0xFFD3015, [peek(0xFFD3015)[0] | 1])
    # extra sprites 0 / 1, all others off
    for n in range(24):
        xsprite(n, 0, 0, 0, 0, 0)
    xsprite(0, 100, 140, 0x1381, 2, 0x01)
    xsprite(1, 160, 100, 0x1381, 5, 0x01)
    if "--keep-adjust" not in sys.argv:
        poke(XS + 0xC0, [0, 0])
    poke(XCTL, [x | 8])

    path, img = capture()
    yb, rb, gb = bbox(img, is_yellow), bbox(img, is_red), bbox(img, is_green)
    print("capture", path)
    print("  VIC sprite 0 (yellow):", yb, " extra 0 (red):", rb, " extra 1 (green):", gb)
    if not (yb and rb and gb):
        sys.exit("XSPRITE FAIL: a sprite is not visible")
    sx = (yb[2] - yb[0] + 1) / 24.0
    sy = (yb[3] - yb[1] + 1) / 21.0
    dx = round((rb[0] - yb[0]) / sx)
    dy = round((gb[1] - yb[1]) / sy)
    wr, hr = (rb[2] - rb[0] + 1) / sx, (rb[3] - rb[1] + 1) / sy
    print("  scale %.2f x %.2f px per VIC-II pixel; extra 0 size %.1f x %.1f; offset dx=%d dy=%d"
          % (sx, sy, wr, hr, dx, dy))
    if abs(rb[0] - yb[0]) > sx / 2 or abs(gb[1] - yb[1]) > sy / 2:    # off by a VIC-II pixel or more
        adj = peek(XS + 0xC0, 2)
        ax = (adj[0] - dx) & 0xFF
        ay = (adj[1] - dy) & 0xFF
        poke(XS + 0xC0, [ax, ay])
        print("  set alignment $FFD71E0=%02X $FFD71E1=%02X, re-checking" % (ax, ay))
        path, img = capture()
        yb, rb, gb = bbox(img, is_yellow), bbox(img, is_red), bbox(img, is_green)
        dx = round((rb[0] - yb[0]) / sx)
        dy = round((gb[1] - yb[1]) / sy)
        print("  after: dx=%d dy=%d (%s)" % (dx, dy, path))
    dxp, dyp = rb[0] - yb[0], gb[1] - yb[1]          # in output pixels
    print("  final offset %d / %d output pixels (VIC-II pixel = %.2f)" % (dxp, dyp, sx))
    ok = abs(dxp) <= 1 and abs(dyp) <= 1 and abs(wr - 24) < 1.5 and abs(hr - 21) < 1.5
    if "--demo" in sys.argv:
        cols = [2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15]
        for n in range(2, 24):
            i = n - 2
            kind = i % 5
            ptr, flags = [(0x1381, 0x01), (0x1382, 0x03), (0x1381, 0x05), (0x1383, 0x21), (0x1386, 0x41)][kind]
            xsprite(n, 30 + (i % 6) * 50, 150 + (i // 6) * 26, ptr, cols[i % len(cols)], flags)
        path, img = capture()
        print("  demo picture:", path)
    print("XSPRITE PASS" if ok else "XSPRITE FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
