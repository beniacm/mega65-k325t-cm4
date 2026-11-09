#!/usr/bin/env python3
"""golden_compare.py — compare two demo_run.py capture sets pixel by pixel.

  scripts/golden_compare.py captures/demos/golden_xctl captures/demos/run_X [--thresh 40] [--max-px 50]

For every program that has a '_b.png' (and '_a.png') in both folders, counts the pixels whose
max channel difference exceeds --thresh and prints the programs over --max-px.  Animated demos
differ by nature; the report groups them by the 'anim'/'static' flag from results.csv so the
reader can concentrate on static screens (title pictures, text, menus), where XCTL = 0 must be
pixel-identical to the golden bitstream.  Exit code 1 when a static screen differs.
"""
import csv, os, sys
from PIL import Image, ImageChops


def flags(folder):
    out = {}
    p = os.path.join(folder, "results.csv")
    if os.path.exists(p):
        for row in csv.reader(open(p)):
            if len(row) >= 3:
                out[(row[0].split(".")[0], row[1])] = row[2]
    return out


def main():
    a, b = sys.argv[1], sys.argv[2]
    thresh = int(sys.argv[sys.argv.index("--thresh") + 1]) if "--thresh" in sys.argv else 40
    maxpx = int(sys.argv[sys.argv.index("--max-px") + 1]) if "--max-px" in sys.argv else 50
    fa = flags(a)
    names = sorted(f for f in os.listdir(a) if f.endswith(".png") and os.path.exists(os.path.join(b, f)))
    bad_static = 0
    print("%-44s %-7s %8s %8s" % ("capture", "flag", "px diff", "verdict"))
    for f in names:
        ia = Image.open(os.path.join(a, f)).convert("RGB")
        ib = Image.open(os.path.join(b, f)).convert("RGB")
        if ia.size != ib.size:
            print("%-44s %-7s %8s %8s" % (f, "-", "size", "DIFF")); continue
        d = ImageChops.difference(ia, ib).getdata()
        n = sum(1 for p in d if max(p) > thresh)
        key = f.rsplit("_", 1)[0]
        flag = "?"
        for (disk, name), fl in fa.items():
            if f.startswith(disk + "_" + "".join(c if c.isalnum() else "_" for c in name)):
                flag = fl
        verdict = "ok" if n <= maxpx else ("anim" if flag == "anim" else "DIFF")
        if verdict == "DIFF":
            bad_static += 1
        print("%-44s %-7s %8d %8s" % (f, flag, n, verdict))
    print("static screens differing: %d of %d captures" % (bad_static, len(names)))
    sys.exit(1 if bad_static else 0)


if __name__ == "__main__":
    main()
