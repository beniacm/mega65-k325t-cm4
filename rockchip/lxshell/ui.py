"""ui.py -- the M2M on-screen menu, file browser and help viewer, drawn by the Linux shell.

A port of the M2M V2.0.1 firmware's user interface (M2M/rom menu.asm, options.asm, screen.asm,
selectfile.asm, dirbrowse.asm, whs.asm, keyboard.asm) as AExp uses it, so the menu looks and
behaves as with QNICE: same characters, colours, positions, cursor rules, submenus, radio groups,
single-select toggles, "%s" summaries, file browser and help pages.  The drawing goes into a local
copy of the overlay VRAM (Screen); Screen.flush() writes the changed cells over the bus.

The interactive parts are generators ("UI coroutines") that yield
    KEY            -> wait for the next menu key (an M2M key bit, KEY_* below)
    ('sleep', s)   -> resume after s seconds
    None           -> resume at the next poll (between bus chunks)
and are driven by shell.AmigaShell.  Long bus work (loading a disk) runs inside them in chunks.
"""

import os

DX, DY = 45, 36                     # overlay size in characters (VGA_DX / FONT_DX x VGA_DY / FONT_DY)

# attributes and characters (M2M/rom/sysdef.asm, font Anikki 16x16)
SA_STD, SA_STD_INV, SA_TTLE, SA_TTLE_INV = 0x0B, 0x8B, 0x0E, 0x8E
FC_TL, FC_SH, FC_TR, FC_SV, FC_BL, FC_BR = 201, 205, 187, 186, 200, 188
FC_HE_LEFT, FC_HE_RIGHT = 185, 204
NC_SH, NC_VE_LEFT, NC_VE_RIGHT = 196, 199, 182
DIR_L, DIR_R = 17, 16
SEL_MULTI, SEL_SINGLE = 7, 61
LD_PROGRESS = 254

# M2M$KEYBOARD bits (high-active here)
KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = 0x001, 0x002, 0x004, 0x008
KEY_RETURN, KEY_SPACE, KEY_RUNSTOP, KEY_HELP = 0x010, 0x020, 0x040, 0x080
KEY_F1, KEY_F3 = 0x100, 0x200

KEY = 'key'                         # coroutine request: the next key

# group word flags as M2M$CFG_OPTM_GROUPS delivers them (config.vhd SEL_OPTM_GROUPS)
W_SINGLESEL, W_SUBMENU, W_HEADLINE = 0x8000, 0x4000, 0x1000
OPTM_CLOSE = 0x00FF

TYPEMATIC_DELAY, TYPEMATIC_RATE = 0.5, 1.0 / 12


# ---------------------------------------------------------------------------------------------
# keys: M2M$KEYBOARD from the MEGA65 matrix, KEYB$SCAN / KEYB$GETKEY semantics
# ---------------------------------------------------------------------------------------------

def m2m_keys(cols, ctrl, osm_a=67, osm_b=67, combo=False):
    """the 10-bit key word m2m_keyb.vhd builds (high-active): cols = 9 matrix columns (bit = row,
    key number = column * 8 + row), ctrl = rk_bridge INPUT ctrl (b4 = key 73 up, b5 = key 74 left)"""
    def down(n):
        if n == 73:
            return bool(ctrl & 0x10)
        if n == 74:
            return bool(ctrl & 0x20)
        c, r = divmod(n, 8)
        return c < len(cols) and bool(cols[c] >> r & 1)
    w = 0
    for bit, n in ((KEY_UP, 73), (KEY_DOWN, 7), (KEY_LEFT, 74), (KEY_RIGHT, 2), (KEY_RETURN, 1),
                   (KEY_SPACE, 60), (KEY_RUNSTOP, 63), (KEY_F1, 4), (KEY_F3, 5)):
        if down(n):
            w |= bit
    a, b = down(osm_a), down(osm_b)
    if (a and b) if combo else a:
        w |= KEY_HELP
    if combo and a:                 # the MEGA half held: Run/Stop is a combo component
        w &= ~KEY_RUNSTOP
    return w


