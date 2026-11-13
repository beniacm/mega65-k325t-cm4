#!/usr/bin/env python3
"""m65d + lxshell smoke test on the PC: a fake spidev in front of the model (model.py) stands in for
rk_bridge (STATUS, INPUT 0x30, HOST_PUT/GET/RESET), a stub replaces m65input (no /dev/input).
Checks the attach on core id 3, the keyboard gating while the menu is open and that the open
key never reaches the core.

    AEXP=~/projects/mega65/aexp-aga python3 test_m65d_lx.py
"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from lxamiga import model as mdl         # noqa: E402
import test_lxshell as T       # noqa: E402
import m65d                    # noqa: E402


class FakeSpi:
    def __init__(self, m, version=1):
        self.m = m
        self.inputs = []
        self.ops = []                   # every transfer's opcode
        self.version = version
        self.down = False               # bridge lost: every transfer answers zeros

    def xfer2(self, b):
        op = b[0]
        self.ops.append(op)
        if self.down:
            return [0] * len(b)
        if op == 0x01:
            st = bytearray(self.m.status().raw)
            st[72] = self.version
            out = [0, 0] + list(st) + [0] * max(0, len(b) - 2 - len(st))
            return out[:len(b)]
        if op == 0x30:
            self.inputs.append(list(b[1:]))
        elif op == 0x80:
            self.m.put(bytes(b[1:]))
        elif op == 0x81:
            return [0, 0] + list(self.m.get(len(b) - 2))
        elif op == 0x82:
            self.m.reset()
        return [0] * len(b)


class StubInput:
    def __init__(self):
        self.cols = [0] * 9
        self.ctrl = 0
        self.linux_focus = False
        self.actions = []

    def matrix(self):
        return list(self.cols)

    def joysticks(self):
        return 0, 0

    def ctrl_bits(self):
        return self.ctrl

    def pots(self):
        return (0, 0, 0xFF, 0)

    def take_pending(self):
        return None

    def set_linux_focus(self, on):
        self.linux_focus = on

    amiga_kbd = 'amiga'

    def set_amiga_keyboard(self, mode):
        self.amiga_kbd = mode


class Args:
    pass


def main():
    data, mid, defaults = T.menu_data()
    with tempfile.TemporaryDirectory(dir=os.environ.get('LXSHELL_TMP')) as tmp:
        rig = T.Rig(tmp)                    # files: menu, kick.rom, an ADF
        menus = os.path.join(tmp, 'menus')
        os.makedirs(menus)
        os.rename(rig.menu_path, os.path.join(menus, 'aexp_k325t.menu'))
        m = mdl.ModelMaster(menu_id=mid, defaults=defaults)
        d = m65d.Daemon.__new__(m65d.Daemon)
        d.br = m65d.Bridge.__new__(m65d.Bridge)
        d.br.spi = FakeSpi(m)
        d.args = Args()
        d.args.amiga_menus, d.args.amiga_dir, d.args.card = menus, rig.dir, None
        d.args.amiga_state = os.path.join(tmp, 'amiga.state')
        d.inp = StubInput()
        d.ctrl, d.ja, d.jb, d.cols = 0, 0, 0, [0] * 9
        d.input_dirty, d.pots_override, d.sent, d.lat = True, None, None, []
        d.lx, d.lx_retry, d.lx_kbd, d.core = None, 0.0, None, 3
        d.lx_core, d.lx_probed, d.lx_settle = None, False, 0.0

        def spin(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                d.lx_step()
                d.push_input()
                time.sleep(0.002)

        d.lx_check(3, False)
        time.sleep(1.05)                                # the settle time after a core change
        d.lx_check(3, False)
        assert d.lx is not None, 'not attached'
        spin(1.0)
        assert not m.held() and m.marker == 1 and m.cfm == defaults
        # a key goes to the core while the menu is closed
        d.inp.cols[1] = 1 << 2                          # "A"
        d.input_dirty = True
        spin(0.1)
        assert d.br.spi.inputs[-1][4] == 1 << 2, d.br.spi.inputs[-1]     # ctrl ja jb col0 col1
        d.inp.cols[1] = 0
        # Help opens the menu and never reaches the core
        d.inp.cols[8] = 1 << 3
        d.input_dirty = True
        spin(0.2)
        assert all(not (inp[3 + 8] >> 3 & 1) for inp in d.br.spi.inputs), 'Help reached the core'
        d.inp.cols[8] = 0
        d.input_dirty = True
        spin(0.2)
        assert d.lx.menu_open and m.csr & 4
        d.inp.cols[1] = 1 << 2                          # "A" while the menu is open: not forwarded
        d.input_dirty = True
        spin(0.1)
        assert d.br.spi.inputs[-1][3:12] == [0] * 9, d.br.spi.inputs[-1]
        d.inp.cols[1] = 0
        d.inp.cols[8] = 1 << 3                          # Help closes it
        d.input_dirty = True
        spin(0.1)
        d.inp.cols[8] = 0
        d.input_dirty = True
        spin(0.6)
        assert not d.lx.menu_open and d.lx.kbd_to_core and m.csr & 8
        print(d.amiga('status'))
        print('PASS m65d + lxshell')
        bridge_lost(d, m, spin)
        follow_rate()
        other_cores(m)
        view_refused(m)
        slot1_via_mega65(m)
        amiga_mega65_keyboard_mode()


def follow_rate():
    """m65d uses the SPI rate m65io publishes (its stats file), only while m65io sees the bridge;
    --hz fixes it"""
    import contextlib
    import io
    with tempfile.TemporaryDirectory(dir=os.environ.get('LXSHELL_TMP')) as tmp:
        stats = os.path.join(tmp, 'm65io.stats')

        class Spi:
            max_speed_hz = 10000000
        d = m65d.Daemon.__new__(m65d.Daemon)
        d.args = Args()
        d.args.stats, d.args.hz = stats, 0
        d.br = m65d.Bridge.__new__(m65d.Bridge)
        d.br.spi = Spi()
        d.spi_hz, d.last_down = m65d.DEFAULT_HZ, 7
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            d.follow_rate(d.bridge_down())          # no stats file yet: keeps the default
            assert d.br.spi.max_speed_hz == 10000000
            open(stats, 'w').write('rd=1 bridge_down=9 polls=5 spi_hz=33333333 bridge_ver=4\n')
            d.follow_rate(d.bridge_down())          # m65io's outage counter moved: not yet
            assert d.br.spi.max_speed_hz == 10000000
            d.last_down = 9
            d.follow_rate(d.bridge_down())          # settled: taken
            assert d.br.spi.max_speed_hz == 33333333 and d.spi_hz == 33333333
            d.follow_rate(d.bridge_down())          # unchanged: no second line
            open(stats, 'w').write('rd=1 bridge_down=9 polls=6 spi_hz=25000000 bridge_ver=4\n')
            d.follow_rate(d.bridge_down())          # m65io stepped down: followed
            assert d.br.spi.max_speed_hz == 25000000
            d.args.hz = 10000000
            open(stats, 'w').write('rd=1 bridge_down=9 polls=7 spi_hz=33333333 bridge_ver=4\n')
            d.follow_rate(d.bridge_down())          # --hz: fixed
            assert d.br.spi.max_speed_hz == 25000000
        assert out.getvalue().count('SPI rate') == 2, out.getvalue()
    print('PASS m65d follows m65io\'s SPI rate (only while the bridge is seen; --hz fixes it)')


def bridge_lost(d, m, spin):
    """the bridge stops answering with the Amiga running: one log line (no traceback), the shell
    detaches, and when the bridge is back it adopts the running machine (no reset, no Kickstart)"""
    import contextlib
    import io
    err = io.StringIO()
    out = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        d.br.spi.down = True
        spin(0.3)
        for _ in range(3):
            d.lx_retry = d.lx_settle = 0.0
            d.lx_check(3, False)
        assert d.lx is None
        d.br.spi.down = False
        d.lx_retry = d.lx_settle = 0.0
        d.lx_check(3, False)
        spin(0.3)
    assert 'Traceback' not in err.getvalue() + out.getvalue(), err.getvalue() + out.getvalue()
    log = out.getvalue()
    assert log.count('no bridge answer') == 1, log      # one line per outage (the detach)
    assert 'adopting the running Amiga' in log and 'Kickstart' not in log, log
    assert d.lx is not None and not m.held() and d.lx.kbd_to_core
    # a core switch from m65d detaches the shell before the bridge goes away
    d.br.iprog = lambda addr: None
    d.br.core_id = lambda: 3
    d.monitor = lambda *a, **k: None
    with contextlib.redirect_stdout(out):
        r = d.switch_core('mega65')
    assert r.startswith('ok') and d.lx is None and 'detached (core switch)' in out.getvalue(), r
    print('PASS bridge lost and back: one line, adopt; core switch detaches first')


def view_refused(m):
    """view linux (command and the Ctrl+Alt+Insert action) is refused on cores without the Linux
    picture: no VIDEO write, the keyboard stays with the core; on the MEGA65 it switches as before"""
    import contextlib
    import io
    runs = []
    real_run = m65d.subprocess.run
    m65d.subprocess.run = lambda *a, **k: runs.append(a)      # loadkeys: never on the PC
    try:
        with tempfile.TemporaryDirectory(dir=os.environ.get('LXSHELL_TMP')) as tmp:
            for core in (2, 3, 4):
                d = daemon(m, 1, tmp)
                d.core = core
                d.br.core_id = lambda core=core: core
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    r = d.command('view linux')
                    d.inp.actions.append('view toggle')
                    d.system_action(d.inp.actions.pop(0))
                assert r.startswith('err no Linux picture'), r
                assert 0x70 not in d.br.spi.ops and not d.inp.linux_focus and runs == [], (core, d.br.spi.ops)
                assert out.getvalue().count('view linux refused') == 2, out.getvalue()
                assert d.command('view mega65') == 'ok view mega65'    # always allowed
            d = daemon(m, 1, tmp)
            d.core = 1
            d.br.core_id = lambda: 1
            with contextlib.redirect_stdout(io.StringIO()):
                assert d.command('view linux') == 'ok view linux' and d.inp.linux_focus
                assert d.command('view toggle') == 'ok view mega65' and not d.inp.linux_focus
            assert d.br.spi.ops.count(0x70) == 2 and len(runs) == 1
    finally:
        m65d.subprocess.run = real_run
    print('PASS view linux refused on the C64 / Amiga / VIXEN-32 cores, unchanged on the MEGA65')


def slot1_via_mega65(m):
    """from a core other than the MEGA65, slot 1 is reached via the MEGA65 (the bridge's IPROG to a
    slot other than 0 lands in slot 0 after a JTAG load): IPROG 0, then the MEGA65's reconfig once it
    has run for a second; to the MEGA65 itself and from the MEGA65: one step as before"""
    import contextlib
    import io
    with tempfile.TemporaryDirectory(dir=os.environ.get('LXSHELL_TMP')) as tmp:
        d = daemon(m, 1, tmp)
        d.inp.linux_focus = False
        os.environ['M65_SLOT1'] = 'amiga'
        cur = [2]
        d.br.core_id = lambda: cur[0]
        iprogs, mons = [], []
        d.br.iprog = iprogs.append
        d.monitor = lambda line, wait=True: mons.append(line)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r = d.switch_core('amiga')
            assert r == 'ok switching to amiga via the MEGA65' and iprogs == [0] and mons == [], (r, iprogs)
            t = time.time()
            d.via_mega65_step(2, t + 0.5)           # still the C64 (reconfiguring)
            d.via_mega65_step(1, t + 3.0)           # the MEGA65 runs: wait a second for its monitor
            assert mons == []
            cur[0] = 1
            d.via_mega65_step(1, t + 4.1)
            assert mons == ['sffd36c8 00 80 00 00', 'sffd36cf 42'], mons
            d.via_mega65_step(1, t + 6.0)           # done: nothing more
            assert len(mons) == 2 and iprogs == [0]
            # C64 -> MEGA65: one IPROG to 0; MEGA65 -> Amiga: the reconfig, no IPROG
            cur[0] = 2
            assert d.switch_core('mega65') == 'ok switching to mega65' and iprogs == [0, 0]
            cur[0] = 1
            assert d.switch_core('amiga') == 'ok switching to amiga' and len(mons) == 4 and iprogs == [0, 0]
            # the MEGA65 never comes: given up after 30 s, one line
            cur[0] = 2
            d.switch_core('amiga')
            d.via_mega65_step(2, time.time() + 31)
            assert d.via_mega65 is None
        assert 'via the MEGA65 (flash 0x000000, then 0x800000)' in out.getvalue(), out.getvalue()
        assert out.getvalue().count('given up') == 1, out.getvalue()
    print('PASS slot 1 from another core: via the MEGA65 (IPROG 0, then its reconfig)')


# AExp doc/keyboard.md, MEGA65 mode ("the cap is law"): the character a MEGA65 key gives alone, with
# Shift, with MEGA -- written from the document's tables, independent of m65input's map
M65_PLAIN = {",": ",", ".": ".", "/": "/", ":": ":", ";": ";", "=": "=", "@": "@", "LEFTARROW": "_",
             "UPARROW": "^", "*": "*", "+": "+", "-": "-", "POUND": "\\", "SPACE": " ", "RETURN": "\n",
             "TAB": "\t"}
M65_SHIFT = {"1": "!", "2": '"', "3": "#", "4": "$", "5": "%", "6": "&", "7": "'", "8": "(", "9": ")",
             ",": "<", ".": ">", "/": "?", ":": "[", ";": "]", "=": "_", "LEFTARROW": "`", "POUND": "|"}
M65_MEGA = {",": "~", ".": "|", "/": "\\", ":": "{", ";": "}", "=": "_", "@": "@", "LEFTARROW": "`"}


def m65_mode_char(names):
    """the character the core types for a set of matrix key names in MEGA65 mode (None: none)"""
    keys = [n for n in names if n not in ("LSHIFT", "RSHIFT", "MEGA")]
    if len(keys) != 1:
        return None
    k = keys[0]
    shift, mega = bool({"LSHIFT", "RSHIFT"} & set(names)), "MEGA" in names
    if mega:
        return M65_MEGA.get(k)
    if len(k) == 1 and k.isalpha():
        return k.upper() if shift else k.lower()
    if len(k) == 1 and k.isdigit() and not shift:
        return k
    return (M65_SHIFT if shift else M65_PLAIN).get(k)


def amiga_mega65_keyboard_mode():
    """the Amiga core in its MEGA65 keyboard mode: every printable US character typed on a USB
    keyboard (and by m65d's type) reaches the core as the MEGA65 keys that give that character; the
    combo chosen at the press stays while the key is down, also when Shift is let go first"""
    import types
    import m65input as mi
    names_of = {v: k for k, v in m65d.KEYPOS.items() if k in m65d.MATRIX[v[0]]}

    def decode(cols):
        return {names_of[(c, r)] for c in range(9) for r in range(8) if cols[c] >> r & 1}
    bad = [(ch, mi.amiga_combo("mega65", *mi.US_CHARS[ch])) for ch in sorted(mi.US_CHARS)
           if m65_mode_char(mi.amiga_combo("mega65", *mi.US_CHARS[ch])) != ch]
    assert not bad, bad
    hub = mi.InputHub(m65d.KEYPOS, log=lambda m: None)
    hub.set_core(3)
    hub.set_amiga_keyboard("mega65")
    dev = types.SimpleNamespace(keys=set(), roles={"kbd"}, pad=None)
    hub.devs = {999: dev}

    def ev(code, val):
        hub._kbd_key(dev, code, val)
        hub._merge_keys()
    for ch in sorted(c for c in mi.US_CHARS if c.isprintable()):
        code, shifted = mi.US_CHARS[ch]
        if shifted:
            ev(42, 1)
        ev(code, 1)
        time.sleep(mi.MOD_LEAD + 0.005)
        got = decode(hub.matrix())
        assert m65_mode_char(got) == ch, (ch, got)
        if shifted:
            ev(42, 0)                       # Shift up first: a key with a combo of its own keeps it
            got2 = decode(hub.matrix())     # (one that passes the PC Shift through just loses it)
            own = mi.AMIGA_M65_KEYMAP[code][1] is not None
            assert got2 == (got if own else got - {"LSHIFT"}), (ch, got, got2)
        ev(code, 0)
        hub.held.clear()
        hub.lead.clear()
        assert decode(hub.matrix()) == set(), ch
    # positional mode (the menu's "Amiga" radio) keeps the old map: PC "-" -> the MEGA65 "+" key
    hub.set_amiga_keyboard("amiga")
    ev(12, 1)
    assert decode(hub.matrix()) == {"+"}
    ev(12, 0)
    # m65d's type on the Amiga core: the same map (the C64-style matrix names would give "[" for ":")
    d = m65d.Daemon.__new__(m65d.Daemon)
    d.keyq, d.core, d.inp = [], 3, StubInput()
    d.inp.amiga_kbd = 'mega65'
    text = 'copy s:startup-sequence ram:x_1 ; echo "[ok]"'
    assert d.command('type ' + text) == 'ok %d keys' % len(text)
    got = ''.join(m65_mode_char(decode(cols)) for _, cols in d.keyq
                  if m65_mode_char(decode(cols)) is not None)
    assert got == text, got
    print('PASS Amiga MEGA65 keyboard mode: all %d US characters, Shift released first keeps the combo; '
          'm65d type' % len(mi.US_CHARS))


def daemon(m, version, tmp):
    d = m65d.Daemon.__new__(m65d.Daemon)
    d.br = m65d.Bridge.__new__(m65d.Bridge)
    d.br.spi = FakeSpi(m, version)
    d.args = Args()
    d.args.amiga_menus = d.args.amiga_dir = d.args.card = None
    d.args.amiga_state = os.path.join(tmp, 'state')
    d.inp = StubInput()
    d.ctrl, d.ja, d.jb, d.cols = 0, 0, 0, [0] * 9
    d.input_dirty, d.pots_override, d.sent, d.lat = True, None, None, []
    d.lx, d.lx_retry, d.lx_kbd, d.core = None, 0.0, None, None
    d.lx_core, d.lx_probed, d.lx_settle = None, False, 0.0
    return d


def other_cores(m):
    """cores 1 (MEGA65), 2 (C64), 4 and an Amiga QNICE build: the shell never attaches; for 1, 2, 4
    not one SPI transfer comes from it and the INPUT register gets the keyboard unfiltered"""
    with tempfile.TemporaryDirectory(dir=os.environ.get('LXSHELL_TMP')) as tmp:
        for core in (1, 2, 4):
            d = daemon(m, 1, tmp)
            for _ in range(5):
                d.lx_check(core, False)
                d.lx_step()
            assert d.lx is None and d.br.spi.ops == [], (core, d.br.spi.ops)
            d.inp.cols[8] = 1 << 3                      # Help: reaches the core as today
            d.inp.cols[1] = 1 << 2
            d.input_dirty = True
            d.push_input()
            assert d.br.spi.ops == [0x30] and d.br.spi.inputs[-1][3:12] == [0, 4, 0, 0, 0, 0, 0, 0, 8], \
                d.br.spi.inputs[-1]
        # the Amiga with a QNICE bitstream (STATUS byte 72 = 0): one STATUS read, then nothing
        d = daemon(m, 0, tmp)
        for _ in range(5):
            d.lx_check(3, False)
            d.lx_retry = d.lx_settle = 0.0              # even with the timers run out
        assert d.lx is None and d.br.spi.ops == [0x01], d.br.spi.ops
        d.lx_check(3, True)                             # reconfigured: probed once more
        d.lx_settle = 0.0
        d.lx_check(3, False)
        assert d.br.spi.ops == [0x01, 0x01], d.br.spi.ops
    print('PASS other cores: no attach, no extra SPI traffic (QNICE Amiga: one STATUS per configuration)')


if __name__ == '__main__':
    main()
