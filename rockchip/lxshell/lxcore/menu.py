"""lxcore/menu.py -- the on-screen menu: items, radio groups, toggles, submenus, help pages, a file
item; drawn into a lxcore.display.Screen with M2M's look and rules (a port of M2M V2.0.1's menu.asm
and options.asm: frame, headlines, "%s" summaries, bullets / "=" markers, separator lines, cursor
skipping, wrap-around, submenu enter / leave, single-select toggles that fire every time, radio items
that do not fire twice).

A menu is a list of Items.  Two sources:
  * from_description(dict): a hand-written menu (for a core with its own settings, no config.vhd):
        {"window": {"dx": 25, "dy": 26},            # with the frame; optional "x", "y"
         "strings": {"file": "<Load>"},              # optional
         "help": [["page 1 text", "page 2 text"]],   # page sets; "\\n" = new line
         "items": [
            {"type": "headline", "text": " My Core"},
            {"type": "line"},
            {"type": "file", "text": " Disk:%s", "group": "disk", "start": true},
            {"type": "submenu", "text": " Video: %s"},          # summary = the chosen radio item
            {"type": "text", "text": " Video Mode"},
            {"type": "radio", "text": " 720p", "group": "video", "default": true},
            {"type": "radio", "text": " 576p", "group": "video"},
            {"type": "end", "text": " Back to main menu"},
            {"type": "toggle", "text": " Scanlines", "group": "scan", "key": "video:scanlines"},
            {"type": "help", "text": " About & Help", "pages": 0},
            {"type": "close", "text": " Close Menu"}]}
    Radio and toggle items are settings (stored by "key", default "<group>:<text>").
  * a core adapter converting its own format (lxamiga.m2m: the .menu file from config.vhd).
"""

from lxcore.display import (DX, SA_STD, SA_STD_INV, SA_TTLE, NC_VE_LEFT, NC_SH, NC_VE_RIGHT,
                            SEL_MULTI, SEL_SINGLE)
from lxcore.keys import KEY, KEY_UP, KEY_DOWN, KEY_RETURN, KEY_SPACE, KEY_RUNSTOP, KEY_HELP

CLOSE = 0xFF                        # group of "close" items (Close Menu, Back to main menu)
W_SINGLESEL, W_SUBMENU, W_HEADLINE = 0x8000, 0x4000, 0x1000


class Item:
    """one menu line.  group: 0 = not selectable, 1..254 a group (radio items of one group share
    it, a toggle has its own), CLOSE; the engine compares items by their "word" (group + kind) as
    M2M compares OPTM_GROUPS entries."""

    def __init__(self, text, group=0, single=False, submenu=False, headline=False, line=False,
                 default=False, start=False, help=None, file=False, key=None):
        self.text = text
        self.g = group
        self.single = single
        self.submenu = submenu
        self.headline = headline
        self.line = line
        self.default = default
        self.start = start
        self.help = help                # index of the help page set, or None
        self.file = file                # a file item ("%s" = the file name or strings['file'])
        self.key = key                  # settings name (None: not a setting)
        self.word = ((W_SINGLESEL if single else 0) | (W_SUBMENU if submenu else 0) |
                     (W_HEADLINE if headline else 0) | (group & 0xFF))
        self.radio = 1 <= self.word <= 254           # a plain group member (submenu summaries)


class MenuModel:
    """items + window geometry + strings + help pages"""

    def __init__(self, items, dx, dy, x=None, y=0, strings=None, help_sets=None, screen_dx=DX):
        self.items = items
        self.dx, self.dy = dx, dy
        self.x = screen_dx - dx if x is None else x
        self.y = y
        self.start = next((i for i, it in enumerate(items) if it.start), 0)
        self.strings = strings or {}
        self.help_sets = help_sets or []
        self.size = len(items)


def items_from_description(items, groups=None):
    """description entries -> Items (group names numbered from 1 in order of appearance; groups
    may be pre-filled to share numbering with another list)"""
    groups = {} if groups is None else groups

    def gnum(name):
        if name not in groups:
            groups[name] = len(groups) + 1
            if groups[name] >= CLOSE:
                raise ValueError('too many groups (at most 254)')
        return groups[name]
    out = []
    for d in items:
        t = d.get('type', 'text')
        text = d.get('text', '')
        if t == 'line':
            it = Item('', line=True)
        elif t == 'headline':
            it = Item(text, headline=True)
        elif t == 'text':
            it = Item(text)
        elif t == 'submenu':
            it = Item(text, submenu=True)
        elif t == 'end':
            it = Item(text, group=CLOSE, submenu=True)
        elif t == 'close':
            it = Item(text, group=CLOSE)
        elif t == 'radio':
            g = d['group']
            it = Item(text, group=gnum(g), default=d.get('default', False),
                      key=d.get('key', '%s:%s' % (g, text.strip())))
        elif t == 'toggle':
            g = d.get('group', text.strip())
            it = Item(text, group=gnum(g), single=True, default=d.get('default', False),
                      key=d.get('key', '%s:%s' % (g, text.strip())))
        elif t == 'help':
            it = Item(text, group=gnum(d.get('group', '__help%d' % d.get('pages', 0))), single=True,
                      help=d.get('pages', 0))
        elif t == 'file':
            it = Item(text, group=gnum(d.get('group', text.strip())), single=True, file=True)
        else:
            raise ValueError('unknown menu item type %r' % t)
        if d.get('start'):
            it.start = True
        out.append(it)
    return out


def from_description(desc, screen_dx=DX):
    w = desc.get('window', {})
    items = items_from_description(desc['items'])
    return MenuModel(items, w.get('dx', 25), w.get('dy', len(items) + 2), w.get('x'), w.get('y', 0),
                     desc.get('strings'), desc.get('help'), screen_dx)


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
            if it.radio:
                if self.stdsel[k]:
                    return it.text.lstrip(' ')
        return None

    def replacement(self, n):
        it = self.d.items[n]
        if it.submenu:
            s = self.summary(n)
            return s if s is not None else ''
        if it.file:
            return self.labels.get(n) or self.d.strings.get('file', '<Load>')
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
            if it.word == CLOSE:
                return cur

    def _level_change(self, it, cur):
        if it is None or it.g == CLOSE:        # leave the submenu
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
