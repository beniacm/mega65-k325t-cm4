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

    def xfer2(self, b):
        op = b[0]
        self.ops.append(op)
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
        other_cores(m)


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
