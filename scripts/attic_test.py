#!/usr/bin/env python3
"""attic_test.py -- check the 8 MB attic RAM ($8000000-$87FFFFF) through the serial monitor.
Writes distinct bytes at word boundaries / top / middle, reads them back, checks the 8 MB
mirror ($8800000 == $8000000) and that the no-RAM placeholder "ExtraRAM" is gone.
Needs m65io running (monitor pipe on TCP 4510)."""
import os, random, re, socket, sys, time
HOST = os.environ.get("M65_HOST", "192.168.1.143")

def mon(cmd, wait=0.6):
    with socket.create_connection((HOST, 4510), timeout=5) as s:
        s.sendall((cmd + "\r").encode())
        s.settimeout(0.1); out, t0 = b"", time.time()
        while time.time() - t0 < wait:
            try:
                out += s.recv(65536)
            except socket.timeout:
                pass
    return out.decode("latin-1")

def peek(addr, n=16):
    r = mon("m%07x" % addr)
    m = re.search(r":0?%07X:([0-9A-F]{32})" % addr, r)
    if not m:
        raise RuntimeError("no monitor reply for %07X: %r" % (addr, r[-80:]))
    return bytes.fromhex(m.group(1))[:n]

def poke(addr, data):
    mon("s%07x %s" % (addr, " ".join("%02x" % b for b in data)), wait=0.3)

fails = 0
first = peek(0x8000000, 8)
print("before: $8000000 = %s %r" % (first.hex(), first))
addrs = [0x8000000, 0x8000001, 0x800000F, 0x8000010, 0x8000011, 0x8123456, 0x8400000, 0x87FFFF0, 0x87FFFFF]
rnd = random.Random(65)
vals = {a: rnd.randrange(256) for a in addrs}
for a, v in vals.items():
    poke(a, [v])
for a, v in vals.items():
    got = peek(a, 1)[0]
    ok = got == v
    fails += not ok
    print("  $%07X wrote %02X read %02X %s" % (a, v, got, "ok" if ok else "FAIL"))
blk = bytes(rnd.randrange(256) for _ in range(16))
poke(0x8234560, blk)
got = peek(0x8234560)
print("  16-byte block at $8234560: %s" % ("ok" if got == blk else "FAIL %s vs %s" % (got.hex(), blk.hex())))
fails += got != blk
mir = peek(0x8800000, 1)[0]
print("  mirror $8800000 = %02X (expect %02X = $8000000) %s" % (mir, vals[0x8000000], "ok" if mir == vals[0x8000000] else "FAIL"))
fails += mir != vals[0x8000000]
if peek(0x8000000, 8) == b"ExtraRAM":
    print("  still 'ExtraRAM' placeholder -> no attic RAM"); fails += 1
print("ATTIC PASS" if not fails else "ATTIC FAIL (%d)" % fails)
sys.exit(1 if fails else 0)
