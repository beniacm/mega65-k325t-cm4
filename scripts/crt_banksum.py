#!/usr/bin/env python3
"""crt_banksum.py <file.crt> <word-address-hex> [base-hex] -- expected sum of the 4096 16-bit words
the C64 core's cartridge cacher reads from <word address> (CRT image stored at word <base>,
default 0x200000), e.g. to check bank loads captured with a probe or ILA."""
import sys
d = open(sys.argv[1], "rb").read()
base = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x200000
for a in sys.argv[2].split(","):
    off = (int(a, 16) - base) * 2
    blk = d[off:off + 8192].ljust(8192, b"\0")
    print(f"word 0x{int(a, 16):06x} (file +0x{off:x}): sum {sum(int.from_bytes(blk[i:i+2], 'little') for i in range(0, 8192, 2)) & 0xFFFF:04x}")
