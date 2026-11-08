#!/usr/bin/env python3
"""rrb_diag.py -- read the K325T RRB list mode diagnostic counters ($FFD71E0-$FFD71E3).

The VIC-IV counts list mode events on the pixel clock (16 bit, saturating at $FFFF except
"parts", which wraps); see docs/rrb_list_mode.md, "Diagnostic counters".  Registers:
  $FFD71E0 write: select counter 0..31 (read: the index)
  $FFD71E1/2 read: low/high byte of the selected counter (may tear: read until two agree)
  $FFD71E3 write: clear all counters and maxima
Through the serial monitor (m65d, port 4510), so each counter takes ~1.5 s to read.

    scripts/rrb_diag.py               print all counters
    scripts/rrb_diag.py clear         clear them
    scripts/rrb_diag.py watch SECONDS print, wait, print again with the deltas

Expected: frames == copies done (one each per frame, HOLD off), exp started == exp done +
aborts, collisions == 0, started-while-copying == 0; the maxima are pixel cycles (81 MHz), to
compare with one raster line = 2592 cycles.  Late pixels (23) == 0: with XCTL bit 5/6 on (the
list mode lead) by construction, so there check that 25 (the longest painting, incl. the
badline's fetch + expansion) stays below 2590: no line cut off by the next line start.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rasterdma_test import peek, poke                       # monitor helpers

SEL, CLR = 0xFFD71E0, 0xFFD71E3
# index: (name, kind) -- kind "max"/"value" are not event counts (no delta meaning)
COUNTERS = {
    0: ("frames (copy starts)", "count"),
    1: ("copies finished", "count"),
    2: ("expansions started", "count"),
    3: ("expansions finished", "count"),
    4: ("expander aborts", "count"),
    5: ("line start in ListWait while copying (row plain)", "count"),
    6: ("v2 rows plain: append point > LIMIT-2", "count"),
    7: ("port collisions (must be 0)", "count"),
    8: ("expansions ended at the buffer limit", "count"),
    9: ("object parts emitted (wraps)", "wrap"),
    10: ("max expansion duration (cycles)", "max"),
    11: ("max copy duration (cycles)", "max"),
    12: ("copy stall cycles", "count"),
    13: ("expansions of $FFFF rows", "count"),
    14: ("line start during an expansion", "count"),
    15: ("v2 expansions with start_scan", "count"),
    16: ("max copy start -> first read (cycles)", "max"),
    17: ("line start in ListWait (no copy), arm wins", "count"),
    18: ("line start in ListRun with exp_done", "count"),
    19: ("expansions started while copying (must be 0)", "count"),
    20: ("ycounter at the last copy end", "value"),
    21: ("ycounter at the last abort", "value"),
    22: ("line start in ListWait, copying, border branch", "count"),
    23: ("late pixels: written behind the display (must be 0)", "count"),
    24: ("ycounter at the last late pixel", "value"),
    25: ("max line start -> end of painting (cycles)", "max"),
    26: ("max line start -> first object part (cycles)", "max"),
}


def read_counter(idx, tries=6):
    poke(SEL, [idx])
    last = None
    for _ in range(tries):
        b = peek(SEL, 3)                                    # index, low, high in one line
        if b[0] != idx:
            raise RuntimeError("select reads back %d, not %d" % (b[0], idx))
        v = b[1] | b[2] << 8
        if v == last:
            return v
        last = v
    return last                                             # still changing: last read


def read_all():
    """all counters, and the time counter 0 (frames) was read"""
    t = time.time()
    return {i: read_counter(i) for i in COUNTERS}, t


def show(vals, prev=None, secs=None):
    for i, (name, kind) in COUNTERS.items():
        line = "%2d  %-50s %6d  $%04X" % (i, name, vals[i], vals[i])
        if kind == "max":
            line += "  (%.2f lines)" % (vals[i] / 2592)
        if prev is not None and kind in ("count", "wrap"):
            d = (vals[i] - prev[i]) & 0xFFFF if kind == "wrap" else vals[i] - prev[i]
            line += "  %+d" % d
            if secs and i == 0:
                line += " (%.1f/s)" % (d / secs)
            if kind == "count" and vals[i] == 0xFFFF:
                line += " (saturated)"
        print(line)


def main():
    args = sys.argv[1:]
    if args and args[0] == "clear":
        poke(CLR, [0])
        print("counters cleared")
    elif args and args[0] == "watch":
        secs = float(args[1]) if len(args) > 1 else 10.0
        a, ta = read_all()
        show(a)
        time.sleep(secs)
        b, tb = read_all()
        dt = tb - ta                                        # includes the reading time
        print("--- %.0f s later (wait %.0f s + reading)" % (dt, secs))
        show(b, a, dt)
    else:
        show(read_all()[0])


if __name__ == "__main__":
    main()
