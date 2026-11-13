"""lxcore/display.py -- a character display for the menu: cells (character + attribute) in a grid.

The menu engine draws into a Screen; Screen.flush(port) hands the changed cells to a display port
(the core adapter decides where they go: the Amiga's M2M overlay RAM, or another core's overlay).
Window control (show the menu window, the full screen, nothing) is the port's too:
    port.write_cells(kind, first_cell, values)     kind 'chars' or 'attrs'
    port.window(x, y, dx, dy) / port.window(None)  show a window of the grid / hide it

Characters are code points of the M2M font (Anikki, CP437 order: 201 = double top-left corner,
196 = horizontal line, 7 = bullet ...); attributes: bit 7 inverse, bits 5..3 background RGB, bits
2..0 foreground RGB.  A display without these glyphs or colours maps them.
"""

DX, DY = 45, 36                     # default grid (the M2M overlay: 720 x 576 in 16 x 16 cells)

# attributes and characters (M2M/rom/sysdef.asm, font Anikki 16x16)
SA_STD, SA_STD_INV, SA_TTLE, SA_TTLE_INV = 0x0B, 0x8B, 0x0E, 0x8E
FC_TL, FC_SH, FC_TR, FC_SV, FC_BL, FC_BR = 201, 205, 187, 186, 200, 188
FC_HE_LEFT, FC_HE_RIGHT = 185, 204
NC_SH, NC_VE_LEFT, NC_VE_RIGHT = 196, 199, 182
DIR_L, DIR_R = 17, 16
SEL_MULTI, SEL_SINGLE = 7, 61
LD_PROGRESS = 254

_GLYPHS = {0: ' ', 201: '+', 187: '+', 200: '+', 188: '+', 205: '=', 186: '|', 196: '-',
           199: '|', 182: '|', 7: '*', 16: '>', 17: '<', 185: '[', 204: ']', 254: '#'}


def glyph(c):
    """a cell as plain text (tests, logs)"""
    if c in _GLYPHS:
        return _GLYPHS[c]
    return chr(c) if 32 <= c < 127 else '?'


class Screen:
    def __init__(self, dx=45, dy=36):
        global DX, DY
        DX, DY = dx, dy                 # one display geometry per process (the overlay's)
        self.dx, self.dy = dx, dy
        self.ch = [0] * (DX * DY)
        self.at = [SA_STD] * (DX * DY)
        self.sent_ch = [None] * (DX * DY)
        self.sent_at = [None] * (DX * DY)
        self.x = self.y = self.ilx = 0

    # SCR$CLR
    def clr(self):
        self.ch = [0] * (DX * DY)
        self.at = [SA_STD] * (DX * DY)
        self.x = self.y = self.ilx = 0

    # SCR$CLRINNER (main window = the whole screen)
    def clrinner(self):
        for y in range(1, DY - 1):
            for x in range(1, DX - 1):
                self.ch[y * DX + x] = 0
                self.at[y * DX + x] = SA_STD
        self.x = self.y = 1

    def gotoxy(self, x, y):
        self.x, self.y = x, y

    # SCR$PRINTSTR: "\n" (backslash + n, as config.vhd strings carry it) or CR LF = new line at
    # the inner left x; "<" / ">" are shown as the directory brackets
    def printstr(self, s):
        off = self.y * DX + self.x
        i = 0
        while i < len(s):
            c = ord(s[i])
            i += 1
            if c in (0x0D, 0x5C):
                n = ord(s[i]) if i < len(s) else 0
                i += 1
                if n in (0x0A, ord('n')):
                    self.x = self.ilx
                    self.y += 1
                    off = self.y * DX + self.x
                    continue
                for v in (c, n):
                    if 0 <= off < DX * DY:
                        self.ch[off] = v
                    off += 1
                continue                    # (the firmware does not move the x cursor here)
            if c == ord('<'):
                c = DIR_L
            elif c == ord('>'):
                c = DIR_R
            if 0 <= off < DX * DY:
                self.ch[off] = c & 0xFF
            off += 1
            self.x += 1

    def printxy(self, s, x, y):
        sx, sy = self.x, self.y
        self.x, self.y = x, y
        self.printstr(s)
        self.x, self.y = sx, sy

    # SCR$PRINTFRAME
    def frame(self, x, y, dx, dy):
        self.ilx = x + 1
        self.x, self.y = x + 1, y + 1
        o = y * DX + x
        self.ch[o] = FC_TL
        for i in range(1, dx - 1):
            self.ch[o + i] = FC_SH
        self.ch[o + dx - 1] = FC_TR
        for r in range(1, dy - 1):
            self.ch[(y + r) * DX + x] = FC_SV
            self.ch[(y + r) * DX + x + dx - 1] = FC_SV
        o = (y + dy - 1) * DX + x
        self.ch[o] = FC_BL
        for i in range(1, dx - 1):
            self.ch[o + i] = FC_SH
        self.ch[o + dx - 1] = FC_BR

    def attr_span(self, x, y, n, attr):
        for i in range(n):
            if 0 <= y * DX + x + i < DX * DY:
                self.at[y * DX + x + i] = attr

    def frame_fullscr(self):
        self.clr()
        self.frame(0, 0, DX, DY)

    # ---- to the display port (the core adapter: where the cells go)
    def flush(self, port):
        """write the changed span of the characters and of the attributes to the port
        (port.write_cells('chars' | 'attrs', first cell, values))"""
        n = 0
        for kind, cur, sent in (('chars', self.ch, self.sent_ch), ('attrs', self.at, self.sent_at)):
            lo = next((i for i in range(DX * DY) if cur[i] != sent[i]), None)
            if lo is None:
                continue
            hi = next(i for i in range(DX * DY - 1, -1, -1) if cur[i] != sent[i])
            port.write_cells(kind, lo, cur[lo:hi + 1])
            sent[lo:hi + 1] = cur[lo:hi + 1]
            n += hi + 1 - lo
        return n

    def invalidate(self):
        self.sent_ch = [None] * (DX * DY)
        self.sent_at = [None] * (DX * DY)

    def text(self, x0=0, y0=0, w=None, h=None):
        w, h = w or DX, h or DY
        return [''.join(glyph(self.ch[y * DX + x]) for x in range(x0, x0 + w)) for y in range(y0, y0 + h)]