class KeyScanner:
    """KEYB$SCAN + KEYB$GETKEY: a key counts again only after a release; up/down repeat"""

    def __init__(self):
        self.pressed = 0
        self.new = 0
        self.rep = {KEY_UP: [None, False], KEY_DOWN: [None, False]}

    def scan(self, word, now):
        self.new |= ~self.pressed & word
        self.pressed = word
        for k, st in self.rep.items():
            if not word & k:
                st[0], st[1] = None, False
                continue
            if st[0] is None:
                st[0] = now
            limit = TYPEMATIC_RATE if st[1] else TYPEMATIC_DELAY
            if now - st[0] >= limit:
                self.pressed &= ~k              # "unpress": counts as new at the next scan
                st[0], st[1] = now, True

    def getkey(self):
        if not self.new:
            return 0
        k = self.new & -self.new                # the lowest bit first, like KEYB$GETKEY
        self.new &= ~k
        return k


# ---------------------------------------------------------------------------------------------
# the overlay VRAM
# ---------------------------------------------------------------------------------------------

class Screen:
    def __init__(self):
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

    # ---- to the hardware
    def flush(self, bus):
        from hostbus import DEV_VRAM_DATA, DEV_VRAM_ATTR
        n = 0
        for dev, cur, sent in ((DEV_VRAM_DATA, self.ch, self.sent_ch), (DEV_VRAM_ATTR, self.at, self.sent_at)):
            lo = next((i for i in range(DX * DY) if cur[i] != sent[i]), None)
            if lo is None:
                continue
            hi = next(i for i in range(DX * DY - 1, -1, -1) if cur[i] != sent[i])
            bus.write_words(dev, lo, cur[lo:hi + 1])
            sent[lo:hi + 1] = cur[lo:hi + 1]
            n += hi + 1 - lo
        return n

    def invalidate(self):
        self.sent_ch = [None] * (DX * DY)
        self.sent_at = [None] * (DX * DY)

    def text(self, x0=0, y0=0, w=DX, h=DY):
        from model import _glyph
        return [''.join(_glyph(self.ch[y * DX + x]) for x in range(x0, x0 + w)) for y in range(y0, y0 + h)]


# ---------------------------------------------------------------------------------------------
# the menu definition (generated from config.vhd) plus the shell's own items
# ---------------------------------------------------------------------------------------------

class Item:
    __slots__ = ('text', 'raw', 'word', 'g', 'stdsel', 'line', 'start', 'mount', 'help', 'crtrom',
                 'single', 'submenu', 'headline', 'cfm_bit', 'key', 'shell')

    def __init__(self, text, raw, cfm_bit=None, shell=None):
        self.text, self.raw = text, raw
        self.word = (raw & 0x8000) | (raw & 0x4000) | (raw & 0x1000) | (raw & 0xFF)
        self.g = raw & 0xFF
        self.stdsel = bool(raw & 0x100)
        self.line = bool(raw & 0x200)
        self.start = bool(raw & 0x400)
        self.mount = bool(raw & 0x800)
        self.help = bool(raw & 0x2000)
        self.crtrom = bool(raw & 0x10000)
        self.single = bool(raw & 0x8000)
        self.submenu = bool(raw & 0x4000)
        self.headline = bool(raw & 0x1000)
        self.cfm_bit = cfm_bit
        self.key = None
        self.shell = shell              # name of a shell-owned setting (no CFM bit)


