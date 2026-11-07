#!/usr/bin/env python3
"""attic_test.py -- check the attic RAM ($8000000-) through the serial monitor.
K325T: after reset the attic is 8 MB with mirrors (like a real MEGA65); the size register
$BFFFFFC (write 64 / read 8 or 64) unlocks 64 MB, whose top 16 bytes are the registers.
Checks the 8 MB mode (register, mirror), unlocks, then writes distinct bytes at word
boundaries / top / middle and on every address line, reads them back, checks the mirror at
the size boundary and that the no-RAM placeholder "ExtraRAM" is gone; restores 8 MB.
Needs m65io running (monitor pipe on TCP 4510).

    scripts/attic_test.py [size_mb]     default 64 (the K325T bitstream); 8 = a real MEGA65"""
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

SIZE = (int(sys.argv[1]) if len(sys.argv) > 1 else 64) << 20
REG = 0xBFFFFFC
fails = 0

def check(what, ok):
    global fails
    fails += not ok
    print("  %s %s" % (what, "ok" if ok else "FAIL"))

if SIZE > 8 << 20:
    # 8 MB compat mode first (the state after any reset)
    poke(REG, [8])
    check("size register reads 8 after reset/compat", peek(REG, 1)[0] == 8)
    poke(0x8000123, [0x5C])
    check("8 MB mirror ($8800123 == $8000123)", peek(0x8800123, 1)[0] == 0x5C)
    poke(REG, [SIZE >> 20])
    check("size register reads %d after unlock" % (SIZE >> 20), peek(REG, 1)[0] == SIZE >> 20)
    TOP = 0x8000000 + SIZE - 17                    # top 16 bytes = controller registers
else:
    TOP = 0x8000000 + SIZE - 1
first = peek(0x8000000, 8)
print("before: $8000000 = %s %r" % (first.hex(), first))
addrs = [0x8000000, 0x8000001, 0x800000F, 0x8000010, 0x8000011, 0x8123456, 0x8000000 + SIZE // 2,
         TOP - 15, TOP]
addrs += [0x8000000 + (1 << k) + 9 for k in range(4, SIZE.bit_length() - 1)]   # every address line
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
mir = peek(0x8000000 + SIZE, 1)[0]
print("  mirror $%07X = %02X (expect %02X = $8000000) %s" % (0x8000000 + SIZE, mir, vals[0x8000000],
                                                           "ok" if mir == vals[0x8000000] else "FAIL"))
fails += mir != vals[0x8000000]
if peek(0x8000000, 8) == b"ExtraRAM":
    print("  still 'ExtraRAM' placeholder -> no attic RAM"); fails += 1
if SIZE > 8 << 20:
    poke(REG, [8])
    check("back to 8 MB (register reads 8)", peek(REG, 1)[0] == 8)
print("ATTIC %d MB PASS" % (SIZE >> 20) if not fails else "ATTIC FAIL (%d)" % fails)
sys.exit(1 if fails else 0)
