#!/usr/bin/env bash
# run_viciv_lead.sh -- the real VIC-IV (viciv.vhdl, report-stripped copy) in GHDL: the K325T
# RRB list mode lead (docs/rrb_list_mode.md, "First raster line budget").  49 scenarios of
# tb_viciv_lead.vhdl in parallel (~14 min), then checks:
#   s4 == s5   plain rows: XCTL bit 5 (lead) changes no pixel
#   s2 == s3   reference GOTOX rows with the lead == v2 objects (6 objects, X 0/10/600, V400)
#   g2 == g3   the same with 30 object parts in one row
#   no late pixels (counter 23) with the lead (s2 s3 s5 g2 g3); late ones without (s1 g1)
#   every painting with the lead ends before the next line start (counter 25 < 2590)
#   DBLRR (V400 timing, parallax65.bas rows, two-line lead): no late pixels, q8 == q9, and
#   q1 (lead off) differs from q8 only on the first line of each pair (the one racing the beam)
#   fast token path (XCTL bit 7): pixel-identical with the old path (three full-colour layers
#   with FLIPX/FLIPY/alpha/NCM/width trim/plain glyphs mixed in, parallax rows, v2 birds, the V400
#   objects, 30 parts, microbenchmark rows), and the V200 one-line picture of the three layers
#   == the old path's on DBLRR's schedule (which shows each painting one line later)
#   XCTL bit 3: sparse rows after dense rows == over an opaque base of spaces (also with the
#   lead, bit 7, DBLRR); without it the stale pixels show
#   bullet hell (level 2: painting overlaps the expansion, 2 KB list rows, XCTL bit 3): 64
#   one-glyph objects on one row (V400, bit 7), 128 and 231 on DBLRR's schedule (bit 7; 64 also
#   with bit 7 off) == the same objects as plain GOTOX parts; 128 in one line (V400, bit 7:
#   painting < 2590; its plain reference loses badlines); 255 (timing only: the plain row would
#   not fit 1 KB) < 5180; no late pixels, no port collisions; the 231 reference also after a v2
#   phase on the same rows (history: a $FFFF after the row's last token is not a short row)
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
# row_advance / short_line_length are "integer range 0 to 512" = 10-bit registers in hardware
# (LINESTEP up to 1023 works on the board, e.g. rrb_list_test.py case H at 640); GHDL checks
# the range: widen it to the register's 10 bits in the sim copy (SC 20 rows are 640 / 1020 apart)
sed -i -E 's/((signal (n_)?short_line_length|signal row_advance) *: *integer range 0 to )512/\11023/' $W/viciv.vhdl
grep -c "integer range 0 to 1023" $W/viciv.vhdl | grep -qx 3 || { echo "row_advance patch failed"; exit 1; }
$G $W/viciv.vhdl 2>&1 | grep -i "error" && exit 1
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
# fast token path (XCTL bit 7, FT=1) against the old path: three full-colour layers with
# attributes (DBLRR schedule: the old path completes; and V200 with the one-line lead), the
# parallax rows, v2 birds, the V400 objects, 30 parts, and the per-token microbenchmarks
run t0 -gSC=11 $D -gFT=0 & run t1 -gSC=11 $D -gFT=1 & run t2 -gSC=11 -gOBJS=2 -gV400=0 -gFT=1 &
run t3 -gSC=8 -gOBJS=2 -gV400=0 -gFT=0 & run t4 -gSC=8 -gOBJS=2 -gV400=0 -gFT=1 &
run t5 -gSC=7 $D -gFT=0 & run t6 -gSC=7 $D -gFT=1 & run t7 -gSC=3 -gFT=1 & run t9 -gSC=3 -gOBJS=1 -gFT=1 &
for m in 12 13 14 15; do run m${m}a -gSC=$m $D -gFT=0 & run m${m}b -gSC=$m $D -gFT=1 & done
wait
# XCTL bit 3 (unpainted pixels show as background): sparse rows after dense rows == the same
# rows over an opaque base of spaces; also with the lead, the fast token path, DBLRR + lead
run u0 -gSC=19 -gV400=0 & run u1 -gSC=18 -gV400=0 -gCU=1 & run u2 -gSC=18 -gV400=0 &
run u3 -gSC=18 -gV400=0 -gCU=1 -gRL=1 & run u4 -gSC=19 -gV400=0 -gRL=1 &
run u5 -gSC=18 -gV400=0 -gCU=1 -gFT=1 & run u6 -gSC=18 $D -gCU=1 -gRL=1 & run u7 -gSC=19 $D -gRL=1 &
# bullet hell: SC 17 (v2 pile in row 10) against SC 20 (the same as plain parts, with the lead);
# XCTL bit 3 (CU 1): the rows are sparse and the reference's padding GOTOX would move the line end
run p64 -gSC=17 -gOBJS=4 -gCU=1 -gFT=1 & run r64 -gSC=20 -gOBJS=4 -gCU=1 -gFT=1 &
run p128 -gSC=17 -gOBJS=5 -gCU=1 -gFT=1 & run p128d -gSC=17 $D -gOBJS=5 -gCU=1 -gFT=1 &
run r128d -gSC=20 $D -gOBJS=5 -gCU=1 -gFT=1 &
run p231 -gSC=17 $D -gOBJS=7 -gCU=1 -gFT=1 & run r231 -gSC=20 $D -gOBJS=7 -gCU=1 -gFT=1 &
run p64d -gSC=17 $D -gOBJS=4 -gCU=1 & run r64d -gSC=20 $D -gOBJS=4 -gCU=1 & run p255 -gSC=17 $D -gOBJS=6 -gCU=1 -gFT=1 &
# the reference after v2 ran on its rows (PRE 1, as rrb_list_test.py case L: v2 leaves its
# expansion's $FFFF right after the 510-token rows, which must not shorten the row advance)
run r231h -gSC=20 $D -gOBJS=7 -gCU=1 -gFT=1 -gPRE=1 &
wait
python3 - $W <<'PY'
import sys
w = sys.argv[1]
def frame(n):
    return {int(l.split()[0]): l.split()[1] for l in open("%s/%s.txt" % (w, n))}
