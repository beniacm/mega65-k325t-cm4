#!/usr/bin/env python3
"""rdfpatch.py OLD NEW PATCH.rdf -- apply a MEGA65 romdiff (.rdf) patch.
Port of decode_diff() from MEGA65/mega65-tools src/tools/romdiff.c."""
import sys

def rdfpatch(ref, rdf, size=128 * 1024):
    if rdf[:16] != b"MEGA65ROMPATCH01":
        raise ValueError("not a MEGA65ROMPATCH01 file")
    d, out, ofs, o = rdf[0x100:], bytearray(size), 0, 0
    while ofs < len(d):
        if o >= size:
            raise ValueError("output complete before end of patch stream")
        t = d[ofs]
        if t == 0x00:                                  # single-byte literal (XOR with ref)
            out[o] = ref[o] ^ d[ofs + 1]; o += 1; ofs += 2
        elif t == 0x01:
            raise ValueError("unsupported token $01")
        elif t < 0x80:                                 # exact match, 1..63 bytes
            n = ((t - 2) >> 1) + 1
            a = ((t & 1) << 16) | d[ofs + 1] | (d[ofs + 2] << 8)
            ofs += 3
            out[o:o + n] = ref[a:a + n]; o += n
        else:                                          # approximate match, 1..64 bytes
            n = ((t & 0x7F) >> 1) + 1
            a = ((t & 1) << 16) | d[ofs + 1] | (d[ofs + 2] << 8)
            ofs += 3
            out[o:o + n] = ref[a:a + n]
            bm, ofs = ofs, ofs + (n + 7) // 8
            for i in range(n):
                if d[bm + (i >> 3)] & (1 << (i & 7)):
                    out[o + i] ^= d[ofs]; ofs += 1
            o += n
    if o != size:
        raise ValueError("patch produced %d bytes, expected %d" % (o, size))
    return bytes(out)

if __name__ == "__main__":
    ref = open(sys.argv[1], "rb").read()
    open(sys.argv[2], "wb").write(rdfpatch(ref, open(sys.argv[3], "rb").read()))
    print("wrote", sys.argv[2])
