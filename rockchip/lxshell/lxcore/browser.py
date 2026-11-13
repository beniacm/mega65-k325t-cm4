"""lxcore/browser.py -- the file browser (M2M selectfile.asm + dirbrowse.asm) on a directory tree:
case-insensitive sort, directories first as <name>, numbers by length, one window of rows,
Up / Down / Left (page up) / Right (page down) / Return (enter or choose) / Run-Stop (cancel); the
position is kept between calls."""

import os

from lxcore.display import SA_STD, SA_STD_INV
from lxcore.keys import KEY, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_RETURN, KEY_RUNSTOP, KEY_SPACE


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
        rows = s.dy - 2
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
                    if len(e) > s.dx - 2:
                        e = e[:s.dx - 2 - 3] + '...'
                    s.printstr(e + '\\n')
                row = cur - top
                s.attr_span(1, 1 + row, s.dx - 2, SA_STD_INV)
                k = yield KEY
                s.attr_span(1, 1 + row, s.dx - 2, SA_STD)
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
