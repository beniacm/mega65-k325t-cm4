#!/usr/bin/env python3
"""rasterdma_test.py -- K325T raster DMA list: border colour bands without CPU timing code.

Writes a 3-job enhanced DMA list to chip RAM (default $5F000) and enables it:
  job 1 (frame start):     fill $FFD3020 (border) with 14 (light blue)
  job 2 (wait line 100):   fill border with 2 (red)
  job 3 (wait line 200):   fill border with 5 (green)
Each job: options $0A (F018A) $81 $FF (dest MB $FF) [$D0 line] $00, then an F018A fill job
(cmd bit 2 = chain).  XCTL ($FFD71F0) bit 1 = on, $FFD71F4-7 = list address.  The CPU keeps
running BASIC: type into it while the bands show.  --off disables the list again.

    scripts/rasterdma_test.py [--addr 0x5f000] [--off]
"""
import os
import re
import socket
import sys
import time

HOST = os.environ.get("M65_HOST", "192.168.1.143")
XCTL = 0xFFD71F0


def mon(cmd, wait=0.4):
    with socket.create_connection((HOST, 4510), timeout=5) as s:
        s.sendall((cmd + "\r").encode())
        s.settimeout(0.1)
        out, t0 = b"", time.time()
        while time.time() - t0 < wait:
            try:
                out += s.recv(65536)
            except socket.timeout:
                pass
    return out.decode("latin-1")


def peek(addr, n=1):
    r = mon("m%07x" % addr, 0.6)
    m = re.search(r":0?%07X:([0-9A-F]{32})" % addr, r)
    if not m:
        raise RuntimeError("no monitor reply for %07X" % addr)
    return bytes.fromhex(m.group(1))[:n]


def poke(addr, data):
    for i in range(0, len(data), 16):                  # the monitor takes short lines only
        mon("s%07x %s" % (addr + i, " ".join("%02x" % b for b in data[i:i + 16])), 0.3)


def job(colour, line, chain):
    opts = [0x0A, 0x81, 0xFF]
    if line is not None:
        opts += [0xD0, line & 0xFF, 0xD1, line >> 8]
    opts += [0x00]
    cmd = 0x03 | (0x04 if chain else 0)               # fill (+ chain)
    # F018A: cmd, count(2), src(3: low byte = fill value), dst(3), modulo(2)
    return opts + [cmd, 1, 0, colour, 0, 0, 0x20, 0x30, 0x0D, 0, 0]


def main():
    addr = int(sys.argv[sys.argv.index("--addr") + 1], 0) if "--addr" in sys.argv else 0x5F000
    x = peek(XCTL)[0]
    if "--off" in sys.argv:
        poke(XCTL, [x & ~2])
        print("raster DMA off (XCTL=%02X)" % peek(XCTL)[0])
        return
    lst = job(14, None, True) + job(2, 100, True) + job(5, 200, False)
    poke(addr, lst)
    if peek(addr, 16) != bytes(lst[:16]):
        sys.exit("list not written correctly at $%07X" % addr)
    poke(0xFFD71F4, [addr & 0xFF, addr >> 8 & 0xFF, addr >> 16 & 0xFF, addr >> 24 & 0x0F])
    poke(XCTL, [x | 2])
    rd = peek(0xFFD71F4, 4)
    print("list at $%07X (%d bytes), XCTL=%02X, list reg=%s" % (addr, len(lst), peek(XCTL)[0], rd[::-1].hex()))


if __name__ == "__main__":
    main()
