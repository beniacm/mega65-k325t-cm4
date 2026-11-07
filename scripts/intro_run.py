#!/usr/bin/env python3
"""intro_run.py -- run the programs of the MEGA65 intro disks through their own menus.

The menus start each program the way it needs (MEGA65 or C64 mode, NTSC switch, ...), so
this drives them like a user: cold start (FPGA reload via a core switch -> menu system),
disk key, title screen key, category, item, RETURN (the description page).  Disks #1-#3: "1".."4" categories, items "1".."9",
"A"..; disk #4: cursor keys + RETURN.  Per item: capture after --t1 s, then SPACE, fire on both
joysticks and RETURN (past title screens), capture at --t2 s.  Metrics + contact sheets as in
demo_run.py, in captures/demos/<run>/.

    scripts/intro_run.py [--set demos|all] [--disk 2] [--only 2:1:C] [--t1 15] [--t2 30]
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from demo_run import ROOT, ctl, typ, capture, metrics, sheet       # noqa: E402

KEYS = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# (disk key, category key, category name, item keys); disk 4 uses cursor positions instead
DEMOS = [
    ("1", "1", "demo", KEYS[:17]), ("1", "3", "music", KEYS[:3]),
    ("2", "1", "demo", KEYS[:34]), ("2", "3", "music", KEYS[:11]),
    ("3", "1", "demo", KEYS[1:17]),                       # "1" = [Bad Apple] submenu
    ("4", 1, "demo", list(range(10))),
]
OTHERS = [
    ("1", "2", "game", KEYS[:31]), ("1", "4", "utility", KEYS[:10]),
    ("2", "4", "tools", KEYS[:11]),
    ("3", "3", "tools", KEYS[2:12]),                      # "1"/"2" = submenus
]


def cold_start():
    ctl("core", "c64")
    time.sleep(8)
    ctl("core", "mega65")
    time.sleep(22)                                        # HYPPO + menu system


def launch(disk, cat, item):
    typ(disk, 9)                                          # menu system -> intro disk autoboot
    if disk == "4":
        ctl("key", "RETURN")                              # "press RETURN to continue"
        time.sleep(3)
        for _ in range(cat):
            ctl("key", "DOWN")
            time.sleep(0.4)
        ctl("key", "RETURN")
        time.sleep(2.5)
        for _ in range(item):
            ctl("key", "DOWN")
            time.sleep(0.4)
        ctl("key", "RETURN")
    else:
        ctl("key", "SPACE")                               # "press any key to begin"
        time.sleep(3)
        typ(cat, 2.5)
        typ(item, 2)
        ctl("key", "RETURN")                              # description page: "RETURN to start" 


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="demos", choices=["demos", "all"])
    ap.add_argument("--disk")
    ap.add_argument("--only", help="disk:category:item, e.g. 2:1:C")
    ap.add_argument("--t1", type=float, default=15)
    ap.add_argument("--t2", type=float, default=30)
    ap.add_argument("--run", default=time.strftime("intro_%Y%m%d_%H%M"))
    a = ap.parse_args()
    outdir = os.path.join(ROOT, "captures/demos", a.run)
    os.makedirs(outdir, exist_ok=True)
    w = csv.writer(open(os.path.join(outdir, "results.csv"), "a", newline=""))
    plan = DEMOS + (OTHERS if a.set == "all" else [])
    for disk, cat, cname, items in plan:
        if a.disk and disk != a.disk:
            continue
        rows = []
        for item in items:
            tag = "%s:%s:%s" % (disk, cat, item)
            if a.only and tag != a.only:
                continue
            cold_start()
            launch(disk, cat, item)
            t0 = time.time()
            base = os.path.join(outdir, "d%s_%s_%s" % (disk, cname, item))
            time.sleep(max(0, a.t1 - (time.time() - t0)))
            f1 = capture(base + "_a.png")
            ctl("key", "SPACE")
            time.sleep(1)
            ctl("joy", "0x10", "0x10")
            time.sleep(0.3)
            ctl("joy", "0", "0")
            time.sleep(1)
            ctl("key", "RETURN")
            time.sleep(max(0, a.t2 - (time.time() - t0)))
            f2 = capture(base + "_b.png")
            if not (f1 and f2):
                w.writerow([tag, cname, "nocapture", ""])
                continue
            nb, ch, cols = metrics(f1, f2)
            flag = "BLACK" if nb < 0.02 else ("static" if ch < 0.002 else "anim")
            w.writerow([tag, cname, flag, "nonblack=%.2f changed=%.3f colours=%d" % (nb, ch, cols)])
            rows.append({"name": "%s %s" % (cname, item), "flag": flag, "frame2": f2, "frame1": f1})
            print("%-8s %-8s %-6s nonblack=%.2f changed=%.3f colours=%d" % (tag, cname, flag, nb, ch, cols),
                  flush=True)
        if rows:
            sheet(rows, os.path.join(outdir, "sheet_d%s_%s.png" % (disk, cname)),
                  "intro disk #%s %s (frame after keys)" % (disk, cname))
            sheet([dict(r, frame2=r["frame1"]) for r in rows],
                  os.path.join(outdir, "sheet_d%s_%s_title.png" % (disk, cname)),
                  "intro disk #%s %s (first frame)" % (disk, cname))


if __name__ == "__main__":
    main()
