#!/usr/bin/env python3
"""rrb_list_test.py -- RRB list mode (XCTL bit 5, docs/rrb_list_mode.md) against plain GOTOX.

Draws 25 16-bit character rows (each its own copy, LINESTEP 256 -- a badline per row, as in
real use; LINESTEP 0 only refetches the row buffer by accident) twice:
  A) list mode: 40 background glyphs, LIST token, 3 list entries -> one 2-glyph object at
     (absolute X 50), (X 150, Y offset 3 down), (REL +100 -> X 250, FLIPX)
  B) reference: the same row written out with ordinary GOTOX tokens and the object's tokens
  C) list mode v2 (XCTL bit 6): the plain background rows, the same three objects from the
     global object table (X 50; REL +100; X 250 FLIPX; Y 0, 25 rows high, stride 0)
and compares the HDMI captures pixel by pixel (expected: identical).  Also: B with the bit off
must equal B with the bit on (list mode changes nothing in rows without a LIST token).
Stops the CPU (monitor t1) while it runs, so BASIC 65 cannot touch the VIC-IV; leaves the
VIC-IV changed (reset afterwards).

    scripts/rrb_list_test.py [--keep]     (--keep: leave picture A on screen)
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rasterdma_test import peek, poke, mon                  # monitor helpers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XCTL, SCR, COLOFS = 0xFFD71F0, 0x40000, 0x4000            # row: screen $40000, colour offset $4000
OBJ, OBJCOL = 0x48000, 0x6000                             # object data (clear of the 25 rows)
TAB = 0x4A000                                             # v2 object table
COLRAM = 0xFF80000
BG = 40
NOISE = 20                                                # px
ROWS, STEP = 25, 256                                      # rows, bytes per row (screen and colour)


def capture():
    out = subprocess.run([os.path.join(ROOT, "scripts/capture.sh"), "-n", "4"], capture_output=True,
                         text=True).stdout
    path = [l.split("file:")[1].strip() for l in out.splitlines() if "file:" in l][-1]
    from PIL import Image
    return path, Image.open(path).convert("RGB")


def load(addr, data):
    """binary load through the serial monitor ('l', as m65run does: ~190 KB/s)"""
    import socket
    import time
    s = socket.create_connection((os.environ.get("M65_HOST", "192.168.1.143"), 4510), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    data = bytes(data)
    for o in range(0, len(data), 4096):
        chunk = data[o:o + 4096]
        a = addr + o
        s.settimeout(0.05)
        try:
            while s.recv(65536):
                pass
        except socket.timeout:
            pass
        s.settimeout(3)
        s.sendall(("l%x %x" % (a, (a + len(chunk)) & 0xFFFF)).encode() + b"\r" + chunk)
        out, t0 = b"", time.time()
        while not out.rstrip(b"\r\n ").endswith(b"\n.") and time.time() - t0 < 5:
            try:
                out += s.recv(65536)
            except socket.timeout:
                break
    s.close()


def w(v):
    return [v & 0xFF, v >> 8 & 0xFF]


def gotox(x, yoff=0, neg=0):
    """GOTOX screen word: X 9..0, sign 12, Y offset 15..13"""
    return w(x | neg << 12 | yoff << 13)


# the object: two glyphs (screen codes 1 'A' red, 2 'B' yellow) and end-of-list
OBJ_S = w(1) + w(2) + w(0xFFFF)
OBJ_C = [0x00, 2, 0x00, 7, 0, 0]


def entry(word0, mask=0xFF, flipx=0, flipy=0, transp=0, behind=0):
    w1 = mask | (OBJ >> 16 & 0xF) << 8 | flipx << 12 | flipy << 13 | transp << 14 | behind << 15
    return w(word0) + w(w1) + w(OBJ & 0xFFFF) + w(OBJCOL)


def background():
    s, c = [], []
    for i in range(BG):
        s += w(160)                       # reverse space = solid block
        c += [0x00, 6 if i % 2 else 14]   # blue / light blue stripes
    return s, c


def setup(scr, col, chars):
    assert len(scr) <= STEP and len(col) <= STEP
    scr = scr + [0x20, 0] * ((STEP - len(scr)) // 2)
    col = col + [0] * (STEP - len(col))
    load(SCR, scr * ROWS)                                     # every row its own copy
    load(COLRAM + COLOFS, col * ROWS)
    poke(0xFFD305D, [peek(0xFFD305D)[0] & 0x7F])              # HOTREG off: BASIC's VIC-II writes
                                                              # would recompute LINESTEP etc.
    poke(0xFFD3054, [peek(0xFFD3054)[0] | 1])                 # CHR16
    poke(0xFFD3058, [STEP & 0xFF, STEP >> 8])                 # LINESTEP
    poke(0xFFD305E, [chars & 0xFF])
    d063 = peek(0xFFD3063)[0]
    poke(0xFFD3060, [SCR & 0xFF, SCR >> 8 & 0xFF, SCR >> 16 & 0xFF,
                     (d063 & 0xC0) | ((chars >> 8) & 3) << 4 | (SCR >> 24 & 0x0F)])
    poke(0xFFD3064, [COLOFS & 0xFF, COLOFS >> 8])


def xctl(bit5, bit6=0):
    x = peek(XCTL)[0] & ~0x60
    poke(XCTL, [x | bit5 << 5 | bit6 << 6])


def v2obj(x, rel=0, y=0, h=ROWS, flipx=0, mask=0xFF):
    """16-byte list mode v2 object (docs/rrb_list_mode.md)"""
    return w(x | rel << 10) + w(y) + [mask, h, (OBJ >> 16 & 0xF) | flipx << 4, 0] \
        + w(OBJ & 0xFFFF) + w(OBJCOL) + w(0) + w(0)


def main():
    mon("t1", 0.3)          # stop the CPU: BASIC 65's interrupt and editor write VIC registers
    try:
        run()
    finally:
        if "--keep" not in sys.argv:
            xctl(0)
        mon("t0", 0.3)


def run():
    poke(OBJ, OBJ_S)
    poke(COLRAM + OBJCOL, OBJ_C)
    bs, bc = background()

    # B) reference: plain GOTOX tokens
    # A raster line ends visibly where its LAST glyph was painted (VIC-IV RRB rule).  List rows
    # keep the extent of their ordinary part; the plain reference gets the same by repainting
    # its last background glyph after the objects.
    ref_s = bs + gotox(50) + OBJ_S[:4] + gotox(150, 3, 1) + OBJ_S[:4] + gotox(250) + OBJ_S[:4] \
        + gotox(8 * (BG - 1)) + bs[-2:]
    ref_c = bc + [0x10, 0xFF] + OBJ_C[:4] + [0x10, 0xFF] + OBJ_C[:4] + [0x10, 0xFF] \
        + [0x40, 2, 0x40, 7] + [0x10, 0xFF] + bc[-2:]         # FLIPX: colour byte 0 bit 6
    xctl(0)
    setup(bs, bc, BG)                                         # same start state for every picture
    setup(ref_s, ref_c, len(ref_s) // 2)
    p_off, img_off = capture()
    xctl(1)
    p_ref, img_ref = capture()

    # A) list mode: LIST token after the background, then the entries (read from chip RAM)
    lst_s = bs + w(0xFFFC) + entry(50) + entry(150 | 1 << 12 | 3 << 13) \
        + entry(100 | 1 << 10, flipx=1) + w(0xFFFF)
    lst_c = bc + [0, 0]
    setup(bs, bc, BG)
    setup(lst_s, lst_c + [0] * (len(lst_s) - len(lst_c)), BG + 1)   # CHRCOUNT up to LIST
    p_lst, img_lst = capture()

    # C) list mode v2: plain rows, objects from the global table (copied by the VIC-IV per frame)
    tab = v2obj(50) + v2obj(100, rel=1) + v2obj(250, flipx=1)
    xctl(0)
    poke(TAB, tab)
    poke(0xFFD71F8, [TAB & 0xFF, TAB >> 8 & 0xFF, TAB >> 16 & 0xF, 3])
    poke(0xFFD71FC, [0])
    setup(bs, bc, BG)
    xctl(0, 1)
    p_v2, img_v2 = capture()

    from PIL import ImageChops

    def diff(a, b):
        d = ImageChops.difference(a, b)
        pts = [(i % a.size[0], i // a.size[0]) for i, p in enumerate(d.getdata()) if max(p) > 40]
        return len(pts), pts[:6]

    n1, e1 = diff(img_off, img_ref)
    n2, e2 = diff(img_lst, img_ref)
    n3, e3 = diff(img_v2, img_ref)
    print("reference, bit 5 off vs on: %d px differ %s" % (n1, e1))
    print("list mode vs reference:     %d px differ %s" % (n2, e2))
    print("list mode v2 vs reference:  %d px differ %s" % (n3, e3))
    print("captures: off %s, ref %s, list %s, v2 %s"
          % tuple(os.path.basename(p) for p in (p_off, p_ref, p_lst, p_v2)))
    # a handful of pixels differ between any two captures of the same picture (capture noise)
    print("RRB_LIST_TEST_%s" % ("PASS" if max(n1, n2, n3) <= NOISE else "FAIL"))


if __name__ == "__main__":
    main()
