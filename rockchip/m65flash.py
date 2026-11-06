#!/usr/bin/env python3
"""m65flash.py -- the FPGA's configuration flash (S25FL128L, 16 MB) through rk_bridge
(command 0x50 pass-through; works while either core runs).  Slots: 0 at 0x000000 (boots at
power-on), 1 at 0x800000.

  m65flash.py id                         JEDEC ID (S25FL128L = 01 60 18)
  m65flash.py read <addr> <len> <file>   read flash to a file
  m65flash.py backup <file>              whole 16 MB
  m65flash.py write <slot|0xaddr> <file.bit|file.bin> [--no-verify] [--raw]
                                         erase (64 KB sectors), program, verify.  Images
                                         outside slot 0 get 4 KB of $FF in front (--raw: not):
                                         the MEGA65's HYPPO then sees the slot as EMPTY
                                         (first 512 bytes $FF) and does not auto-start it or
                                         warn; the FPGA skips the $FF bytes before the sync.
  m65flash.py verify <slot|0xaddr> <file.bit|file.bin>
  m65flash.py boot <slot|0xaddr>         reboot the FPGA into that slot (C64 core: bridge
                                         IPROG; MEGA65 core: $D6C8..$D6CF via m65d)

Frame: 0x50, len_hi, len_lo, len data bytes, 2 pads; the answer to data byte k comes
back in frame byte k + 5 (FPGA shifts each byte at ~27 MHz, two bytes behind).
"""
import sys
import time

import spidev

FLASH_SIZE = 16 * 1024 * 1024
SLOT = {0: 0x000000, 1: 0x800000}
CHUNK = 4000                      # read bytes per frame (spidev buffer is 4096)

spi = spidev.SpiDev()
spi.open(3, 0)
spi.mode = 0
spi.max_speed_hz = 20000000


def flash(data):
    """one flash command (CS low for exactly len(data) bytes); returns the answers"""
    n = len(data)
    r = spi.xfer2([0x50, n >> 8, n & 0xFF] + list(data) + [0, 0])
    return bytes(r[5:5 + n])


def status():
    return flash([0x05, 0])[1]


def wait_ready(timeout=10.0):
    t0 = time.time()
    while status() & 1:
        if time.time() - t0 > timeout:
            sys.exit("flash busy timeout")


def wren():
    flash([0x06])
    if not status() & 2:
        sys.exit("write enable failed (WEL not set) -- is the bridge pass-through in this bitstream?")


def jedec():
    return flash([0x9F, 0, 0, 0])[1:4]


def check_id():
    i = jedec()
    if i != b"\x01\x60\x18":
        sys.exit("unexpected JEDEC ID %s (expected 01 60 18, S25FL128L)" % " ".join("%02x" % b for b in i))


def read(addr, n):
    out = bytearray()
    while n > 0:
        k = min(n, CHUNK)
        out += flash([0x03, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF] + [0] * k)[4:]
        addr += k
        n -= k
    return bytes(out)


def unprotect():
    sr = status()
    if sr & 0x1C:                      # BP2..BP0 set: clear block protection
        wren()
        flash([0x01, 0x00])
        wait_ready()


def erase64k(addr):
    wren()
    flash([0xD8, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF])
    wait_ready(5.0)


def program(addr, data):
    for o in range(0, len(data), 256):
        page = data[o:o + 256]
        if page == b"\xff" * len(page):
            continue
        a = addr + o
        wren()
        flash([0x02, (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF] + list(page))
        wait_ready(1.0)
        if (o // 256) % 4096 == 0:
            print("  programmed %d / %d KB" % (o // 1024, len(data) // 1024), flush=True)


def load_image(path):
    raw = open(path, "rb").read()
    if path.endswith(".bit"):
        # Xilinx .bit: header fields a..d, then 'e' + 32-bit length + configuration data
        p = 13                                    # after the fixed preamble
        while p < len(raw):
            key = raw[p]
            if key == 0x65:
                n = int.from_bytes(raw[p + 1:p + 5], "big")
                return raw[p + 5:p + 5 + n]
            n = int.from_bytes(raw[p + 1:p + 3], "big")
            p += 3 + n
        sys.exit("not a .bit file")
    return raw


def where(s):
    return SLOT[int(s)] if s in ("0", "1") else int(s, 0)


def verify(addr, data):
    got = read(addr, len(data))
    if got != data:
        bad = next(i for i in range(len(data)) if got[i] != data[i])
        sys.exit("VERIFY FAILED at 0x%06x: %02x != %02x" % (addr + bad, got[bad], data[bad]))
    print("verify ok: %d bytes at 0x%06x" % (len(data), addr))


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "id":
        print("JEDEC ID", " ".join("%02x" % b for b in jedec()), " status %02x" % status())
    elif a[0] == "read":
        open(a[3], "wb").write(read(int(a[1], 0), int(a[2], 0)))
    elif a[0] == "backup":
        check_id()
        t0 = time.time()
        data = read(0, FLASH_SIZE)
        open(a[1], "wb").write(data)
        print("backup: 16 MB in %.0f s -> %s" % (time.time() - t0, a[1]))
    elif a[0] in ("write", "verify"):
        check_id()
        addr, data = where(a[1]), load_image(a[2])
        if addr != 0 and "--raw" not in a:
            data = b"\xff" * 4096 + data
        if addr + len(data) > FLASH_SIZE or (addr == 0 and len(data) > 0x800000):
            sys.exit("image does not fit the slot")
        if a[0] == "write":
            t0 = time.time()
            unprotect()
            end = addr + len(data)
            for s in range(addr & ~0xFFFF, end, 0x10000):
                erase64k(s)
            print("erased %d KB in %.0f s" % ((end - (addr & ~0xFFFF) + 0xFFFF) // 1024, time.time() - t0),
                  flush=True)
            program(addr, data)
            print("programmed %d bytes at 0x%06x in %.0f s" % (len(data), addr, time.time() - t0))
            if "--no-verify" in a:
                return
        verify(addr, data)
    elif a[0] == "boot":
        addr = where(a[1])
        spi.xfer2([0x60, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF,
                   0xA5])
        print("IPROG 0x%06x requested (C64 core); the MEGA65 core reboots via m65d 'core'" % addr)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
