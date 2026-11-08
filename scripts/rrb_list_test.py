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
  G) V400 (50 rows, one line per glyph row), fast RRB, 80 background glyphs: v2 two-row objects
     (case D's) at X 0, 10, 600 at Y = 8k + 3 (alone, and three in one row) against plain GOTOX
     rows painted with XCTL bit 5 on (the list mode lead makes them complete); G2: 15 one-glyph
     two-row objects, 30 parts in one row.  The late-pixel counter (23) must stay 0 with the
     lead; without it the plain reference is late on its badlines (printed, expected > 0).
  H) three full-colour layers per row with the fast token path (XCTL bit 7, with 4 and 5):
     H1 3 x 81 glyphs on DBLRR's schedule (the old path completes there), bit 7 off == on, and
     V200 one line per painting with bit 7 (counters); H2 V400 3 x 41 glyphs, bit 7 off == on; H3 V200 and V400 3 x 81 glyphs
     + 12 v2 object parts in one row == the parts as plain tokens; counter 25 < 2590, 23 == 0.
  J) 64 one-glyph v2 objects on one row of 48 tokens (bit 7): timing in V400 (counter 25 <
     2590 for the pile's badline, prints 26 and 10; sparse rows: no pixel compare), and pixels
     over an opaque base on DBLRR's schedule: v2 == plain parts
  I) list mode v2 collisions: objects placed to collide / not collide (groups, mask 0, group 0,
     negative Y, REL, width, height, touching edges); the table $FFD71A0-BF, the partners
     ($FFD71C1/C2), the count ($FFD71C3) and $FFD71C0 against a model; IRQ pending with the
     enable set (the IRQ itself is tested from BASIC)
  G3) the same as G with DBLRR ($D051 = $C0) on its schedule: V400 timing with 25 rows (paints
     every other line, each painting shown on two lines), the two-line list mode lead.
     Restores $D031/$D07B/$D05B/$D054/$D051 and XCTL.
Stops the CPU (monitor t1) while it runs, so BASIC 65 cannot touch the VIC-IV; leaves the
VIC-IV changed (reset afterwards).

    scripts/rrb_list_test.py [--keep] [--bit7]
      --keep: leave picture A on screen
      --bit7: run every case with XCTL bits 4 + 7 (fast RRB + fast token path) kept on; the cases
              keep the other XCTL bits they do not set (H switches bit 7 off itself where it
              compares with the old path).  Expected: the same PASS.
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


def background(n=BG):
    s, c = [], []
    for i in range(n):
        s += w(160)                       # reverse space = solid block
        c += [0x00, 6 if i % 2 else 14]   # blue / light blue stripes
    return s, c


