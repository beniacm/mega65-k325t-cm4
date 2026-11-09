#!/usr/bin/env python3
"""golden_compare.py — compare two demo_run.py capture sets pixel by pixel.

  scripts/golden_compare.py captures/demos/golden_xctl captures/demos/run_X
        [--thresh 40] [--max-px 200] [--mask x0,y0,x1,y1 ...] [--ignore NAME,...] [--all]

For every program that has a '_b.png' (and '_a.png') in both folders, counts the pixels whose
max channel difference exceeds --thresh and prints the programs over --max-px.  Animated demos
differ by nature; the report groups them by the 'anim'/'static' flag from results.csv so the
reader can concentrate on static screens (title pictures, text, menus), where XCTL = 0 must be
pixel-identical to the golden bitstream.  Exit code 1 when a static screen differs.

Masked by default (720x576 captures): the ROM's clock on the BASIC screen (x 482..539, y 166..183)
-- it is drawn by the ROM from the soft RTC and differs between any two runs.  --max-px defaults
to 200 so that a blinking cursor (128 px) or a running counter does not count.  Ignored by
default: programs whose 'static' flag is wrong because the screen is random, slowly animated or
captured mid-typing (see DEFAULT_IGNORE; the 2026-11-09 golden_xctl vs golden_step5 run found
exactly these, every one checked by eye).  Contact sheets (sheet_*.png) are skipped.  --all prints
every capture, not only the differing ones.

Reference set: captures/demos/golden_step5 (2026-11-09, step-5 bitstream mega65_slot0_step5b.bit),
verified equal to golden_xctl (07-11 13:25 bitstream) apart from the list above; golden_xctl's
PALEDIT capture shows the menu system instead of the program, so compare against golden_step5.
"""
import csv, os, sys
from PIL import Image, ImageChops

DEFAULT_MASKS = [(482, 166, 540, 184)]          # ROM clock (BASIC screens, top line)
DEFAULT_IGNORE = {
    "INTRO2_MEGASWAP": "random puzzle board",
    "INTRO2_ANALOG_CLOCK": "clock hands (flagged BLACK)",
    "INTRO2_XANADU": "dancing figures (flagged BLACK)",
    "INTRO1_FB_BS_DEMO": "moving shapes (flagged BLACK)",
    "INTRO1_LUNAR_TAXI_2": "intro text typed out over seconds (capture phase)",
}


def flags(folder):
    out = {}
    p = os.path.join(folder, "results.csv")
    if os.path.exists(p):
        for row in csv.reader(open(p)):
            if len(row) >= 3:
                out[(row[0].split(".")[0], row[1])] = row[2]
    return out


def opt(name, default, conv=lambda s: s):
    return conv(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main():
    a, b = sys.argv[1], sys.argv[2]
    thresh = opt("--thresh", 40, int)
    maxpx = opt("--max-px", 200, int)
    masks = list(DEFAULT_MASKS)
    for i, arg in enumerate(sys.argv):
        if arg == "--mask":
            masks.append(tuple(int(v) for v in sys.argv[i + 1].split(",")))
    ignore = dict(DEFAULT_IGNORE)
    for n in opt("--ignore", "", str).split(","):
        if n:
            ignore[n] = "command line"
    show_all = "--all" in sys.argv
    fa = flags(a)
    names = sorted(f for f in os.listdir(a) if f.endswith(".png") and not f.startswith("sheet_")
                   and os.path.exists(os.path.join(b, f)))
    bad_static = 0
    print("%-44s %-7s %8s %8s" % ("capture", "flag", "px diff", "verdict"))
    for f in names:
        ia = Image.open(os.path.join(a, f)).convert("RGB")
        ib = Image.open(os.path.join(b, f)).convert("RGB")
        if ia.size != ib.size:
            print("%-44s %-7s %8s %8s" % (f, "-", "size", "DIFF")); bad_static += 1; continue
        d = ImageChops.difference(ia, ib)
        for (x0, y0, x1, y1) in masks:
            d.paste((0, 0, 0), (x0, y0, x1, y1))
        n = sum(1 for p in d.getdata() if max(p) > thresh)
        prog = f.rsplit("_", 1)[0]
        flag = "?"
        for (disk, name), fl in fa.items():
            if f.startswith(disk + "_" + "".join(c if c.isalnum() else "_" for c in name)):
                flag = fl
        if n <= maxpx:
            verdict = "ok"
        elif flag == "anim":
            verdict = "anim"
        elif prog in ignore:
            verdict = "ignored"
        else:
            verdict = "DIFF"
            bad_static += 1
        if show_all or verdict != "ok":
            note = ("  (%s)" % ignore[prog]) if verdict == "ignored" else ""
            print("%-44s %-7s %8d %8s%s" % (f, flag, n, verdict, note))
    print("static screens differing: %d of %d captures" % (bad_static, len(names)))
    sys.exit(1 if bad_static else 0)


if __name__ == "__main__":
    main()
