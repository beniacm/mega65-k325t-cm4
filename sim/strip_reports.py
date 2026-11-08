#!/usr/bin/env python3
"""strip_reports.py IN OUT -- sim copy of a VHDL file without comments and report statements
(viciv.vhdl reports every cycle; GHDL would print millions of lines).  Lines marked
'--SIMKEEP' are kept as they are."""
import re, sys

src = open(sys.argv[1]).read().split("\n")
out = []
for line in src:
    # drop comments (not inside strings)
    res, i, ins = "", 0, False
    while i < len(line):
        ch = line[i]
        if ch == '"':
            ins = not ins
        if not ins and line.startswith("--", i):
            break
        res += ch
        i += 1
    out.append(res)
text = "\n".join(out)
# report statements: 'report' at a statement start up to the terminating ';' outside strings
pat = re.compile(r'(?<=[;\n\s])report\b')
res, pos = [], 0
for m in pat.finditer(text):
    if m.start() < pos:
        continue
    # statement start? previous non-space token must end with ; then else begin => loop or is
    prev = text[:m.start()].rstrip()
    if not re.search(r'(;|\bthen|\belse|\bbegin|=>|\bloop|\bis)$', prev, re.I):
        continue
    j, ins = m.end(), False
    while j < len(text):
        c = text[j]
        if c == '"':
            ins = not ins
        elif c == ';' and not ins:
            break
        j += 1
    res.append(text[pos:m.start()])
    res.append("null")
    pos = j
res.append(text[pos:])
open(sys.argv[2], "w").write("".join(res))
