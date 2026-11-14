"""lxamiga/shell.py -- the Amiga core's M2M shell on the Linux module (replaces the QNICE firmware).

AmigaShell runs inside m65d (no service of its own).  m65d calls:
    shell.poll(now)             often (every loop); returns the seconds until it wants the next call
    shell.keys(cols, ctrl, now) with the matrix state m65input builds (also when nothing changed)
    shell.filter_input(cols, ctrl) -> (cols, ctrl): what the core may see of the keyboard
                                (nothing while the menu is open; never the menu-open key)
Everything that QNICE did for this core is here (INVENTORY.md): start-up with the core held
(settings, Kickstart, filter, screen offsets), the menu, the floppy (mount from ADF files, eject
with SPACE, write-back of the tracks the Amiga wrote), the screen-mode detector, the clock, the
long-press reload; a restarted m65d adopts a running Amiga without touching it (start marker).

Foreground work (start sequence, a menu session) runs as one generator; it yields between bus
chunks, so a key event never waits for more than one chunk.  Background work (write-back,
screen detector, clock, long-press check) runs from poll() in small steps.
"""

import json
import os
import time
import zlib

from lxcore import settings as st
from lxcore.browser import Browser
from lxcore.display import FC_HE_LEFT, FC_HE_RIGHT, LD_PROGRESS
from lxcore.keys import KEY, KEY_SPACE, KEY_HELP, KEY_RUNSTOP
from lxcore.menu import Menu, CLOSE
from lxcore.pages import show_pages, message
from lxcore.runner import Runner
from lxamiga import m2m as hb

TRACK = 5632
ADF_MIN, ADF_MAX = 160 * TRACK, 166 * TRACK
KICK_SIZES = (256 << 10, 512 << 10)
CSR_RUN_IO = hb.CSR_KBD | hb.CSR_JOY1 | hb.CSR_JOY2

# adf_mount_wrapper windows (device 0x0103)
CSR_WIN, WBC_WIN = 0xFFFF, 0xFFFE
REQ_IDLE, REQ_LDNG, REQ_ERR, REQ_OK = 0, 1, 2, 3
PT_IDLE, PT_PRSNG, PT_OK, PT_ERR = 0, 1, 2, 3

# screen adjustment (m2m-rom.asm SCR_*).  Timing (the one place; DESIGN.md section 10): the
# firmware read the mode every 256th main-loop pass (a few ms) and switched after 5 equal readings
# (tens of ms); here one batched read every SCR_POLL_S, SCR_DEBOUNCE + 2 equal readings ->
# about 5 x 25 ms = 125 ms from a mode change to the new crop / overscan / pan.
SCR_POLL_S = 0.025
SCR_LORES = (367, 388)
SCR_HIRES = (744, 765)
SCR_NOSIG = 200
SCR_DEBOUNCE = 3
SCR_UNKNOWN = 4

# m2m-rom.asm WRN_ADF_SIZE; the shell prints "Error code: 0001", a new line, this, a new line and
# STR_SPACE (shell.asm _HM_SDMOUNTED5 / 5S: hence the two "press space" lines, as today)
WRN_ADF_SIZE = ("\\n\\nThis is not a valid ADF disk image:\\n"
                "the file size must be 901,120 bytes\\n"
                "(880 KB standard ADF; 81..83-track over-\\n"
                "dumps up to 934,912 bytes are accepted)."
                "\\n\\nPress SPACE to continue.\\n")
STR_SPACE = "Press Space to continue."
# screen offsets without a user file (aexp_screen.cfg in the Amiga directory, or on the card image):
# the AExp project's two ready-made files (doc/screen_adjust.md), installed next to this module by
# setup_rockchip.sh, chosen by the HDMI mode; 5:4 has none of its own and takes the 4:3 one (it pulls
# the edges in)
PRESET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'presets')
HDMI_PRESETS = (('HDMI_16_9_50', 'aexp_screen.cfg_16_9'), ('HDMI_4_3_50', 'aexp_screen.cfg_4_3'),
                ('HDMI_5_4_50', 'aexp_screen.cfg_4_3'))
# what the RAM submenu's summary shows with the AGA chipset chosen (summary_in_effect)
AGA_MEMORY = "2M Chip (AGA)"


