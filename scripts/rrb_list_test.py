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
  D) v2, two-row full-colour objects at Y = 8k + 3 (height 2, stride = the object's row size)
     against per-row plain GOTOX rows: row k the top part (Y offset 3 down, mask $F8), row k+1
     the bottom part (5 up, mask $07) and the object's second row's top part, row k+2 its bottom
  E) v2 on a short row (20 background glyphs, then $FFFF; CHRCOUNT 40) against the plain short
     row with the objects (X 20; REL +40; X 100 FLIPX, all inside the glyphs' extent) written
     out before its $FFFF; rows are packed at their own length (a $FFFF row advances the next
     row by its length).  E2: the same with LINESTEP 256 (only the short-row advance places the
     rows): reference and v2 must still match E
  F) a VIC-II sprite (0: X 100, Y 10, solid 24x21) above the first text row, top border opened
     (TBDRPOS 0), captured with XCTL bit 6 off and on (255 objects, copied every frame at the
     top of the frame): the sprite's rows must be identical
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
OBJD, OBJDCOL = 0x48100, 0x6100                           # D: two-row object (rows 6 bytes apart)
FCM_G = 0x1400                                            # D: full-colour glyphs (data at $50000)
SPR_PTRS, SPR_DATA, SPR_Y = 0x1FF8, 0x1F00, 10            # F: sprite pointers / data (bank 0)
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


