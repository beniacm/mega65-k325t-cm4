"""shell.py -- the Amiga core's M2M shell on the Linux module (replaces the QNICE firmware).

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

import hostbus as hb
import settings as st
import ui

TRACK = 5632
ADF_MIN, ADF_MAX = 160 * TRACK, 166 * TRACK
KICK_SIZES = (256 << 10, 512 << 10)
CSR_RUN_IO = hb.CSR_KBD | hb.CSR_JOY1 | hb.CSR_JOY2

# adf_mount_wrapper windows (device 0x0103)
CSR_WIN, WBC_WIN = 0xFFFF, 0xFFFE
REQ_IDLE, REQ_LDNG, REQ_ERR, REQ_OK = 0, 1, 2, 3
PT_IDLE, PT_PRSNG, PT_OK, PT_ERR = 0, 1, 2, 3

# screen adjustment (m2m-rom.asm SCR_*)
SCR_LORES = (367, 388)
SCR_HIRES = (744, 765)
SCR_NOSIG = 200
SCR_DEBOUNCE = 3
SCR_UNKNOWN = 4

WRN_ADF_SIZE = ("\\n\\nThis is not a valid ADF disk image:\\n"
                "the file size must be 901,120 bytes\\n"
                "(880 KB standard ADF; 81..83-track over-\\n"
                "dumps up to 934,912 bytes are accepted).")
STR_SPACE = "Press Space to continue."


def bcd(v):
    return (v // 10) << 4 | v % 10


class AmigaShell:
    def __init__(self, bus, menu_path, amiga_dir, card_img=None, state_path='/run/m65/amiga.state',
                 log=print, mono=time.monotonic, wall=time.time, localtime=time.localtime):
        self.bus = bus
        self.menu_path = menu_path
        self.dir = amiga_dir
        self.card_img = card_img
        self.state_path = state_path
        self.log = log
        self.mono, self.wall, self.localtime = mono, wall, localtime
        self.scr = ui.Screen()
        self.scan = ui.KeyScanner()
        self.task = None                # the foreground generator
        self.task_kind = None           # 'start' | 'menu'
        self.osm_req = None
        self.want = None                # what it waits for: ui.KEY, a wake-up time, or None
        self.keyq = []
        self.menu_open = False
        self.kbd_to_core = False        # keyboard forwarded (menu closed and connected)
        self.holding = False            # the shell itself holds the core
        self.mdef = None
        self.menu = None
        self.menu_id = None
        self.osm_key = (67, 67, False)
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
        self.started = False

    # ------------------------------------------------------------------ files
    def path(self, name):
        return os.path.join(self.dir, name)

    def read_card(self, name):
        """a file from the old card image (read-only take-over), or None"""
        if not self.card_img or not os.path.exists(self.card_img):
            return None
        try:
            import fatimg
            img = fatimg.FatImage(self.card_img)
            try:
                return img.read(name)
            finally:
                img.close()
        except (OSError, Exception) as e:            # a broken image must not stop the start
            self.log('lxshell: card image %s: %s' % (name, e))
            return None

    def load_menu(self):
        try:
            with open(self.menu_path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            return None, 'menu data %s: %s' % (self.menu_path, e.strerror)
        if zlib.crc32(raw) & 0xFFFFFFFF != self.menu_id:
            return None, 'menu data %s does not match this core' % os.path.basename(self.menu_path)
        return ui.MenuDef(json.loads(raw.decode('ascii'))), None

    def kickstart(self):
        p = self.path('kick.rom')
        try:
            with open(p, 'rb') as f:
                data = f.read((1 << 20) + 1)
            src = p
        except OSError:
            data = self.read_card('/amiga/kick.rom')
            src = 'card image /amiga/kick.rom'
        if data is None:
            return None, 'Kickstart missing:\\n %s' % p
        if len(data) not in KICK_SIZES:
            return None, 'Kickstart %s: %d bytes\\n(256 KB or 512 KB expected).' % (src, len(data))
        return data, src

    def load_values(self):
        v = st.load(self.path('amiga.settings'))
        if v is not None:
            self.saved_values = dict(v)
            return v, 'settings loaded'
        raw = self.read_card(self.mdef.data['cfg_file']) if self.mdef else None
        if raw is not None:
            v = st.import_legacy(self.mdef, raw)
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
            self.menu = ui.Menu(self.mdef, self.scr)
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

    def filter_input(self, cols, ctrl):
        if not self.started:
            return cols, ctrl
        if not self.kbd_to_core:
            return [0] * len(cols), ctrl & ~0x30
        a, b, combo = self.osm_key
        cols = list(cols)
        if combo:
            if cols[a // 8] >> (a % 8) & 1:       # MEGA held: Run/Stop is the combo's part
                cols[b // 8] &= ~(1 << (b % 8))
        elif a not in (73, 74):
            cols[a // 8] &= ~(1 << (a % 8))
        return cols, ctrl

    def keys(self, cols, ctrl, now):
        if not self.started:
            return
        a, b, combo = self.osm_key
        self.scan.scan(ui.m2m_keys(cols, ctrl, a, b, combo), now)
        while True:
            k = self.scan.getkey()
            if not k:
                break
            if self.task is not None and self.want == ui.KEY:
                self.keyq.append(k)
            elif self.task is None and k == ui.KEY_HELP:
                self.run_task(self.menu_session(), 'menu')
            # other keys with the menu closed are consumed, like the firmware's main loop

    def poll(self, now):
        if not self.started:
            return 0.5
        self.step_task(now)
        if self.task is None or self.want == ui.KEY:
            self.background(now)
            self.bus.flush()
        if self.task is not None and self.want is None:
            return 0.0
        nxt = min(self.next_bg, self.next_scr, self.next_rtc or 1e18)
        if self.task is not None and isinstance(self.want, float):
            nxt = min(nxt, self.want)
        return max(0.0, min(0.05, nxt - now))

    # ------------------------------------------------------------------ task driver
    def run_task(self, gen, kind):
        self.task, self.task_kind, self.want = gen, kind, None
        self.keyq.clear()
        self.step_task(self.mono())

    def step_task(self, now):
        """resume the foreground generator while it can run; after a plain 'yield None' (between
        two bus chunks) return to m65d's loop, so input is served before the next chunk"""
        for _ in range(64):
            if self.task is None:
                break
            if self.want == ui.KEY:
                if not self.keyq:
                    break
                send = self.keyq.pop(0)
            elif isinstance(self.want, float):
                if now < self.want:
                    break
                send = None
            else:
                send = None
            try:
                req = self.task.send(send)
            except StopIteration:
                req = 'done'
            self.scr.flush(self.bus)
            self.apply_osm()
            self.bus.flush()                # queued writes leave now, not with the next read
            if req == 'done':
                self.task = self.task_kind = self.want = None
                break
            now = self.mono()
            if isinstance(req, tuple) and req[0] == 'sleep':
                self.want = now + req[1]
            else:
                self.want = req
            if req is None:
                break

    @staticmethod
    def sleep(s):
        yield ('sleep', s)

    # ------------------------------------------------------------------ start
    def start_seq(self, why):
        b = self.bus
        self.log('lxshell: %s' % why)
        self.holding = True
        self.kbd_to_core = False
        csr = b.rd(hb.CSR)
        b.wr(hb.CSR, csr | hb.CSR_RESET)
        # settings
        if self.mdef:
            self.values, how = self.load_values()
            sel = st.selection_from_values(self.mdef, self.values)
            words = st.cfm_words(self.mdef, sel)
            self.log('lxshell: ' + how)
        else:
            words = b.cfm_defaults()
            self.values = {}
            self.log('lxshell: no menu data: the core starts with its factory defaults')
        b.cfm_write(words)
        self.cfm = words
        self.update_osm_key()
        # Kickstart: wait for it with the core held
        shown = False
        while True:
            data, info = self.kickstart()
            if data is not None:
                break
            if not shown:
                self.log('lxshell: ' + info.replace('\\n', ' '))
                ui.message(self.scr, '\\n ' + info + '\\n\\n The core starts as soon as the file is there.')
                self.osm_full(True)
                shown = True
            yield ('sleep', 1.0)
        if shown:
            self.osm_off()
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
        self.update_osm_key()
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

    def update_osm_key(self):
        c = self.mdef.data['c_menu'] if self.mdef else {}

        def on(name):
            n = c.get(name)
            return n is not None and self.cfm[n // 16] >> (n % 16) & 1
        if on('OSMKEY_F11'):
            self.osm_key = (69, 67, False)
        elif on('OSMKEY_F13'):
            self.osm_key = (70, 67, False)
        elif on('OSMKEY_COMBO'):
            self.osm_key = (61, 63, True)
        else:
            self.osm_key = (67, 67, False)

    # ------------------------------------------------------------------ overlay windows
    # A window change is a request, carried out after the next VRAM flush, so a window never
    # shows the content of the previous one (the firmware drew with the overlay off or the same
    # window).  'off' is immediate.
    def osm_full(self, on=True):
        self.osm_req = 'full' if on else 'off'
        if not on:
            self.apply_osm()

    def osm_menu(self):
        self.osm_req = 'menu'

    def osm_off(self):
        self.osm_req = 'off'
        self.apply_osm()

    def apply_osm(self):
        req, self.osm_req = getattr(self, 'osm_req', None), None
        if req is None:
            return
        b = self.bus
        csr = b.rd(hb.CSR)
        if req == 'off':
            b.wr(hb.CSR, csr & ~hb.CSR_OSM)
            return
        self.scr.flush(b)
        if req == 'full':
            b.wr(hb.OSM_XY, 0)
            b.wr(hb.OSM_DXDY, ui.DX << 8 | ui.DY)
        else:
            d = self.mdef
            b.wr(hb.OSM_XY, d.x << 8 | d.y)
            b.wr(hb.OSM_DXDY, d.dx << 8 | d.dy)
        b.wr(hb.CSR, csr | hb.CSR_OSM)

    # ------------------------------------------------------------------ the menu
    def menu_session(self):
        b = self.bus
        if not self.mdef:
            ui.message(self.scr, '\\n ' + (self.menu_err or 'no menu data') +
                       '\\n\\n The core runs with its factory\\n defaults.\\n\\n ' + STR_SPACE)
            self.menu_open = True
            self.kbd_to_core = False
            self.osm_full(True)
            while (yield ui.KEY) not in (ui.KEY_SPACE, ui.KEY_HELP, ui.KEY_RUNSTOP):
                pass
            self.osm_off()
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
        m.stdsel = st.selection_from_cfm(self.mdef, self.cfm, self.values)
        adf_n = self.adf_index()
        m.stdsel[adf_n] = self.adf is not None
        m.labels[adf_n] = os.path.basename(self.adf) if self.adf else None
        m.on_space = self.space_eject
        before = st.values_from_selection(self.mdef, m.stdsel)     # ROSM_REM_OLD
        m.show()
        self.osm_menu()
        end = yield from m.run(m.selected, self.on_select)
        self.osm_off()
        self.save_settings(before)
        m.selected = m.d.start if m.d.items[end].word == ui.OPTM_CLOSE else end
        yield from self.close_menu()

    def close_menu(self):
        yield ('sleep', 0.333)          # the Return / Help of the menu must not reach the core
        b = self.bus
        b.wr(hb.CSR, (b.rd(hb.CSR) & ~hb.CSR_PAUSE) | CSR_RUN_IO)
        self.menu_open = False
        self.kbd_to_core = True

    def adf_index(self):
        return next(n for n, it in enumerate(self.mdef.items) if it.crtrom)

    def on_select(self, n, word, r9, key):
        """OPTM_CB_SEL + our OSM_SEL_PRE / OSM_SEL_POST"""
        m, d = self.menu, self.mdef
        it = d.items[n]
        if it.help:
            idx = sum(1 for k in range(n) if d.items[k].help) + 1
            self.osm_off()
            pages = ui.show_pages(self.scr, d.help_sets[idx])
            req = next(pages)                   # first page drawn ...
            self.osm_full(True)                 # ... shown after the flush
            while True:
                k = yield req
                try:
                    req = pages.send(k)
                except StopIteration:
                    break
            self.osm_off()
            m.show()
            self.osm_menu()
            m.set_item(n, False)
        elif it.crtrom:
            yield from self.mount_dialog(n)
        if it.g == ui.OPTM_CLOSE:
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
            self.scr.flush(self.bus)
            yield ('sleep', 0.5)
            self.load_screen_offsets()
            m.stdsel[n] = False
            yield from self.apply_cfm()
            m.show()
        elif name == 'OSMKEY':
            self.update_osm_key()

    def apply_cfm(self):
        """menu selection -> CFM; topology lines only with the core held (lead's rule)"""
        b, d = self.bus, self.mdef
        new = st.cfm_words(d, self.menu.stdsel, self.cfm)
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

    def topology_mask(self):
        mask = [0] * 16
        names = ('MEM', 'CHIPSET', 'FAST', 'CPU', 'IDE')
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
        self.osm_off()
        s.frame_fullscr()
        s.gotoxy(1, 1)
        self.osm_full(True)                     # after the next flush
        if not hasattr(self, 'browser'):
            def hide(name, is_dir):
                return not is_dir and not name.endswith('.ADF')
            self.browser = ui.Browser(self.dir, s, hide)
        while True:
            path = yield from self.browser.run()
            if path is None:                    # Run/Stop: back, the menu shows the old state
                self.osm_off()
                m.stdsel[n] = self.adf is not None
                break
            err = yield from self.mount(path)
            if err is None:
                self.osm_off()
                m.labels[n] = os.path.basename(path)
                m.set_item(n, True)
                break
            s.clrinner()
            s.printstr(err + '\\n\\n' + STR_SPACE)
            while (yield ui.KEY) != ui.KEY_SPACE:
                pass
            s.clrinner()
        m.labels[n] = os.path.basename(self.adf) if self.adf else None
        m.show()
        self.osm_menu()

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
                return 'Error code: 0001' + WRN_ADF_SIZE
            data = f.read()
        # progress bar on the bottom frame line, like LOAD_IMAGE
        w = ui.DX - 6
        s.printxy(chr(ui.FC_HE_LEFT) + ' ' * w + chr(ui.FC_HE_RIGHT), 2, ui.DY - 1)
        steps, done = max(1, len(data) // w), 0
        for pos in b.write_bytes_iter(hb.DEV_ADF, 0, data):
            k = min(w, pos // steps)
            if k > done:
                s.printxy(chr(ui.LD_PROGRESS) * (k - done), 3 + done, ui.DY - 1)
                done = k
            yield None
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
        if not self.mdef.items[n].crtrom or self.adf is None:
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
        data = bytearray()
        for _, chunk in b.read_bytes_iter(hb.DEV_ADF, track * TRACK, TRACK):
            data += chunk
            yield None
        try:
            os.pwrite(self.adf_fd, bytes(data), track * TRACK)
            self.flush_failed.discard(track)
        except OSError as e:
            if track not in self.flush_failed:
                self.log('lxshell: write-back of track %d to %s failed: %s' % (track, self.adf, e))
            self.flush_failed.add(track)

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

    def load_screen_offsets(self, apply_now=True):
        tbl = [0] * 40
        try:
            with open(self.path('aexp_screen.cfg'), 'rb') as f:
                raw = f.read(256)
        except OSError:
            raw = self.read_card('/amiga/aexp_screen.cfg') or b''
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
            self.next_scr = now + 0.025
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
