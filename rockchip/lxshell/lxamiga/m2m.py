"""lxamiga/m2m.py -- the Amiga core's MiSTer2MEGA65 side: the host master's QNICE register map,
the M2M overlay as the display port, the menu from config.vhd (the .menu file of menu_gen.py) with
the shell's own section, CFM bits, the 93..95-byte settings file of the QNICE builds."""

import os

from lxcore import settings
from lxcore.bus import HostBus, C_RD
from lxcore.menu import Item, MenuModel, items_from_description

# ---- QNICE register map (unchanged from the firmware, PROTOCOL.md section 3)
CSR = 0xFFE0
OSM_XY = 0xFFE1
OSM_DXDY = 0xFFE2
ASCAL_MODE = 0xFFE3
CFD_ADDR, CFD_DATA = 0xFFF0, 0xFFF1
CFM_ADDR, CFM_DATA = 0xFFF2, 0xFFF3
RAMROM_DEV, RAMROM_4KWIN = 0xFFF4, 0xFFF5
RAMROM_DATA = 0x7000
MASTER_VERSION = 0xFFF8
MENU_ID_HI, MENU_ID_LO = 0xFFF9, 0xFFFA
START_MARKER = 0xFFFB
CFM_DEFAULTS = 0xFFC0                     # 0xFFC0..0xFFCF

CSR_RESET, CSR_PAUSE, CSR_OSM = 0x0001, 0x0002, 0x0004
CSR_KBD, CSR_JOY1, CSR_JOY2 = 0x0008, 0x0010, 0x0020
CSR_ASCAL_AUTO = 0x0800
CSR_POWERUP = 0x0839

# ---- devices behind RAMROM_DEV
DEV_VRAM_DATA, DEV_VRAM_ATTR = 0x0000, 0x0001
DEV_ASCAL_PPHASE = 0x0003
DEV_RTC = 0x0006
DEV_SYS_INFO = 0x00FF
DEV_KICK = 0x0100
DEV_ADF = 0x0103

# the QNICE builds' settings file has one byte per menu line (OPTM_SIZE bytes); the lines added over
# time, oldest first: (file size with the line, its line number, its settings key).  93 = the builds
# before the IDE line, 94 = before the "Fast 68020" line (CPUFAST, the fast-CPU builds: 95)
LEGACY_LINES = ((94, 87, 'IDE:IDE Hard Disk'), (95, 88, 'CPUFAST:Fast 68020'))
LEGACY_SIZES = (95, 94, 93)


class M2MBus(HostBus):
    """HostBus + the M2M device window (RAMROM_DEV / RAMROM_4KWIN / 0x7000) and the CFM registers"""

    def rd_many(self, addrs, dev=None, win=None):
        if dev is not None:
            self.wr(RAMROM_DEV, dev)
            self.wr(RAMROM_4KWIN, win)
        return HostBus.rd_many(self, addrs)

    def dev_wr(self, dev, win, off, data):
        """one write into a device (sets device and window first: bursts leave them changed)"""
        self.wr(RAMROM_DEV, dev)
        self.wr(RAMROM_4KWIN, win)
        self.wr(RAMROM_DATA + off, data)

    def dev_rd(self, dev, win, off):
        self.wr(RAMROM_DEV, dev)
        self.wr(RAMROM_4KWIN, win)
        return self.rd(RAMROM_DATA + off)

    def cfm_write(self, words, only=None):
        """write CFM windows (each WR to CFM_DATA is atomic per 16-bit window)"""
        for w in range(16) if only is None else only:
            self.wr(CFM_ADDR, w)
            self.wr(CFM_DATA, words[w])

    def cfm_read(self):
        """the 16 CFM windows in one round trip (the stream keeps the order: select, read, ...)"""
        for w in range(16):
            self.wr(CFM_ADDR, w)
            self.out += bytes([C_RD, CFM_DATA >> 8, CFM_DATA & 0xFF])
            self.expect += 2
        d = self._collect(32)
        return [d[i] << 8 | d[i + 1] for i in range(0, 32, 2)]

    def menu_id(self):
        return self.rd(MENU_ID_HI) << 16 | self.rd(MENU_ID_LO)

    def cfm_defaults(self):
        return [self.rd(CFM_DEFAULTS + i) for i in range(16)]


class Overlay:
    """the display port: VRAM data / attributes (devices 0 / 1) and the OSM window (OSM_XY,
    OSM_DXDY, CSR bit 2)"""

    def __init__(self, bus):
        self.bus = bus

    def write_cells(self, kind, first, values):
        self.bus.write_words(DEV_VRAM_DATA if kind == 'chars' else DEV_VRAM_ATTR, first, values)

    def window(self, w):
        b = self.bus
        csr = b.rd(CSR)
        if w is None:
            b.wr(CSR, csr & ~CSR_OSM)
            return
        x, y, dx, dy = w
        b.wr(OSM_XY, x << 8 | y)
        b.wr(OSM_DXDY, dx << 8 | dy)
        b.wr(CSR, csr | CSR_OSM)


