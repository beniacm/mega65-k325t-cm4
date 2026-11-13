"""model.py -- a Python model of the Amiga core's host master and the devices behind it.

Only for tests on the PC: it implements the link interface of hostbus (put/get/reset/status) and
executes the command stream of PROTOCOL.md v0 at once, against:
  * the register file (CSR 0x0839 at power-up, OSM, ascal mode, CFD, CFM, device/window, menu id,
    factory defaults, start marker);
  * VRAM data/attributes (45 x 36), the polyphase RAM, SYS_INFO (VGA window 0x10, core video
    window 0x30), the RTC registers (rtc_controller with the host build's completing read);
  * the Kickstart device 0x0100 (byte address, write-only) and the ADF device 0x0103 with
    adf_mount_wrapper.vhd's behaviour: byte windows, the M2M CSR on window 0xFFFF (ST_LDNG drops
    "mounted", ST_OK runs the size validator: 160..166 tracks of 5632 bytes -> READY, else ERROR 1
    "Invalid ADF size"), the write-back CSR on window 0xFFFE (WR_EN, any_dirty / flush_start,
    anti-thrash countdown in ms, 166 dirty bits write-1-to-clear, a hardware set wins).
The model's time is explicit: tick(ms) advances the anti-thrash countdown.  amiga_writes(track,
data) plays the track engine (commits a track into the image and raises its dirty bit).
"""

from lxcore.bus import HostStatus, ST_LEN

TRACK = 5632
DX, DY = 45, 36

REQ_IDLE, REQ_LDNG, REQ_ERR, REQ_OK = 0, 1, 2, 3
RESP_IDLE, RESP_PARSING, RESP_READY, RESP_ERROR = 0, 1, 2, 3
ADF_ERR_STR = "Invalid ADF size   \n"


