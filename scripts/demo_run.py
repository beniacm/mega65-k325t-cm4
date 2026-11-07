#!/usr/bin/env python3
"""demo_run.py -- run every program on MEGA65 D81 images and record what the screen shows.

For each PRG on each disk image (read from the PC's copy of the card's files): reset the core,
leave the menu system ("/"), MOUNT the image, then
  load address $2001 (BASIC 65 / MEGA65):  DLOAD "name" : RUN
  load address $0801 (C64):               GO64, Y, LOAD"name",8, RUN
and capture the HDMI picture after --t1 seconds (title screen), then press SPACE, fire on both
joysticks and RETURN (past title screens, as a player would) and capture again at --t2 seconds.  Files with other load addresses,
SEQ/USR files and separator/data entries are listed as skipped.  Per program: non-black share,
change between the two captures (animation), saved frames; per disk a labelled contact sheet.
Results go to captures/demos/<run>/ (results.csv, sheet_<disk>.png).

    scripts/demo_run.py [--disks INTRO1.D81,INTRO2.D81] [--only NAME] [--t1 15] [--t2 25]
                        [--run NAME] [--images DIR]
"""
import argparse
import csv
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTL = os.path.join(ROOT, "scripts/m65ctl.py")
IMAGES = os.path.join(ROOT, "ext/filehost/intros/sdcard-files")
DEMO_DISKS = ["INTRO1.D81", "INTRO2.D81", "INTRO3.D81", "INTRO4.D81", "DEMOCOMP.D81",
              "XMAS2024.D81", "FIREWORK.D81", "SPIRALS.D81", "FRACTALS.D81"]
SKIP_NAMES = {"AUTOBOOT.C65", "MENU", "MENU.BIN", "SCREEN.BIN", "CLR.BIN", "BORDER", "INTRODATA"}


def d81_files(path):
    """[(name, type, load_address)] of a D81 image"""
    d = open(path, "rb").read()
    sec = lambda t, s: d[((t - 1) * 40 + s) * 256:((t - 1) * 40 + s) * 256 + 256]
    out, t, s, seen = [], 40, 3, set()
    while t and (t, s) not in seen and t <= 80:
        seen.add((t, s))
        b = sec(t, s)
        for i in range(8):
            e = b[i * 32:i * 32 + 32]
            if e[2] & 7:
                name = e[5:21].split(b"\xa0")[0].decode("latin-1")
                ft, fs = e[3], e[4]
                load = None
                if 1 <= ft <= 80:
                    first = sec(ft, fs)
                    load = first[2] | first[3] << 8
                out.append((name, e[2] & 7, load))
        t, s = b[0], b[1]
    return out


def ctl(*args):
    subprocess.run([sys.executable, CTL] + list(args), capture_output=True, timeout=60)


def typ(text, settle=0.5):
    ctl("type", text)
    time.sleep(len(text) * 0.1 + settle)


def capture(out):
    r = subprocess.run([os.path.join(ROOT, "scripts/capture.sh"), "-n", "2"], capture_output=True, text=True)
    path = [l.split("file:")[1].strip() for l in r.stdout.splitlines() if "file:" in l]
    if not path:
        return None
    os.replace(path[-1], out)
    return out


def metrics(a, b):
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    sm = ia.resize((180, 144))
    px = list(sm.getdata())
    nonblack = sum(1 for p in px if max(p) > 24) / len(px)
    diff = ImageChops.difference(ia.resize((180, 144)), ib.resize((180, 144)))
    changed = sum(1 for p in diff.getdata() if max(p) > 24) / len(px)
    colours = len(set((p[0] >> 4, p[1] >> 4, p[2] >> 4) for p in px))
    return nonblack, changed, colours


