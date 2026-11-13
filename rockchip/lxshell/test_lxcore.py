#!/usr/bin/env python3
"""Tests of the core-independent layer (lxcore) on its own: no Amiga, no M2M, no bus.

    python3 test_lxcore.py          (or: pytest test_lxcore.py)

1. the layer rule: nothing in lxcore imports an adapter or names a QNICE register;
2. a hand-written menu (lxcore.menu description format) runs on an in-memory display port:
   drawing, cursor, submenu with its summary, radio group, toggle, help pages, settings round trip.
"""

import ast
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lxcore import menu as lm, settings as st                    # noqa: E402
from lxcore.display import Screen, SA_TTLE, SA_STD_INV           # noqa: E402
from lxcore.keys import KEY, KEY_DOWN, KEY_RETURN, KEY_RUNSTOP, KEY_SPACE, KEY_HELP   # noqa: E402
from lxcore.pages import show_pages                              # noqa: E402

QNICE_REGS = re.compile(r'0x(FFE[0-9A-F]|FFF[0-9A-F]|FFC[0-9A-F])\b', re.I)


def test_layer_rule():
    d = os.path.join(HERE, 'lxcore')
    for name in sorted(os.listdir(d)):
        if not name.endswith('.py'):
            continue
        src = open(os.path.join(d, name)).read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or '']
            for m in mods:
                assert not m.startswith('lxamiga'), '%s imports %s' % (name, m)
                assert m.split('.')[0] in ('lxcore', 'os', 'sys', 'json', 'time', 'zlib', 'struct',
                                           'functools', 'hostbus', 'spidev'), '%s imports %s' % (name, m)
        code = '\n'.join(l.split('#')[0] for l in src.splitlines())
        for word in ('RAMROM', 'CFM_', 'CFD_', 'OSM_XY', 'OSM_DXDY', 'START_MARKER', 'DEV_KICK', 'DEV_ADF'):
            assert word not in code, '%s names %s' % (name, word)
        if name != 'fatimg.py':             # FAT end-of-chain values look like register numbers
            m = QNICE_REGS.search(code)
            assert not m, '%s names a QNICE register: %s' % (name, m.group() if m else '')


DESC = {
    'window': {'dx': 25, 'dy': 14},
    'strings': {'file': '<Insert>'},
    'help': [['\\n Help page one', '\\n Help page two']],
    'items': [
        {'type': 'headline', 'text': ' My Core'},
        {'type': 'line'},
        {'type': 'file', 'text': ' Disk:%s', 'start': True},
        {'type': 'line'},
        {'type': 'submenu', 'text': ' Video: %s'},
        {'type': 'text', 'text': ' Video Mode'},
        {'type': 'line'},
        {'type': 'radio', 'text': ' 720p', 'group': 'video', 'default': True},
        {'type': 'radio', 'text': ' 576p', 'group': 'video'},
        {'type': 'line'},
        {'type': 'end', 'text': ' Back to main menu'},
        {'type': 'toggle', 'text': ' Scanlines', 'group': 'scan', 'key': 'video:scanlines'},
        {'type': 'help', 'text': ' About', 'pages': 0},
        {'type': 'close', 'text': ' Close Menu'},
    ]}


class Port:
    """an in-memory display port"""

    def __init__(self):
        self.cells = {'chars': {}, 'attrs': {}}
        self.win = None

    def write_cells(self, kind, first, values):
        for i, v in enumerate(values):
            self.cells[kind][first + i] = v

    def window(self, w):
        self.win = w


def drive(gen, keys):
    """run a menu coroutine with a list of keys; returns what it returned"""
    req = next(gen)
    for k in keys:
        assert req == KEY, req
        try:
            req = gen.send(k)
        except StopIteration as e:
            return e.value
    raise AssertionError('the menu did not end')


def rows(scr, x0=20, w=25, h=14):
    return [r[x0:x0 + w] for r in scr.text()[:h]]


def test_hand_written_menu():
    model = lm.from_description(DESC)
    scr = Screen()
    m = lm.Menu(model, scr)
    m.stdsel = st.factory(model)
    m.show()
    r = rows(scr)
    assert r[0] == '+' + '=' * 23 + '+'
    assert r[1] == '| My Core               |'
    assert r[3] == '| Disk:<Insert>         |'
    assert r[5] == '| Video: 720p           |', r[5]
    assert r[6] == '| Scanlines             |'
    assert scr.at[1 * 45 + 21] == SA_TTLE
    selected = []

    def on_select(n, word, r9, key):
        selected.append((model.items[n].text.strip(), r9))
        if model.items[n].help is not None:
            yield from show_pages(scr, model.help_sets[model.items[n].help])
            m.show()
        return
        yield
    # Down to Video, enter, Down to 576p, select, Run/Stop back, Down to Scanlines, toggle,
    # About (help: Space closes), Close Menu
    end = drive(m.run(model.start, on_select),
                [KEY_DOWN, KEY_RETURN, KEY_DOWN, KEY_RETURN, KEY_RUNSTOP, KEY_DOWN, KEY_RETURN,
                 KEY_DOWN, KEY_RETURN, KEY_SPACE, KEY_DOWN, KEY_RETURN])
    assert model.items[end].text == ' Close Menu'
    assert selected == [('576p', 1), ('Scanlines', 1), ('About', 1), ('Close Menu', 0)], selected
    m.show()
    assert rows(scr)[5] == '| Video: 576p           |'
    # settings round trip by name
    v = st.values_from_selection(model, m.stdsel)
    assert v == {'video:720p': 0, 'video:576p': 1, 'video:scanlines': 1}, v
    with tempfile.TemporaryDirectory(dir=os.environ.get('LXSHELL_TMP')) as tmp:
        p = os.path.join(tmp, 'core.settings')
        st.save(p, v, 0x1234)
        back = st.selection_from_values(model, st.load(p))
        keyed = [n for n, it in enumerate(model.items) if it.key]
        assert [back[n] for n in keyed] == [m.stdsel[n] for n in keyed]
    # the display port gets the cells
    port = Port()
    scr.flush(port)
    assert port.cells['chars'][0 * 45 + 20] == 201 and len(port.cells['attrs']) > 0


def test_cursor_highlight():
    model = lm.from_description(DESC)
    scr = Screen()
    m = lm.Menu(model, scr)
    m.stdsel = st.factory(model)
    m.show()
    g = m.run(model.start, lambda *a: iter(()))
    assert next(g) == KEY
    assert scr.at[3 * 45 + 21] == SA_STD_INV            # the start item is highlighted
    try:
        g.send(KEY_HELP)                                 # closes the menu
        raise AssertionError('Help did not close the menu')
    except StopIteration as e:
        assert e.value == model.start


if __name__ == '__main__':
    bad = 0
    for n, f in sorted(globals().items()):
        if n.startswith('test_') and callable(f):
            try:
                f()
                print('PASS', n)
            except Exception:
                bad += 1
                import traceback
                print('FAIL', n)
                traceback.print_exc()
    sys.exit(1 if bad else 0)
