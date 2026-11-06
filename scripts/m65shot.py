#!/usr/bin/env python3
"""m65shot.py -- screenshot of the MEGA65 without the HDMI capture dongle.

Reads the VIC-IV state through the serial monitor (m65io's pipe, TCP 4510): registers, screen
RAM, colour RAM, the character set and the palette, and renders the text display in software
as the 720x576 PAL picture the HDMI output shows (VIC-IV x 40..760, y 17..593 -- measured on
dongle captures).  The CPU keeps running (~0.3 s of reads; fast-changing screens may tear).

    scripts/m65shot.py [-o file.png] [--host 192.168.1.143] [--text]
        --text   also print the screen as text (screen codes -> ASCII)

Rendered: text modes (40/80+ columns, any rows; CHRCOUNT/LINESTEP/DISPROWS, CHRXSCL/CHRYSCL),
colour RAM with VIC-III attributes (reverse, blink as on, bold = colour + 16, underline),
multicolour text, 16-bit character numbers (CHR16), the text palette bank, border/background.
Not rendered: bitmap modes, sprites, full-colour (FCM/NCM) characters, raster splits -- the
picture shows the text layer only.
"""
import argparse
import os
import socket
import sys
import time

from PIL import Image

VIC = 0xFFD3000
COLOUR_RAM = 0xFF80000
CHARROM = 0xFF7E000                   # what the VIC sees at $1000-$1FFF / $9000-$9FFF (as a C64)
OUT_W, OUT_H = 720, 576
X0, Y0 = 40, 17                       # VIC-IV coordinate of the picture's top-left pixel


class Monitor:
    def __init__(self, host, port=4510):
        self.s = socket.create_connection((host, port), timeout=5)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def _discard(self):
        self.s.setblocking(False)
        try:
            while self.s.recv(65536):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        finally:
            self.s.setblocking(True)

    def cmd(self, c):
        self._discard()              # the pipe sends every client's output to all clients
        self.s.sendall(c.encode() + b"\r")
        out, t0 = b"", time.time()
        self.s.settimeout(3)
        while not out.rstrip(b"\r\n ").endswith(b"\n.") and time.time() - t0 < 4:
            d = self.s.recv(65536)
            if not d:
                break
            out += d
        return out.decode(errors="replace")

    def read(self, addr, n):
        data = bytearray()
        a = addr
        while len(data) < n:
            got = b""
            for line in self.cmd("M%x" % a).splitlines():
                if line.startswith(":") and line.count(":") >= 2:
                    got += bytes.fromhex(line.split(":")[2].strip())
            if not got:
                raise OSError("no data from the monitor at $%x" % a)
            data += got
            a += len(got)
        return bytes(data[:n])


