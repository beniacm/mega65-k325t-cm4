#!/usr/bin/env python3
"""mkd64.py -- build a D64 disk image from C64 BASIC V2 text and/or PRG files.

   scripts/mkd64.py out.d64 "DISKNAME" name1=file1.bas [name2=file2.prg ...]

*.bas files are tokenised (C64 BASIC V2, keywords may be written without spaces;
text in quotes and after REM is kept); anything else is copied as a PRG (first two
bytes = load address).  Files are stored from track 1 upwards, the BAM and the
directory on track 18 as usual.
"""
import sys

TOKENS = ["END", "FOR", "NEXT", "DATA", "INPUT#", "INPUT", "DIM", "READ", "LET", "GOTO",
          "RUN", "IF", "RESTORE", "GOSUB", "RETURN", "REM", "STOP", "ON", "WAIT", "LOAD",
          "SAVE", "VERIFY", "DEF", "POKE", "PRINT#", "PRINT", "CONT", "LIST", "CLR", "CMD",
          "SYS", "OPEN", "CLOSE", "GET", "NEW", "TAB(", "TO", "FN", "SPC(", "THEN", "NOT",
          "STEP", "+", "-", "*", "/", "^", "AND", "OR", ">", "=", "<", "SGN", "INT", "ABS",
          "USR", "FRE", "POS", "SQR", "RND", "LOG", "EXP", "COS", "SIN", "TAN", "ATN", "PEEK",
          "LEN", "STR$", "VAL", "ASC", "CHR$", "LEFT$", "RIGHT$", "MID$", "GO"]
# token value = 0x80 + index in the ROM table (INPUT# / PRINT# precede INPUT / PRINT there)
ROM_ORDER = ["END", "FOR", "NEXT", "DATA", "INPUT#", "INPUT", "DIM", "READ", "LET", "GOTO",
             "RUN", "IF", "RESTORE", "GOSUB", "RETURN", "REM", "STOP", "ON", "WAIT", "LOAD",
             "SAVE", "VERIFY", "DEF", "POKE", "PRINT#", "PRINT", "CONT", "LIST", "CLR", "CMD",
             "SYS", "OPEN", "CLOSE", "GET", "NEW", "TAB(", "TO", "FN", "SPC(", "THEN", "NOT",
             "STEP", "+", "-", "*", "/", "^", "AND", "OR", ">", "=", "<", "SGN", "INT", "ABS",
             "USR", "FRE", "POS", "SQR", "RND", "LOG", "EXP", "COS", "SIN", "TAN", "ATN", "PEEK",
             "LEN", "STR$", "VAL", "ASC", "CHR$", "LEFT$", "RIGHT$", "MID$", "GO"]
TOKVAL = {k: 0x80 + i for i, k in enumerate(ROM_ORDER)}
# greedy matching: try longer keywords first
MATCH = sorted(TOKVAL, key=len, reverse=True)


def tokenise_line(text):
    out, i, quote, rem = bytearray(), 0, False, False
    while i < len(text):
        c = text[i]
        if quote or rem:
            out += c.encode("ascii")
            if c == '"':
                quote = False
            i += 1
            continue
        if c == '"':
            quote = True
            out += b'"'
            i += 1
            continue
        for kw in MATCH:
            if text.startswith(kw, i):
                out.append(TOKVAL[kw])
                i += len(kw)
                if kw == "REM":
                    rem = True
                break
        else:
            out += c.encode("ascii")
            i += 1
    return bytes(out)


def tokenise(src, load=0x0801):
    body, addr = bytearray(), load
    for raw in src.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        num, _, rest = raw.partition(" ")
        line = tokenise_line(rest.strip().upper()) + b"\0"
        nxt = addr + 4 + len(line)
        body += nxt.to_bytes(2, "little") + int(num).to_bytes(2, "little") + line
        addr = nxt
    body += b"\0\0"
    return load.to_bytes(2, "little") + bytes(body)


def spt(track):
    return 21 if track <= 17 else 19 if track <= 24 else 18 if track <= 30 else 17


def offset(track, sector):
    return (sum(spt(t) for t in range(1, track)) + sector) * 256


def petscii_name(name, n=16):
    return name.upper().encode("ascii")[:n].ljust(n, b"\xa0")


def build(diskname, files):
    img = bytearray(174848)
    used = {t: set() for t in range(1, 36)}
    used[18] |= {0, 1}
    t, s = 1, 0
    entries = []
    for name, data in files:
        chunks = [data[i:i + 254] for i in range(0, len(data), 254)] or [b""]
        first = (t, s)
        for k, ch in enumerate(chunks):
            o = offset(t, s)
            used[t].add(s)
            if k + 1 < len(chunks):
                nt, ns = (t, s + 1) if s + 1 < spt(t) else (t + 1, 0)
                if nt == 18:
                    nt, ns = 19, 0
                img[o], img[o + 1] = nt, ns
            else:
                img[o], img[o + 1] = 0, len(ch) + 1
                nt, ns = (t, s + 1) if s + 1 < spt(t) else (t + 1, 0)
                if nt == 18:
                    nt, ns = 19, 0
            img[o + 2:o + 2 + len(ch)] = ch
            t, s = nt, ns
        entries.append((name, first, len(chunks)))
    assert len(entries) <= 8, "one directory sector only"
    # directory 18/1
    d = offset(18, 1)
    img[d], img[d + 1] = 0, 0xFF
    for i, (name, (ft, fs), blocks) in enumerate(entries):
        e = d + 32 * i
        img[e + 2] = 0x82
        img[e + 3], img[e + 4] = ft, fs
        img[e + 5:e + 21] = petscii_name(name)
        img[e + 30:e + 32] = blocks.to_bytes(2, "little")
    # BAM 18/0
    b = offset(18, 0)
    img[b:b + 4] = bytes([18, 1, 0x41, 0])
    for tr in range(1, 36):
        free = [x for x in range(spt(tr)) if x not in used[tr]]
        bits = sum(1 << x for x in free)
        img[b + 4 * tr:b + 4 * tr + 4] = bytes([len(free), bits & 0xFF, (bits >> 8) & 0xFF,
                                                (bits >> 16) & 0xFF])
    img[b + 0x90:b + 0xA0] = petscii_name(diskname)
    img[b + 0xA0:b + 0xAB] = b"\xa0\xa0K3\xa02A\xa0\xa0\xa0\xa0"
    return bytes(img)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    files = []
    for arg in sys.argv[3:]:
        name, _, path = arg.partition("=")
        raw = open(path, "rb").read()
        files.append((name, tokenise(raw.decode()) if path.endswith(".bas") else raw))
    open(sys.argv[1], "wb").write(build(sys.argv[2], files))
    print(f"{sys.argv[1]}: {len(files)} file(s)")