def setup(scr, col, chars, step=STEP, linestep=None):
    """scr/col: one row (every row its own copy) or a list of ROWS rows; rows step bytes apart"""
    rows_s = scr if isinstance(scr[0], list) else [scr] * ROWS
    rows_c = col if isinstance(col[0], list) else [col] * ROWS
    s, c = [], []
    for rs, rc in zip(rows_s, rows_c):
        assert len(rs) <= step and len(rc) <= step and (step - len(rs)) % 2 == 0
        s += rs + [0x20, 0] * ((step - len(rs)) // 2)
        c += rc + [0] * (step - len(rc))
    load(SCR, s)
    load(COLRAM + COLOFS, c)
    linestep = step if linestep is None else linestep
    poke(0xFFD305D, [peek(0xFFD305D)[0] & 0x7F])              # HOTREG off: BASIC's VIC-II writes
                                                              # would recompute LINESTEP etc.
    poke(0xFFD3031, [peek(0xFFD3031)[0] & ~8])                # V200, 25 rows of 8 lines x 2: the
    poke(0xFFD307B, [ROWS - 1])                               # V400 demos leave the VIC-IV in V400
    poke(0xFFD305B, [1])                                      # (HOTREG is off: set the three)
    poke(0xFFD3054, [peek(0xFFD3054)[0] | 1])                 # CHR16
    poke(0xFFD3058, [linestep & 0xFF, linestep >> 8])         # LINESTEP
    poke(0xFFD305E, [chars & 0xFF])
    d063 = peek(0xFFD3063)[0]
    poke(0xFFD3060, [SCR & 0xFF, SCR >> 8 & 0xFF, SCR >> 16 & 0xFF,
                     (d063 & 0xC0) | ((chars >> 8) & 3) << 4 | (SCR >> 24 & 0x0F)])
    poke(0xFFD3064, [COLOFS & 0xFF, COLOFS >> 8])


def xctl(bit5, bit6=0):
    x = peek(XCTL)[0] & ~0x60
    poke(XCTL, [x | bit5 << 5 | bit6 << 6])


def v2obj(x, rel=0, y=0, h=ROWS, flipx=0, mask=0xFF, obj=OBJ, objcol=OBJCOL, stride=0):
    """16-byte list mode v2 object (docs/rrb_list_mode.md)"""
    return w(x | rel << 10) + w(y) + [mask, h, (obj >> 16 & 0xF) | flipx << 4, 0] \
        + w(obj & 0xFFFF) + w(objcol) + w(stride) + w(0)


def v2table(tab):
    """object table at TAB, count, HOLD off (XCTL bit 6 must be off while the registers change)"""
    load(TAB, tab)
    poke(0xFFD71F8, [TAB & 0xFF, TAB >> 8 & 0xFF, TAB >> 16 & 0xF, len(tab) // 16])
    poke(0xFFD71FC, [0])


def parts(r, objs, rows_s, rows_c):
    """plain GOTOX tokens of the v2 objects (x, y, height) covering character row r: per object
    the top part of object row r - y//8 (Y offset y%8 down, mask $FF << y%8), then the bottom
    part of the object row above (8 - y%8 up, the complementary mask) -- the VIC-IV's order"""
    s, c = [], []
    for x, y, h in objs:
        k, o = r - y // 8, y % 8
        for row, yoff, neg, mask in ((k, o, 1, 0xFF << o & 0xFF), (k - 1, (8 - o) % 8, 0, (1 << o) - 1)):
            if 0 <= row < h and mask and (row == k or o):
                s += gotox(x, yoff, neg) + rows_s[row][:-2]
                c += [0x10 | (8 if mask != 0xFF else 0), mask] + rows_c[row][:-2]
    return s, c


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

    # D) v2 two-row objects at Y = 8k + 3 (full colour: the Y offsets matter) against plain rows
    d054 = peek(0xFFD3054)[0]
    for g in range(4):                                        # glyph g: every pixel row its colour
        poke(FCM_G * 64 + 64 * g, [1 + (3 * g + y) % 15 for y in range(8) for _ in range(8)])
    d_s = [w(FCM_G) + w(FCM_G + 1) + w(0xFFFF), w(FCM_G + 2) + w(FCM_G + 3) + w(0xFFFF)]
    d_c = [[0, 1, 0, 1, 0, 0]] * 2
    poke(OBJD, d_s[0] + d_s[1])                               # object row 1 = row 0 + 6 bytes
    poke(COLRAM + OBJDCOL, d_c[0] + d_c[1])
    d_objs = [(50, 8 * 2 + 3, 2), (150, 8 * 3 + 3, 2), (250, 8 * 12 + 3, 2)]   # rows 2-4, 3-5, 12-14
    ref_rows, ref_cols = [], []
    for r in range(ROWS):
        ps, pc = parts(r, d_objs, d_s, d_c)
        ref_rows.append(bs + ps)
        ref_cols.append(bc + pc)
    n_tok = max(len(x) for x in ref_rows) // 2
    for rs, rc in zip(ref_rows, ref_cols):                    # same CHRCOUNT for every row: pad
        while len(rs) // 2 < n_tok:                           # with GOTOX (paints nothing), then
            rs += gotox(8 * (BG - 1))                         # repaint the last background glyph
            rc += [0x10, 0xFF]                                # (the line's visible end, as in B)
        rs += gotox(8 * (BG - 1)) + bs[-2:]
        rc += [0x10, 0xFF] + bc[-2:]
    xctl(0)
    v2table(b"".join(bytes(v2obj(x, y=y, h=h, obj=OBJD, objcol=OBJDCOL, stride=6))
                     for x, y, h in d_objs))
    poke(0xFFD3054, [d054 | 4])                               # FCLRHI: glyphs > $FF full colour
    setup(ref_rows, ref_cols, n_tok + 2)
    p_dref, img_dref = capture()
    setup(bs, bc, BG)
    xctl(0, 1)
    p_dv2, img_dv2 = capture()
    xctl(0)
    poke(0xFFD3054, [d054])
    n4, e4 = diff(img_dv2, img_dref)
    print("D two-row objects vs rows:  %d px differ %s  (ref %s, v2 %s)"
          % (n4, e4, os.path.basename(p_dref), os.path.basename(p_dv2)))

    # E) v2 on a short row: the objects replace the row's $FFFF (rows packed at their length)
    # The objects stay inside the 20 glyphs' extent (X < 160): pixels no glyph paints are stale
    # raster buffer content, undefined in both pictures.  The reference repaints the last
    # background glyph after the objects so its line ends where the v2 row's does (B's trick).
    e_s, e_c = bs[:40] + w(0xFFFF), bc[:40] + [0, 0]
    eref_s = bs[:40] + gotox(20) + OBJ_S[:4] + gotox(60) + OBJ_S[:4] + gotox(100) + OBJ_S[:4] \
        + gotox(8 * 19) + bs[38:40] + w(0xFFFF)
    eref_c = bc[:40] + [0x10, 0xFF] + OBJ_C[:4] + [0x10, 0xFF] + OBJ_C[:4] + [0x10, 0xFF] \
        + [0x40, 2, 0x40, 7] + [0x10, 0xFF] + bc[38:40] + [0, 0]
    v2table(v2obj(20) + v2obj(40, rel=1) + v2obj(100, flipx=1))
    shots = {}
    for name, rs, rc, bit6, ls in (("ref", eref_s, eref_c, 0, None), ("v2", e_s, e_c, 1, None),
                                   ("ref256", eref_s, eref_c, 0, 256), ("v2_256", e_s, e_c, 1, 256)):
        xctl(0)
        setup(rs, rc, BG, step=len(rs), linestep=ls)
        xctl(0, bit6)
        shots[name] = capture()
    xctl(0)
    n5, e5 = diff(shots["v2"][1], shots["ref"][1])
    n6, e6 = diff(shots["ref256"][1], shots["ref"][1])
    n7, e7 = diff(shots["v2_256"][1], shots["ref"][1])
    print("E short row, v2 vs ref:     %d px differ %s  (%s)"
          % (n5, e5, " ".join(os.path.basename(p) for p, _ in shots.values())))
    print("E2 LINESTEP 256: ref %d px %s, v2 %d px %s differ (short-row advance)" % (n6, e6, n7, e7))

    # F) a sprite above the first text row while the v2 copy (255 objects) runs at the frame top
    regs = {a: peek(a)[0] for a in (0xFFD3000, 0xFFD3001, 0xFFD3010, 0xFFD3015, 0xFFD3017,
                                    0xFFD301C, 0xFFD301D, 0xFFD3027, 0xFFD3048, 0xFFD3049,
                                    0xFFD3055, 0xFFD3057, 0xFFD305F, 0xFFD306B, 0xFFD306C,
                                    0xFFD306D, 0xFFD306E)}
    keep = [peek(SPR_DATA + o, 16) for o in range(0, 64, 16)] + [peek(SPR_PTRS, 1)]
    try:
        xctl(0)
        setup(bs, bc, BG)
        v2table(b"".join(bytes(v2obj(2 * k, y=8 * 20, h=1)) for k in range(255)))
        poke(SPR_DATA, [0xFF] * 63 + [0])                     # solid 24 x 21
        poke(SPR_PTRS, [SPR_DATA // 64])
        poke(0xFFD306C, [SPR_PTRS & 0xFF, SPR_PTRS >> 8 & 0xFF, 0])   # 8-bit pointers, bank 0
        for a, m in ((0xFFD3010, 0xFE), (0xFFD3017, 0xFE), (0xFFD301C, 0xFE), (0xFFD301D, 0xFE),
                     (0xFFD3055, 0xFE), (0xFFD3057, 0xFE), (0xFFD305F, 0xFE), (0xFFD306B, 0xFE)):
            poke(a, [regs[a] & m])                            # sprite 0: plain VIC-II sprite
        poke(0xFFD3000, [100, SPR_Y])
        poke(0xFFD3027, [1])
        poke(0xFFD3048, [0, regs[0xFFD3049] & 0xF0])          # TBDRPOS 0: open the top border
        p_fns, img_fns = capture()                            # no sprite yet
        poke(0xFFD3015, [regs[0xFFD3015] | 1])
        p_foff, img_foff = capture()
        xctl(0, 1)
        p_fon, img_fon = capture()
    finally:
        xctl(0)
        for a, v in regs.items():
            poke(a, [v])
        for o, b in enumerate(keep[:4]):
            poke(SPR_DATA + 16 * o, list(b))
        poke(SPR_PTRS, list(keep[4]))
    dd = ImageChops.difference(img_foff, img_fns)
    rows = sorted({i // dd.size[0] for i, p in enumerate(dd.getdata()) if max(p) > 40})
    if rows:
        box = (0, rows[0], img_foff.size[0], rows[-1] + 1)
        n8, e8 = diff(img_fon.crop(box), img_foff.crop(box))
        print("F sprite rows %d-%d, bit 6 off vs on: %d px differ %s  (%s %s %s)"
              % (rows[0], rows[-1], n8, e8, os.path.basename(p_fns), os.path.basename(p_foff),
                 os.path.basename(p_fon)))
    else:
        n8 = NOISE + 1
        print("F sprite not visible (no difference with / without it): %s %s"
              % (os.path.basename(p_fns), os.path.basename(p_foff)))

    # a handful of pixels differ between any two captures of the same picture (capture noise)
    print("RRB_LIST_TEST_%s" % ("PASS" if max(n1, n2, n3, n4, n5, n6, n7, n8) <= NOISE else "FAIL"))


if __name__ == "__main__":
    main()