def lines(n):   # per raster line: late, paint, obj, exp (the first read holds the setup frames)
    return [list(map(int, l.split()[1::2])) for l in open("%s/%s.log" % (w, n))][1:]
ok = True
for a, b in (("s4", "s5"), ("s2", "s3"), ("g2", "g3"), ("q8", "q9"),
             ("t0", "t1"), ("t3", "t4"), ("t5", "t6"), ("s3", "t7"), ("g3", "t9"),
             ("m12a", "m12b"), ("m13a", "m13b"), ("m14a", "m14b"), ("m15a", "m15b"),
             ("u0", "u1"), ("u4", "u3"), ("u0", "u5"), ("u7", "u6"),
             ("p64", "r64"), ("p128d", "r128d"), ("p231", "r231"), ("p64d", "r64d"),
             ("p231", "r231h")):
    fa, fb = frame(a), frame(b)
    n = sum(1 for y in fa if fa[y] != fb.get(y))
    print("%s vs %s: %d lines differ (frame %d lines)" % (a, b, n, len(fa)))
    ok = ok and n == 0 and len(fa) == 576
fu0, fu2 = frame("u0"), frame("u2")
n = sum(1 for y in fu0 if fu0[y] != fu2.get(y))
print("u0 vs u2 (sparse rows without XCTL bit 3: stale pixels): %d lines differ (expected > 0)" % n)
ok = ok and n > 0
# V200 (one line per painting) against DBLRR's schedule: the same glyph rows, but DBLRR shows
# each painting on lines 2k, 2k+1 and V200 on 2k-1, 2k: t0 line y == t2 line y - (y mod 2)
# (the first and last text lines are the same lines in both)
f0, f2 = frame("t0"), frame("t2")
n = sum(1 for y in f0 if f0[y] not in (f2.get(y - (y % 2)), f2.get(y)))   # (+ text start/end)
print("t0 vs t2 (DBLRR old path / V200 fast path, phase-aligned): %d lines differ" % n)
ok = ok and n == 0
# DBLRR: without the lead the first line of each pair races the beam; the second lines must be
# identical with and without the two-line lead
f1, f8 = frame("q1"), frame("q8")
par = {y % 2 for y in f8 if f1[y] != f8[y]}
print("q1 vs q8 (DBLRR, lead off / on): lines differ only in one parity of the pair: %s" % par)
ok = ok and len(par) <= 1
for n in ("s1", "s2", "s3", "s4", "s5", "g1", "g2", "g3", "q1", "q8", "q3", "q9", "t0", "t1", "t2",
          "t3", "t4", "t5", "t6", "t7", "t9", "m12a", "m12b", "m13a", "m13b", "m14a", "m14b",
          "m15a", "m15b", "p64", "r64", "p128", "p128d", "r128d", "p231", "r231", "p64d", "r64d",
          "p255", "r231h"):
    L = lines(n)
    late = sum(1 for l in L if l[1] > 0)
    paint = max(l[2] for l in L)
    col = sum(l[5] for l in L)
    print("%s: %d lines with late pixels, longest painting %d cycles, latest first object %d, "
          "longest expansion %d, port collisions %d" % (n, late, paint, max(l[3] for l in L),
                                                        max(l[4] for l in L), col))
    ok = ok and col == 0                         # expander read while the VIC-IV reads: never
    if n in ("s2", "s3", "s5", "g2", "g3", "t2", "t4", "t7", "t9", "p64", "r64", "p128"):
        ok = ok and late == 0 and paint < 2590
    if n in ("q8", "q3", "q9", "t0", "t1", "t5", "t6", "p128d", "r128d", "p231", "r231", "p64d", "r64d",
             "p255", "r231h"):
        # DBLRR: two lines per painting
        ok = ok and late == 0 and paint < 5180
    if n in ("s1", "g1"):
        ok = ok and late > 0
print("TB_VICIV_LEAD_%s" % ("PASS" if ok else "FAIL"))
PY
