#!/usr/bin/env python3
"""m65term.py -- a Linux shell on the MEGA65's own screen (90x72 text), for cross-development.

The MEGA65 is only the display: through its serial monitor (m65io's pipe, TCP 4510) this
daemon stops the CPU ("t1"), saves the VIC-IV registers and the memory it borrows, loads an
8x8 Linux console font, switches to 90x72 text and writes the terminal into screen/colour RAM
with the monitor's binary load command "L" (~190 KB/s: a full screen in ~45 ms; only changed
rows are sent).  Leaving restores memory and registers and lets the CPU run on ("t0") -- the
MEGA65 program continues where it was frozen.

Keyboard: m65d owns the USB keyboards; in terminal mode it sends key events here instead of
to the MEGA65 matrix (Ctrl+Alt+T toggles).  The shell (user linaro, TERM=xterm, 90x72) keeps
running while the MEGA65 is shown.

Control socket /run/m65term.sock, one command per line, reply "ok ..."/"err ...":
    enter | leave | toggle | state      show / hide the terminal on the MEGA65
    k <NAME> <mods>                      special key (UP DOWN LEFT RIGHT HOME END PGUP PGDN INS
                                         DEL F1..F12), mods = bits 1 shift 2 alt 4 ctrl
    t <hex>                              text bytes for the shell
    redraw                               send the whole screen again

Borrowed MEGA65 memory (saved and restored): screen $5D000 (90x72 = 6480 bytes), font $5F000
(2048 bytes), colour RAM offset $2000 (6480 bytes).
"""
import codecs
import errno
import fcntl
import gzip
import json
import os
import pty
import select
import signal
import socket
import struct
import sys
import termios
import time

COLS, ROWS = 90, 72                # the whole 720x576 PAL picture in 8x8 characters
SCREEN, FONT, COLOUR = 0x005D000, 0x005F000, 0x2000      # chip RAM, chip RAM, colour RAM offset
COLOUR_BASE = 0xFF80000
MON = ("127.0.0.1", 4510)
SOCK = "/run/m65term.sock"
SAVED = "/run/m65term.saved"       # the MEGA65 state while the terminal is up (survives restarts)
FONTFILE = "/usr/share/consolefonts/Lat15-VGA8.psf.gz"
SHELL = ["/bin/su", "-", "linaro"]
FPS = 30.0

# The terminal's own palette (bank PAL_BANK, entries 0-15 = the 16 ANSI colours; saved/restored).
# Readable on black: a lighter blue than xterm's.  Red values even: bit 0 of red lands in bit 4
# of the (nibble-swapped) palette register.
PAL_BANK = 2
ANSI16 = [(0, 0, 0), (204, 0, 0), (0, 190, 0), (200, 180, 0), (60, 110, 230), (190, 0, 190),
          (0, 180, 190), (200, 200, 200), (110, 110, 110), (254, 84, 84), (84, 254, 84),
          (254, 254, 84), (110, 150, 254), (254, 84, 254), (84, 254, 254), (254, 254, 254)]
DEFAULT_FG = 7                               # light grey, like a Linux console


def nib(v):
    return ((v & 0x0F) << 4) | (v >> 4)     # MEGA65 palette registers store nibbles swapped


def nearest16(r, g, b):
    return min(range(16), key=lambda i: (ANSI16[i][0] - r) ** 2 + (ANSI16[i][1] - g) ** 2 +
               (ANSI16[i][2] - b) ** 2)


