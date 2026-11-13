"""hostbus.py -- the Linux shell's access to the Amiga core's host master (PROTOCOL.md v0).

Two layers:
  * a *link* moves raw bytes: put(bytes) into the master's PUT FIFO, get(n) from its GET FIFO,
    reset(), status() -> HostStatus.  SpiLink talks to rk_bridge over spidev (opcodes 0x80/0x81/
    0x82, STATUS bytes 72..81); model.ModelLink is the Python model used by the tests.
  * HostBus encodes the master's command stream (WR, RD, BW8, BW16, BR8, BR16, MARK) and does the
    flow control.  Every bridge transaction is bounded (<= MAX_PUT bytes), so a caller that yields
    between chunks (the *_iter methods) never holds the link longer than one chunk: the input path
    of m65d gets the bridge between two chunks (about 1.1 ms at 33 MHz).

All addresses are QNICE addresses as the firmware used them (INVENTORY.md section 1).
"""

import time

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

# ---- bridge opcodes and STATUS layout
OP_STATUS, OP_HOST_PUT, OP_HOST_GET, OP_HOST_RESET = 0x01, 0x80, 0x81, 0x82
ST_VERSION, ST_PUT_FREE, ST_GET_FILL, ST_FLAGS, ST_MENU_ID = 72, 73, 75, 77, 78
ST_LEN = 82
FL_OVERFLOW, FL_BUSY, FL_STREAM_ERR, FL_TIMEOUT, FL_MARKER = 1, 2, 4, 8, 16

C_WR, C_RD, C_BW8, C_BW16, C_BR8, C_BR16, C_MARK = 1, 2, 3, 4, 5, 6, 7

MAX_PUT = 4093                  # data bytes per HOST_PUT (spidev's 4096-byte buffer - opcode ...)
MAX_GET = 4094                  # answer bytes per HOST_GET (4096 - opcode - dummy)
GET_FIFO = 4096
BURST_HDR = 10                  # op dev(2) win(2) off(2) n(3)


class BusError(Exception):
    pass


class HostStatus:
    def __init__(self, raw):
        self.raw = bytes(raw)
        self.version = raw[ST_VERSION]
        self.put_free = raw[ST_PUT_FREE] << 8 | raw[ST_PUT_FREE + 1]
        self.get_fill = raw[ST_GET_FILL] << 8 | raw[ST_GET_FILL + 1]
        self.flags = raw[ST_FLAGS]
        self.menu_id = int.from_bytes(bytes(raw[ST_MENU_ID:ST_MENU_ID + 4]), 'big')
        self.core_id = raw[52]


class SpiLink:
    """the host master behind rk_bridge, over an open spidev object (m65d's Bridge.spi)"""

    def __init__(self, spi):
        self.spi = spi

    def status(self):
        r = self.spi.xfer2([OP_STATUS, 0] + [0] * ST_LEN)[2:]
        if bytes(r[0:2]) != b"Me":
            raise BusError('no bridge answer')
        return HostStatus(r)

    def put(self, data):
        self.spi.xfer2([OP_HOST_PUT] + list(data))

    def get(self, n):
        return bytes(self.spi.xfer2([OP_HOST_GET, 0] + [0] * n)[2:])

    def reset(self):
        self.spi.xfer2([OP_HOST_RESET])


