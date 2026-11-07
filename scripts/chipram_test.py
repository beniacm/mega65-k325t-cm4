#!/usr/bin/env python3
"""chipram_test.py -- K325T 1 MB chip RAM through the serial monitor.

After reset the CPU sees 384 KB like a real MEGA65 (writes to $60000-$FFFFF are dropped);
XCTL ($FFD71F0) bit 0 unlocks $00000-$FFFFF.  Checks: compat mode, unlock + read back of
XCTL, distinct bytes at region edges and on every address line above 384 KB, no aliasing with
the lower 384 KB, then restores XCTL.  Needs m65io (monitor pipe on TCP 4510).

    scripts/chipram_test.py
"""
import os
import random
import re
import socket
import sys
import time

HOST = os.environ.get("M65_HOST", "192.168.1.143")
XCTL = 0xFFD71F0


def mon(cmd, wait=0.6):
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
    r = mon("m%07x" % addr)
    m = re.search(r":0?%07X:([0-9A-F]{32})" % addr, r)
    if not m:
        raise RuntimeError("no monitor reply for %07X: %r" % (addr, r[-80:]))
    return bytes.fromhex(m.group(1))[:n]


def poke(addr, data):
    mon("s%07x %s" % (addr, " ".join("%02x" % b for b in data)), wait=0.3)


fails = 0


def check(what, ok):
    global fails
    fails += not ok
    print("  %-58s %s" % (what, "ok" if ok else "FAIL"))


xctl0 = peek(XCTL)[0]
poke(XCTL, [xctl0 & ~1])
check("XCTL reads back without bit 0", peek(XCTL)[0] & 1 == 0)
low = peek(0x00000)[0]
old = peek(0x60000)[0]
poke(0x60000, [old ^ 0xFF])
check("compat: write to $60000 dropped", peek(0x60000)[0] == old)

poke(XCTL, [xctl0 | 1])
check("XCTL bit 0 set (1 MB)", peek(XCTL)[0] & 1 == 1)
rnd = random.Random(1)
addrs = [0x5FFFF, 0x60000, 0x7FFFF, 0x80000, 0xABCDE, 0xFFFFF]
addrs += [0x60000 + (1 << k) + 3 for k in range(0, 19)]       # every address line above $60000
addrs += [0x80000 + (1 << k) + 5 for k in range(0, 19)]
addrs = sorted(set(addrs))
vals = {a: rnd.randrange(1, 256) for a in addrs}
for a, v in vals.items():
    poke(a, [v])
bad = [a for a, v in vals.items() if peek(a)[0] != v]
check("%d bytes in $5FFFF-$FFFFF read back" % len(addrs), not bad)
for a in bad[:5]:
    print("    $%05X" % a)
check("$00000 untouched (no alias of $80000)", peek(0x00000)[0] == low)
blk = bytes(rnd.randrange(256) for _ in range(16))
poke(0x9A000, blk)
check("16-byte block at $9A000", peek(0x9A000, 16) == blk)

poke(XCTL, [xctl0])
print("CHIPRAM 1 MB PASS" if not fails else "CHIPRAM FAIL (%d)" % fails)
sys.exit(1 if fails else 0)