# the shell-owned section (lead 2026-11-13): inserted before the "About & Help" block, as a
# submenu, so the core's 94 lines and their CFM bits stay exactly as config.vhd defines them
G_SHELL_RTC = 0xF0
SHELL_SECTION = [
    ('', 0x200, None),                                            # line
    (' Linux Module', 0xC000, None),                              # submenu start
    (' Real-time clock from module', 0x8000 | G_SHELL_RTC, 'rtc'),
    ('', 0x200, None),
    (' On: the Amiga clock is set', 0, None),
    (' at start and every minute.', 0, None),
    (' Off: takes effect at the', 0, None),
    (' next cold boot.', 0, None),
    ('', 0x200, None),
    (' Back to main menu', 0xC0FF, None),                         # submenu end
]


class MenuDef:
    def __init__(self, data, shell_section=True):
        self.data = data
        self.core_groups = {v: k for k, v in data['core_groups'].items()}
        self.items = []
        for n, (text, raw) in enumerate(zip(data['items'], data['groups'])):
            it = Item(text, raw, cfm_bit=n)
            self.items.append(it)
        # the shell section goes in front of the first help item's separator line
        help_at = next(i for i, it in enumerate(self.items) if it.help)
        at = help_at - 1 if help_at and self.items[help_at - 1].line else help_at
        shell = [Item(t, raw, shell=name) for t, raw, name in SHELL_SECTION] if shell_section else []
        self.items[at:at] = shell
        self.n_shell_main = 2 if shell_section else 0   # main-menu lines (line + headline)
        for it in self.items:
            it.key = self._key(it)
        self.dx = data['optm_dx'] + 2
        self.dy = data['optm_dy'] + self.n_shell_main + 2
        self.x = DX - self.dx
        self.y = 0
        self.start = next(i for i, it in enumerate(self.items) if it.start)
        self.strings = data['strings']
        self.help_sets = data['help']
        self.size = len(self.items)

    def _key(self, it):
        """the name a setting is stored under (amiga.settings), None for non-settings"""
        if it.shell:
            return 'shell:' + it.shell
        if it.g in (0, OPTM_CLOSE) or it.submenu or it.crtrom or it.mount or it.help:
            return None
        name = self.core_groups.get(it.g, 'G%d' % it.g)
        if name == 'SCRRELOAD':                 # momentary action, not a setting
            return None
        return name + ':' + it.text.strip()

    def index_of_cfm(self, bit):
        return next(i for i, it in enumerate(self.items) if it.cfm_bit == bit)


# ---------------------------------------------------------------------------------------------
# the menu engine (menu.asm OPTM_* + options.asm OPT_* / OPTM_CB_SHOW)
# ---------------------------------------------------------------------------------------------

