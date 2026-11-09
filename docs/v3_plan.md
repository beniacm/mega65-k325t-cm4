# K325T list mode v3 — plan (revision 2, approved 2026-11-09)

Draft 2026-11-09. Goal, in the user's words: find work for the K325T silicon that supports BASIC,
in the spirit of the MEGA65, by extending the RRB concept. Everything renders through the existing
raster buffer; no new pipeline, no framebuffer. Revision 2 moves the per-object motion and the
pixel-level collision test from hardware into B65 machine code, because B65 has an inline
assembler and built-in statements can be machine code too.

## The split

Rule: hardware does what needs the VIC's bandwidth or must happen atomically per frame; software
(B65 statements written in 45GS02 assembler, or the user's own `CALL`ed routines) does per-object
logic, which the CPU handles with room to spare.

CPU budget check (40.5 MHz, 810 k cycles per 50 Hz frame): a machine-code integrator costs ~40-60
cycles per object (cartesian) or ~80 (polar with a sine table), so **1023 objects take 6-10 % of a
frame** — in software, inside B65's frame interrupt, invisible to the BASIC program. An all-pairs
box collision for 1023 objects is 523 k pair tests: impossible in software, trivial in hardware.
That is the line.

| | hardware | software (B65 built-ins in asm) | user asm (`CALL`) |
|---|---|---|---|
| rendering: overlap, 2 KB rows, 4 px/cycle, row buckets, snapshot/HOLD, bit 3 | X | | |
| 1023-object table, 10-bit numbers | X | | |
| collision level 1: box / circle, group + collides-with masks, event table, IRQ | X | | |
| per-object motion: cartesian, polar, orbit, harmonic; bounds wrap/bounce/kill; lifetimes; events | | X (frame IRQ) | custom motions |
| collision level 2: pixel overlap of hardware-flagged pairs | | X (`HITPIX(a,b)`) | |
| game logic | BASIC | | |

Why the motion is better in software: modifiable on the fly by writing a variable (a double jump
is `OBJVEL n,,-9`), new motion types are an afternoon of assembler instead of an FPGA step, no
block RAM, no DSP timing risk, debuggable in Xemu, and the same routine works for BASIC 65 as a
`SYS` library. What hardware motion would have added: zero CPU cost (vs ~8 % at 1023 objects) and
the statement without loading anything on BASIC 65. Not worth a hardware step now; it can come
later if a demo is CPU-bound.

Why pixel collision is software: the hardware box pass leaves a handful of candidate pairs per
frame (player vs bullets, player vs platforms); ~200 cycles each in machine code. The hardware
version needed glyph fetches in idle VIC port cycles and a budget mechanism — the riskiest part
of the previous draft, for pairs we can count on one hand.

## Hardware steps

| # | content | BRAM | demo / board case |
|---|---|---|---|
| 5 (running) | overlapped expansion, 2 KB row buffer, `+15` collides-with mask, feature register `$FFD71FD` | 0 | swarm (255), cases L, I2 |
| 6 | **v3 table**: 32-byte entries, no v2 mode (bytes 0..15 = the v2 fields incl. +15; 16..31 = the software motion block, never read by the hardware), count 10 bits, snapshot 1024 x 128 bits, **per-row buckets** built during the snapshot (row scan and collision pass visit only the objects on the row), collision budget register + "truncated" flag, results for 1023 in page `$FFD7200-$FFD72FF` (`$FFD71A0` window stays for 0..255), circle test with a radius byte | 4 (snapshot) + 1 (buckets) | swarm 1022 bullets, cases M |
| 7 | **4 pixels per cycle**: raster buffer halves as 512 x 72 with per-pixel write enables, two-cycle FCM dispatch, expander dual-issue; register bit, off = today | 0-2 | parallax 6 layers, 255 objects on one line without DBLRR, all cases pixel-exact on/off |

BRAM total ~7 of the 23 free tiles. Steps 6 and 7 are independent; 6 first (the demos need it).

## Software steps (B65)

| # | content |
|---|---|
| S1 (running) | pack the resident code (room for the statements below) |
| S2 | **object statements**: `OBJECT n,x,y,tokens[,colour,stride,height,mask,group,with]`, `OBJVEL n,vx,vy[,ax,ay]`, `OBJMOVE n,speed,heading[,turn[,dspeed]]`, `OBJORBIT n,parent,r,omega`, `OBJSPRING n,anchor,k`, `OBJLIFE n,frames`, `OBJBOUNDS n,set` + `OBJLIMITS set,x0,y0,x1,y1,action`, `OBJX(n)`, `OBJY(n)`, `OBJVX(n)`, `OBJVY(n)`, `OBJEVENT(n)`, `ON OBJEVENT PROC`, `COLLIDE(n)`, `HITPIX(a,b)`. State per object in a B65-owned chip-RAM block (position 10.8, velocity or speed/heading, angle, lifetime, mode, flags), the table written in place each frame. |
| S3 | **frame integrator** in B65's vsync interrupt: for every object with a mode, the update (cartesian / polar via a 1024-entry sine table / orbit / harmonic), bounds, lifetime, events; writes X/Y into the object table inside HOLD; 1023 objects measured, target < 2 ms per frame. `OBJUPDATE` as the manual alternative when the program wants control of the moment. |
| S4 | `HITPIX(a,b)`: pixel overlap of two objects' glyphs over the box intersection (FCM index 0 / mono bit 0 = transparent). |
| S5 | BASIC 65: `OBJ65` machine-code library (the same routines, `SYS` entry points) + POKE recipes; docs. |
| demos | swarm-hw (1022 curving bullets, BASIC only spawns), jumper (gravity, double jump, bounce, pixel hits on platforms), orbits (moons, pendulums via REL), stress (1023 on one row with step 7) |

The user's own assembler routines: documented entry points into the object state block, so a
program can `CALL` its custom per-object logic from the same interrupt slot.

## Order and gates

5 (hardware, running) and S1 (software, running) → 6 and S2/S3 in parallel → swarm-hw on the
board (gate: 1022 bullets, hardware motion in the IRQ, 50 fps, hits correct) → S4 + jumper → 7 →
S5, docs, B65 release v0.2. Each hardware step: GHDL model tests, board cases, commits after the
board run, then pushed. Each software step: Xemu check scripts, smoke, board run.

## Decisions taken (user, 2026-11-09)

1. The split as above: motion and pixel collision in B65 machine code, rendering and level-1
   collision in hardware.
2. **32-byte entries, no v2 mode** ("no need to support v2"): flag day — the step-6 bitstream,
   the test suite (`v2obj()` -> 32 bytes) and all demos change together. The hardware reads bytes
   0..15 of each entry (stride 32), so the snapshot cost stays 16 bytes per object.
3. All four motion modes (cartesian, polar, orbit, harmonic) in S3.
4. Pixel collision: transparent = FCM colour index 0 (mono: bit 0).
5. Register page: **`$FFD7200-$FFD72FF`** (recommendation; to be confirmed in the iomapper (done: free on this target) the
   iomapper has no I2C device there on the K325T target, else `$FFD7300`). Proposed layout:
   `$00-$7F` collision bits for objects 0..1023, `$80` status/control (bit 0 latched hit, bit 1 IRQ
   enable, bit 2 budget truncated, bit 7 pending), `$82-$83` index (10 bits), `$84-$85` first
   partner (`$3FF` = none), `$86-$87` collided count, `$88-$89` pair budget per frame, `$8A-$8B`
   object count (10 bits; `$FFD71FB` keeps the low byte for old programs), `$8C` feature level
   (mirrors `$FFD71FD`), rest reserved. The `$FFD71A0-$FFD71C3` window stays for objects 0..255.
6. Order: hardware 6 before 7; software S2/S3 before S4. Circle test: shape flag = entry byte +5
   bit 7, radius = width/2 about the box centre (no new byte).

### Software motion block (bytes 16..31 of an entry) — FINAL (B65 1afe815, USER.md "Objects")

Table placement by B65: K325T chip RAM `$E0000` (entry n at base + 32 n; event bits at base +
`$8000`); elsewhere attic `$8200000` (logic only). `OBJECT` writes base and count to `$FFD71F8-FB`
and, from feature level 3 (`$FFD71FD`), the count also to `$FFD728A-B`. Units in the statements:
pixels per frame and degrees (0 = right, 90 = down); stored as below.

| byte | field |
|---|---|
| +16 | mode bits 3..0: 0 static, 1 cartesian, 2 polar, 3 orbit; bit 4 harmonic (with mode 1); bits 6..5 limit set; bit 7 pixel hint |
| +17 | exit action X (bits 1..0) and Y (bits 3..2): 0 none, 1 wrap, 2 bounce, 3 kill; bit 4 kill at lifetime 0; bit 5 half restitution |
| +18 | VX / polar speed / orbit radius (8.8) |
| +20 | VY / polar heading / orbit angle (1/65536 turn) |
| +22 | AX (8.8) / polar speed change per frame / harmonic k (8.8) |
| +24 | AY / polar and orbit turn rate (1/65536 turn per frame) |
| +26 | lifetime in frames (0 = unlimited) |
| +28 | orbit parent / harmonic anchor (object number) |
| +30 | X fraction |
| +31 | Y fraction |

Statements: `OBJECT`, `OBJVEL`, `OBJMOVE`, `OBJORBIT`, `OBJSPRING`, `OBJLIFE`, `OBJLIMITS`,
`OBJBOUNDS`, `OBJKILL`, `OBJUPDATE [ON]`, `ON OBJEVENT PROCname/OFF`; functions `OBJX(`, `OBJY(`,
`OBJVX(`, `OBJVY(`, `OBJEVENT(` (`OBJEVENT(-1)` = lowest object with an event or -1). Each statement
writes its entry under SEI..CLI. The harness `docs/rrb_list_mode.md` "v3 table" section (step 6)
copies this table.


## Added after the upstream review (2026-11-09, user: "get it done the best you can")

- **Golden regression (board):** fix `scripts/demo_run.py` (a mounted intro disk autoboots
  after a reset; mount a disk without AUTOBOOT first), capture the intro disks and BASIC screens
  with the pre-extension port bitstream, then compare every later bitstream with XCTL = 0 against
  that set pixel-for-pixel. Required before the step-6 flag day and after every hardware step.
  Result 2026-11-09: `golden_step5` (step-5 bitstream, 169 captures: INTRO1 + INTRO2 + BASIC
  screens) equals `golden_xctl` (07-11 bitstream) on every static screen; the only differences are
  the ROM clock, blinking cursors, running counters, random boards and animation phase, each one
  checked by eye and now masked or listed in `scripts/golden_compare.py`. `golden_step5` is the
  reference for step 6 (its PALEDIT capture is the valid one).
- **VIC refactor (between steps 6 and 7):** the list-mode front end now inside `viciv.vhdl`
  (ListWait/ListRun, fast token decoder states, lead, bit-3 valid bits, counters) moves into a
  sub-entity with a narrow interface; `viciv.vhdl` keeps hooks only. Gate: all testbenches and board
  cases pixel-exact, resources and timing unchanged. Then a quarterly rebase onto upstream
  `development`.
- **Snapshot depth generic** (512/1024) so the 4 block-RAM tiles can be reclaimed if needed.
- **Documentation of the CPU regime:** hundreds of individually controlled actors, a thousand
  pattern-driven objects; one-row capacity ~500 one-glyph parts with graceful truncation.

### S3 implementation notes (B65 a4190e2, model-exact against tests/objmodel.py)

- Runs in the frame IRQ (or per `OBJUPDATE`): cartesian (+ acceleration or spring pull), polar,
  orbit, then the limit sets (none / wrap / bounce with half restitution / kill), then the lifetime;
  a kill sets the event bit and makes `ON OBJEVENT` pending.
- The table is mapped 8 KB at a time at `$E000` (like the overlays) with interrupts off (the
  vectors at `$FFFA` would be object bytes); base page `$1F` (`$1FEC-$1FFF`) is its zero page; the
  IRQ handler saves B and runs with B = 0; HOLD around the writes on the K325T; without the K325T
  the table is chip RAM `$58000` and the event bits are in bank 0.
- Sine table: 1024 quarter-wave 16-bit values at `$4E800`, built at start-up by an integer
  recurrence (reproduced in Python). Boot frame count at `$201B`.
- Cost at a4190e2 (Xemu, 1023 objects): static 1.6 ms, cartesian 10.4, +acceleration 12.8, +limits
  18, polar 29, orbit 32, spring 32 (~400 cycles per cartesian object). Targets after optimisation:
  cartesian <= 150 cycles, FAST integer sub-mode <= 80, polar <= 250, orbit/spring <= 300.
- e7ba308: FAST mode 4 (whole-pixel steps, chosen automatically by OBJVEL/OBJBOUNDS when
  fractions, acceleration, spring and bounce are absent); acceleration only with +17 bit 6; limits
  and lifetime skipped when +17 bits 4..0 are 0. Cycles per object (Xemu): static 47, FAST 222,
  FAST + wrap 475, cartesian 317, + acceleration 412, + limits 665, polar 1108, orbit 1283, spring
  1267. Next optimisation round (after the demos): specialised Y-indexed loops per mode, quick
  in-range test before the limits routine, sine table in a 2 KB bank-0 buffer; goals polar <= 300,
  FAST <= 100.
- f7fd97d: all v2-object examples converted to 32-byte entries (bounce + twin, swarm, rrblist_b65v2,
  parallax + twin, walkers; token lists after the table moved $5F880 -> $5F900) -- they need the
  step-6 core, do not run them on earlier bitstreams. WAIT now waits on the `$D7FA` frame counter
  (polling `$D011` bit 7 missed frames when the IRQ ran longer than the bit's window). Demos:
  swarm2 (1000 bullets, 8 spawned per frame, ~660 FAST + ~220 polar live, 50 fps in Xemu) and jumper
  (gravity, double jump, bounce; platforms via the box collisions until HITPIX). Xemu transcript area
  now $4A000-$4E7FF.
- Polar redesign (e0ecada/c655ea8): OBJMOVE without speed change -> mode 5, VX/VY computed once
  then cartesian; turn rounded to 1/2^k rad/frame (k 1..7, +28 = k with bit 7 = negative) and
  rotated per frame by shifts (VX -= VY>>k; VY += VX>>k); exact polar stays for other rates.
  Cycles/object with wrap limits: straight polar 570, curve k=5 931, exact polar 982, orbit 1140.
  Sine table 2 KB in bank 0 after the interpreter ($9B00 at that build), LOMEM $A300.
- S4 HITPIX (e3076ff, overlay 2, src/hitpix.a): TRUE when a non-transparent pixel of a lies on one
  of b inside the box intersection; FCM index 0 / mono bit 0 transparent; FLIPX/FLIPY honoured;
  not handled: REL, NCM, width trims. Model tests/hitmodel.py + hitcheck.py (seeds 1..12). Cost
  0.12-1.8 ms per call. Fixes on the way: OBJECT height (+5 is the row count, 0 = 1), math-unit
  clash between the frame IRQ's polar/orbit/spring objects and the program's *, DIV, MOD, FP
  multiply (OBJSTEP saves/restores $D770-$D777 and waits for busy). Build split: ACME's 64 KB
  limit -> five assembler runs, one per overlay. Edge rule: the pixel rule wins, a player
  without pixel support under its feet falls off the edge.
- B65 d6b4a8b: HITPIX(a,b,dx,dy) (a moved by dx,dy); jumper stands only while HITPIX(0,P%,0,1)
  finds platform pixels, falls off edges; speed: PFB remembers its DEF (PROCb 100 -> 86 us, FNd
  116), compiled MOD/DIV with power-of-two fast paths, unwind in one walk; every example starts
  with `1 REM Core: ...` (any core / K325T any level / K325T level 3); docs/RELEASE_v0.2.md draft.
  List mode v1 (bit 5, LIST token) survives step 6 unchanged .

### Step 6 implementation notes (interim, expander/bucket simulation passing)

- Hardware reads bytes 0..15 of each 32-byte entry only (checked in the testbench); count 10 bits;
  generic NOBJ (512/1024) = snapshot depth (NOBJ x 128 bits, true dual-port, 4 RAMB36 at 1024);
  REL resolved during the copy (the snapshot holds absolute X/Y).
- Buckets: rows 0..63, 2048 entries in one RAMB36, counting sort during the copy (counts, prefix
  sums, fill in table order); more than 2048 entries -> extra ones dropped, status bit 3 (bk_full).
  Row scan reads only the row's bucket (one 128-bit word per object).
- Collision pass: pairs sharing a bucket, tested in the first row both are in; empty rows and the
  tail of a row without starting objects skipped; 6-stage pipeline, one pair per cycle, 3 DSPs for
  the circle test (+5 bit 7, radius W/2 about the box centre; keep radius <= 4H); pair budget +
  truncated flag; first partner = lowest-numbered (min-tracked).
- Only rows 0..63 are drawn and collision-checked.
- GHDL: 1023 objects copy + buckets 21,280 cycles (8.2 lines); 1023 random objects over 50 rows
  pass 52,694 cycles (20 lines); 255 objects straddling two rows 35,753; expansion unchanged at 17
  cycles per part. Feature level mirror at `$FFD728C`.

