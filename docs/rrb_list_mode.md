# RRB list mode (K325T extension, after upstream issue #747)

Status: implemented and verified on the board (2026-11-08); demos in `scripts/demos/`. Follows the text of
[MEGA65/mega65-core#747](https://github.com/MEGA65/mega65-core/issues/747) ("Make RRB even more
amazing") and its comments (relative GOTOX), plus #597 (flip per object). Our own encoding: if
upstream implements #747 differently later, we switch to theirs.

## Goal

Raster-rewrite objects ("pixies") that are moved like hardware sprites: write X, Y offset and a
draw mask into a small **list entry**; the object's character data stays where it is. Today a
moving RRB object means rewriting screen and colour RAM of every row it touches, and reserving
row space for the longest possible line.

## What the program sees

Gated by **XCTL `$FFD71F0` bit 5** (off after reset: a real MEGA65). Only 16-bit character mode
(CHR16), the mode RRB is used in.

A screen row may end its ordinary tokens with the **LIST token** (screen word `$FFFC`). The words
after it are **list entries**, 4 words (8 bytes of screen RAM) each, until an **end-of-lists** word
`$FFFF`:

Word 0 is exactly a GOTOX screen word (same bits, same meaning), plus the REL bit that GOTOX
does not use:

| word | bits | meaning |
|---|---|---|
| 0 | 9..0 | X position, as GOTOX; a signed 10-bit offset if REL is set |
|   | 10 | REL: X = the previous entry's X + this signed offset (anchor + members, #747 comment) |
|   | 11 | reserved (0) |
|   | 12 | Y offset sign, as GOTOX (1 = the glyph rows move up) |
|   | 15..13 | Y offset 0..7, as GOTOX |
| 1 | 7..0 | draw mask: pixel rows of this character row that are drawn (GOTOX ROWMASK byte) |
|   | 11..8 | screen address bits 19..16 of the object's data (1 MB chip RAM) |
|   | 12 | FLIPX: mirror every glyph of the object (#597) |
|   | 13 | FLIPY: flip every glyph of the object vertically (#597) |
|   | 14 | transparent: do not paint background pixels (GOTOX colour byte 0 bit 7) |
|   | 15 | behind: force the object into the background (GOTOX colour byte 0 bit 6) |
| 2 | 15..0 | screen address bits 15..0 of the object's data (2-byte aligned) |
| 3 | 15..0 | colour RAM offset of the object's data (2-byte aligned) |

The synthesised GOTOX gets colour byte 0 = GOTOX (bit 4) + ROWMASK enable (bit 3, if the mask is
not `$FF`) + bits 14/15 above, colour byte 1 = the draw mask.

The **object data** is an ordinary RRB token sequence (glyphs, GOTOX for multi-column objects)
with its colour RAM bytes at the given colour offset, ended by an **end-of-list** word `$FFFF`.
The same object data can be referenced by many entries (e.g. 50 identical bullets: one copy of
the data, 50 entries of 8 bytes).

Moving an object = changing word 0 (X, Y offset) and word 1 (mask) of its entry. Moving it to
another character row = moving its entry to that row's list (8 bytes), or giving it entries in
two rows with complementary draw masks, exactly as with GOTOX today.

Normal tokens before the LIST token work as before (background, HUD).

## List mode v2: global object table

Gated by **XCTL bit 6**. Instead of entries per row, one table of up to 255 objects, each with
its own X **and Y**, like a sprite table; the VIC-IV works out the rows, Y offsets and draw masks.
Moving an object = writing its X and Y (4 bytes), nothing else.

Registers (cleared by reset):

| address | meaning |
|---|---|
| `$FFD71F8-$FFD71FA` | table address in chip RAM (20 bits, little endian) |
| `$FFD71FB` | number of objects (0..255) |
| `$FFD71FC` | bit 0 HOLD: keep the last snapshot (no copy per frame) |

The address and the count are several bytes in the CPU's clock domain; the VIC-IV latches them
at the start of each copy. Write them before setting XCTL bit 6, or with HOLD set (then clear
HOLD: the next frame copies with the new values).

Object (16 bytes):

| offset | meaning |
|---|---|
| +0 | X 9..0 (as GOTOX); bit 10 REL: X = previous table object's X + signed offset |
| +2 | Y 9..0, signed: pixel rows from the top of character row 0; bit 10 REL (as X) |
| +4 | draw mask, ANDed with the computed one (`$FF` normally, `0` hides the object) |
| +5 | bits 5..0: height in character rows, 0..63 (0 = 1); bits 7..6 ignored |
| +6 | bits 3..0 screen address 19..16, 4 FLIPX, 5 FLIPY, 6 transparent, 7 behind |
| +7 | reserved (collision group, step 3) |
| +8 | screen address 15..0 of object row 0's tokens (an RRB token list ending with `$FFFF`) |
| +10 | colour RAM offset of object row 0's tokens |
| +12 | row stride: bytes from one object row's tokens to the next (screen and colour) |
| +14 | reserved |

- **Snapshot per frame**: at the top of every frame (unless HOLD is set) the VIC-IV copies the
  table into its own table RAM (LUT RAM, 512 x 64 bits, no block RAM). The count is part of the
  snapshot: every row of the frame scans the copied count, so an object beyond a new count never
  appears, and an object can never be shown in two places or in halves.
- **The copy only uses idle chip RAM cycles** of the VIC-IV (after the sprite fetches of a line,
  and the list-mode wait states); it never disturbs the row, glyph or sprite fetches (also in an
  opened top border). 255 objects (4080 bytes) take a few lines of the top border. The fetch of
  the first text row waits for the copy if it is still running; if it is not done by the next
  line, that row is painted without objects for that frame.
- **HOLD** keeps the last snapshot, its count included (no copy at all while set). After reset
  the snapshot is empty: HOLD set before the first copy shows nothing.
- A program may rewrite the table at any time. The copy reads an object's bytes within a few
  cycles, so an X/Y pair written with separate stores can still tear against it (a window of
  roughly 16 cycles per object per frame, also with B65's 4-byte `!`); HOLD makes it atomic.
- **Per character row** the VIC-IV scans the snapshot (one object per cycle, from the table RAM, no chip
  RAM time) and turns every object covering the row into one or two entries exactly as in v1:
  the top part of object row k = row - floor(Y/8) (Y offset -(Y mod 8), mask `$FF << (Y mod 8)`)
  and the bottom part of object row k-1 (offset +(8 - Y mod 8), the complementary mask).
  FLIPY reverses the object rows and swaps the offset's sign (the VIC-IV applies the offset to
  the flipped glyph row).
- Rows need **no LIST token**: the objects are appended after the row's last token (CHRCOUNT),
  or, in a row ended early by `$FFFF`, at that `$FFFF` (the objects' first GOTOX overwrites it,
  the expansion ends with its own `$FFFF`; the short row keeps its row advance). A row with a
  LIST token gets them at the token, after its own v1 entries if bit 5 is on too. The first of
  LIST / `$FFFF` in the row counts; nothing after an end of row does.
- **Full rows**: if the append point is beyond 1018 (the 1 KB buffer minus a GOTOX), the row is
  painted plainly, without objects; an expansion that reaches the buffer limit ends there with
  `$FFFF` (no object part is started that cannot fit).
- Objects later in the table are painted later (on top).
- REL lets a group follow an anchor: write the anchor's X/Y only. A hidden anchor (mask 0) is
  fine.
- Limits: the scan costs about one cycle per object per character row plus ~20 cycles per
  object part drawn, in the badline's line time (shared with the row fetch and painting the
  row's first raster line: see step 1 in the TODO); vertical sub-row positioning needs
  full-colour/NCM glyphs, as in v1.

## How the VIC-IV does it

Today: at the badline the VIC-IV copies the screen row (screen RAM) into the 1 KB row buffer
(`screen_ram_buffer`); while painting each raster line it reads tokens from that buffer, and the
colour bytes **directly from colour RAM**, sequentially from `colour_ram_base + first_card`.

List mode expands the lists **at the badline**:

1. A second 1 KB buffer, the **colour row buffer**, is filled in step with the screen row buffer:
   ordinary tokens get their colour RAM bytes (the VIC's colour RAM port is idle during the
   badline fetch), list data gets the bytes at its own colour offset.
2. On the LIST token the fetch FSM stops copying the row and walks the entries: for each one it
   writes a **synthesised GOTOX** (word 0 with REL cleared, colour bytes as above) and then
   copies the object data (screen and colour) until `$FFFF`. FLIPX/FLIPY are XORed into every
   copied glyph's colour byte 0 bits 6/7 (not into GOTOX tokens inside the object, where those
   bits mean background control); REL turns into an absolute X during the walk (previous entry's X
   + offset). FLIPX mirrors each glyph; an object several glyphs wide also needs its columns in
   reverse order, which its (second) data copy provides -- the hardware does not reorder.
3. While painting, with XCTL bit 5 set, the colour bytes come from the colour row buffer at the
   same index as the screen bytes, instead of colour RAM. With the bit clear nothing changes.

Upstream's unfinished GOTO/GOSUB/RETURN skeleton in `FetchScreenRamLine2`
(`next_token_is_goto`, `$FFFE`/`$FFFD`/`$FFFF`) is the same kind of redirection; list mode
finishes that idea in its own states instead of touching the skeleton.

## Limits

- Row buffer: 1 KB = 512 tokens per character row after expansion (background + all objects).
- Badline time: expansion reads 2 bytes per token (+8 per entry) from chip RAM at the pixel
  clock; 512 tokens about 13 us of a 64 us line. The badline fetch must finish before the row's
  first raster line; if it does not, the rest of the lists is dropped for that row (like running
  out of RRB time).
- Painting time per raster line is unchanged: objects still cost RRB time (fast RRB, XCTL bit 4,
  doubles it).
- BRAM: one more 18 Kb block for the colour row buffer.

## Diagnostic counters

Hardware counters in the VIC-IV (pixel clock, 81 MHz) for the list mode path, read through the
K325T page; `scripts/rrb_diag.py` (print / `clear` / `watch SECONDS`) reads them via the serial
monitor. Not gated by XCTL; reset does not clear them.

| address | meaning |
|---|---|
| `$FFD71E0` | write: select counter 0..31; read: the selected index |
| `$FFD71E1` / `$FFD71E2` | read: low / high byte of the selected counter |
| `$FFD71E3` | write (any value): clear all counters and maxima; reads 0 |

The value crosses from the pixel clock to the CPU clock through two registers on each side:
allow a few CPU cycles after a select, and read until two reads agree (a 16-bit read can tear
while the counter moves). The clear is a toggle (synchronised and edge-detected in the
VIC-IV), so it works for any CPU write timing. Counters are 16 bit and stick at `$FFFF`
(except 9, which wraps); unused indices read 0.

| # | counts |
|---|---|
| 0 | frames: table copy starts (`exp_copy_start`) |
| 1 | copies finished (`exp_copying` falls) |
| 2 / 3 | expansions started (`exp_start`) / finished (`exp_done`) |
| 4 | expander aborts (`exp_abort`, both the line-start and the overtime abort) |
| 5 | line start while in ListWait waiting for the copy: the kept first text row painted plainly |
| 6 | v2 rows painted plainly by the full-row guard (append point beyond LIMIT - 2) |
| 7 | port collisions: expander read in a cycle where the VIC-IV reads too (must stay 0) |
| 8 | expansions ended by the buffer limit (expander `ovf`: EntWait, ObjDrain, Scan, HitEmit) |
| 9 | object parts / v1 entries emitted (Gotox0), wraps |
| 10 | max expansion duration, pixel cycles from start to done (aborted ones not counted) |
| 11 | max copy duration, pixel cycles from copy start to `exp_copying` falling |
| 12 | copy stall cycles (bytes left to issue, port not free) |
| 13 | expansions of rows ended by `$FFFF` (`row_short`) |
| 14 | line starts during an expansion without `exp_done` in that cycle (abort cause; 4 counts all) |
| 15 | v2 expansions started with `start_scan` (rows without a LIST token) |
| 16 | max pixel cycles from copy start to the first copy read |
| 17 | line starts in ListWait (no copy running) in the edge where the ListWait arm decides: its assignment (ListRun or FetchFirstCharacter) wins over the line start's |
| 18 | line starts in ListRun in the `exp_done` edge (ListRun's FetchFirstCharacter wins) |
| 19 | expansions started while copying (must stay 0) |
| 20 / 21 | `ycounter` at the last copy end / the last abort (values, not counts) |
| 22 | line starts in ListWait while copying, in the border-line branch (its FetchFirstCharacter wins over the line start; also in 5) |

Expected with HOLD off: 0 == 1 (one per frame); 2 == 3 + 4; 7 == 0 and 19 == 0 always; 10 and
11 compare with one raster line (2592 pixel cycles).

## Tests

- GHDL (`sim/run_sim.sh`, MLAT 3 and 4): the expansion FSM with a fake screen/colour RAM
  (token streams in, buffer contents out), including REL, FLIPX/FLIPY, empty lists, `$FFFF`
  handling, buffer overflow and aborts; v2: copies with count 0/255 and count changes, user
  masks, REL Y, FLIPY, scans at any start index, overflow, a copy with the port only sometimes
  free (`sim/tb_rrb_list_v2.vhdl`).
- Board: `scripts/rrb_list_test.py` (pixel-exact comparison of a list-mode screen with the same
  picture built with plain GOTOX tokens; v2 two-row objects, short rows, a sprite in the opened
  top border during the copy), then the demos.
- Regression: intro-disk demo run with the bit clear.

## Demos

- **BASIC 65**: N bouncing objects (full-colour 2x2 characters) driven by POKEs into the list
  entries; the background row text stays untouched.
- **B65 BASIC**: the same through its far-memory `?`/`!` operators (spec to the B65 subagent).

## Things learned on the board

- **A raster line ends where its last glyph was painted.** The VIC-IV's
  `raster_buffer_max_write_address` follows the write position (it is not a maximum); the
  display stops there and shows background colour to the right. A plain RRB row whose last
  object sits on the left therefore cuts off everything to its right, and a GOTOX alone does not
  move the end (only painted pixels do). List rows keep the extent of their ordinary part: at
  the first GOTOX of the expanded part (all earlier glyphs are painted by then) the VIC-IV
  saves the row's end, and the line ends at the larger of that and the objects' end.
- **Vertical sub-row positioning needs full-colour (or NCM) glyphs**: the GOTOX Y offset is only
  applied to them, not to normal 8x8 characters. Full-colour glyphs come from glyph number x 64
  in the **first 512 KB** of chip RAM.
- An object at pixel row y straddling two character rows uses two entries: row y/8 with Y
  offset -(y mod 8) (sign bit) and draw mask `$FF << (y mod 8)`, row y/8+1 with Y offset
  +(8 - y mod 8) and mask `(1 << (y mod 8)) - 1`. Unused slots: draw mask 0.
- Updating the two entries of an object from BASIC is not atomic: a frame can show the halves
  from different positions (tearing). List mode v2 (one Y per object, rows computed by the
  VIC-IV from a per-frame snapshot) removes this.
- An expansion still running at the next raster line is cut off and the row painted plainly
  (with or without `$D07C` bit 3; the expander never reads chip RAM in the next line's fetch);
  the colour RAM port is always released at the end of an expansion.
- **A `$FFFF` row advances the next row by its own length** (VIC-IV short-row rule, not
  LINESTEP); list mode keeps that for the rows whose `$FFFF` the v2 objects replace.
- Tests must stop the CPU (monitor `t1`): BASIC 65 rewrites VIC-IV registers while it runs.

## Demos

- `scripts/demos/rrblist65.bas` -- BASIC 65: 6 full-colour balls over an 80-column starfield,
  two list entries per ball, moved with WPOKE (type it in; m65d cannot type `^`, so powers of
  two come from a table). 200-line mode: a glyph pixel is 1 pixel wide and 2 lines tall, so
  the 8x8 ball shows as an 8x16 ellipse; in V400 (below) it is round. The v1 demos stay in
  the 200-line mode: in V400 their 12 entries per row (hidden ones are expanded and painted
  too) exceed the first-raster-line budget and objects on the left lose most of their rows
  (step 1 in the TODO).
- `scripts/demos/rrblist_b65.bas` -- the same in B65 BASIC (type it in **lower case**: B65
  swaps the case of letters like a BBC Micro). Screen rows at `$80000`, ball glyphs at `$60000`
  (B65 uses `$40000-$5FFFF`; full-colour glyphs must stay below 512 KB).
- `scripts/demos/rrblist65v2.bas`, `scripts/demos/rrblist_b65v2.bas` -- the same with list mode
  v2: plain 80-column rows, a 6-object table, the main loop writes X and Y per ball (B65: one
  4-byte `!` write). These run in **V400** (`$D031` bit 3: 50 rows, 640x400 square pixels, round
  balls). The VIC-IV derives row count, row height and borders from `$D031` only while HOTREG
  (`$D05D` bit 7) is on (BASIC 65: yes, and the recompute keeps its charset); B65 BASIC runs
  with HOTREG off and a recompute would move CHARPTR, so its demo sets `$D07B` = 49 rows and
  `$D05B` = 0 (one line per glyph row) itself; the vertical borders are the same in V200 and
  V400. Known until step 1: in V400 an object at the left edge that straddles two rows loses
  the first raster line of the lower row (the badline's fetch + expansion delay the painting;
  the beam passes the left part first). Measured with the CPU stopped: X 10 loses one 8-pixel
  row, X 600 never; V200 hides it because every glyph row has two lines.