class Menu:
    def __init__(self, mdef, screen):
        self.d = mdef
        self.scr = screen
        self.level = 0                  # OPTM_MENULEVEL (kept across openings, like the firmware)
        self.mainsel = 0
        self.selected = mdef.start      # OPTM_SELECTED: where the cursor starts next time
        self.cur = mdef.start
        self.stdsel = [False] * mdef.size
        self.labels = {}                # item index -> replacement for "%s" (ADF file name, ...)
        self.on_space = None            # generator(n) -> True if it used the SPACE (ADF eject)

    # _OPTM_STRUCT: per item its (sub)menu number and whether it is visible at the current level
    def struct(self):
        ids, region, cur, nxt = [], False, 0, 1
        for it in self.d.items:
            if it.submenu:
                if not region:
                    region, cur = True, nxt
                    nxt += 1
                    ids.append(cur)
                else:
                    ids.append(cur)
                    region, cur = False, 0
            else:
                ids.append(cur)
        vis = [i == self.level for i in ids]
        last, first = 0, True
        for n, i in enumerate(ids):
            if i != last:
                last, first = i, True
            if not vis[n]:
                if self.level == 0 and first:
                    first = False
                    vis[n] = True
            elif self.level != 0 and first:
                first = False
                vis[n] = False
        return ids, vis

    def row_of(self, n, vis=None):
        vis = vis or self.struct()[1]
        if not vis[n]:
            raise ValueError('menu index %d not on this level' % n)
        return sum(vis[:n])

    def summary(self, n):
        """OPTM_CB_SHOW case (a): the first selected group item inside the submenu"""
        items = self.d.items
        for k in range(n + 1, len(items)):
            it = items[k]
            if it.submenu:
                return None
            if 1 <= it.word <= 254 or (1 <= it.g <= 254 and not it.word & ~0xFF):
                if self.stdsel[k]:
                    return it.text.lstrip(' ')
        return None

    def replacement(self, n):
        it = self.d.items[n]
        if it.submenu:
            s = self.summary(n)
            return s if s is not None else ''
        if it.crtrom:
            return self.labels.get(n) or self.d.strings['crtrom']
        if it.mount:
            return self.labels.get(n) or self.d.strings['mount']
        return None

    @staticmethod
    def rpl_s(src, repl, maxw):
        """M2M$RPL_S: replace the first %s, shorten with "..." to maxw characters"""
        p = src.index('%s')
        pre, post = src[:p], src[p + 2:]
        if len(pre) + len(repl) > maxw:
            out = (pre + repl)[:maxw]
            return out[:-3] + '...'
        out = pre + repl
        if len(out) < maxw and post:
            out += post[:maxw - len(out)]
        return out

    # OPTM_SHOW
    def show(self):
        s, d = self.scr, self.d
        ids, vis = self.struct()
        s.clr()
        s.frame(d.x, d.y, d.dx, d.dy)
        for n, it in enumerate(d.items):            # OPT_PRINTSTR: each line ends with "\n"
            if vis[n]:
                s.printstr(it.text + '\\n')
        rows = {}
        r = 0
        for n in range(d.size):
            if vis[n]:
                rows[n] = r
                r += 1
        for n, row in rows.items():                 # headlines
            if d.items[n].headline:
                s.attr_span(d.x + 1, d.y + 1 + row, d.dx - 2, SA_TTLE)
        for n, row in rows.items():                 # "%s"
            it = d.items[n]
            if '%s' in it.text:
                rep = self.replacement(n)
                if rep is not None:
                    s.printxy(self.rpl_s(it.text, rep, d.dx - 2), d.x + 1, d.y + 1 + row)
        for n, row in rows.items():                 # selection markers and separator lines
            it = d.items[n]
            if self.stdsel[n]:
                s.printxy(chr(SEL_SINGLE if it.word & W_SINGLESEL else SEL_MULTI), d.x + 1, d.y + 1 + row)
            elif it.line:
                self.hline(1 + row)
        if self.level != 0:                         # a short submenu: line under its last item
            y = 1 + len(rows)
            if y <= d.y + d.dy - 3:
                self.hline(y)

    def hline(self, y):
        d = self.d
        self.scr.printxy(chr(NC_VE_LEFT) + chr(NC_SH) * (d.dx - 2) + chr(NC_VE_RIGHT), d.x, y)

    # OPT_SELECT
    def select_row(self, row, attr):
        d = self.d
        self.scr.attr_span(d.x + 1, d.y + 1 + row, d.dx - 2, attr)

    def select(self, n, on=True):
        self.select_row(self.row_of(n), SA_STD_INV if on else SA_STD)

    def marker(self, n, ch):
        self.scr.printxy(ch, self.d.x + 1, self.d.y + 1 + self.row_of(n))

    # OPTM_SET "from the outside"
    def set_item(self, n, on):
        it = self.d.items[n]
        vis = self.struct()[1]
        cleared = None
        if on and not it.single:
            for k, o in enumerate(self.d.items):
                if k != n and o.word == it.word and self.stdsel[k]:
                    self.stdsel[k] = False
                    cleared = k
                    if vis[k]:
                        self.marker(k, ' ')
        self.stdsel[n] = on
        if vis[n]:
            self.marker(n, (chr(SEL_SINGLE if it.single else SEL_MULTI)) if on else ' ')
        return cleared

    def selectable(self, n):
        it = self.d.items[n]
        return it.submenu or it.g != 0

    # OPTM_RUN.  on_select(n, word, r9, key) is a generator (the shell's OPTM_CB_SEL).
    # Returns the index the menu was closed on.
    def run(self, cur, on_select):
        d = self.d
        size = d.size
        old = cur
        while True:
            vis = self.struct()[1]
            if not vis[cur]:
                raise RuntimeError('menu cursor %d not on level %d' % (cur, self.level))
            self.select(cur, True)
            self.cur = cur
            k = yield KEY
            if k == KEY_UP or k == KEY_DOWN:
                step = -1 if k == KEY_UP else 1
                n = cur
                while True:
                    n = (n + step) % size
                    if vis[n] and self.selectable(n):
                        break
                self.select(old, False)
                cur = old = n
                continue
            if k == KEY_HELP:
                return cur
            if k == KEY_RUNSTOP:
                if self.level == 0:
                    return cur
                cur = old = yield from self._level_change(None, cur)
                continue
            if k == KEY_SPACE and self.on_space is not None:
                used = yield from self.on_space(cur)
                if used:
                    continue
            if k not in (KEY_RETURN, KEY_SPACE):
                continue
            it = d.items[cur]
            if it.submenu:
                cur = old = yield from self._level_change(it, cur)
                continue
            if self.stdsel[cur]:
                if not it.single:
                    continue                        # radio item already selected: no double fire
                self.stdsel[cur] = False
                self.marker(cur, ' ')
                yield from on_select(cur, it.word, 0, k)
                continue
            if it.single:
                self.marker(cur, chr(SEL_SINGLE))
                self.stdsel[cur] = True
                r9 = 1
            else:
                for n2, o in enumerate(d.items):
                    if o.word == it.word and n2 != cur:
                        self.marker(n2, ' ')        # (fatal in the firmware if not on this level)
                        self.stdsel[n2] = False
                self.marker(cur, chr(SEL_MULTI))
                self.stdsel[cur] = True
                r9 = sum(1 for n2 in range(cur) if d.items[n2].word == it.word)
            yield from on_select(cur, it.word, r9, k)
            if it.word == OPTM_CLOSE:
                return cur

    def _level_change(self, it, cur):
        if it is None or it.g == OPTM_CLOSE:        # leave the submenu
            self.level = 0
            cur = self.mainsel
        else:                                       # enter
            ids = self.struct()[0]
            self.level = ids[cur]
            self.mainsel = cur
            n = cur + 1
            while n < self.d.size and self.d.items[n].g == 0:
                n += 1
            if n >= self.d.size:
                raise RuntimeError('submenu without a selectable item')
            cur = n
        self.show()
        return cur
        yield                                       # (a generator, like the callers expect)


