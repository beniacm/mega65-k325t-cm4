#!/usr/bin/env python3
"""colourram_test.py -- K325T 64 KB colour RAM through the serial monitor.
XCTL ($FFD71F0) bit 2 clear (after reset): $FF88000-$FF8FFFF mirrors $FF80000- (32 KB, like a
real MEGA65).  Bit 2 set: 64 KB, the halves are separate.  Restores XCTL and the bytes it used."""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from rasterdma_test import peek, poke        # monitor helpers

XCTL = 0xFFD71F0
LO, HI = 0xFF87F00, 0xFF8FF00                # $7F00 and $FF00: same low 15 bits
fails = 0


def check(what, ok):
    global fails
    fails += not ok
    print("  %-50s %s" % (what, "ok" if ok else "FAIL"))


x = peek(XCTL)[0]
save = peek(LO, 1)[0]
poke(XCTL, [x & ~4])
poke(LO, [0x5A])
check("32 KB mode: $FF8FF00 mirrors $FF87F00", peek(HI)[0] == 0x5A)
poke(XCTL, [x | 4])
check("XCTL bit 2 reads back", peek(XCTL)[0] & 4 == 4)
poke(HI, [0xA5])
check("64 KB mode: $FF8FF00 holds its own byte", peek(HI)[0] == 0xA5)
check("64 KB mode: $FF87F00 unchanged", peek(LO)[0] == 0x5A)
poke(XCTL, [x & ~4])
check("back to 32 KB: mirror again", peek(HI)[0] == 0x5A)
poke(LO, [save])
poke(XCTL, [x])
print("COLOURRAM 64 KB PASS" if not fails else "COLOURRAM FAIL (%d)" % fails)
sys.exit(1 if fails else 0)