# the shell-owned section (lead 2026-11-13), a hand-written menu description (lxcore.menu format):
# inserted before the "About & Help" block, so the core's lines (94, or 95 with "Fast 68020") and their
# CFM bits stay exactly as config.vhd defines them
SHELL_SECTION = [
    {'type': 'line'},
    {'type': 'submenu', 'text': ' Linux Module'},
    {'type': 'toggle', 'text': ' Real-time clock from module', 'group': 'shell:rtc', 'key': 'shell:rtc'},
    {'type': 'line'},
    {'type': 'text', 'text': ' On: the Amiga clock is set'},
    {'type': 'text', 'text': ' at start and every minute.'},
    {'type': 'text', 'text': ' Off: takes effect at the'},
    {'type': 'text', 'text': ' next cold boot.'},
    {'type': 'line'},
    {'type': 'end', 'text': ' Back to main menu'},
]
SHELL_MAIN_LINES = 2                # main-menu lines the section adds (line + headline)


def item_from_raw(text, raw, cfm_bit, help_index, key):
    """one config.vhd menu line (OPTM_ITEMS text + OPTM_GROUPS word) as a generic Item"""
    it = Item(text, group=raw & 0xFF, single=bool(raw & 0x8000), submenu=bool(raw & 0x4000),
              headline=bool(raw & 0x1000), line=bool(raw & 0x200), default=bool(raw & 0x100),
              start=bool(raw & 0x400), help=help_index if raw & 0x2000 else None,
              file=bool(raw & 0x10000), key=key)
    it.cfm_bit = cfm_bit
    it.shell = None
    return it


def menu_from_m2m(data, shell_section=True, screen_dx=45):
    """the .menu file (menu_gen.py, from config.vhd) + the shell section -> MenuModel"""
    core_groups = {v: k for k, v in data['core_groups'].items()}

    def key(raw, text):
        g = raw & 0xFF
        if g in (0, 0xFF) or raw & (0x4000 | 0x10000 | 0x800 | 0x2000):
            return None
        name = core_groups.get(g, 'G%d' % g)
        if name == 'SCRRELOAD':                 # momentary action, not a setting
            return None
        return name + ':' + text.strip()
    items, nhelp = [], 0
    for n, (text, raw) in enumerate(zip(data['items'], data['groups'])):
        if raw & 0x2000:
            nhelp += 1
        items.append(item_from_raw(text, raw, n, nhelp if raw & 0x2000 else None, key(raw, text)))
    if shell_section:
        help_at = next(i for i, it in enumerate(items) if it.help is not None)
        at = help_at - 1 if help_at and items[help_at - 1].line else help_at
        top = max(list(core_groups) + [0])
        groups = {('core', i): i for i in range(1, top + 1)}       # number after the core's groups
        shell = items_from_description(SHELL_SECTION, groups)
        for it in shell:
            it.cfm_bit = None
            it.shell = it.key.split(':', 1)[1] if it.key and it.key.startswith('shell:') else None
        items[at:at] = shell
    extra = SHELL_MAIN_LINES if shell_section else 0
    dx = data['optm_dx'] + 2
    m = MenuModel(items, dx, data['optm_dy'] + extra + 2, screen_dx - dx, 0,
                  {'file': data['strings']['crtrom']}, data['help'], screen_dx)
    m.data = data
    m.core_groups = core_groups
    m.n_shell_main = extra
    return m


def import_legacy(mdef, raw):
    """a QNICE settings file of 95, 94 or 93 bytes -> values, or None (defaults).
    Like the firmware: wrong size or first byte 0xFF = defaults; a byte other than 0/1 = corrupt.
    The file's lines are moved to the running menu's layout (LEGACY_LINES): a line the file is too
    old for is off; a line the running menu is too old for is not in the menu, its value is kept
    under its key (like an unknown key of amiga.settings: a later bitstream with the line uses it)."""
    if len(raw) not in LEGACY_SIZES or raw[0] == 0xFF or any(b > 1 for b in raw):
        return None
    bits = list(raw)
    size = mdef.data.get('size', len(mdef.data['items']))
    extra = {}
    for with_line, line, key in LEGACY_LINES:
        if len(raw) < with_line <= size:
            bits.insert(line, 0)                # the file has no such line yet: off
    for with_line, line, key in reversed(LEGACY_LINES):
        if size < with_line <= len(raw):
            extra[key] = bits.pop(line)         # the menu has no such line (yet)
    sel = settings.factory(mdef)
    for n, it in enumerate(mdef.items):
        if it.cfm_bit is not None and it.cfm_bit < len(bits):
            sel[n] = bool(bits[it.cfm_bit])
    values = settings.values_from_selection(mdef, sel)
    values.update(extra)
    return values


def cfm_words(mdef, sel, base=None):
    """the 16 CFM words for a selection (core items only; shell items have no CFM bit)"""
    words = list(base) if base else [0] * 16
    for n, it in enumerate(mdef.items):
        b = it.cfm_bit
        if b is None:
            continue
        if sel[n]:
            words[b // 16] |= 1 << (b % 16)
        else:
            words[b // 16] &= ~(1 << (b % 16))
    return words


def selection_from_cfm(mdef, words, shell_values=None):
    sel = []
    for it in mdef.items:
        if it.cfm_bit is not None:
            sel.append(bool(words[it.cfm_bit // 16] >> (it.cfm_bit % 16) & 1))
        else:
            sel.append(bool((shell_values or {}).get(it.key, 0)) if it.key else False)
    return sel
