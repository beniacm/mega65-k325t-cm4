#!/usr/bin/env python3
"""rom_rtcpatch.py -- let BASIC 65 use the RTC on the QMTech K325T (board id $D629 = $62).

The ROM reads the RTC ($FFD7110..) for DT$, TI$ and the banner only when the board id is
below $0A (the official R2..R6 boards); otherwise it prints NO-RTC-2000.  This board's RTC is
soft_rtc in the bitstream, set by the Rockchip.  Both checks are `LDA $D629 / CMP #$0A / BCS`;
the patch raises the limit to #$63 so id $62 counts as "has an RTC".  Only applied when the
exact instruction bytes are found (ROM 920413); a patched ROM is left as it is.

    scripts/rom_rtcpatch.py MEGA65.ROM            patch in place
    scripts/rom_rtcpatch.py --check MEGA65.ROM    report only
"""
import sys

OLD = (bytes.fromhex("ad29d6c90ab029"), bytes.fromhex("ad29d6c90ab036"))   # DT$/banner, TI$
NEW_LIMIT = 0x63


def find(rom):
    hits = []
    for pat in OLD:
        for limit in (0x0A, NEW_LIMIT):
            p = pat[:4] + bytes([limit]) + pat[5:]
            i = rom.find(p)
            if i >= 0 and rom.find(p, i + 1) < 0:
                hits.append((i + 4, limit))
                break
        else:
            return None
    return hits


def main():
    check = "--check" in sys.argv
    path = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    rom = bytearray(open(path, "rb").read())
    hits = find(rom)
    if hits is None:
        sys.exit("%s: RTC board-id checks not found (unknown ROM version) -- not patched" % path)
    todo = [o for o, limit in hits if limit != NEW_LIMIT]
    if not todo:
        print("%s: already patched (RTC on board id < $%02X)" % (path, NEW_LIMIT))
        return
    if check:
        print("%s: unpatched, RTC checks at $%s" % (path, ", $".join("%05X" % (o - 4) for o in todo)))
        sys.exit(1)
    for o in todo:
        rom[o] = NEW_LIMIT
    open(path, "wb").write(rom)
    print("%s: patched %d RTC board-id checks ($0A -> $%02X)" % (path, len(todo), NEW_LIMIT))


if __name__ == "__main__":
    main()