def nib(v):
    return ((v & 0x0F) << 4) | (v >> 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output")
    ap.add_argument("--host", default="192.168.1.143")
    ap.add_argument("--text", action="store_true")
    a = ap.parse_args()
    m = Monitor(a.host)
    t0 = time.time()
    r = m.read(VIC, 0x80)
    w12 = lambda lo: (r[lo] | r[lo + 1] << 8) & 0xFFF
    d011, d016, d031, d054 = r[0x11], r[0x16], r[0x31], r[0x54]
    chr16 = d054 & 1
    cols = r[0x5E] | ((r[0x63] >> 4) & 3) << 8
    rows = r[0x7B] + 1
    step = r[0x58] | r[0x59] << 8
    scrn = r[0x60] | r[0x61] << 8 | r[0x62] << 16 | (r[0x63] & 0x0F) << 24
    cptr = r[0x64] | r[0x65] << 8
    chrp = r[0x68] | r[0x69] << 8 | r[0x6A] << 16
    xscl, yscl = r[0x5A] or 120, r[0x5B]
    h640, attr = d031 & 0x80, d031 & 0x20
    mcm = d016 & 0x10
    if d011 & 0x20:
        print("m65shot: bitmap mode -- only text modes are rendered", file=sys.stderr)
    tx, ty = w12(0x4C), w12(0x4E)
    tb, bb = w12(0x48), w12(0x4A)
    sbw = (r[0x5C] | (r[0x5D] & 0x3F) << 8)
    # pixel size of one character pixel in output pixels
    px_w = (1 if h640 else 2) * xscl / 120.0
    px_h = yscl + 1
    bpc = 2 if chr16 else 1                      # screen/colour bytes per character
    nscr = step * rows if step else cols * bpc * rows
    screen = m.read(scrn, nscr)
    colour = m.read(COLOUR_RAM + cptr, nscr)
    # palette: the text palette bank (BTPALSEL); the CPU sees MAPEDPAL -- switch briefly if needed
    d070 = r[0x70]
    bank = (d070 >> 4) & 3
    if (d070 >> 6) != bank:
        m.cmd("sffd3070 %02x" % ((d070 & 0x3F) | bank << 6))
    try:
        reds, greens, blues = m.read(VIC + 0x100, 32), m.read(VIC + 0x200, 32), m.read(VIC + 0x300, 32)
    finally:
        if (d070 >> 6) != bank:
            m.cmd("sffd3070 %02x" % d070)
    pal = [(nib(reds[i]), nib(greens[i]), nib(blues[i])) for i in range(32)]
    # characters in use -> glyph data
    codes = []
    for y in range(rows):
        for x in range(cols):
            o = y * step + x * bpc
            if o + bpc > len(screen):
                codes.append(32)
                continue
            codes.append((screen[o] | (screen[o + 1] & 0x1F) << 8) if chr16 else screen[o])
    glyphs = {}
    maxc = max(codes) if codes else 0
    if chrp < 0x10000 and 0x1000 <= (chrp & 0x7FFF) < 0x2000:
        chrp = CHARROM + (chrp & 0x0FFF)                 # character ROM, not the RAM there
    if maxc < 256:
        font = m.read(chrp, 256 * 8)
        glyphs = {c: font[c * 8:c * 8 + 8] for c in range(256)}
    else:
        for c in sorted(set(codes)):
            glyphs[c] = m.read(chrp + c * 8, 8)
    t_read = time.time() - t0

    img = Image.new("RGB", (OUT_W, OUT_H), pal[r[0x20] & 0x1F] if r[0x20] < 32 else (0, 0, 0))
    px = img.load()
    bg = pal[r[0x21] & 0x1F]
    bgm = [bg, pal[r[0x22] & 0x1F], pal[r[0x23] & 0x1F]]
    # background inside the borders
    left, right = max(0, sbw - X0), min(OUT_W, 800 - sbw - X0)
    top, bottom = max(0, tb - Y0), min(OUT_H, bb - Y0)
    for y in range(top, bottom):
        for x in range(left, right):
            px[x, y] = bg
    cw, ch = 8 * px_w, 8 * px_h
    for y in range(rows):
        for x in range(cols):
            i = y * cols + x
            o = y * step + x * bpc
            if o >= len(colour):
                continue
            code = codes[i]
            cr = colour[o + 1] if chr16 and o + 1 < len(colour) else colour[o]
            fgi = cr & 0x0F
            if attr and cr & 0x40:
                fgi += 16                                     # bold = second 16 colours
            fg = pal[fgi]
            rev = attr and cr & 0x20
            ul = attr and cr & 0x80
            g = glyphs.get(code, bytes(8))
            ox, oy = tx - X0 + x * cw, ty - Y0 + y * ch
            for gy in range(8):
                bits = g[gy]
                if ul and gy == 7:
                    bits = 0xFF
                for gx in range(8):
                    if mcm and cr & 0x08 and not attr:        # multicolour text: 2-bit pixels
                        v = (bits >> (6 - (gx & 6))) & 3       # 00 bg, 01 $D022, 10 $D023,
                        on = v != 0                             # 11 colour RAM (low 3 bits)
                        col = pal[cr & 0x07] if v == 3 else bgm[v]
                    else:
                        on = (bits >> (7 - gx)) & 1
                        if rev:
                            on = not on
                        col = fg
                    if not on:
                        continue
                    x0 = int(ox + gx * px_w)
                    x1 = int(ox + (gx + 1) * px_w)
                    y0 = int(oy + gy * px_h)
                    for yy in range(y0, y0 + px_h):
                        if top <= yy < bottom and 0 <= yy < OUT_H:
                            for xx in range(x0, max(x1, x0 + 1)):
                                if left <= xx < right:
                                    px[xx, yy] = col
    out = a.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captures",
                                    time.strftime("shot_%Y%m%d_%H%M%S.png"))
    img.save(out)
    print("saved=%s  %dx%d chars%s%s, screen $%x, font $%x, read %.2f s" % (
        os.path.normpath(out), cols, rows, " CHR16" if chr16 else "", " MCM" if mcm else "",
        scrn, chrp, t_read))
    if a.text:
        for y in range(rows):
            line = ""
            for x in range(cols):
                c = codes[y * cols + x] & 0x7F
                line += chr(c + 64) if 1 <= c <= 26 else chr(c) if 32 <= c < 64 else \
                    chr(c) if 65 <= c <= 90 else "@" if c == 0 else "."
            print(line.rstrip())


if __name__ == "__main__":
    main()
