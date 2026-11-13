"""lxcore/keys.py -- menu keys from the MEGA65 keyboard matrix (m65d's input state).

Key bits as M2M$KEYBOARD numbers them (high-active here); KeyScanner = KEYB$SCAN + KEYB$GETKEY:
a key counts again only after a release, cursor up / down repeat (0.5 s, 12/s).  The menu-open key
is chosen by the core adapter (key numbers, optional two-key combination).
"""

# M2M$KEYBOARD bits (high-active here)
KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = 0x001, 0x002, 0x004, 0x008
KEY_RETURN, KEY_SPACE, KEY_RUNSTOP, KEY_HELP = 0x010, 0x020, 0x040, 0x080
KEY_F1, KEY_F3 = 0x100, 0x200

KEY = 'key'                         # coroutine request: the next key

KEY = 'key'                         # coroutine request: the next key

TYPEMATIC_DELAY, TYPEMATIC_RATE = 0.5, 1.0 / 12


def m65_keys(cols, ctrl, osm_a=67, osm_b=67, combo=False):
    """the 10-bit menu-key word from the MEGA65 keyboard matrix that m65d sends (what M2M's
    m2m_keyb.vhd builds, high-active): cols = 9 matrix columns (bit = row,
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
