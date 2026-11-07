#!/usr/bin/env python3
"""rrb_stress.py -- how many full-width RRB layers the VIC-IV paints per line (K325T fast RRB).

Builds one 16-bit character row of L layers in chip RAM ($40000) and colour RAM (offset $4000):
each layer = GOTOX 0 + 40 solid characters (screen code 160); every layer red except the last,
green.  LINESTEP = 0 makes all rows reuse that row.  If the VIC-IV paints all L layers in its
raster time the picture is green; when it runs out, red (or the background) shows.  Binary
search for the largest L that stays green, with XCTL bit 4 (fast RRB, 2 px/cycle) off and on.
Needs BASIC 65 at READY; restores nothing (reset afterwards).

    scripts/rrb_stress.py [--max 24] [--chars 40]
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rasterdma_test import peek, poke                       # monitor helpers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XCTL, SCR, COLOFS = 0xFFD71F0, 0x40000, 0x4000
CHARS = int(sys.argv[sys.argv.index("--chars") + 1]) if "--chars" in sys.argv else 40
MAXL = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 24


def capture():
    out = subprocess.run([os.path.join(ROOT, "scripts/capture.sh"), "-n", "3"], capture_output=True, text=True).stdout
    path = [l.split("file:")[1].strip() for l in out.splitlines() if "file:" in l][-1]
    from PIL import Image
    return path, Image.open(path).convert("RGB")


def green_share(img):
    """share of green among the clearly coloured pixels of the central area"""
    px = img.crop((60, 100, 660, 480)).resize((150, 95)).getdata()
    g = sum(1 for p in px if p[1] > 120 and p[0] < 110)
    r = sum(1 for p in px if p[0] > 110 and p[1] < 80)
    return g / max(1, g + r), g + r


def setup(layers):
    row_s, row_c = [], []
    for l in range(layers):
        row_s += [0, 0]                       # GOTOX X=0
        row_c += [0x10, 0]                    # colour RAM byte 0 bit 4 = GOTOX
        col = 5 if l == layers - 1 else 2
        for _ in range(CHARS):
            row_s += [160, 0]
            row_c += [0, col]
    n = len(row_s) // 2
    poke(SCR, row_s)
    poke(0xFF80000 + COLOFS, row_c)
    poke(0xFFD3054, [peek(0xFFD3054)[0] | 1])                 # CHR16
    poke(0xFFD3058, [0, 0])                                   # LINESTEP = 0: every row the same
    poke(0xFFD305E, [n & 0xFF])                               # CHRCOUNT
    d063 = peek(0xFFD3063)[0]
    poke(0xFFD3060, [SCR & 0xFF, SCR >> 8 & 0xFF, SCR >> 16 & 0xFF, (d063 & 0xC0) | ((n >> 8) & 3) << 4 | (SCR >> 24 & 0x0F)])
    poke(0xFFD3064, [COLOFS & 0xFF, COLOFS >> 8])


def ok(layers):
    setup(layers)
    path, img = capture()
    share, n = green_share(img)
    return share > 0.98 and n > 1000, share, path


def search(fast):
    x = peek(XCTL)[0]
    poke(XCTL, [(x | 0x10) if fast else (x & ~0x10)])
    lo, hi, best = 1, MAXL, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        good, share, path = ok(mid)
        print("  fast=%d layers=%2d green=%.2f %s" % (fast, mid, share, os.path.basename(path)), flush=True)
        if good:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def main():
    a = search(False)
    b = search(True)
    print("RRB layers of %d chars painted per line: 1 px/cycle %d, 2 px/cycle %d" % (CHARS, a, b))


if __name__ == "__main__":
    main()
