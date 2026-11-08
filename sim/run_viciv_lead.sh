#!/usr/bin/env bash
# run_viciv_lead.sh -- the real VIC-IV (viciv.vhdl, report-stripped copy) in GHDL: the K325T
# RRB list mode lead (docs/rrb_list_mode.md, "First raster line budget").  12 scenarios of
# tb_viciv_lead.vhdl in parallel (~7 min), then checks:
#   s4 == s5   plain rows: XCTL bit 5 (lead) changes no pixel
#   s2 == s3   reference GOTOX rows with the lead == v2 objects (6 objects, X 0/10/600, V400)
#   g2 == g3   the same with 30 object parts in one row
#   no late pixels (counter 23) with the lead (s2 s3 s5 g2 g3); late ones without (s1 g1)
#   every painting with the lead ends before the next line start (counter 25 < 2590)
#   DBLRR (V400 timing, parallax65.bas rows, two-line lead): no late pixels, q8 == q9, and
#   q1 (lead off) differs from q8 only on the first line of each pair (the one racing the beam)
# (V200: run the scenarios by hand with -gV400=0.)  Expects "TB_VICIV_LEAD_PASS".  Work dir: viciv_work (scratch, not committed).
set -uo pipefail
cd "$(dirname "$0")"
V=../mega65-core/src/vhdl
W=viciv_work
rm -rf $W && mkdir -p $W
G="ghdl -a --std=93c -fexplicit -frelaxed -fsynopsys --workdir=$W"
for f in debugtools.vhdl cputypes.vhdl victypes.vhdl ghdl_ram18x2k.vhdl ghdl_screen_ram_buffer.vhdl \
         colourram.vhdl colourram_k325t.vhdl ghdl_ram32x1024.vhdl charrom.vhdl ghdl_alpha_blend.vhdl \
         pal_simulation.vhdl sprite.vhdl bitplane.vhdl ghdl_ram8x4096.vhdl bitplanes.vhdl \
         vicii_sprites.vhdl rrb_list_expander.vhdl viciv.vhdl; do
  python3 strip_reports.py $V/$f $W/$f && $G $W/$f 2>&1 | grep -i "error" && exit 1
done
$G tb_viciv_lead.vhdl 2>&1 | grep -i error && exit 1
ghdl -e --std=93c -fexplicit -frelaxed -fsynopsys --workdir=$W tb_viciv_lead 2>/dev/null
run() {
  n=$1; shift
  ghdl -r --std=93c -fexplicit -frelaxed -fsynopsys --workdir=$W tb_viciv_lead "$@" -gOUTF=$W/$n.txt \
    --stop-time=80ms --ieee-asserts=disable 2>/dev/null | grep "^L " > $W/$n.log
}
run s1 -gSC=1 & run s2 -gSC=2 & run s3 -gSC=3 & run s4 -gSC=4 & run s5 -gSC=5 &
run g1 -gSC=1 -gOBJS=1 & run g2 -gSC=2 -gOBJS=1 & run g3 -gSC=3 -gOBJS=1 &
# DBLRR on its schedule (V400 timing, 25 rows), parallax65.bas rows: plain, lead only, v2 birds,
# v2 with an empty table
D="-gOBJS=2 -gV400=2 -gD051=192"
run q1 -gSC=6 $D & run q8 -gSC=8 $D & run q3 -gSC=7 $D & run q9 -gSC=9 $D &
wait
python3 - $W <<'PY'
import sys
w = sys.argv[1]
def frame(n):
    return {int(l.split()[0]): l.split()[1] for l in open("%s/%s.txt" % (w, n))}
def lines(n):   # per raster line: late, paint, obj, exp (the first read holds the setup frames)
    return [list(map(int, l.split()[1::2])) for l in open("%s/%s.log" % (w, n))][1:]
ok = True
for a, b in (("s4", "s5"), ("s2", "s3"), ("g2", "g3"), ("q8", "q9")):
    fa, fb = frame(a), frame(b)
    n = sum(1 for y in fa if fa[y] != fb.get(y))
    print("%s vs %s: %d lines differ (frame %d lines)" % (a, b, n, len(fa)))
    ok = ok and n == 0 and len(fa) == 576
# DBLRR: without the lead the first line of each pair races the beam; the second lines must be
# identical with and without the two-line lead
f1, f8 = frame("q1"), frame("q8")
par = {y % 2 for y in f8 if f1[y] != f8[y]}
print("q1 vs q8 (DBLRR, lead off / on): lines differ only in one parity of the pair: %s" % par)
ok = ok and len(par) <= 1
for n in ("s1", "s2", "s3", "s4", "s5", "g1", "g2", "g3", "q1", "q8", "q3", "q9"):
    L = lines(n)
    late = sum(1 for l in L if l[1] > 0)
    paint = max(l[2] for l in L)
    print("%s: %d lines with late pixels, longest painting %d cycles, latest first object %d, "
          "longest expansion %d" % (n, late, paint, max(l[3] for l in L), max(l[4] for l in L)))
    if n in ("s2", "s3", "s5", "g2", "g3"):
        ok = ok and late == 0 and paint < 2590
    if n in ("q8", "q3", "q9"):                 # DBLRR: two lines per painting
        ok = ok and late == 0 and paint < 5180
    if n in ("s1", "g1"):
        ok = ok and late > 0
print("TB_VICIV_LEAD_%s" % ("PASS" if ok else "FAIL"))
PY