class ModelMaster:
    def __init__(self, menu_id=0, defaults=None, put_fifo=8192, get_fifo=4096):
        self.menu_id = menu_id
        self.defaults = list(defaults or [0] * 16)
        self.put_fifo, self.get_fifo = put_fifo, get_fifo
        self.marker = 0                     # cleared only by "FPGA configuration" (configure())
        self.kick = bytearray(1 << 20)
        self.kick_writes = 0
        self.adf = bytearray(4 << 20)       # the DDR3 image, one byte per ADF address
        self.vram = [[0] * (DX * DY), [0x0B] * (DX * DY)]
        self.pphase = [0] * 0x200
        self.core_video = {0: 376, 1: 288, 0xB: 0}   # window 0x30: X (hdmax), Y, flags
        self.events = []                    # ('release'|'hold', cfm snapshot), ...
        self.reads = self.writes = 0
        self.flags = 0
        self.configure()

    # ---- FPGA configuration / resets
    def configure(self):
        """a fresh FPGA configuration: everything at power-up, marker cleared"""
        self.marker = 0
        self._registers_powerup()
        self.inbuf = bytearray()
        self.getbuf = bytearray()
        self.flags = 0

    def long_press(self):
        """the M2M reset: master registers to power-up values except the start marker"""
        self._registers_powerup()
        self.events.append(('m2m_reset', None))

    def _registers_powerup(self):
        self.csr = 0x0839
        self.osm_xy = self.osm_dxdy = 0
        self.ascal = 0
        self.cfd = [0] * 16
        self.cfm = [0] * 16
        self.cfd_addr = self.cfm_addr = 0
        self.dev = self.win = 0
        self.rtc = [0] * 8
        self.rtc_cmd = 0x08                 # running
        self.rtc_toggles = 0
        # adf_mount_wrapper (its reset is the M2M reset)
        self.adf_req = REQ_IDLE
        self.adf_fs = 0
        self.adf_resp, self.adf_err = RESP_IDLE, 0
        self.adf_mounted, self.adf_tracks = False, 0
        self.wbc_wr_en = 0
        self.wbc_delay = 2000
        self.wbc_cnt = 0
        self.wbc_dirty = [0] * 166

    # ---- the core side (test hooks)
    def tick(self, ms):
        self.wbc_cnt = max(0, self.wbc_cnt - ms)

    def amiga_writes(self, track, data=None):
        """the track engine commits a track the Amiga wrote and raises its dirty bit"""
        if data is not None:
            self.adf[track * TRACK:(track + 1) * TRACK] = data
        self.wbc_dirty[track] = 1
        self.wbc_cnt = self.wbc_delay

    def held(self):
        return bool(self.csr & 1)

    # ---- link interface
    def status(self):
        r = bytearray(ST_LEN)
        r[0:2] = b"Me"
        r[52] = 3
        r[72] = 1
        free = self.put_fifo - len(self.inbuf)
        r[73], r[74] = free >> 8, free & 0xFF
        fill = min(len(self.getbuf), 0xFFFF)
        r[75], r[76] = fill >> 8, fill & 0xFF
        r[77] = self.flags | (16 if self.marker else 0)
        r[78:82] = self.menu_id.to_bytes(4, 'big')
        return HostStatus(r)

    def put(self, data):
        if len(self.inbuf) + len(data) > self.put_fifo:
            self.flags |= 1
            data = data[:self.put_fifo - len(self.inbuf)]
        self.inbuf += data
        self._run()

    def get(self, n):
        out = bytes(self.getbuf[:n])
        del self.getbuf[:n]
        return out + bytes(n - len(out))

    def reset(self):
        self.inbuf.clear()
        self.getbuf.clear()
        self.flags = 0

    # ---- the stream parser
    def _run(self):
        b = self.inbuf
        while b:
            op = b[0]
            if op == 1:
                if len(b) < 5:
                    return
                self.write(b[1] << 8 | b[2], b[3] << 8 | b[4])
                del b[:5]
            elif op == 2:
                if len(b) < 3:
                    return
                v = self.read(b[1] << 8 | b[2])
                self._answer(bytes([v >> 8, v & 0xFF]))
                del b[:3]
            elif op in (3, 4, 5, 6):
                if len(b) < 10:
                    return
                dev, win, off = b[1] << 8 | b[2], b[3] << 8 | b[4], b[5] << 8 | b[6]
                n = b[7] << 16 | b[8] << 8 | b[9]
                width = 1 if op in (3, 5) else 2
                need = 10 + (n * width if op in (3, 4) else 0)
                if len(b) < need:
                    if op in (3, 4) and len(b) >= 10 + width:
                        # execute the complete part now, keep the rest as a shorter burst
                        k = (len(b) - 10) // width
                        self._burst(op, dev, win, off, k, b[10:10 + k * width])
                        a = win * 0x1000 + off + k
                        n -= k
                        rest = bytes([op, dev >> 8, dev & 0xFF, (a >> 12) >> 8, (a >> 12) & 0xFF,
                                      (a & 0xFFF) >> 8, a & 0xFF, n >> 16, (n >> 8) & 0xFF, n & 0xFF])
                        del b[:10 + k * width]
                        b[0:0] = rest
                    return
                self._burst(op, dev, win, off, n, b[10:need])
                del b[:need]
            elif op == 7:
                if len(b) < 2:
                    return
                self._answer(bytes([b[1]]))
                del b[:2]
            else:
                self.flags |= 4
                b.clear()
                return

    def _answer(self, data):
        self.getbuf += data

    def _burst(self, op, dev, win, off, n, data):
        self.dev = dev
        a = win * 0x1000 + off
        for i in range(n):
            self.win, o = (a + i) >> 12, (a + i) & 0xFFF
            if op == 3:
                self.write(0x7000 + o, data[i])
            elif op == 4:
                self.write(0x7000 + o, data[2 * i] << 8 | data[2 * i + 1])
            elif op == 5:
                self._answer(bytes([self.read(0x7000 + o) & 0xFF]))
            else:
                v = self.read(0x7000 + o)
                self._answer(bytes([v >> 8, v & 0xFF]))

    # ---- QNICE address space
    def write(self, addr, v):
        self.writes += 1
        if 0x7000 <= addr <= 0x7FFF:
            return self._dev_write(addr - 0x7000, v)
        if addr == 0xFFE0:
            old = self.csr
            self.csr = v & ~0x07C0          # SD bits read 0
            if old & 1 and not v & 1:
                self.events.append(('release', list(self.cfm)))
            elif v & 1 and not old & 1:
                self.events.append(('hold', list(self.cfm)))
        elif addr == 0xFFE1:
            self.osm_xy = v
        elif addr == 0xFFE2:
            self.osm_dxdy = v
        elif addr == 0xFFE3:
            self.ascal = v
        elif addr == 0xFFF0:
            self.cfd_addr = v & 15
        elif addr == 0xFFF1:
            self.cfd[self.cfd_addr] = v
        elif addr == 0xFFF2:
            self.cfm_addr = v & 15
        elif addr == 0xFFF3:
            if not self.csr & 1:
                self.events.append(('cfm_while_running', (self.cfm_addr, self.cfm[self.cfm_addr], v)))
            self.cfm[self.cfm_addr] = v
        elif addr == 0xFFF4:
            self.dev = v
        elif addr == 0xFFF5:
            self.win = v
        elif addr == 0xFFFB:
            self.marker = v

    def read(self, addr):
        self.reads += 1
        if 0x7000 <= addr <= 0x7FFF:
            return self._dev_read(addr - 0x7000)
        return {0xFFE0: self.csr, 0xFFE1: self.osm_xy, 0xFFE2: self.osm_dxdy, 0xFFE3: self.ascal,
                0xFFE8: 0xFFFF, 0xFFF0: self.cfd_addr, 0xFFF1: self.cfd[self.cfd_addr],
                0xFFF2: self.cfm_addr, 0xFFF3: self.cfm[self.cfm_addr], 0xFFF4: self.dev,
                0xFFF5: self.win, 0xFFF8: 1, 0xFFF9: self.menu_id >> 16,
                0xFFFA: self.menu_id & 0xFFFF, 0xFFFB: self.marker}.get(
            addr, self.defaults[addr - 0xFFC0] if 0xFFC0 <= addr <= 0xFFCF else 0)

    def _dev_write(self, o, v):
        d, w = self.dev, self.win
        if d in (0, 1):
            if w == 0 and o < DX * DY:
                self.vram[d][o] = v & 0xFF
        elif d == 3:
            if w == 0 and o < 0x200:
                self.pphase[o] = v
        elif d == 6:
            if o < 8 and not self.rtc_cmd & 8:
                self.rtc[o] = v & 0xFF
            elif o == 8:
                self.rtc_cmd = v & 0x0E
                if v & 2:                   # host build: the read completes with the written time
                    self.rtc_toggles += 1
                    self.rtc_cmd &= ~2
        elif d == 0x100:
            a = w * 0x1000 + o
            if a < len(self.kick):
                self.kick[a] = v & 0xFF
                self.kick_writes += 1
        elif d == 0x103:
            if w == 0xFFFF:
                self._adf_csr_write(o, v)
            elif w == 0xFFFE:
                self._wbc_write(o, v)
            else:
                a = w * 0x1000 + o
                if a < len(self.adf):
                    self.adf[a] = v & 0xFF

    def _dev_read(self, o):
        d, w = self.dev, self.win
        if d in (0, 1):
            return self.vram[d][o] if w == 0 and o < DX * DY else 0
        if d == 3:
            return self.pphase[o] if w == 0 and o < 0x200 else 0
        if d == 6:
            return self.rtc[o] if o < 8 else (self.rtc_cmd if o == 8 else 0)
        if d == 0xFF:
            if w in (0x10, 0x11):
                return {0: DX << 8 | DY, 1: 0, 2: DX << 8 | DY}.get(o, 0)
            if w == 0x30:
                return self.core_video.get(o, 0)
            return 0
        if d == 0x103:
            if w == 0xFFFF:
                return self._adf_csr_read(o)
            if w == 0xFFFE:
                return self._wbc_read(o)
            a = w * 0x1000 + o
            return self.adf[a] if a < len(self.adf) else 0
        return 0

    # adf_mount_wrapper: M2M CSR (window 0xFFFF)
    def _adf_csr_write(self, o, v):
        if o == 0x000:
            self.adf_req = v & 15
            if self.adf_req == REQ_OK and self.adf_resp == RESP_IDLE:
                ok = self.adf_fs % TRACK == 0 and 160 <= self.adf_fs // TRACK <= 166
                self.adf_resp = RESP_READY if ok else RESP_ERROR
                self.adf_err = 0 if ok else 1
                self.adf_mounted = ok
                self.adf_tracks = self.adf_fs // TRACK if ok else 0
            elif self.adf_req != REQ_OK:
                self.adf_resp, self.adf_err, self.adf_mounted = RESP_IDLE, 0, False
        elif o == 0x001:
            self.adf_fs = (self.adf_fs & 0xFFFF0000) | v
        elif o == 0x002:
            self.adf_fs = (self.adf_fs & 0xFFFF) | v << 16

    def _adf_csr_read(self, o):
        if o == 0x000:
            return self.adf_req
        if o == 0x001:
            return self.adf_fs & 0xFFFF
        if o == 0x002:
            return self.adf_fs >> 16
        if o == 0x010:
            return self.adf_resp
        if o == 0x011:
            return self.adf_err
        if 0x100 <= o < 0x100 + len(ADF_ERR_STR) and self.adf_err:
            return ord(ADF_ERR_STR[o - 0x100])
        return 0

    # adf_mount_wrapper: WBC (window 0xFFFE)
    def _wbc_write(self, o, v):
        if o == 0x000:
            self.wbc_wr_en = v & 1
        elif o == 0x002:
            self.wbc_delay = v
        elif 0x010 <= o <= 0x01A:
            base = (o - 0x010) * 16
            for b in range(16):
                if v >> b & 1 and base + b < 166:
                    self.wbc_dirty[base + b] = 0

    def _wbc_read(self, o):
        if o == 0x000:
            return self.wbc_wr_en
        if o == 0x001:
            any_dirty = any(self.wbc_dirty)
            return (1 if any_dirty else 0) | (2 if any_dirty and self.wbc_cnt == 0 else 0)
        if o == 0x002:
            return self.wbc_delay
        if 0x010 <= o <= 0x01A:
            base = (o - 0x010) * 16
            return sum(self.wbc_dirty[base + b] << b for b in range(16) if base + b < 166)
        return 0

    # ---- views for tests
    def screen_text(self, x0=0, y0=0, w=DX, h=DY):
        rows = []
        for y in range(y0, y0 + h):
            rows.append(''.join(_glyph(self.vram[0][y * DX + x]) for x in range(x0, x0 + w)))
        return rows


_GLYPHS = {0: ' ', 201: '+', 187: '+', 200: '+', 188: '+', 205: '=', 186: '|', 196: '-',
           199: '|', 182: '|', 7: '*', 16: '>', 17: '<', 185: '[', 204: ']', 254: '#'}


def _glyph(c):
    if c in _GLYPHS:
        return _GLYPHS[c]
    return chr(c) if 32 <= c < 127 else '?'