def _burst(op, dev, win, off, n):
    return bytes([op, dev >> 8, dev & 0xFF, win >> 8, win & 0xFF, off >> 8, off & 0xFF,
                  (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])


class HostBus:
    def __init__(self, link, timeout=0.5):
        self.link = link
        self.timeout = timeout
        self.credit = 0                     # PUT FIFO bytes known to be free
        self.out = bytearray()              # commands not yet sent
        self.expect = 0                     # answer bytes owed by sent commands
        self.mark_tag = 0
        self.stats = {'puts': 0, 'gets': 0, 'status': 0, 'bytes_out': 0}

    # ---- link level
    def status(self):
        self.stats['status'] += 1
        st = self.link.status()
        if st.version == 0:
            raise BusError('no host master in this bitstream')
        if st.flags & FL_STREAM_ERR:
            raise BusError('host master stream error (needs HOST_RESET)')
        self.credit = st.put_free
        return st

    def reset(self):
        """drop a half-sent stream of an earlier process; registers are not touched"""
        self.link.reset()
        self.out.clear()
        self.expect = 0
        self.credit = 0

    def flush(self):
        """send everything queued, in PUT transactions of at most MAX_PUT bytes"""
        while self.out:
            n = min(len(self.out), MAX_PUT)
            t0 = time.monotonic()
            while self.credit < n:
                st = self.status()
                if self.credit >= n:
                    break
                if self.credit >= 64 and self.credit < n:
                    n = self.credit
                    break
                if time.monotonic() - t0 > self.timeout:
                    raise BusError('PUT FIFO stays full (flags %02x)' % st.flags)
            self.link.put(self.out[:n])
            self.stats['puts'] += 1
            self.stats['bytes_out'] += n
            del self.out[:n]
            self.credit -= n

    def _collect(self, n):
        """the next n answer bytes (sends what is queued first)"""
        self.flush()
        data = bytearray()
        t0 = time.monotonic()
        while len(data) < n:
            st = self.status()
            avail = min(st.get_fill, n - len(data), MAX_GET)
            if avail:
                data += self.link.get(avail)
                self.stats['gets'] += 1
                t0 = time.monotonic()
            elif time.monotonic() - t0 > self.timeout:
                raise BusError('answer missing: %d of %d bytes (flags %02x)' % (len(data), n, st.flags))
        self.expect -= n
        return bytes(data)

    # ---- single accesses
    def wr(self, addr, data):
        self.out += bytes([C_WR, addr >> 8, addr & 0xFF, (data >> 8) & 0xFF, data & 0xFF])
        if len(self.out) >= MAX_PUT:
            self.flush()

    def rd(self, addr):
        self.out += bytes([C_RD, addr >> 8, addr & 0xFF])
        self.expect += 2
        a = self._collect(2)
        return a[0] << 8 | a[1]

    def rd_many(self, addrs, dev=None, win=None):
        """several reads in one round trip (optionally after selecting a device and window)"""
        if dev is not None:
            self.wr(RAMROM_DEV, dev)
            self.wr(RAMROM_4KWIN, win)
        for a in addrs:
            self.out += bytes([C_RD, a >> 8, a & 0xFF])
        self.expect += 2 * len(addrs)
        d = self._collect(2 * len(addrs))
        return [d[i] << 8 | d[i + 1] for i in range(0, len(d), 2)]

    def dev_wr(self, dev, win, off, data):
        """one write into a device (sets device and window first: bursts leave them changed)"""
        self.wr(RAMROM_DEV, dev)
        self.wr(RAMROM_4KWIN, win)
        self.wr(RAMROM_DATA + off, data)

    def dev_rd(self, dev, win, off):
        self.wr(RAMROM_DEV, dev)
        self.wr(RAMROM_4KWIN, win)
        return self.rd(RAMROM_DATA + off)

    def sync(self):
        """MARK barrier: returns when everything sent before has completed"""
        self.mark_tag = (self.mark_tag + 1) & 0xFF
        self.out += bytes([C_MARK, self.mark_tag])
        self.expect += 1
        tag = self._collect(1)[0]
        if tag != self.mark_tag:
            raise BusError('MARK echo %02x, expected %02x' % (tag, self.mark_tag))

    # ---- bursts.  addr = window * 0x1000 + offset (the master carries offset into the window)
    def write_bytes_iter(self, dev, addr, data, chunk=MAX_PUT - BURST_HDR):
        """BW8 in chunks; yields after each chunk has been handed to the link"""
        pos = 0
        while pos < len(data):
            n = min(chunk, len(data) - pos)
            a = addr + pos
            self.out += _burst(C_BW8, dev, a >> 12, a & 0xFFF, n) + bytes(data[pos:pos + n])
            self.flush()
            pos += n
            yield pos

    def write_words_iter(self, dev, addr, words, chunk=(MAX_PUT - BURST_HDR) // 2):
        pos = 0
        while pos < len(words):
            n = min(chunk, len(words) - pos)
            a = addr + pos
            body = bytearray()
            for w in words[pos:pos + n]:
                body += bytes([(w >> 8) & 0xFF, w & 0xFF])
            self.out += _burst(C_BW16, dev, a >> 12, a & 0xFFF, n) + body
            self.flush()
            pos += n
            yield pos

    def read_bytes_iter(self, dev, addr, n, chunk=GET_FIFO):
        """BR8 in chunks that fit the GET FIFO; yields (offset, bytes) per chunk"""
        pos = 0
        while pos < n:
            k = min(chunk, n - pos)
            a = addr + pos
            self.out += _burst(C_BR8, dev, a >> 12, a & 0xFFF, k)
            self.expect += k
            data = self._collect(k)
            yield pos, data
            pos += k

    def read_words(self, dev, addr, n):
        out = []
        pos = 0
        while pos < n:
            k = min(GET_FIFO // 2, n - pos)
            a = addr + pos
            self.out += _burst(C_BR16, dev, a >> 12, a & 0xFFF, k)
            self.expect += 2 * k
            d = self._collect(2 * k)
            out += [d[i] << 8 | d[i + 1] for i in range(0, len(d), 2)]
            pos += k
        return out

    # blocking conveniences (tests, short transfers)
    def write_bytes(self, dev, addr, data):
        for _ in self.write_bytes_iter(dev, addr, data):
            pass

    def write_words(self, dev, addr, words):
        for _ in self.write_words_iter(dev, addr, words):
            pass

    def read_bytes(self, dev, addr, n):
        out = bytearray()
        for _, d in self.read_bytes_iter(dev, addr, n):
            out += d
        return bytes(out)

    # ---- CFM helpers
    def cfm_write(self, words, only=None):
        """write CFM windows (each WR to CFM_DATA is atomic per 16-bit window)"""
        for w in range(16) if only is None else only:
            self.wr(CFM_ADDR, w)
            self.wr(CFM_DATA, words[w])

    def cfm_read(self):
        out = []
        for w in range(16):
            self.wr(CFM_ADDR, w)
            out.append(self.rd(CFM_DATA))
        return out

    def menu_id(self):
        return self.rd(MENU_ID_HI) << 16 | self.rd(MENU_ID_LO)

    def cfm_defaults(self):
        return [self.rd(CFM_DEFAULTS + i) for i in range(16)]