def xterm256(c):
    """xterm 256-colour index -> one of the 16"""
    if c < 16:
        return c
    if c < 232:
        c -= 16
        lv = [0, 95, 135, 175, 215, 255]
        return nearest16(lv[c // 36], lv[c // 6 % 6], lv[c % 6])
    v = 8 + (c - 232) * 10
    return nearest16(v, v, v)
ATTR_BLINK, ATTR_REVERSE = 0x10, 0x20

# characters the 8x8 console font lacks -> ASCII (ca65 quotes its messages with ‘ ’)
FALLBACK = {"‘": "'", "’": "'", "‚": ",", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-",
            "…": ".", "•": "*", "→": ">", "←": "<", "\u00a0": " "}

# DEC special graphics (ESC ( 0) -> Unicode box drawing
DEC_GFX = dict(zip("`afgjklmnopqrstuvwxyz{|}~",
                   "◆▒°±┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·"))


def log(*a):
    print("m65term:", *a, flush=True)


# ------------------------------------------------------------------ font
def load_font(path):
    """PSF1 8x8 font -> (2048 bytes of glyphs, {unicode char: glyph index})"""
    raw = gzip.open(path).read() if path.endswith(".gz") else open(path, "rb").read()
    if raw[0:2] != b"\x36\x04":
        raise ValueError("not a PSF1 font")
    mode, size = raw[2], raw[3]
    if size != 8:
        raise ValueError("font is not 8 pixels high")
    n = 512 if mode & 1 else 256
    glyphs = raw[4:4 + n * 8]
    umap = {}
    if mode & 2:                                 # unicode table: per glyph u16 list, 0xFFFF ends
        p, g = 4 + n * 8, 0
        while p + 1 < len(raw) and g < n:
            u = raw[p] | raw[p + 1] << 8
            p += 2
            if u == 0xFFFF:
                g += 1
            elif u != 0xFFFE and g < 256:
                umap.setdefault(chr(u), g)
    else:
        umap = {chr(i): i for i in range(256)}
    return glyphs[:2048], umap


# ------------------------------------------------------------------ terminal emulator
class Cell:
    __slots__ = ("ch", "fg", "bg", "bold", "rev", "ul")

    def __init__(self, ch=" ", fg=None, bg=None, bold=False, rev=False, ul=False):
        self.ch, self.fg, self.bg, self.bold, self.rev, self.ul = ch, fg, bg, bold, rev, ul


class VT:
    """The subset of xterm that shells, editors (vi, nano), less and compilers use."""

    def __init__(self, cols, rows, reply):
        self.cols, self.rows, self.reply = cols, rows, reply
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")
        self.reset()

    def reset(self):
        self.main = self._blank_screen()
        self.alt = None
        self.buf = self.main
        self.x = self.y = 0
        self.wrap_pending = False
        self.top, self.bot = 0, self.rows - 1
        self.attr = Cell()
        self.saved = (0, 0, Cell())
        self.cursor_on = True
        self.autowrap = True
        self.origin = False
        self.insert = False
        self.app_cursor = False
        self.g0_gfx = False
        self.tabs = set(range(8, self.cols, 8))
        self.state, self.params, self.priv = "N", "", ""
        self.dirty = set(range(self.rows))

    def _blank_screen(self):
        return [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]

    def _blank_cell(self):
        return Cell(" ", None, self.attr.bg)

    # -- low level
    def _touch(self, y):
        self.dirty.add(y)

    def _scroll_up(self, top, bot, n=1):
        for _ in range(n):
            del self.buf[top]
            self.buf.insert(bot, [self._blank_cell() for _ in range(self.cols)])
        self.dirty.update(range(top, bot + 1))

    def _scroll_down(self, top, bot, n=1):
        for _ in range(n):
            del self.buf[bot]
            self.buf.insert(top, [self._blank_cell() for _ in range(self.cols)])
        self.dirty.update(range(top, bot + 1))

    def _index(self):
        if self.y == self.bot:
            self._scroll_up(self.top, self.bot)
        elif self.y < self.rows - 1:
            self.y += 1

    def _rindex(self):
        if self.y == self.top:
            self._scroll_down(self.top, self.bot)
        elif self.y > 0:
            self.y -= 1

    def _put(self, ch):
        if self.g0_gfx:
            ch = DEC_GFX.get(ch, ch)
        if self.wrap_pending and self.autowrap:
            self.x = 0
            self._index()
        self.wrap_pending = False
        row = self.buf[self.y]
        if self.insert:
            row.insert(self.x, Cell())
            del row[self.cols]
        a = self.attr
        row[self.x] = Cell(ch, a.fg, a.bg, a.bold, a.rev, a.ul)
        self._touch(self.y)
        if self.x == self.cols - 1:
            self.wrap_pending = True
        else:
            self.x += 1

    def _clamp(self):
        self.x = max(0, min(self.cols - 1, self.x))
        self.y = max(0, min(self.rows - 1, self.y))
        self.wrap_pending = False

    def _erase(self, y, x0, x1):
        row = self.buf[y]
        for x in range(max(0, x0), min(self.cols, x1)):
            row[x] = self._blank_cell()
        self._touch(y)

    # -- input
    def feed(self, data):
        for ch in self.dec.decode(data):
            self._char(ch)

    def _char(self, ch):
        o = ord(ch)
        st = self.state
        if st == "N":
            if o >= 0x20 and o != 0x7F:
                self._put(ch)
            else:
                self._control(o)
        elif st == "E":                               # after ESC
            self.state = "N"
            if ch == "[":
                self.state, self.params, self.priv = "C", "", ""
            elif ch == "]":
                self.state, self.params = "O", ""
            elif ch in "()*+":
                self.state = "G" if ch == "(" else "g"
            elif ch == "#":
                self.state = "H"
            elif ch == "D":
                self._index()
            elif ch == "E":
                self.x = 0
                self._index()
            elif ch == "M":
                self._rindex()
            elif ch == "7":
                self.saved = (self.x, self.y, Cell(" ", self.attr.fg, self.attr.bg, self.attr.bold,
                                                   self.attr.rev, self.attr.ul))
            elif ch == "8":
                self.x, self.y, a = self.saved
                self.attr = Cell(" ", a.fg, a.bg, a.bold, a.rev, a.ul)
                self._clamp()
            elif ch == "c":
                self.reset()
            elif ch == "H":
                self.tabs.add(self.x)
        elif st == "G":                               # ESC ( x: G0 charset
            self.g0_gfx = ch == "0"
            self.state = "N"
        elif st in "gH":                              # other charsets / ESC # 8: ignore
            if st == "H" and ch == "8":
                for y in range(self.rows):
                    for x in range(self.cols):
                        self.buf[y][x] = Cell("E")
                self.dirty.update(range(self.rows))
            self.state = "N"
        elif st == "O":                               # OSC ... BEL or ST
            if o == 7 or ch == "\\" or o == 0x9C:
                self.state = "N"
            elif o == 0x1B:
                pass
        elif st == "C":                               # CSI
            if ch in "0123456789;:":
                self.params += ch
            elif ch in "?>=!":
                self.priv += ch
            elif 0x20 <= o <= 0x2F:
                self.priv += ch
            elif 0x40 <= o <= 0x7E:
                self.state = "N"
                self._csi(ch)
            elif o < 0x20:
                self._control(o)
            else:
                self.state = "N"

    def _control(self, o):
        if o == 0x1B:
            self.state = "E"
        elif o == 0x0D:
            self.x = 0
            self.wrap_pending = False
        elif o in (0x0A, 0x0B, 0x0C):
            self._index()
            self.wrap_pending = False
        elif o == 0x08:
            if self.x > 0:
                self.x -= 1
            self.wrap_pending = False
        elif o == 0x09:
            nxt = [t for t in sorted(self.tabs) if t > self.x]
            self.x = nxt[0] if nxt else self.cols - 1
        elif o == 0x0E:
            self.g0_gfx = True
        elif o == 0x0F:
            self.g0_gfx = False

    def _nums(self, default=0, count=1):
        v = [int(p) if p.isdigit() else default for p in self.params.replace(":", ";").split(";")] \
            if self.params else []
        while len(v) < count:
            v.append(default)
        return v

    def _csi(self, f):
        priv = self.priv
        if priv.startswith("?"):
            if f in "hl":
                on = f == "h"
                for m in self._nums(0, 1):
                    if m == 1:
                        self.app_cursor = on
                    elif m == 6:
                        self.origin = on
                        self.x, self.y = 0, self.top if on else 0
                    elif m == 7:
                        self.autowrap = on
                    elif m == 25:
                        self.cursor_on = on
                        self._touch(self.y)
                    elif m in (47, 1047, 1049):
                        self._alt_screen(on, m == 1049)
            return
        if priv and f not in "pq":
            if f == "c" and priv == ">":           # secondary DA
                self.reply(b"\x1b[>0;0;0c")
            return
        n = self._nums(0, 2)
        n1 = n[0] or 1
        if f == "@":
            row = self.buf[self.y]
            for _ in range(n1):
                row.insert(self.x, self._blank_cell())
                del row[self.cols]
            self._touch(self.y)
        elif f == "A":
            self.y = max(self.top if self.y >= self.top else 0, self.y - n1)
            self.wrap_pending = False
        elif f in "Be":
            self.y = min(self.bot if self.y <= self.bot else self.rows - 1, self.y + n1)
            self.wrap_pending = False
        elif f in "Ca":
            self.x = min(self.cols - 1, self.x + n1)
            self.wrap_pending = False
        elif f == "D":
            self.x = max(0, self.x - n1)
            self.wrap_pending = False
        elif f == "E":
            self.y = min(self.rows - 1, self.y + n1)
            self.x = 0
            self.wrap_pending = False
        elif f == "F":
            self.y = max(0, self.y - n1)
            self.x = 0
            self.wrap_pending = False
        elif f in "G`":
            self.x = n1 - 1
            self._clamp()
        elif f in "Hf":
            self.y = (n[0] or 1) - 1 + (self.top if self.origin else 0)
            self.x = (n[1] or 1) - 1
            self._clamp()
        elif f == "d":
            self.y = n1 - 1
            self._clamp()
        elif f == "J":
            if n[0] == 0:
                self._erase(self.y, self.x, self.cols)
                for y in range(self.y + 1, self.rows):
                    self._erase(y, 0, self.cols)
            elif n[0] == 1:
                for y in range(0, self.y):
                    self._erase(y, 0, self.cols)
                self._erase(self.y, 0, self.x + 1)
            else:
                for y in range(self.rows):
                    self._erase(y, 0, self.cols)
        elif f == "K":
            if n[0] == 0:
                self._erase(self.y, self.x, self.cols)
            elif n[0] == 1:
                self._erase(self.y, 0, self.x + 1)
            else:
                self._erase(self.y, 0, self.cols)
        elif f == "L":
            if self.top <= self.y <= self.bot:
                self._scroll_down(self.y, self.bot, min(n1, self.bot - self.y + 1))
        elif f == "M":
            if self.top <= self.y <= self.bot:
                self._scroll_up(self.y, self.bot, min(n1, self.bot - self.y + 1))
        elif f == "P":
            row = self.buf[self.y]
            for _ in range(min(n1, self.cols - self.x)):
                del row[self.x]
                row.append(self._blank_cell())
            self._touch(self.y)
        elif f == "X":
            self._erase(self.y, self.x, self.x + n1)
        elif f == "S":
            self._scroll_up(self.top, self.bot, n1)
        elif f == "T":
            self._scroll_down(self.top, self.bot, n1)
        elif f == "Z":
            prev = [t for t in sorted(self.tabs) if t < self.x]
            self.x = prev[-1] if prev else 0
        elif f == "g":
            if n[0] == 3:
                self.tabs.clear()
            else:
                self.tabs.discard(self.x)
        elif f in "hl":
            if n[0] == 4:
                self.insert = f == "h"
        elif f == "m":
            self._sgr(self._nums(0, 1))
        elif f == "n":
            if n[0] == 6:
                self.reply(b"\x1b[%d;%dR" % (self.y + 1, self.x + 1))
            elif n[0] == 5:
                self.reply(b"\x1b[0n")
        elif f == "r":
            t, b = (n[0] or 1) - 1, (n[1] or self.rows) - 1
            if 0 <= t < b < self.rows:
                self.top, self.bot = t, b
                self.x, self.y = 0, self.top if self.origin else 0
        elif f == "s":
            self.saved = (self.x, self.y, self.attr)
        elif f == "u":
            self.x, self.y, self.attr = self.saved
            self._clamp()
        elif f == "c":
            self.reply(b"\x1b[?6c")

    def _sgr(self, ps):
        a = self.attr
        a = Cell(" ", a.fg, a.bg, a.bold, a.rev, a.ul)
        i = 0
        while i < len(ps):
            p = ps[i]
            if p == 0:
                a = Cell()
            elif p == 1:
                a.bold = True
            elif p in (2, 22):
                a.bold = False
            elif p == 4:
                a.ul = True
            elif p == 24:
                a.ul = False
            elif p == 7:
                a.rev = True
            elif p == 27:
                a.rev = False
            elif 30 <= p <= 37:
                a.fg = p - 30
            elif p == 39:
                a.fg = None
            elif 40 <= p <= 47:
                a.bg = p - 40
            elif p == 49:
                a.bg = None
            elif 90 <= p <= 97:
                a.fg = p - 90 + 8
            elif 100 <= p <= 107:
                a.bg = p - 100 + 8
            elif p in (38, 48) and i + 1 < len(ps):      # 256 colours / rgb: nearest of the 16
                c = None
                if ps[i + 1] == 5 and i + 2 < len(ps):
                    c = xterm256(ps[i + 2])
                    i += 2
                elif ps[i + 1] == 2 and i + 4 < len(ps):
                    c = nearest16(ps[i + 2], ps[i + 3], ps[i + 4])
                    i += 4
                if c is not None:
                    if p == 38:
                        a.fg = c
                    else:
                        a.bg = c
            i += 1
        self.attr = a

    def _alt_screen(self, on, save_cursor):
        if on and self.alt is None:
            if save_cursor:
                self.saved = (self.x, self.y, self.attr)
            self.alt = self._blank_screen()
            self.buf = self.alt
        elif not on and self.alt is not None:
            self.alt = None
            self.buf = self.main
            if save_cursor:
                self.x, self.y, self.attr = self.saved
                self._clamp()
        self.dirty.update(range(self.rows))


# ------------------------------------------------------------------ MEGA65 side
class Monitor:
    """the MEGA65 serial monitor through m65io's pipe"""

    def __init__(self):
        self.s = None

    def connect(self):
        if self.s is None:
            self.s = socket.create_connection(MON, timeout=5)
            self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._drain()

    def close(self):
        if self.s:
            self.s.close()
        self.s = None

    def _drain(self):
        self.s.settimeout(0.05)
        try:
            while self.s.recv(65536):
                pass
        except socket.timeout:
            pass

    def cmd(self, c, raw=b"", timeout=3.0):
        """send a command (+ raw bytes for L), wait for the prompt, return the output"""
        self.connect()
        self.s.sendall(c.encode() + b"\r" + raw)
        out, t0 = b"", time.time()
        self.s.settimeout(timeout)
        while not out.rstrip(b"\r\n ").endswith(b"\n.") and time.time() - t0 < timeout:
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                break
            if not d:
                self.close()
                raise OSError("monitor connection closed")
            out += d
        return out

    def read(self, addr, n):
        data = b""
        for a in range(addr, addr + n, 256):
            for line in self.cmd("M%x" % a).decode(errors="replace").splitlines():
                if line.startswith(":") and line.count(":") >= 2:
                    data += bytes.fromhex(line.split(":")[2].strip())
        return data[:n]

    def load(self, addr, data):
        """binary load (monitor L: 32-bit start, 16-bit end exclusive)"""
        for o in range(0, len(data), 4096):
            chunk = data[o:o + 4096]
            a = addr + o
            self.cmd("l%x %x" % (a, (a + len(chunk)) & 0xFFFF), chunk)

    def poke(self, addr, data):
        self.cmd("s%x %s" % (addr, " ".join("%02x" % b for b in data)))


VIC = 0xFFD3000
# the VIC-IV registers this mode sets; saved on enter and written back on leave
REGS = [0x15, 0x11, 0x16, 0x20, 0x21, 0x54, 0x58, 0x59, 0x5E, 0x60, 0x61, 0x62, 0x63,
        0x64, 0x65, 0x68, 0x69, 0x6A, 0x7B, 0x5B, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F,
        0x5C, 0x70, 0x31]
# PAL: the HDMI picture shows VIC-IV x 40..760, y 17..593 (measured on captures: TEXTYPOS 16
# put row 1 one line above the picture and the VIC showed it twice), so text and borders go to
# the very edge: top border, bottom border, text x, text y (12 bits each)
PAL_POS = {0x48: 17, 0x4A: 17 + 8 * ROWS, 0x4C: 40, 0x4E: 17}
SIDE_BORDER = 40                   # $D05C/$D05D SDBDRWD: left border ends / right one starts


class Term:
    def __init__(self):
        self.glyphs, self.umap = load_font(FONTFILE)
        self.qmark = self.umap.get("?", 63)
        self.mon = Monitor()
        self.active = False
        self.saved = None
        if os.path.exists(SAVED):             # restarted while the terminal was up: keep its state
            try:
                d = json.load(open(SAVED))
                self.saved = {"regs": {int(k): v for k, v in d["regs"].items()},
                              **{k: bytes.fromhex(d.get(k, "")) for k in
                                 ("screen", "font", "colour", "palette")}}
                self.active = True
                log("terminal was up before the restart: state from %s" % SAVED)
            except (OSError, ValueError, KeyError) as e:
                log("ignoring %s: %r" % (SAVED, e))
        self.sent = [None] * ROWS            # last (codes, colours) per row on the MEGA65
        self.last_cursor = None
        self.next_render = 0.0
        self.pid, self.fd = None, None
        self.vt = VT(COLS, ROWS, self.write_pty)
        self.spawn()

    # -- shell
    def spawn(self):
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ, TERM="xterm", COLUMNS=str(COLS), LINES=str(ROWS), LANG="C.UTF-8")
            os.execvpe(SHELL[0], SHELL, env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.pid, self.fd = pid, fd
        log("shell started (pid %d)" % pid)

    def write_pty(self, data):
        try:
            os.write(self.fd, data)
        except OSError:
            pass

    def pty_read(self):
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:                          # shell exited: start a new one
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
            os.close(self.fd)
            self.vt.reset()
            self.spawn()
            return
        self.vt.feed(data)

    # -- keys
    def key(self, name, mods):
        shift, alt, ctrl = mods & 1, mods & 2, mods & 4
        app = self.vt.app_cursor
        seq = {
            "UP": b"\x1bOA" if app else b"\x1b[A", "DOWN": b"\x1bOB" if app else b"\x1b[B",
            "RIGHT": b"\x1bOC" if app else b"\x1b[C", "LEFT": b"\x1bOD" if app else b"\x1b[D",
            "HOME": b"\x1bOH" if app else b"\x1b[H", "END": b"\x1bOF" if app else b"\x1b[F",
            "INS": b"\x1b[2~", "DEL": b"\x1b[3~", "PGUP": b"\x1b[5~", "PGDN": b"\x1b[6~",
            "F1": b"\x1bOP", "F2": b"\x1bOQ", "F3": b"\x1bOR", "F4": b"\x1bOS",
            "F5": b"\x1b[15~", "F6": b"\x1b[17~", "F7": b"\x1b[18~", "F8": b"\x1b[19~",
            "F9": b"\x1b[20~", "F10": b"\x1b[21~", "F11": b"\x1b[23~", "F12": b"\x1b[24~",
        }.get(name)
        if seq is None:
            return
        m = 1 + shift + alt + ctrl          # xterm modifier parameter (bits 1/2/4 -> +1)
        if m > 1 and name in ("UP", "DOWN", "RIGHT", "LEFT", "HOME", "END"):
            seq = b"\x1b[1;%d%s" % (m, seq[-1:])
        self.write_pty(seq)

    # -- MEGA65 display
    def enter(self):
        if self.active:
            return "ok already"
        m = self.mon
        m.cmd("t1")                                       # stop the CPU
        try:
            regs = m.read(VIC, 0x80)
            m.poke(VIC + 0x70, [(regs[0x70] & 0x3F) | PAL_BANK << 6])   # CPU sees our bank
            saved = {
                "palette": m.read(VIC + 0x100, 16) + m.read(VIC + 0x200, 16) + m.read(VIC + 0x300, 16),
                "regs": {r: regs[r] for r in REGS + [0x5D, 0x6F]},
                "screen": m.read(SCREEN, COLS * ROWS),
                "font": m.read(FONT, 2048),
                "colour": m.read(COLOUR_BASE + COLOUR, COLS * ROWS),
            }
            if len(regs) != 0x80 or len(saved["screen"]) != COLS * ROWS or \
                    len(saved["font"]) != 2048 or len(saved["colour"]) != COLS * ROWS:
                raise OSError("short read from the monitor")
            tmp = SAVED + ".tmp"                          # on disk before anything is changed
            json.dump({"regs": saved["regs"],
                       **{k: saved[k].hex() for k in ("screen", "font", "colour", "palette")}},
                      open(tmp, "w"))
            os.rename(tmp, SAVED)
        except Exception:
            m.cmd("t0")                                   # nothing changed yet: just run on
            raise
        self.saved = saved
        try:
            self._setup(saved["regs"])
        except Exception:
            log("enter failed, restoring")
            self.active = True
            self.leave()
            raise
        self.sent = [None] * ROWS
        self.last_cursor = None
        self.active = True
        self.render(force=True)
        return "ok entered"

    def _setup(self, r):
        m = self.mon
        m.poke(VIC + 0x5D, [r[0x5D] & 0x7F])             # no hot registers while we set ours
        m.load(FONT, self.glyphs)
        m.poke(VIC + 0x15, [0])                           # sprites off
        m.poke(VIC + 0x20, [0, 0])                        # border, background black
        m.poke(VIC + 0x11, [0x1B])
        m.poke(VIC + 0x16, [0xC8])
        m.poke(VIC + 0x54, [r[0x54] & ~0x07 & 0xFF])     # no 16-bit / full-colour chars
        m.poke(VIC + 0x58, [COLS, 0])                     # bytes per row
        m.poke(VIC + 0x5E, [COLS])                        # chars per row
        m.poke(VIC + 0x60, [SCREEN & 0xFF, SCREEN >> 8 & 0xFF, SCREEN >> 16 & 0xFF,
                            r[0x63] & 0xC0])
        m.poke(VIC + 0x64, [COLOUR & 0xFF, COLOUR >> 8])
        m.poke(VIC + 0x68, [FONT & 0xFF, FONT >> 8 & 0xFF, FONT >> 16 & 0xFF])
        m.poke(VIC + 0x7B, [ROWS - 1])
        m.poke(VIC + 0x5B, [0])                           # CHRYSCL: no line doubling (V400)
        if not r.get(0x6F, 0) & 0x80:                     # PAL: a full 400-line text window
            for lo, v in PAL_POS.items():                 # (high nibbles hold other flags)
                m.poke(VIC + lo, [v & 0xFF, (r[lo + 1] & 0xF0) | (v >> 8)])
            m.poke(VIC + 0x5C, [SIDE_BORDER & 0xFF, (r[0x5D] & 0x40) | (SIDE_BORDER >> 8)])
        else:
            log("NTSC: the window is sized for PAL (576 lines); rows at the bottom may be cut")
        for ch, base in ((0, 0x100), (1, 0x200), (2, 0x300)):   # ANSI colours into our bank
            m.poke(VIC + base, [nib(c[ch]) for c in ANSI16])
        m.poke(VIC + 0x70, [(r[0x70] & 0x0F) | PAL_BANK << 6 | PAL_BANK << 4])  # text uses it
        m.poke(VIC + 0x31, [0xE8 | (r[0x31] & 0x40)])    # H640 + ATTR + V400 (keep FAST)

    def leave(self):
        if not self.active:
            return "ok already"
        m, s = self.mon, self.saved
        m.load(SCREEN, s["screen"])
        m.load(FONT, s["font"])
        m.load(COLOUR_BASE + COLOUR, s["colour"])
        if len(s.get("palette", b"")) == 48:              # our bank back (CPU still maps it)
            m.poke(VIC + 0x70, [(s["regs"].get(0x70, 0) & 0x3F) | PAL_BANK << 6])
            for i, base in enumerate((0x100, 0x200, 0x300)):
                m.poke(VIC + base, list(s["palette"][i * 16:(i + 1) * 16]))
        for r in REGS:
            if r in s["regs"]:                            # (older saved states lack some)
                m.poke(VIC + r, [s["regs"][r]])
        m.poke(VIC + 0x5D, [s["regs"][0x5D]])
        m.cmd("t0")                                       # the CPU runs on
        self.active = False
        self.saved = None
        try:
            os.unlink(SAVED)
        except OSError:
            pass
        return "ok left"

    def cell_bytes(self, c, cursor):
        code = self.umap.get(c.ch)
        if code is None:
            code = self.umap.get(FALLBACK.get(c.ch, "?"), self.qmark)
        fg = DEFAULT_FG if c.fg is None else c.fg
        col = fg + 8 if c.bold and fg < 8 else fg        # bold = the bright colour
        rev = c.rev
        if c.bg is not None and not rev:             # no per-cell background in text mode:
            col, rev = c.bg, True                     # show it as reverse video
            if col == 0:
                rev, col = False, fg
        attr = col | (ATTR_REVERSE if rev else 0)
        if cursor:
            attr = (attr ^ ATTR_REVERSE) | ATTR_BLINK
        return code, attr

    def render(self, force=False):
        vt = self.vt
        cur = (vt.x, vt.y) if vt.cursor_on else None
        if cur != self.last_cursor:
            for c in (cur, self.last_cursor):
                if c:
                    vt.dirty.add(c[1])
        rows = sorted(vt.dirty)
        vt.dirty.clear()
        changed = []
        for y in rows:
            codes, cols = bytearray(COLS), bytearray(COLS)
            row = vt.buf[y]
            for x in range(COLS):
                codes[x], cols[x] = self.cell_bytes(row[x], cur == (x, y))
            if force or self.sent[y] != (codes, cols):
                self.sent[y] = (bytes(codes), bytes(cols))
                changed.append(y)
        self.last_cursor = cur
        i = 0
        while i < len(changed):                     # contiguous rows -> one load each
            j = i
            while j + 1 < len(changed) and changed[j + 1] == changed[j] + 1:
                j += 1
            y0, y1 = changed[i], changed[j]
            codes = b"".join(self.sent[y][0] for y in range(y0, y1 + 1))
            cols = b"".join(self.sent[y][1] for y in range(y0, y1 + 1))
            self.mon.load(SCREEN + y0 * COLS, codes)
            self.mon.load(COLOUR_BASE + COLOUR + y0 * COLS, cols)
            i = j + 1


def main():
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    t = Term()
    try:
        os.unlink(SOCK)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o666)
    srv.listen(4)
    clients = {}
    log("ready on %s, font %s" % (SOCK, FONTFILE))
    while True:
        rd = [srv, t.fd] + list(clients)
        timeout = None
        if t.active and t.vt.dirty:
            timeout = max(0.0, t.next_render - time.time())
        r, _, _ = select.select(rd, [], [], timeout)
        for s in r:
            try:
                if s is srv:
                    c, _ = srv.accept()
                    clients[c] = b""
                elif s == t.fd:
                    t.pty_read()
                else:
                    serve_client(t, s, clients)
            except Exception as e:              # one bad request must not end the daemon
                log("request: %r" % e)
                if s in clients:
                    clients.pop(s, None)
                    s.close()
        if t.active and t.vt.dirty and time.time() >= t.next_render:
            try:
                t.render()
            except Exception as e:
                t.mon.close()
                log("render: %r" % e)
            t.next_render = time.time() + 1.0 / FPS


def serve_client(t, s, clients):
    try:
        d = s.recv(4096)
    except OSError:
        d = b""
    if not d:
        clients.pop(s, None)
        s.close()
        return
    buf = clients[s] + d
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        try:
            rep = command(t, line.decode(errors="replace").strip())
        except Exception as e:                  # monitor trouble: report, keep running
            t.mon.close()
            rep = "err %r" % e
            log(rep)
        if rep is not None:
            try:
                s.sendall((rep + "\n").encode())
            except OSError:
                pass
    clients[s] = buf


def command(t, line):
    p = line.split(" ", 1)
    cmd, arg = p[0], (p[1] if len(p) > 1 else "")
    if cmd == "enter":
        return t.enter()
    if cmd == "leave":
        return t.leave()
    if cmd == "toggle":
        return t.leave() if t.active else t.enter()
    if cmd == "state":
        return "ok %s" % ("on" if t.active else "off")
    if cmd == "redraw":
        if t.active:
            t.render(force=True)
        return "ok"
    if cmd == "k":
        a = arg.split()
        t.key(a[0], int(a[1]) if len(a) > 1 else 0)
        return None
    if cmd == "t":
        t.write_pty(bytes.fromhex(arg))
        return None
    return "err unknown command"


if __name__ == "__main__":
    main()