# ---------------------------------------------------------------------------------------------
# file browser (selectfile.asm + dirbrowse.asm) on a directory tree of the module
# ---------------------------------------------------------------------------------------------

def _sort_key_cmp(a, b):
    """dirbrowse.asm _DIRBR_COMPARE: case-insensitive, directories first (their brackets sort as
    1 / 2), and names that are numbers compare by length first"""
    def norm(s):
        return s.upper().replace('<', '\x01').replace('>', '\x02')

    def isnum(s):
        t = s.replace('\x01', '').replace('\x02', '')
        return s != '' and all(c in '\x01\x02' or '0' <= c <= '9' for c in s) and t != ''
    a, b = norm(a), norm(b)
    if isnum(a) and isnum(b) and len(a) != len(b):
        return -1 if len(a) < len(b) else 1
    return (a > b) - (a < b)


class Browser:
    def __init__(self, root, screen, file_filter, start=None):
        self.root = os.path.realpath(root)
        self.cwd = os.path.realpath(start) if start else self.root
        self.scr = screen
        self.filter = file_filter       # name (upper case), is_dir -> True = hide
        self.cursor = 0                 # absolute index of the selected entry (kept between calls)
        self.stack = []                 # cursor positions of the parent directories

    def entries(self):
        import functools
        out = []
        try:
            names = os.listdir(self.cwd)
        except OSError:
            names = []
        if self.cwd != self.root:
            names.append('..')
        for n in names:
            if n.startswith('.') and n != '..':
                continue                # hidden (FAT32$FA_DEFAULT shows no hidden entries)
            p = os.path.join(self.cwd, n)
            is_dir = os.path.isdir(p)
            if self.filter(n.upper(), is_dir):
                continue
            out.append('<%s>' % n if is_dir else n)
        out.sort(key=functools.cmp_to_key(_sort_key_cmp))
        return out

    def run(self):
        """generator: returns the selected path, or None (Run/Stop) """
        s = self.scr
        rows = DY - 2
        while True:
            ents = self.entries()
            s.clrinner()
            if not ents:
                s.printstr('This directory contains no files that\nmatch the criteria of this core.\n\n'
                           'Press Space to continue.')
                while (yield KEY) != KEY_SPACE:
                    pass
                return None
            total = len(ents)
            cur = min(self.cursor, total - 1)
            top = (cur // rows) * rows
            if top + rows > total:
                top = max(0, total - rows)
            while True:
                s.clrinner()
                for i, e in enumerate(ents[top:top + rows]):
                    if len(e) > DX - 2:
                        e = e[:DX - 2 - 3] + '...'
                    s.printstr(e + '\\n')
                row = cur - top
                s.attr_span(1, 1 + row, DX - 2, SA_STD_INV)
                k = yield KEY
                s.attr_span(1, 1 + row, DX - 2, SA_STD)
                if k == KEY_UP:
                    if cur > 0:
                        cur -= 1
                        if cur < top:
                            top = cur
                elif k == KEY_DOWN:
                    if cur < total - 1:
                        cur += 1
                        if cur >= top + rows:
                            top = cur - rows + 1
                elif k == KEY_LEFT:
                    if top == 0:
                        cur = 0
                    else:
                        step = min(rows, top)
                        top -= step
                        cur -= step
                elif k == KEY_RIGHT:
                    remaining = total - (top + rows)
                    if remaining <= 0:
                        cur = total - 1
                    else:
                        step = min(rows, remaining)
                        top += step
                        cur = min(cur + step, total - 1)
                elif k == KEY_RUNSTOP:
                    self.cursor = cur
                    return None
                elif k == KEY_RETURN:
                    e = ents[cur]
                    if e.startswith('<') and e.endswith('>'):
                        name = e[1:-1]
                        if name == '..':
                            self.cwd = os.path.dirname(self.cwd)
                            self.cursor = self.stack.pop() if self.stack else 0
                        else:
                            self.stack.append(cur)
                            self.cwd = os.path.join(self.cwd, name)
                            self.cursor = 0
                        break
                    self.cursor = cur
                    return os.path.join(self.cwd, e)
                # (F1 / F3, the SD card slot keys, have no meaning here: one file system)


# ---------------------------------------------------------------------------------------------
# help pages (whs.asm)
# ---------------------------------------------------------------------------------------------

def show_pages(screen, pages):
    """generator: Left / Right page, Space or Run/Stop ends"""
    p = 0
    while True:
        screen.frame_fullscr()
        screen.printstr(pages[p])
        while True:
            k = yield KEY
            if k in (KEY_SPACE, KEY_RUNSTOP):
                return
            if k == KEY_LEFT and p > 0:
                p -= 1
                break
            if k == KEY_RIGHT and p < len(pages) - 1:
                p += 1
                break


def message(screen, text):
    """a full-screen message (replaces the firmware's FATAL screen and the warnings)"""
    screen.frame_fullscr()
    screen.printstr(text)