def sheet(rows, out, title):
    from PIL import Image, ImageDraw
    tw, th, cols = 240, 192, 6
    n = len(rows)
    img = Image.new("RGB", (cols * tw, ((n + cols - 1) // cols) * (th + 14) + 16), (40, 40, 40))
    d = ImageDraw.Draw(img)
    d.text((4, 2), title, fill=(255, 255, 0))
    for i, r in enumerate(rows):
        x, y = (i % cols) * tw, 16 + (i // cols) * (th + 14)
        if r.get("frame2") and os.path.exists(r["frame2"]):
            img.paste(Image.open(r["frame2"]).convert("RGB").resize((tw - 4, th)), (x + 2, y))
        d.text((x + 2, y + th), "%s %s" % (r["name"][:22], r["flag"]), fill=(255, 255, 255))
    img.save(out)


def run_one(disk, name, load, outdir, t1, t2):
    ctl("reset")
    time.sleep(9)                                   # HYPPO + menu system
    typ("/", 3)                                     # leave the menu: BASIC 65
    typ('MOUNT "%s"\\n' % disk, 3)
    if load == 0x0801:
        typ("GO64\\n", 1.5)
        typ("Y\\n", 4)
        typ('LOAD"%s",8\\n' % name, 8)
    else:
        typ('DLOAD "%s"\\n' % name, 8)
    typ("RUN\\n", 0)
    t0 = time.time()
    base = os.path.join(outdir, "%s_%s" % (disk.split(".")[0], "".join(c if c.isalnum() else "_" for c in name)))
    time.sleep(max(0, t1 - (time.time() - t0)))
    f1 = capture(base + "_a.png")
    ctl("key", "SPACE")                              # past the title screen
    time.sleep(1)
    ctl("joy", "0x10", "0x10")
    time.sleep(0.3)
    ctl("joy", "0", "0")
    time.sleep(1)
    ctl("key", "RETURN")
    time.sleep(max(0, t2 - (time.time() - t0)))
    f2 = capture(base + "_b.png")
    return f1, f2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disks", default=",".join(DEMO_DISKS))
    ap.add_argument("--only")
    ap.add_argument("--t1", type=float, default=15)
    ap.add_argument("--t2", type=float, default=25)
    ap.add_argument("--run", default=time.strftime("run_%Y%m%d_%H%M"))
    ap.add_argument("--images", default=IMAGES)
    a = ap.parse_args()
    outdir = os.path.join(ROOT, "captures/demos", a.run)
    os.makedirs(outdir, exist_ok=True)
    res = open(os.path.join(outdir, "results.csv"), "a", newline="")
    w = csv.writer(res)
    for disk in a.disks.split(","):
        rows = []
        for name, ftype, load in d81_files(os.path.join(a.images, disk)):
            if a.only and name != a.only:
                continue
            printable = all(32 <= ord(c) < 127 for c in name) and any(c.isalnum() for c in name)
            if ftype != 2 or name in SKIP_NAMES or not printable or load not in (0x0801, 0x2001):
                w.writerow([disk, name, "skip", "type %d load %s" % (ftype, hex(load) if load else "-")])
                continue
            f1, f2 = run_one(disk, name, load, outdir, a.t1, a.t2)
            if not (f1 and f2):
                w.writerow([disk, name, "nocapture", ""])
                continue
            nb, ch, cols = metrics(f1, f2)
            flag = "BLACK" if nb < 0.02 else ("static" if ch < 0.002 else "anim")
            w.writerow([disk, name, flag, "%s nonblack=%.2f changed=%.3f colours=%d load=%04X"
                        % (os.path.basename(f2), nb, ch, cols, load)])
            res.flush()
            rows.append({"name": name, "flag": flag, "frame2": f2})
            print("%-12s %-18s %-6s nonblack=%.2f changed=%.3f" % (disk, name, flag, nb, ch), flush=True)
        if rows:
            sheet(rows, os.path.join(outdir, "sheet_%s.png" % disk.split(".")[0]), disk)
    res.close()


if __name__ == "__main__":
    main()