def setup(scr, col, chars, step=STEP, linestep=None, rows=ROWS, colofs=COLOFS, v400=False):
    """scr/col: one row (every row its own copy) or a list of rows rows; rows step bytes apart.
    v400: 2 x 25 rows of one line per glyph row ($D031 bit 3, $D07B, $D05B 0), else V200"""
    rows_s = scr if isinstance(scr[0], list) else [scr] * rows
    rows_c = col if isinstance(col[0], list) else [col] * rows
    s, c = [], []
    for rs, rc in zip(rows_s, rows_c):
        assert len(rs) <= step and len(rc) <= step and (step - len(rs)) % 2 == 0
        s += rs + [0x20, 0] * ((step - len(rs)) // 2)
        c += rc + [0] * (step - len(rc))
    load(SCR, s)
    load(COLRAM + colofs, c)
    linestep = step if linestep is None else linestep
    poke(0xFFD305D, [peek(0xFFD305D)[0] & 0x7F])              # HOTREG off: BASIC's VIC-II writes
                                                              # would recompute LINESTEP etc.
    if v400:                                                  # as the B65 V400 demo does it
        poke(0xFFD3031, [peek(0xFFD3031)[0] | 8])
        poke(0xFFD307B, [rows - 1])
        poke(0xFFD305B, [0])
    else:
        poke(0xFFD3031, [peek(0xFFD3031)[0] & ~8])            # V200, 25 rows of 8 lines x 2: the
        poke(0xFFD307B, [rows - 1])                           # V400 demos leave the VIC-IV in V400
        poke(0xFFD305B, [1])                                  # (HOTREG is off: set the three)
    poke(0xFFD3054, [peek(0xFFD3054)[0] | 1])                 # CHR16
    poke(0xFFD3058, [linestep & 0xFF, linestep >> 8])         # LINESTEP
    poke(0xFFD305E, [chars & 0xFF])
    d063 = peek(0xFFD3063)[0]
    poke(0xFFD3060, [SCR & 0xFF, SCR >> 8 & 0xFF, SCR >> 16 & 0xFF,
                     (d063 & 0xC0) | ((chars >> 8) & 3) << 4 | (SCR >> 24 & 0x0F)])
    poke(0xFFD3064, [colofs & 0xFF, colofs >> 8])


def xctl(bit5, bit6=0):
    x = peek(XCTL)[0] & ~0x60
    poke(XCTL, [x | bit5 << 5 | bit6 << 6])


def v2obj(x, rel=0, y=0, h=ROWS, flipx=0, mask=0xFF, obj=OBJ, objcol=OBJCOL, stride=0, grp=0,
          width=0, yrel=0):
    """16-byte list mode v2 object (docs/rrb_list_mode.md); grp: collision group mask (+7),
    width: collision box width in pixels (+14, 0 = 8)"""
    return w(x | rel << 10) + w((y & 0x3FF) | yrel << 10) + [mask, h, (obj >> 16 & 0xF) | flipx << 4,
                                                           grp] \
        + w(obj & 0xFFFF) + w(objcol) + w(stride) + [width, 0]


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
    x_orig = peek(XCTL)[0]
    try:
        if "--bit7" in sys.argv:            # every case with fast RRB + the fast token path
            poke(XCTL, [x_orig | 0x90])
        run()
    finally:
        if "--keep" not in sys.argv:
            xctl(0)
            poke(XCTL, [x_orig])
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

    # G) V400 + fast RRB: objects at the left edge on the badlines (first raster line budget)
    ok_g = case_g(d_s, d_c)
    # H) three full-colour layers per row with the fast token path (XCTL bit 7)
    ok_h = case_h(d_s, d_c)
    # I) list mode v2 collisions ($FFD71A0-C3)
    ok_i = case_i()
    # J) a pile of 64 objects on one row (expansion time)
    ok_j = case_j()

    # a handful of pixels differ between any two captures of the same picture (capture noise)
    print("RRB_LIST_TEST_%s" % ("PASS" if max(n1, n2, n3, n4, n5, n6, n7, n8) <= NOISE and ok_g
                                and ok_h and ok_i and ok_j else "FAIL"))


FCM_H = 0x1410                                            # H: full-colour layer glyphs ($50400)


def case_h(d_s, d_c):
    """H: rows of three full-colour layers (GOTOX 0 opaque, GOTOX 3 and 5 transparent; FLIPX /
    FLIPY mixed in; glyph pixels 1..15, or 0 = transparent) with the fast token path (XCTL bit
    7, with bits 4 and 5: fast RRB, the list mode lead):
      H1 3 x 81 glyphs on DBLRR's schedule (V400 timing, 25 rows, $D051 = $C0: two lines per
         painting, so the old path completes): bit 7 off == on; and V200 (one line per
         painting) with bit 7: counters only;
      H2 V400 (50 rows), 3 x 41 glyphs: bit 7 off == on (the old path completes in one line);
      H3 V200 and V400, 3 x 81 glyphs + 6 two-row v2 objects at Y = 8*10 + 3 (12 parts in row
         11), bits 6 + 7  ==  the parts as plain GOTOX tokens, bits 5 + 7;
    and for every bit 7 picture counter 23 (late) == 0 and 25 (longest painting) < 2590."""
    from PIL import ImageChops
    regs = {a: peek(a)[0] for a in (0xFFD3031, 0xFFD307B, 0xFFD305B, 0xFFD3054, 0xFFD3051)}
    x0 = peek(XCTL)[0]
    for g in range(4):                                        # glyph g: pixel (x, y)
        poke(FCM_H * 64 + 64 * g, [0 if (x + y + g) % 5 == 0 else 1 + (3 * g + y + 5 * x) % 15
                                   for y in range(8) for x in range(8)])
    objcol = 0x7E00
    poke(COLRAM + objcol, d_c[0] + d_c[1])

    def rows3(n_glyphs, nrows):
        """three layers per row (screen, colour lists per row)"""
        rs, rc = [], []
        for r in range(nrows):
            s_, c_ = [], []
            for ly in range(3):
                s_ += gotox((0, 3, 5)[ly])
                c_ += [0x10 if ly == 0 else 0x90, 0xFF]
                for i in range(n_glyphs):
                    s_ += w(FCM_H + (i + r + ly) % 4)
                    c_ += [(0x40 if (i + r) % 5 == 1 else 0) | (0x80 if (i + r) % 7 == 2 else 0),
                           1 + (ly * 5 + r + i) % 15]
            rs.append(s_)
            rc.append(c_)
        return rs, rc

    def diff(a, b):
        d = ImageChops.difference(a, b)
        pts = [(i % a.size[0], i // a.size[0]) for i, p in enumerate(d.getdata()) if max(p) > 40]
        return len(pts), pts[:6]

    def shot(rs, rc, chars, bits, nrows, v400, d051=0x80):
        poke(XCTL, [x0 & ~0xE0 | 0x10])                       # list mode / bit 7 off while set up
        poke(0xFFD3051, [d051])
        setup(rs, rc, chars, step=640, rows=nrows, colofs=0, v400=v400)
        poke(XCTL, [x0 & ~0xE0 | 0x10 | bits])
        return late_capture()

    def counters_ok(name, c):
        good = c[23] == 0 and c[25] < 2590
        print("  %s: late %d (must be 0), longest painting %d cycles (must be < 2590)%s"
              % (name, c[23], c[25], "" if good else "  <-- FAIL"))
        return good

    ok = True
    try:
        poke(0xFFD3054, [regs[0xFFD3054] | 4])                # FCLRHI: glyphs > $FF full colour
        # H1: on DBLRR's schedule both paths complete (the old one needs ~4400 cycles per
        # painting); V200 with one line per painting only with bit 7 (its picture shows each
        # glyph row one raster line earlier than DBLRR's, so it is checked by the counters)
        rs, rc = rows3(81, 25)
        p_db0 = shot(rs, rc, 246, 0x20, 25, True, 0xC0)       # old path, DBLRR schedule
        p_db1 = shot(rs, rc, 246, 0xA0, 25, True, 0xC0)       # fast token path, same
        n, e = diff(p_db0[1], p_db1[1])
        print("H1 3 x 81 full-colour glyphs on the DBLRR schedule, bit 7 off vs on: %d px differ "
              "%s; old path longest painting %d, bit 7 %d (< 5180)  (%s %s)"
              % (n, e, p_db0[2][25], p_db1[2][25], os.path.basename(p_db0[0]),
                 os.path.basename(p_db1[0])))
        ok = ok and n <= NOISE and p_db1[2][25] < 5180 and p_db1[2][23] == 0
        p_on = shot(rs, rc, 246, 0xA0, 25, False)             # V200, one line per painting
        ok = counters_ok("H1 V200 bit 7, one line per painting", p_on[2]) and ok
        # H2
        rs2, rc2 = rows3(41, 50)
        p_off = shot(rs2, rc2, 126, 0x20, 50, True)
        p_on2 = shot(rs2, rc2, 126, 0xA0, 50, True)
        n, e = diff(p_off[1], p_on2[1])
        print("H2 V400 3 x 41 full-colour glyphs, bit 7 off vs on: %d px differ %s; old path "
              "longest painting %d" % (n, e, p_off[2][25]))
        ok = counters_ok("H2 bit 7", p_on2[2]) and ok and n <= NOISE
        # H3
        objs = [(40 + 100 * k, 83, 2) for k in range(6)]
        for v400, nrows in ((False, 25), (True, 50)):
            name = "H3 V400" if v400 else "H3 V200"
            rs, rc = rows3(81, nrows)
            ps_rows, pc_rows = [], []
            for r in range(nrows):
                ps, pc = parts(r, objs, d_s, d_c)
                # the line's visible end stays the last layer's (repaint its last glyph after
                # the parts, as in D)
                ps_rows.append(rs[r] + ps + gotox(5 + 8 * 80) + rs[r][-2:])
                pc_rows.append(rc[r] + pc + [0x90, 0xFF] + rc[r][-2:])
            n_tok = max(len(x) for x in ps_rows) // 2
            for a_, b_ in zip(ps_rows, pc_rows):                # same CHRCOUNT: pad in front of
                while len(a_) // 2 < n_tok:                     # the repaint with GOTOX tokens
                    a_[-4:-4] = gotox(8 * 79)
                    b_[-4:-4] = [0x10, 0xFF]
            p_ref = shot(ps_rows, pc_rows, n_tok, 0xA0, nrows, v400)
            poke(XCTL, [x0 & ~0xE0 | 0x10])
            v2table(b"".join(bytes(v2obj(x, y=y, h=h, obj=OBJD, objcol=objcol, stride=6))
                             for x, y, h in objs))
            p_v2 = shot(rs, rc, 246, 0xC0, nrows, v400)
            n, e = diff(p_v2[1], p_ref[1])
            print("%s 3 x 81 glyphs + 12 v2 object parts vs plain parts (bit 7): %d px differ %s"
                  % (name, n, e))
            ok = counters_ok(name + " v2", p_v2[2]) and counters_ok(name + " ref", p_ref[2]) \
                and ok and n <= NOISE
    finally:
        poke(XCTL, [x0])
        for a, v in regs.items():
            poke(a, [v])
    return ok


COL = 0xFFD71A0                                           # I: collision table, $C0-$C3 at +$20


def col_model(objs):
    """expected collision result for objects (x, xrel, y, yrel, mask, h, grp, width): REL X/Y
    from the previous object, Y signed 10 bit, box X..X+W-1 x Y..Y+8H-1; collide if both draw
    masks and group masks are non-zero, the groups share a bit and the boxes overlap; the first
    partner is the lowest-numbered one"""
    boxes, ax, ay = [], 0, 0
    for x, xrel, y, yrel, mask, h, grp, width in objs:
        ax = (ax + x) % 1024 if xrel else x % 1024
        ay = (ay + y) % 1024 if yrel else y % 1024
        y0 = ay - 1024 if ay >= 512 else ay
        hh = (h % 64) or 1
        boxes.append((ax, ax + (width or 8) - 1, y0, y0 + 8 * hh - 1, grp, mask != 0 and grp != 0))
    part = [255] * 256
    for i, a in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            if a[5] and b[5] and a[4] & b[4] and a[0] <= b[1] and b[0] <= a[1] \
                    and a[2] <= b[3] and b[2] <= a[3]:
                part[i] = min(part[i], j)
                part[j] = min(part[j], i)
    bits = [p != 255 for p in part]
    return bits, part, sum(bits)


def case_i():
    """I: list mode v2 collisions.  Objects placed to collide / not collide (groups, mask 0,
    group 0, negative Y, REL, width, height, touching edges); after two frames the table at
    $FFD71A0-BF, the partners ($FFD71C1/C2), the count ($FFD71C3) and the status ($FFD71C0: bit 0
    a collision, bit 7 IRQ pending with bit 1 enabled) must match the model; then a table where
    nothing collides; the IRQ enable is cleared again at the end (the CPU is stopped)."""
    import time
    x0 = peek(XCTL)[0]
    cases = (
        ("I colliding", [(10, 0, 10, 0, 255, 1, 1, 0), (14, 0, 14, 0, 255, 1, 1, 0),
                         (14, 0, 14, 0, 255, 1, 2, 0), (16, 0, 16, 0, 255, 1, 3, 0),
                         (12, 0, 12, 0, 0, 1, 1, 0), (2, 1, 0, 1, 255, 1, 1, 0),
                         (300, 0, -5, 0, 255, 1, 4, 0), (300, 0, 2, 0, 255, 1, 4, 0),
                         (300, 0, 3, 0, 255, 1, 0, 0), (500, 0, 100, 0, 255, 1, 8, 1),
                         (501, 0, 100, 0, 255, 1, 8, 0), (499, 0, 107, 0, 255, 1, 8, 2),
                         (700, 0, 200, 0, 255, 4, 16, 255), (950, 0, 231, 0, 255, 1, 16, 0),
                         (955, 0, 231, 0, 255, 1, 16, 0), (1020, 0, 500, 0, 255, 63, 32, 8),
                         (1, 1, 503, 0, 255, 1, 32, 0)]),
        ("I apart", [(40 * k, 0, 8 * k, 0, 255, 1, 255, 8) for k in range(20)]))
    ok = True
    try:
        poke(COL + 0x20, [0x01])                              # IRQ off, clear bits 0 and 7
        for name, objs in cases:
            xctl(0)
            v2table(b"".join(bytes(v2obj(x, rel=xr, y=y, yrel=yr, mask=m, h=h, grp=g, width=wd))
                             for x, xr, y, yr, m, h, g, wd in objs))
            xctl(0, 1)
            time.sleep(0.2)                                   # a few frames: a pass each
            poke(COL + 0x20, [0x01])                          # clear, then one more pass
            time.sleep(0.1)
            got = list(peek(COL, 16)) + list(peek(COL + 16, 16))
            st = peek(COL + 0x20, 4)
            bits, part, count = col_model(objs)
            exp = [sum(1 << b for b in range(8) if bits[8 * n + b]) for n in range(32)]
            parts = []
            for k in range(len(objs) + 2):
                poke(COL + 0x21, [k])
                parts.append(peek(COL + 0x22)[0])
            good = got == exp and st[3] == count and parts == part[:len(objs) + 2] \
                and (st[0] & 1) == (1 if count else 0) and (st[0] & 0x80) == (0x80 if count else 0)
            print("%s: %d objects collided (expected %d), status $%02X, table %s, partners %s%s"
                  % (name, st[3], count, st[0], "ok" if got == exp else "differs %s" % got[:4],
                     parts[:len(objs)], "" if good else "  <-- FAIL (expected %s)" % part[:len(objs)]))
            ok = ok and good
        # IRQ enable: bit 1 reads back, bit 7 pending once a colliding frame came
        xctl(0)
        v2table(b"".join(bytes(v2obj(x, rel=xr, y=y, yrel=yr, mask=m, h=h, grp=g, width=wd))
                         for x, xr, y, yr, m, h, g, wd in cases[0][1]))
        xctl(0, 1)
        poke(COL + 0x20, [0x03])                              # enable, clear
        time.sleep(0.1)
        st = peek(COL + 0x20)[0]
        print("I IRQ enabled: status $%02X (expected $83: pending, enabled, a collision)" % st)
        ok = ok and st == 0x83
    finally:
        poke(COL + 0x20, [0x01])                              # IRQ off, cleared
        xctl(0)
        poke(XCTL, [x0])
    return ok


def case_j():
    """J: 64 one-glyph v2 objects on one row (a pile on the floor); rows of 48 tokens = 6 groups
    of 4 x (GOTOX, one full-colour glyph), fast RRB + fast token path.
      J timing: V400, one line per painting, no base: counter 25 (longest painting: the pile's
        badline = fetch + expansion of 64 parts + painting) < 2590, 23 (late) == 0; prints 26
        (first object part) and 10 (expansion).  No pixel comparison: the rows are sparse, so
        their gaps show the raster buffer's history, which differs between two different rows.
      J pixels: the same rows over an opaque 80-glyph base (the background() stripes) on DBLRR's
        V400 schedule (25 rows, two lines per painting: base + rows + 64 parts need more than one
        line), v2 (bits 6 + 7) == the object parts as plain GOTOX tokens (bits 5 + 7)."""
    from PIL import ImageChops
    from rrb_diag import read_counter
    regs = {a: peek(a)[0] for a in (0xFFD3031, 0xFFD307B, 0xFFD305B, 0xFFD3054, 0xFFD3051)}
    x0 = peek(XCTL)[0]
    for g in range(4):
        poke(FCM_H * 64 + 64 * g, [0 if (x + y + g) % 5 == 0 else 1 + (3 * g + y + 5 * x) % 15
                                   for y in range(8) for x in range(8)])
    obj, objcol = 0x48300, 0x7E20
    poke(obj, w(FCM_H + 1) + w(0xFFFF))
    poke(COLRAM + objcol, [0, 1, 0, 0])
    o_s, o_c = [w(FCM_H + 1) + w(0xFFFF)], [[0, 1, 0, 0]]
    objs = [(10 * k, 80, 1) for k in range(64)]                # all in row 10
    bs, bc = background(80)

    def rows(nrows, base):
        rs, rc = [], []
        for r in range(nrows):
            s_, c_ = (list(bs), list(bc)) if base else ([], [])
            for g in range(6):
                for t in range(4):
                    s_ += gotox(100 * g + 20 * t) + w(FCM_H + t)
                    c_ += [0x10, 0xFF, 0, 1 + (g + t + r) % 15]
            rs.append(s_)
            rc.append(c_)
        return rs, rc

    def with_parts(rs, rc):
        ps_rows, pc_rows = [], []
        for r in range(len(rs)):
            ps, pc = parts(r, objs, o_s, o_c)
            ps_rows.append(rs[r] + ps)
            pc_rows.append(rc[r] + pc)
        n_tok = max(len(x) for x in ps_rows) // 2
        for a_, b_ in zip(ps_rows, pc_rows):
            while len(a_) // 2 < n_tok:                        # GOTOX: paints nothing
                a_ += gotox(560)
                b_ += [0x10, 0xFF]
        return ps_rows, pc_rows, n_tok

    def diff(a, b):
        d = ImageChops.difference(a, b)
        pts = [(i % a.size[0], i // a.size[0]) for i, p in enumerate(d.getdata()) if max(p) > 40]
        return len(pts), pts[:6]

    ok = True
    try:
        poke(0xFFD3054, [regs[0xFFD3054] | 4])
        # J timing: V400 one line per painting, 48-token rows, v2
        poke(0xFFD3051, [0x80])
        poke(XCTL, [x0 & ~0xE0 | 0x10])
        v2table(b"".join(bytes(v2obj(x, y=y, h=h, obj=obj, objcol=objcol)) for x, y, h in objs))
        rs, rc = rows(50, False)
        setup(rs, rc, 48, step=512, rows=50, colofs=0, v400=True)
        poke(XCTL, [x0 & ~0xE0 | 0xD0])                        # v2: bits 4 + 6 + 7
        p_t = late_capture()
        exp_max = read_counter(10)
        c = p_t[2]
        good = c[23] == 0 and c[25] < 2590
        print("J timing, 64 objects on a 48-token row (V400, bit 7): longest painting %d (< 2590), "
              "first object part %d, expansion %d, late %d%s  (%s)" % (c[25], c[26], exp_max, c[23],
              "" if good else "  <-- FAIL", os.path.basename(p_t[0])))
        ok = good
        # J pixels: opaque base, DBLRR's V400 schedule (25 rows), v2 vs plain parts
        rs, rc = rows(25, True)
        ps_rows, pc_rows, n_tok = with_parts(rs, rc)
        poke(XCTL, [x0 & ~0xE0 | 0x10])
        poke(0xFFD3051, [0xC0])
        setup(ps_rows, pc_rows, n_tok, step=512, rows=25, colofs=0, v400=True)
        poke(XCTL, [x0 & ~0xE0 | 0xB0])                        # plain parts: bits 4 + 5 + 7
        p_ref = late_capture()
        poke(XCTL, [x0 & ~0xE0 | 0x10])
        setup(rs, rc, 128, step=512, rows=25, colofs=0, v400=True)
        poke(XCTL, [x0 & ~0xE0 | 0xD0])                        # v2: bits 4 + 6 + 7
        p_v2 = late_capture()
        n, e = diff(p_v2[1], p_ref[1])
        cv, cr = p_v2[2], p_ref[2]
        good = n <= NOISE and cv[23] == 0 and cr[23] == 0 and cv[25] < 5180 and cr[25] < 5180
        print("J pixels, opaque base + 48 tokens + 64 objects (DBLRR schedule), v2 vs plain parts: "
              "%d px differ %s; longest painting v2 %d, ref %d (< 5180), late %d/%d%s  (%s %s)"
              % (n, e, cv[25], cr[25], cv[23], cr[23], "" if good else "  <-- FAIL",
                 os.path.basename(p_v2[0]), os.path.basename(p_ref[0])))
        ok = ok and good
    finally:
        poke(XCTL, [x0])
        for a, v in regs.items():
            poke(a, [v])
    return ok


def late_capture():
    """capture with the list mode counters cleared before; returns the late-pixel count (23),
    the longest painting (25: line start -> end of the row's tokens, pixel cycles; a painting
    cut off by the next line start counts ~2592) and the latest first object (26)"""
    from rrb_diag import read_counter, CLR
    poke(CLR, [0])
    p, img = capture()
    return p, img, {n: read_counter(n) for n in (23, 25, 26)}


def case_g(d_s, d_c):
    """G / G2: V400 (50 rows of one line per glyph row), 80 background glyphs, fast RRB, v2
    two-row objects at Y = 8k + 3 (G: case D's object, X 0, 10 and 600, alone and three in one
    row; G2: 15 one-glyph objects, 30 parts in one row).  The reference is the same picture with
    plain GOTOX rows (80 background glyphs, the parts, a repaint of the last background glyph);
    painted with XCTL bit 5 on (no LIST token: only the list mode lead) it is complete on every
    line, so the whole screen is compared.  The late-pixel counter must stay 0 for the
    reference with the lead and for v2; the reference with bits 5/6 off is incomplete on its
    badlines (objects painted after 80 glyphs and the row fetch): informational."""
    from PIL import ImageChops
    regs = {a: peek(a)[0] for a in (0xFFD3031, 0xFFD307B, 0xFFD305B, 0xFFD3054, 0xFFD3051)}
    x0 = peek(XCTL)[0]
    bs, bc = background(80)
    obj_e, objcol_g = 0x48200, 0x7E00                         # G2 object; colour of G's objects
    poke(obj_e, w(FCM_G) + w(0xFFFF) + w(FCM_G + 2) + w(0xFFFF))      # one glyph per object row
    poke(COLRAM + objcol_g, d_c[0] + d_c[1])                  # G: case D's object (stride 6)
    poke(COLRAM + objcol_g + 0x10, [0, 1, 0, 0, 0, 1, 0, 0])  # G2 (stride 4)
    e_s, e_c = [w(FCM_G) + w(0xFFFF), w(FCM_G + 2) + w(0xFFFF)], [[0, 1, 0, 0]] * 2
    # name, objects, object rows (screen, colour), data, colour, stride, rows, $D051
    cases = (("G", [(0, 83, 2), (10, 163, 2), (600, 243, 2), (0, 323, 2), (10, 323, 2),
                    (600, 323, 2)], d_s, d_c, OBJD, objcol_g, 6, 50, 0x80),
             ("G2", [(42 * k, 163, 2) for k in range(15)], e_s, e_c, obj_e, objcol_g + 0x10, 4,
              50, 0x80),
             # G3: DBLRR ($D051 bit 6) on its own schedule: V400 timing with 25 rows (a painting
             # every other line, shown on two lines: the V200 look with twice the RRB time);
             # list mode uses the two-line lead there
             ("G3", [(0, 83, 2), (10, 163, 2), (600, 43, 2), (0, 123, 2), (10, 123, 2),
                    (600, 123, 2)], d_s, d_c, OBJD, objcol_g, 6, 25, 0xC0))
    ok = True
    try:
        poke(0xFFD3054, [regs[0xFFD3054] | 4])                # FCLRHI: glyphs > $FF full colour
        for name, objs, os_, oc_, oaddr, ocol, stride, nrows, d051 in cases:
            poke(0xFFD3051, [d051])                           # NORRDEL (bit 7) + DBLRR (bit 6)
            rows_s, rows_c = [], []
            for r in range(nrows):
                ps, pc = parts(r, objs, os_, oc_)
                rows_s.append(bs + ps)
                rows_c.append(bc + pc)
            n_tok = max(len(x) for x in rows_s) // 2
            for rs, rc in zip(rows_s, rows_c):                # same CHRCOUNT for every row (D)
                while len(rs) // 2 < n_tok:
                    rs += gotox(8 * 79)
                    rc += [0x10, 0xFF]
                rs += gotox(8 * 79) + bs[-2:]                 # the line's visible end: 640
                rc += [0x10, 0xFF] + bc[-2:]
            # 50 rows x 512 bytes: screen $40000-$463FF, colour RAM $0000-$63FF
            shots = {}
            for shot, bit5, bit6 in (("off", 0, 0), ("ref", 1, 0), ("v2", 0, 1)):
                poke(XCTL, [x0 & ~0x60 | 0x10])               # fast RRB, list mode off
                if bit6:
                    v2table(b"".join(bytes(v2obj(x, y=y, h=h, obj=oaddr, objcol=ocol,
                                                 stride=stride)) for x, y, h in objs))
                    setup(bs, bc, 80, step=512, rows=nrows, colofs=0, v400=True)
                else:
                    setup(rows_s, rows_c, n_tok + 2, step=512, rows=nrows, colofs=0, v400=True)
                poke(XCTL, [x0 & ~0x60 | 0x10 | bit5 << 5 | bit6 << 6])
                shots[shot] = late_capture()
            poke(XCTL, [x0 & ~0x60 | 0x10])

            def diff(a, b):
                d = ImageChops.difference(a, b)
                pts = [(i % a.size[0], i // a.size[0]) for i, p in enumerate(d.getdata())
                       if max(p) > 40]
                return len(pts), pts[:6]
            n, e = diff(shots["v2"][1], shots["ref"][1])
            n0, e0 = diff(shots["off"][1], shots["ref"][1])
            cr, cv, co = shots["ref"][2], shots["v2"][2], shots["off"][2]
            print("%s v2 vs ref (lead): %d px differ %s; late pixels ref %d, v2 %d (must be 0); "
                  "longest painting ref %d, v2 %d cycles (must be < 2590, DBLRR 5180: none cut off); first "
                  "object of a v2 line at most %d cycles after its line start"
                  % (name, n, e, cr[23], cv[23], cr[25], cv[25], cv[26]))
            print("%s ref without the lead vs with: %d px differ %s, late pixels %d (expected > 0:"
                  " the plain badlines are late)  (%s)" % (name, n0, e0, co[23],
                  " ".join(os.path.basename(p) for p, _, _ in shots.values())))
            limit = 5180 if d051 & 0x40 else 2590             # DBLRR: two lines per painting
            ok = ok and n <= NOISE and cr[23] == 0 and cv[23] == 0 and cr[25] < limit \
                and cv[25] < limit
    finally:
        poke(XCTL, [x0])
        for a, v in regs.items():
            poke(a, [v])
    return ok


if __name__ == "__main__":
    main()
