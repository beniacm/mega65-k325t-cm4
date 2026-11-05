#!/usr/bin/env python3
"""bspatch.py OLD NEW PATCH -- apply a BSDIFF40 patch (Colin Percival's format, bzip2 blocks)."""
import bz2, struct, sys

def offtin(b):
    v = int.from_bytes(b[:7] + bytes([b[7] & 0x7F]), "little")
    return -v if b[7] & 0x80 else v

def bspatch(old, patch):
    if patch[:8] != b"BSDIFF40":
        raise ValueError("not a BSDIFF40 patch")
    clen, dlen, nsize = offtin(patch[8:16]), offtin(patch[16:24]), offtin(patch[24:32])
    ctrl = bz2.decompress(patch[32:32 + clen])
    diff = bz2.decompress(patch[32 + clen:32 + clen + dlen])
    extra = bz2.decompress(patch[32 + clen + dlen:])
    new = bytearray(nsize)
    op = np_ = cp = dp = ep = 0
    while np_ < nsize:
        x, y, z = (offtin(ctrl[cp + 8 * i:cp + 8 * i + 8]) for i in range(3))
        cp += 24
        for i in range(x):                      # add diff bytes to old bytes
            o = old[op + i] if 0 <= op + i < len(old) else 0
            new[np_ + i] = (diff[dp + i] + o) & 0xFF
        dp += x; np_ += x; op += x
        new[np_:np_ + y] = extra[ep:ep + y]     # copy extra bytes
        ep += y; np_ += y; op += z
    return bytes(new), diff

if __name__ == "__main__":
    old = open(sys.argv[1], "rb").read()
    new, diff = bspatch(old, open(sys.argv[3], "rb").read())
    open(sys.argv[2], "wb").write(new)
    zero = sum(1 for b in diff if b == 0)
    print("wrote %s (%d bytes); diff block %d bytes, %.1f%% zero" % (sys.argv[2], len(new), len(diff), 100.0 * zero / max(1, len(diff))))