def bcd(v):
    return (v // 10) << 4 | v % 10


class AmigaShell(Runner):
    def __init__(self, bus, menu_path, amiga_dir, card_img=None, state_path='/run/m65/amiga.state',
                 log=print, mono=time.monotonic, wall=time.time, localtime=time.localtime):
        Runner.__init__(self, bus, hb.Overlay(bus), log, mono)
        self.menu_path = menu_path
        self.dir = amiga_dir
        self.card_img = card_img
        self.state_path = state_path
        self.wall, self.localtime = wall, localtime
        self.holding = False            # the shell itself holds the core
        self.mdef = None
        self.menu = None
        self.menu_id = None
        self.adf = None                 # path of the mounted ADF, or None
        self.adf_fd = None
        self.adf_ro = False
        self.flush_iter = None
        self.flush_failed = set()
        self.screen_table = [0] * 40
        self.scr_cand, self.scr_cnt, self.scr_applied = None, 0, None
        self.values = {}                # the stored settings (amiga.settings), by name
        self.saved_values = None
        self.next_bg = self.next_scr = self.next_rtc = 0.0
        self.preset_dir = PRESET_DIR
        self.screen_src = None          # where the screen offsets came from (logged when it changes)

    # ------------------------------------------------------------------ files
    def path(self, name):
        return os.path.join(self.dir, name)

    def read_card(self, name):
        """a file from the old card image (read-only take-over), or None"""
        if not self.card_img or not os.path.exists(self.card_img):
            return None
        try:
            from lxcore import fatimg
            img = fatimg.FatImage(self.card_img)
            try:
                return img.read(name)
            finally:
                img.close()
        except FileNotFoundError:
            return None
        except (OSError, Exception) as e:            # a broken image must not stop the start
            self.log('lxshell: card image %s: %s' % (name, e))
            return None

    def load_menu(self):
        """the menu data file of the running bitstream: menu_path is the file, or a directory in
        which the *.menu file whose CRC-32 equals the FPGA's menu id is used (m65d does not know
        which bitstream is loaded: JTAG or flash)"""
        paths = [self.menu_path]
        if os.path.isdir(self.menu_path):
            paths = sorted(os.path.join(self.menu_path, n) for n in os.listdir(self.menu_path)
                           if n.endswith('.menu'))
        for p in paths:
            try:
                with open(p, 'rb') as f:
                    raw = f.read()
            except OSError:
                continue
            if zlib.crc32(raw) & 0xFFFFFFFF == self.menu_id:
                self.log('lxshell: menu data %s (id %08X)' % (p, self.menu_id))
                return hb.menu_from_m2m(json.loads(raw.decode('ascii')),
                                        getattr(self, 'shell_section', True), self.scr.dx), None
        return None, 'no menu data for this core (id %08X)' % self.menu_id

    def kickstart(self):
        p = self.path('kick.rom')
        try:
            with open(p, 'rb') as f:
                data = f.read((1 << 20) + 1)
            src = p
        except OSError:
            data = self.read_card('/amiga/kick.rom')
            src = 'card image /amiga/kick.rom'
        w = self.scr.dx - 4                     # message lines: inside the frame, after a space

        def fit(t):
            return t if len(t) <= w else '...' + t[-(w - 3):]
        if data is None:
            return None, 'Kickstart missing:\\n %s' % fit(p)
        if len(data) not in KICK_SIZES:
            return None, 'Kickstart of the wrong size:\\n %s\\n %d bytes (256 or 512 KB expected).' % (
                fit(src), len(data))
        return data, src

    def load_values(self):
        v = st.load(self.path('amiga.settings'))
        if v is not None:
            self.saved_values = dict(v)
            return v, 'settings loaded'
        raw = self.read_card(self.mdef.data['cfg_file']) if self.mdef else None
        if raw is not None:
            v = hb.import_legacy(self.mdef, raw)
            if v is not None:
                return v, 'settings imported from the card image (%d bytes)' % len(raw)
            return {}, 'card image settings unusable (%d bytes): factory defaults' % len(raw)
        return {}, 'no settings: factory defaults'

    def write_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, 'w') as f:
                json.dump({'adf': self.adf}, f)
        except OSError as e:
            self.log('lxshell: %s: %s' % (self.state_path, e))

    def read_state(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ m65d interface
    def attach(self):
        """called when the Amiga core with a host master is seen: start or adopt"""
        b = self.bus
        s = b.status()
        b.reset()                       # drop a half-sent stream of an earlier process
        self.menu_id = s.menu_id
        self.mdef, err = self.load_menu()
        if err:
            self.log('lxshell: ' + err)
        self.menu_err = err
        if self.mdef:
            self.menu = self.new_menu()
        marker = b.rd(hb.START_MARKER)
        csr = b.rd(hb.CSR)
        if marker and not csr & hb.CSR_RESET:
            self.adopt()
        else:
            self.run_task(self.start_seq('start' if not marker else 'reload after an M2M reset'), 'start')
        self.started = True

    def detach(self):
        """the core went away (FPGA reconfigured, other core): forget everything"""
        self.close_adf()
        self.task = None
        self.started = False
        self.menu_open = self.kbd_to_core = self.holding = False

    def next_wakeup(self):
        return min(self.next_bg, self.next_scr, self.next_rtc or 1e18)

    @staticmethod
    def sleep(s):
        yield ('sleep', s)

    # ------------------------------------------------------------------ start
    def load_held(self):
        """the start's writes with the core held; returns the CFM words written"""
        b = self.bus
        csr = b.rd(hb.CSR)
        b.wr(hb.CSR, csr | hb.CSR_RESET)
        # settings
        if self.mdef:
            self.values, how = self.load_values()
            sel = st.selection_from_values(self.mdef, self.values)
            words = hb.cfm_words(self.mdef, sel)
            self.log('lxshell: ' + how)
        else:
            words = b.cfm_defaults()
            self.values = {}
            self.log('lxshell: no menu data: the core starts with its factory defaults')
        b.cfm_write(words)
        if b.cfm_read() != words:
            # the master ignores writes while the M2M reset is held (a long press still going on)
            self.log('lxshell: the M2M reset is still held: waiting for its end')
            for _ in range(300):                # 30 s: no press lasts that long
                yield ('sleep', 0.1)
                b.cfm_write(words)
                if b.cfm_read() == words:
                    break
            else:
                self.log('lxshell: CFM read-back still differs after 30 s: going on')
            csr = b.rd(hb.CSR)
            b.wr(hb.CSR, csr | hb.CSR_RESET)
            # overlay cells flushed meanwhile were dropped too: send the whole screen again
            self.scr.invalidate()
        self.cfm = words
        self.update_open_key()
        # Kickstart: wait for it with the core held
        shown = False
        while True:
            data, info = self.kickstart()
            if data is not None:
                break
            if not shown:
                self.log('lxshell: ' + info.replace('\\n', ' '))
                message(self.scr, '\\n ' + info + '\\n\\n The core starts as soon as the file\\n is there.')
                self.show_full()
                shown = True
            yield ('sleep', 1.0)
        if shown:
            self.hide()
        t0 = self.mono()
        for _ in b.write_bytes_iter(hb.DEV_KICK, 0, data):
            yield None
        b.sync()
        self.log('lxshell: Kickstart %s (%d KB) in %.2f s' % (info, len(data) >> 10, self.mono() - t0))
        # video: ascal under our control (ASCAL_USAGE = custom), filter, screen offsets
        self.load_filter()
        self.load_screen_offsets()
        b.write_words(hb.DEV_ADF, WBC_WIN * 0x1000 + 2, [
            self.mdef.data['general']['vd_anti_thrashing_delay'] if self.mdef else 2000])
        # nothing mounted after a fresh configuration or a reset of the wrapper
        self.close_adf()
        self.write_state()
        if self.rtc_on():
            self.rtc_write()
        return words

    def start_seq(self, why):
        b = self.bus
        self.log('lxshell: %s' % why)
        # a QNICE restart started the menu and the file browser afresh (OPTM_INIT, FB_INIT)
        if self.mdef:
            self.menu = self.new_menu()
        self.__dict__.pop('browser', None)
        self.holding = True
        self.kbd_to_core = False
        for _ in range(5):
            words = yield from self.load_held()
            # a long press (M2M reset, SW3 or m65d hold_reset) keeps the master's registers at
            # their power-up values for as long as it lasts, and a new press may come in between:
            # release only when the CFM words written before the Kickstart are still there
            if b.cfm_read() == words:
                break
            self.log('lxshell: the M2M reset came back during the start: loading again')
        # release (RP_SYSTEM_START): keyboard and joysticks off, core running, ascal custom
        csr = b.rd(hb.CSR)
        b.wr(hb.CSR, csr & ~(hb.CSR_RESET | CSR_RUN_IO | hb.CSR_ASCAL_AUTO | hb.CSR_PAUSE))
        b.wr(hb.START_MARKER, 1)
        b.sync()
        self.holding = False
        self.log('lxshell: core released')
        yield ('sleep', 0.333)          # START_CONNECT
        csr = b.rd(hb.CSR)
        b.wr(hb.CSR, csr | CSR_RUN_IO)
        self.kbd_to_core = True

    def adopt(self):
        """m65d restarted with the Amiga running: take over without touching the machine"""
        b = self.bus
        self.log('lxshell: adopting the running Amiga (start marker set)')
        self.cfm = b.cfm_read()
        self.update_open_key()
        if self.mdef:
            v = st.load(self.path('amiga.settings'))
            self.saved_values = dict(v) if v is not None else None
            self.values = v or {}
        self.load_screen_offsets(apply_now=False)
        state = self.read_state()
        req, parse = b.rd_many([0x7000, 0x7010], hb.DEV_ADF, CSR_WIN)
        if req == REQ_OK and parse == PT_OK and state.get('adf') and os.path.exists(state['adf']):
            self.open_adf(state['adf'])
            self.log('lxshell: floppy %s still mounted' % state['adf'])
        elif req == REQ_OK and parse == PT_OK:
            self.log('lxshell: a floppy is mounted but its file is unknown: write-back off')
            b.dev_wr(hb.DEV_ADF, WBC_WIN, 0, 0)
        csr = b.rd(hb.CSR)
        self.kbd_to_core = bool(csr & hb.CSR_KBD)
        if csr & hb.CSR_OSM:            # the old process died inside the menu
            b.wr(hb.CSR, (csr & ~hb.CSR_OSM) | CSR_RUN_IO)
            self.kbd_to_core = True

    def update_open_key(self):
        c = self.mdef.data['c_menu'] if self.mdef else {}

        def on(name):
            n = c.get(name)
            return n is not None and self.cfm[n // 16] >> (n % 16) & 1
        if on('OSMKEY_F11'):
            self.open_key = (69, 67, False)
        elif on('OSMKEY_F13'):
            self.open_key = (70, 67, False)
        elif on('OSMKEY_COMBO'):
            self.open_key = (61, 63, True)
        else:
            self.open_key = (67, 67, False)

    def new_menu(self):
        m = Menu(self.mdef, self.scr)
        m.summary_hook = self.summary_in_effect
        return m

    def summary_in_effect(self, n, text):
        """the RAM submenu's summary shows the memory radio's choice ("512K+512K Slow"); with the
        AGA chipset the core ignores that choice: AGA runs with 2 MB chip RAM (the cache's) and no
        slow RAM (mega65.vhd: main_mem_chip2m, main_mem_slow), so the summary says so.  Only the
        text shown changes: the menu file, the stored choice and the CFM bits stay as they are."""
        d = self.mdef
        c = d.data['c_menu']
        aga, mem = c.get('CHIPSET_AGA'), d.data['core_groups'].get('MEM')
        if aga is None or mem is None or not self.menu.stdsel[self.item_of_line(aga)]:
            return text
        for k in range(n + 1, len(d.items)):        # is this the submenu with the memory radio?
            it = d.items[k]
            if it.submenu:
                return text
            if it.g == mem and it.radio:
                return AGA_MEMORY
        return text

    def item_of_line(self, line):
        """menu item index of a config.vhd line (the shell section shifts the lines after it)"""
        return next(n for n, it in enumerate(self.mdef.items) if it.cfm_bit == line)

    def keyboard_mode(self):
        """the core's keyboard mode from the CFM words: "amiga" (positional) or "mega65" (the
        legends, the factory default); None without menu data (m65input keeps its map)"""
        n = (self.mdef.data['c_menu'] if self.mdef else {}).get('KBD_AMIGA')
        if n is None or not getattr(self, 'cfm', None):
            return None
        return 'amiga' if self.cfm[n // 16] >> (n % 16) & 1 else 'mega65'

    def show_menu_window(self):
        d = self.mdef
        self.show_window(d.x, d.y, d.dx, d.dy)

    # ------------------------------------------------------------------ the menu
    def menu_session(self):
        b = self.bus
        if not self.mdef:
            message(self.scr, '\\n ' + (self.menu_err or 'no menu data') +
                       '\\n\\n The core runs with its factory\\n defaults.\\n\\n ' + STR_SPACE)
            self.menu_open = True
            self.kbd_to_core = False
            self.show_full()
            while (yield KEY) not in (KEY_SPACE, KEY_HELP, KEY_RUNSTOP):
                pass
            self.hide()
            yield from self.close_menu()
            return
        m = self.menu
        # RP_OPTM_START: keyboard to the menu, mouse port stays (JOY_1_AT_OSD), joystick 2 off
        g = self.mdef.data['general']
        csr = b.rd(hb.CSR) & ~(hb.CSR_PAUSE | CSR_RUN_IO)
        csr |= (hb.CSR_KBD if g['keyboard_at_osd'] else 0) | (hb.CSR_JOY1 if g['joy_1_at_osd'] else 0) \
            | (hb.CSR_JOY2 if g['joy_2_at_osd'] else 0) | (hb.CSR_PAUSE if g['optm_pause'] else 0)
        b.wr(hb.CSR, csr)
        self.kbd_to_core = False
        self.menu_open = True
        m.stdsel = hb.selection_from_cfm(self.mdef, self.cfm, self.values)
        adf_n = self.adf_index()
        m.stdsel[adf_n] = self.adf is not None
        m.labels[adf_n] = os.path.basename(self.adf) if self.adf else None
        m.on_space = self.space_eject
        before = st.values_from_selection(self.mdef, m.stdsel)     # ROSM_REM_OLD
        m.show()
        self.show_menu_window()
        end = yield from m.run(m.selected, self.on_select)
        self.hide()
        self.save_settings(before)
        m.selected = m.d.start if m.d.items[end].word == CLOSE else end
        yield from self.close_menu()

    def close_menu(self):
        yield ('sleep', 0.333)          # the Return / Help of the menu must not reach the core
        b = self.bus
        b.wr(hb.CSR, (b.rd(hb.CSR) & ~hb.CSR_PAUSE) | CSR_RUN_IO)
        self.menu_open = False
        self.kbd_to_core = True

    def adf_index(self):
        return next(n for n, it in enumerate(self.mdef.items) if it.file)

    def on_select(self, n, word, r9, key):
        """OPTM_CB_SEL + our OSM_SEL_PRE / OSM_SEL_POST"""
        m, d = self.menu, self.mdef
        it = d.items[n]
        if it.help is not None:
            self.hide()
            pages = show_pages(self.scr, d.help_sets[it.help])
            req = next(pages)                   # first page drawn ...
            self.show_full()                    # ... shown after the flush
            while True:
                k = yield req
                try:
                    req = pages.send(k)
                except StopIteration:
                    break
            self.hide()
            m.show()
            self.show_menu_window()
            m.set_item(n, False)
        elif it.file:
            yield from self.mount_dialog(n)
        if it.g == CLOSE:
            return
        if it.shell:
            self.values[it.key] = int(m.stdsel[n])
            if it.shell == 'rtc' and m.stdsel[n]:
                self.rtc_write()
            return
        yield from self.apply_cfm()
        name = d.core_groups.get(it.g)
        if name == 'FILTER':
            self.load_filter()
        elif name == 'SCRRELOAD':
            row = m.row_of(n)
            self.scr.printxy('<Loading Screen Config>', d.x + 1, d.y + 1 + row)
            self.scr.flush(self.port)
            yield ('sleep', 0.5)
            self.load_screen_offsets()
            m.stdsel[n] = False
            yield from self.apply_cfm()
            m.show()
        elif name == 'OSMKEY':
            self.update_open_key()
        elif name == 'HDMI':
            self.load_screen_offsets()          # without a user file: the new mode's preset

    def apply_cfm(self):
        """menu selection -> CFM; topology lines only with the core held (lead's rule)"""
        b, d = self.bus, self.mdef
        new = hb.cfm_words(d, self.menu.stdsel, self.cfm)
        changed = [w for w in range(16) if new[w] != self.cfm[w]]
        if not changed:
            return
        topo = self.topology_mask()
        if any((new[w] ^ self.cfm[w]) & topo[w] for w in changed):
            self.log('lxshell: machine change: hold, write, release (one cold boot)')
            csr = b.rd(hb.CSR)
            b.wr(hb.CSR, csr | hb.CSR_RESET)
            b.cfm_write(new, changed)
            b.wr(hb.CSR, csr & ~hb.CSR_RESET)
        else:
            b.cfm_write(new, changed)
        b.sync()
        self.cfm = new
        return
        yield

    # the machine-changing groups: a change holds the core, writes and releases (one cold boot).
    # CPUFAST = "Fast 68020" (the second CPU on its own clock, AA719CD5 and later menus): the core
    # cold-boots on a change of the line anyway; the shell treats it like the other machine lines
    TOPOLOGY_GROUPS = ('MEM', 'CHIPSET', 'FAST', 'CPU', 'IDE', 'CPUFAST')

    def topology_mask(self):
        mask = [0] * 16
        names = self.TOPOLOGY_GROUPS
        gids = {self.mdef.data['core_groups'][n] for n in names if n in self.mdef.data['core_groups']}
        for it in self.mdef.items:
            if it.cfm_bit is not None and it.g in gids:
                mask[it.cfm_bit // 16] |= 1 << (it.cfm_bit % 16)
        return mask

    def save_settings(self, before):
        """ROSM_SAVE: write only if a setting differs from when the menu was opened"""
        if not self.mdef:
            return
        v = st.values_from_selection(self.mdef, self.menu.stdsel)
        merged = dict(self.values)
        merged.update(v)
        self.values = merged
        if v == before:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            st.save(self.path('amiga.settings'), merged, self.menu_id)
            self.saved_values = dict(merged)
            self.log('lxshell: settings saved')
        except OSError as e:
            self.log('lxshell: saving the settings failed: %s' % e)

    # ------------------------------------------------------------------ floppy
    def mount_dialog(self, n):
        m, s = self.menu, self.scr
        self.hide()
        s.frame_fullscr()
        s.gotoxy(1, 1)
        self.show_full()                        # after the next flush
        if not hasattr(self, 'browser'):
            def hide(name, is_dir):
                return not is_dir and not name.endswith('.ADF')
            self.browser = Browser(getattr(self, 'browse_root', None) or self.dir, s, hide,
                                      start=self.dir)
        while True:
            path = yield from self.browser.run()
            if path is None:                    # Run/Stop: back, the menu shows the old state
                self.hide()
                m.stdsel[n] = self.adf is not None
                break
            err = yield from self.mount(path)
            if err is None:
                self.hide()
                m.labels[n] = os.path.basename(path)
                m.set_item(n, True)
                break
            s.clrinner()
            s.printstr(err + '\\n' + STR_SPACE)
            while (yield KEY) != KEY_SPACE:
                pass
            s.clrinner()
        m.labels[n] = os.path.basename(self.adf) if self.adf else None
        m.show()
        self.show_menu_window()

    def mount(self, path):
        """LOAD_IMAGE + PREP_LOAD_IMAGE + HANDLE_CRTROM_M for the ADF device; None or an error"""
        b, s = self.bus, self.scr
        b.dev_wr(hb.DEV_ADF, CSR_WIN, 0, REQ_LDNG)          # eject: the Amiga sees the change
        yield from self.flush_all()                         # the old disk's tracks to its file
        b.dev_wr(hb.DEV_ADF, WBC_WIN, 0, 0)
        self.close_adf()
        self.write_state()
        try:
            size = os.path.getsize(path)
            f = open(path, 'rb')
        except OSError as e:
            b.dev_wr(hb.DEV_ADF, CSR_WIN, 0, REQ_ERR)
            return 'Error: %s' % e.strerror
        with f:
            if not ADF_MIN <= size <= ADF_MAX:
                return 'Error code: 0001\\n' + WRN_ADF_SIZE
            data = f.read()
        # progress bar on the bottom frame line, like LOAD_IMAGE
        w = s.dx - 6
        s.printxy(chr(FC_HE_LEFT) + ' ' * w + chr(FC_HE_RIGHT), 2, s.dy - 1)
        steps, done = max(1, len(data) // w), 0
        for attempt in (1, 2):
            for pos in b.write_bytes_iter(hb.DEV_ADF, 0, data):
                k = min(w, pos // steps)
                if k > done:
                    s.printxy(chr(LD_PROGRESS) * (k - done), 3 + done, s.dy - 1)
                    done = k
                yield None
            # safety net for the SPI link: read the image back and compare (~0.3 s at 33 MHz)
            crc = 0
            for _, chunk in b.read_bytes_iter(hb.DEV_ADF, 0, len(data)):
                crc = zlib.crc32(chunk, crc)
                yield None
            if crc == zlib.crc32(data):
                break
            self.log('lxshell: %s read back differently from the core%s' % (
                os.path.basename(path), ': sent again' if attempt == 1 else ' again: mounted unverified'))
        b.dev_wr(hb.DEV_ADF, CSR_WIN, 1, size & 0xFFFF)
        b.dev_wr(hb.DEV_ADF, CSR_WIN, 2, size >> 16)
        b.dev_wr(hb.DEV_ADF, CSR_WIN, 0, REQ_OK)
        t0 = self.mono()
        while True:
            parse = b.dev_rd(hb.DEV_ADF, CSR_WIN, 0x10)
            if parse in (PT_OK, PT_ERR):
                break
            if self.mono() - t0 > 2.0:
                parse = PT_ERR
                break
            yield None
        if parse != PT_OK:
            code = b.dev_rd(hb.DEV_ADF, CSR_WIN, 0x11)
            msg = ''.join(chr(c) for c in (b.dev_rd(hb.DEV_ADF, CSR_WIN, 0x100 + i) for i in range(20)) if c)
            b.dev_wr(hb.DEV_ADF, CSR_WIN, 0, REQ_IDLE)
            return 'Error code: %04X\\n%s' % (code, msg.replace('\n', '\\n'))
        self.open_adf(path)
        self.write_state()
        self.log('lxshell: mounted %s%s' % (path, ' (read-only)' if self.adf_ro else ''))
        return None

    def open_adf(self, path):
        self.adf = path
        try:
            self.adf_fd = os.open(path, os.O_RDWR)
            self.adf_ro = False
        except OSError:
            self.adf_fd = None
            self.adf_ro = True
        self.bus.dev_wr(hb.DEV_ADF, WBC_WIN, 0, 0 if self.adf_ro else 1)    # arm: df0 writable

    def close_adf(self):
        if self.adf_fd is not None:
            try:
                os.fdatasync(self.adf_fd)
            except OSError:
                pass
            os.close(self.adf_fd)
        self.adf, self.adf_fd, self.adf_ro = None, None, False
        self.flush_iter = None

    def space_eject(self, n):
        """SPACE on the highlighted ADF line with a disk in df0 (issue #16): eject first (the
        Amiga sees df0 empty and stops writing), then flush the written tracks, then disarm.
        The menu label reverts to <Load> and the line's marker goes (CRTROM_MLST_GET)."""
        if not self.mdef.items[n].file or self.adf is None:
            return False
        b, m = self.bus, self.menu
        b.dev_wr(hb.DEV_ADF, CSR_WIN, 0, REQ_IDLE)
        yield from self.flush_all()
        b.dev_wr(hb.DEV_ADF, WBC_WIN, 0, 0)
        self.log('lxshell: ejected %s' % self.adf)
        self.close_adf()
        self.write_state()
        m.labels[n] = None
        m.stdsel[n] = False
        yield from self.apply_cfm()
        m.show()
        return True

    def dirty_tracks(self):
        words = self.bus.rd_many([0x7010 + i for i in range(11)], hb.DEV_ADF, WBC_WIN)
        return [w * 16 + bit for w in range(11) for bit in range(16) if words[w] >> bit & 1]

    def flush_track(self, track):
        """clear the dirty bit first (a new write sets it again), then read and write the track"""
        b = self.bus
        b.dev_wr(hb.DEV_ADF, WBC_WIN, 0x10 + track // 16, 1 << (track % 16))
        # safety net for the SPI link: what goes into the file is read twice and must agree; a third
        # read decides (the Amiga may write the track in between: then the 2nd and 3rd agree)
        data = yield from self.read_track(track)
        again = yield from self.read_track(track)
        if again != data:
            data = yield from self.read_track(track)
            if data != again:
                self.log('lxshell: track %d read back three different ways: written as the last read'
                         % track)
        try:
            os.pwrite(self.adf_fd, bytes(data), track * TRACK)
            self.flush_failed.discard(track)
        except OSError as e:
            if track not in self.flush_failed:
                self.log('lxshell: write-back of track %d to %s failed: %s' % (track, self.adf, e))
            self.flush_failed.add(track)

    def read_track(self, track):
        data = bytearray()
        for _, chunk in self.bus.read_bytes_iter(hb.DEV_ADF, track * TRACK, TRACK):
            data += chunk
            yield None
        return data

    def flush_all(self):
        if self.adf_fd is None:
            return
        for _ in range(4 * 166):
            tracks = self.dirty_tracks() + sorted(self.flush_failed)
            if not tracks:
                break
            yield from self.flush_track(tracks[0])
        try:
            os.fdatasync(self.adf_fd)
        except OSError:
            pass

    def flush_background(self):
        """one step: started when the anti-thrash countdown has run out (WBC STAT b1)"""
        if self.flush_iter is not None:
            try:
                next(self.flush_iter)
            except StopIteration:
                self.flush_iter = None
            return
        if self.adf_fd is None:
            return
        stat = self.bus.dev_rd(hb.DEV_ADF, WBC_WIN, 1)
        if stat & 2 or (self.flush_failed and not stat & 1):
            self.flush_iter = self.flush_all()

    # ------------------------------------------------------------------ video
    def load_filter(self):
        b = self.bus
        if not self.mdef:
            b.wr(hb.ASCAL_MODE, 3)          # bicubic: the tables travel with the menu data
            return
        d = self.mdef.data
        row = None
        for r in d['filters']:
            n = r['line']
            if self.cfm[n // 16] >> (n % 16) & 1:
                row = r
                break
        if row is None:                     # defensive fallback of LOAD_HDMI_FILTER: Lanczos
            row = {'ascal_mode': d['ascal']['polyphase'], 'h': 'LANCZOS2_12', 'v': 'LANCZOS2_12'}
        b.wr(hb.ASCAL_MODE, row['ascal_mode'])
        if row['h']:
            b.write_words(hb.DEV_ASCAL_PPHASE, 0x000, d['filter_tables'][row['h']])
            b.write_words(hb.DEV_ASCAL_PPHASE, 0x100, d['filter_tables'][row['v']])

    def screen_preset(self):
        """the ready-made screen file for the HDMI mode in the CFM words, or None"""
        c = self.mdef.data['c_menu'] if self.mdef else {}
        for name, fname in HDMI_PRESETS:
            n = c.get(name)
            if n is not None and self.cfm[n // 16] >> (n % 16) & 1:
                return os.path.join(self.preset_dir, fname)
        return None

    def screen_cfg(self):
        """(bytes, source): the user's aexp_screen.cfg wins (Amiga directory, then the card image);
        without one, the preset for the selected HDMI mode"""
        p = self.path('aexp_screen.cfg')
        try:
            with open(p, 'rb') as f:
                return f.read(256), p
        except OSError:
            pass
        raw = self.read_card('/amiga/aexp_screen.cfg')
        if raw is not None:
            return raw, 'card image /amiga/aexp_screen.cfg'
        pre = self.screen_preset()
        if pre:
            try:
                with open(pre, 'rb') as f:
                    return f.read(256), 'preset ' + pre
            except OSError:
                return b'', 'none (preset %s missing)' % pre
        return b'', 'none'

    def load_screen_offsets(self, apply_now=True):
        tbl = [0] * 40
        raw, src = self.screen_cfg()
        if src != self.screen_src:
            self.screen_src = src
            self.log('lxshell: screen offsets: %s' % src)
        if len(raw) >= 4 and raw[0:2] == b'AX' and raw[2] in (3, 4) and raw[3] == 4:
            per = 20 if raw[2] == 4 else 16
            if len(raw) >= 4 + 4 * per:
                for r in range(4):
                    row = raw[4 + r * per:4 + (r + 1) * per]
                    words = [row[i] << 8 | row[i + 1] for i in range(0, per, 2)]
                    tbl[r * 10:r * 10 + len(words)] = words
        self.screen_table = tbl
        if apply_now:                       # the detector pushes the row of the current mode
            self.scr_applied, self.scr_cand, self.scr_cnt = None, None, 0

    def detect_screen_mode(self):
        x, flags = self.bus.rd_many([0x7000, 0x700B], hb.DEV_SYS_INFO, 0x30)
        if x < SCR_NOSIG:
            return
        if SCR_LORES[0] <= x < SCR_LORES[1]:
            mode = 0
        elif SCR_HIRES[0] <= x < SCR_HIRES[1]:
            mode = 1
        else:
            mode = SCR_UNKNOWN
        if mode != SCR_UNKNOWN and flags & 1:
            mode += 2
        if mode != self.scr_cand:
            self.scr_cand, self.scr_cnt = mode, 0
            return
        if self.scr_cnt < SCR_DEBOUNCE:
            self.scr_cnt += 1
            return
        if mode == self.scr_applied:
            return
        self.scr_applied = mode
        row = [0] * 10 if mode == SCR_UNKNOWN else self.screen_table[mode * 10:mode * 10 + 10]
        b = self.bus
        for i, cfd in enumerate((4, 5, 6, 7, 0, 1, 2, 3, 8, 9)):
            b.wr(hb.CFD_ADDR, cfd)
            b.wr(hb.CFD_DATA, row[i])

    # ------------------------------------------------------------------ clock
    def rtc_on(self):
        return bool(self.values.get('shell:rtc', 0))

    def rtc_write(self):
        """PROTOCOL.md 3.1: stop, write the time (BCD), read + run"""
        t = self.wall()
        lt = self.localtime(t)
        b = self.bus
        b.dev_wr(hb.DEV_RTC, 0, 8, 0)
        regs = [0, bcd(lt.tm_sec), bcd(lt.tm_min), bcd(lt.tm_hour), bcd(lt.tm_mday),
                bcd(lt.tm_mon), bcd(lt.tm_year % 100), lt.tm_wday]
        b.wr(hb.RAMROM_DEV, hb.DEV_RTC)
        b.wr(hb.RAMROM_4KWIN, 0)
        for i, v in enumerate(regs):
            b.wr(hb.RAMROM_DATA + i, v)
        b.wr(hb.RAMROM_DATA + 8, 0x000A)
        b.sync()
        self.next_rtc = self.mono() + (60 - lt.tm_sec) - (t % 1)

    # ------------------------------------------------------------------ background
    def background(self, now):
        b = self.bus
        if now >= self.next_scr:
            self.next_scr = now + SCR_POLL_S
            self.detect_screen_mode()
        if now >= self.next_bg:
            self.next_bg = now + 0.1
            csr, marker = b.rd_many([hb.CSR, hb.START_MARKER])
            if self.task_kind != 'start' and not self.holding and csr & hb.CSR_RESET and marker:
                # the master's registers were reset (CSR = power-up value) while the shell did
                # not hold the core: a long press.  Today's QNICE restart reloaded settings and
                # Kickstart; so do we (an open menu is dropped: the overlay is off anyway).
                self.log('lxshell: the core is held without the shell: M2M reset (long press)')
                self.close_adf()
                self.menu_open = False
                self.run_task(self.start_seq('reload after an M2M reset'), 'start')
                return
            if self.task is None:
                self.flush_background()
        elif self.flush_iter is not None and self.task is None:
            self.flush_background()
        if self.next_rtc and now >= self.next_rtc:
            if self.rtc_on():
                self.rtc_write()
            else:
                self.next_rtc = 0.0
