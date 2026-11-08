# RRB list mode (K325T extension, after upstream issue #747)

Status: implemented and verified on the board (2026-11-08); demos in `scripts/demos/`. The list
mode lead ("First raster line budget", step 1) is verified in simulation only, board test pending. Follows the text of
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
- Limits: the scan costs about one cycle per object per character row plus ~16-20 cycles per
  object part drawn, in the badline's line time (shared with the row fetch and painting the
  row's first raster line: see "First raster line budget"); vertical sub-row positioning needs
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

## First raster line budget

Step 1 of the TODO: objects must be complete on the first raster line of every character row.
Measured before (bitstream 9719b5145, V400, 80 columns, fast RRB, 6 v2 objects): an object at
X 10 that straddles two rows lost the badline of the lower row; at X 600 never; V200 hid it
(two lines per glyph row); the v1 demo (12 entries per row) lost most rows on the left.

### Timeline (from the code; numbers from `sim/run_viciv_lead.sh`, the real `viciv.vhdl` in GHDL)

PAL 720x576 50 Hz: a raster line is 864 pixels x 3 = **2592 pixel cycles** (81 MHz). H640 with
CHRXSCL 120: one raster buffer pixel per output pixel (one every 3 cycles). F = the line start
(`is_fetch_start`, `xcounter` = `display_fetch_start` = 719; `xcounter` counts pixels from the
frame generator's `x_zero`, which holds it at 0 for 3 pixels). Cycles count from F.

| when | cycle | why |
|---|---|---|
| F: raster buffer half toggles, FSM to `FetchScreenRamLine` (badline) or `FetchFirstCharacter` | 0 | line-start block |
| `xcounter` wraps (start of the next raster line) | ~426 | 864 - 719 - 3 pixels |
| display reads buffer pixel X of what F painted (`$D051` NORRDEL = 1, the default: write half = read half from x = 0) | **675 + 3X** | (145 + TEXTXPOS 80 + X) x 3 |
| next F: the FSM restarts, an unfinished painting is cut off | 2592 | one line |
| **with the list mode lead** (below): display reads pixel X of what F painted | **3267 + 3X** | one line later |

What the FSM spends after F (fast RRB, XCTL bit 4):

| step | cycles |
|---|---|
| to the first token's dispatch | 9 |
| badline: row fetch (`FetchScreenRamLine2`, one byte per cycle) | 1 + 2 x CHRCOUNT: **161** for 80 tokens |
| list mode (bit 5 or 6): `ListWait` + start + `ListRun` exit | ~8 |
| list mode: expansion (counter 10) | v2 scan ~7 + 1 per table object (13 for 6, 22 for 15 objects without a part in the row); per object part ~16-36 (1 part 36, 6 parts 128 -- the board measured 137 --, 15 parts 335, 30 parts 545) |
| per token: normal glyph, or GOTOX whose high byte bits 4..0 are 0 | 8 (without fast RRB a normal glyph waits for the painter: ~11; fast token path, bit 7: 6) |
| per token: full-colour glyph (8 chip RAM reads) | 16 (fast token path: 4) |
| per token: GOTOX with X >= 256 or the Y offset sign (high byte bits 4..0 /= 0) while FCLRHI/FCLRLO make that glyph number full colour | 16 (it takes the full-colour fetch path) |
| first pixels of a glyph written after its dispatch | 2-5 (2 pixels per cycle) |

Measured painting ends (counter 25): 80 normal tokens 656 (non-badline) / 817 (badline); a v2
row of 80 tokens with no object part in it 838 on its badline.

Margin of a left-edge object part (X 0..10, read at 675..705) = read time - write time. Its
GOTOX is dispatched after the row's 80 background tokens (objects are the last tokens):

| line | first object part dispatched (counter 26) | margin at X 0 / X 10, without the lead | with the lead |
|---|---|---|---|
| non-badline, first part | 656 | ~0 / +30 (sim: one write cycle late at X 0) | +2592 |
| non-badline, second part (after a 2-glyph part: 40-48 cycles) | ~700 | ~-45 / -15: late | ~+2550 |
| plain GOTOX badline (fetch first) | 817 | ~-160 | ~+2430 |
| v2 badline, 1 part in the row | 861 | ~-205 | ~+2390 |
| v2 badline, 6 parts (expansion 128) | 953 | ~-300 | ~+2290 |
| v2 badline, 30 parts (15 two-row objects, expansion 545) | 1370; the painting ends at **2330** | ~-715 (the last part ~-1650) | +1900; the painting ends 262 cycles before the next F |

So without the lead even plain RRB is marginal at the left edge (objects painted after a full
80-column row have ~0 cycles), and every badline is late by its fetch (+161), plus the
expansion in list mode. 30 parts at the left edge are late on every line, list mode or not.

### Design: the list mode lead

Candidates, judged against the timeline:

- (A) prefetch + expand the next row during the current row's last raster line into a second
  row buffer, swap at the badline: the badline becomes a non-badline -- whose margin is ~0 for
  the first object after an 80-token row and negative from the second part on. Not a full line
  more, and 30 parts stay late on every line; plus a second screen and colour row buffer, the
  per-row state twice, `$FFFF` row advances, forced badlines, pointer changes one line early.
- (B) paint the row's own tokens while the expander appends the objects: saves at most the
  expansion (128 cycles for 6 parts); the objects are still painted last: still ~-170.
- (C) more lead: the VIC-IV already has it -- `$D051` NORRDEL = 0 double buffers the raster
  buffer (the painting started at F is shown one line later), but the picture moves down one
  line. (C) for list rows only is impossible: the half pairing holds for the whole frame.
- **Chosen: (C) for the whole frame while XCTL bit 5 or 6 is on**, with the row schedule moved
  one line ahead so that the picture stays where it is. Every pixel gets +2592 cycles (one full
  line more than a plain non-badline has); the only budget left is the RRB time of a line: the
  badline's fetch + expansion + painting must end before the next F (2592 cycles), the same rule
  as for every RRB row. Robust for many parts (30 parts on one row: 2330 cycles).

Implementation (`viciv.vhdl`, `rl_shift`), active when bit 5 or 6 is on, NORRDEL = 1 and DBLRR
(`$D051` bit 6) = 0, latched at the top of each frame (with NORRDEL = 0 a program already has
the delay and keeps its own timing; DBLRR gets the two-line version `rl_shift2`, below):

1. the fetch side's vertical position is `displayy + 1` (`pixels_since_last_char`,
   `before_y_chargen_start`, `justbefore_y_chargen_start`): the whole row schedule -- chargen_y,
   bumps, `first_card_of_row`, badlines, the forced badlines above the text area, the row 0
   fetch, the stop after the last row, V200 double lines -- runs exactly one line earlier;
2. the raster buffer write half is chosen as with NORRDEL = 0: the painting that starts at F of
   line N is shown in line N + 2 (instead of N + 1), i.e. in the same line as before;
3. the display side's text-area mask (`chargen_active` forced off above and below the text)
   is the fetch side's mask of the line before (sampled at x = 0 at the end of that line),
   which is the original mask of the line shown.

**DBLRR** (`$D051` bit 6: a line start only every other raster line, so a painting has two
lines, 5184 cycles, and is shown on two lines): with list mode on the lead is two lines
(`rl_shift2`): fetch side at `displayy + 2`, double buffered (the painting started in line N is
shown in N + 3 and N + 4 instead of N + 1 and N + 2), the mask two lines late. Without it (and on
a plain MEGA65) the first line of each pair races the beam; with it both lines are complete.

**DBLRR needs the V400 schedule** (upstream behaviour, not list mode): the row schedule advances
one glyph row per line start (`$D05B` CHRYSCL counts line starts). In V200 (CHRYSCL 1, two line
starts per glyph row) DBLRR therefore makes every text row 32 lines tall (simulated: badlines
every 32 lines, the picture stretched twice from the top -- mostly the top rows). For the V200
look with DBLRR use V400 timing with 25 rows: `$D031` bit 3, `$D05B` = 0, `$D07B` = 24 (HOTREG
off), `$D051` = `$C0`; every glyph row is then one painting shown on two lines.

**A line start can be lost** (upstream rule): the line-start code sets the FSM's state, but the
state's own case arm comes later in the same process and wins whenever it assigns a state in
that cycle -- while painting (RRB overtime) and during the sprite fetch that follows it. So a
line whose fetch + expansion + painting + sprite fetch (~100 cycles for 8 sprites) runs past the
next line start makes the *next* line unpainted (it shows its buffer half's old content). With
the lead the budget is therefore ~2490 cycles per line for fetch + expansion + painting
(DBLRR: ~5080 per pair). Example `scripts/demos/parallax65.bas` (25 rows of 246 tokens, V200):
badline 2477 cycles plain, 2498 with v2 on (+8 ListWait, +13 empty scan), more with birds
in the row: with the one-line lead the line after every badline is lost (simulated: 24 of 400
lines); on the DBLRR schedule above with the two-line lead every line is complete.

Nothing changes with bits 5 and 6 off (`rl_shift` = `rl_shift2` = 0: every changed expression
reduces to the original). In the simulation the plain 80-column screen is pixel-identical with the lead on
and off (V400), and v2 equals the plain GOTOX reference painted with the lead.

**For programs, with list mode on:** everything the VIC-IV uses *while painting* takes effect
one raster line later in the picture than without list mode, relative to the raster counter:
screen/colour/charset pointers (`$D060`, `$D064`, `$D068`), CHRCOUNT/LINESTEP, `$D054`, the
painted background colours `$D021-$D024`, and screen/colour/glyph data changed while the beam
runs. A program that changes them per raster line (raster IRQ, the K325T raster DMA list) must
do it one line earlier. Display-time things are unchanged: border colour, sprites, the raster
counter and raster IRQs, X position/scroll. Also, a line's visible end ("a raster line ends
where its last glyph was painted") is the line's own: without the lead the VIC-IV applies the
end of the line painted before it (`raster_buffer_max_write_address_hold` -> `_prev` is one
line behind with NORRDEL = 1); rows whose lines all end at the same X look the same either way.

## Fast token path (XCTL bit 7)

Full-colour (FCM) art in several 80-column layers does not fit a raster line with the token
state machine as it is: every token goes through it one after the other.

### Where the cycles go (bit 4 on, bit 7 off; from the code, measured in GHDL)

Per token, after the previous token's dispatch:

| state | cycles | what it does |
|---|---|---|
| FetchNextCharacter, FetchCharHighByte | 2 | the token's two screen bytes from the row buffer (LUT RAM, no latency) and colour byte 0 (block RAM, one cycle) |
| FetchTextCell | 1 | glyph address (FCM: glyph x 64 + row +- Y offset; normal: charset + glyph x 8 + row), draw mask, `$FFFF` |
| FetchTextCellColourAndSource | 1 | colour byte 1, attributes; puts the glyph address on the chip RAM port |
| PaintMemWait, PaintMemWait2 | 2 | chip RAM latency (block RAM + one register) |
| PaintFullColourFetch | 8 (FCM/NCM only) | one byte per cycle (the VIC-IV's chip RAM port is 8 bits wide) |
| PaintMemWait3, PaintDispatch | 2 | end-of-row checks; wait for the painter, start it |

| token | cycles | chip RAM | pixel write cycles (2 px/cycle) | painter (setup + pixels + idle) |
|---|---|---|---|---|
| plain glyph | **8** | 1 read | 4 | 1 + 4 + 2 = 7, hidden behind the 8 |
| full colour (FCM) | **16** | 8 reads | 4 | 1 + 4 + 2 |
| NCM (16 px) | **16** | 8 reads | 8 | 1 + 8 + 2 |
| GOTOX | **8**, or **16** when its high byte bits 4..0 are not 0 (X >= 256 or the Y offset sign) while FCLRHI makes that "glyph number" full colour: it then fetches 8 useless bytes | 1 / 8 | 0 | - |

So a plain glyph's 8 cycles are pure state machine: 4 states to read and decode, 2 to wait for
the chip RAM, 2 to check and dispatch; its 4 pixel write cycles run in parallel with the next
token's first states. Three FCM layers (3 x (GOTOX + 81 glyphs) = 246 tokens): **3928** cycles
per line, **4421** on a badline (+ the 493-cycle row fetch), against 2592.

### Design

- (a) a 32-bit VIC-IV read port: `chipram_k325t.vhdl` keeps all 1 MB as four byte lanes (lane =
  address bits 1..0; the pre-loaded 384 KB split by `src/tools/k325t_chipram_lanes.py`), port B
  returns the aligned word next to the byte (`dob32` -> `gs4510` -> `machine` -> `viciv`
  `chipram_datain32`). An FCM row = 2 reads instead of 8. No extra block RAM (the same bits).
- (c) a decoder one token ahead: it does the four read/decode states for token k+1 (into the
  `n_*` registers) while token k waits for the chip RAM and is dispatched; at k's dispatch the
  `n_*` registers become the `glyph_*` registers and k+1 goes straight to PaintMemWait. The two
  words of an FCM row are asked for in PaintMemWait / PaintMemWait2; TurboFetch32 assembles the
  8 bytes (flips, NCM nybl order, reverse/XOR, underline, draw mask exactly as
  PaintFullColourFetch) and does PaintMemWait3's checks. A token decoded behind a GOTOX that is
  not dispatched yet waits (the GOTOX changes the draw mask and Y offset the decoder needs).
- painter back to back: an FCM, NCM or plain mono glyph is dispatched in the painter's last
  pixel cycle; FCM/NCM skip the painter's set-up state (done at dispatch). A GOTOX still waits
  for the painter to be idle (it moves the write position).
- (b) 4 pixels per cycle was not needed: with (a) + (c) the painter (4 cycles per 8 FCM pixels)
  is the limit for FCM, and the token rate matches it.

Gating: XCTL bit 7 together with bit 4, CHR16 text lines (not bitmap, not 8-bit characters, not
glyphs from hyperram); with bit 7 off nothing changes (the old states run, the lanes return the
same bytes). The pictures are pixel-identical with the old path (GHDL: FCM layers with FLIPX,
FLIPY, alpha, NCM, width trim and plain glyphs mixed in, v2 objects, parallax rows,
microbenchmarks; board: `rrb_list_test.py` case H); only the time changes.

### Measured (GHDL, `sim/run_viciv_lead.sh`, rows of 3 x (GOTOX + 81 tokens), non-badline)

| row | bit 7 off | bit 7 on | per token |
|---|---|---|---|
| all FCM | 3928 | **1006** | 16 -> 4 |
| all plain | 1984 | 1490 | 8 -> 6 (PaintMono + PaintMonoDrive + 4 pixel cycles) |
| GOTOX + FCM pairs | 3544 | 1611 | pair 28.7 -> 13 (GOTOX ~9: waits for an idle painter, the next token's decode waits for it) |
| all NCM | 3934 | 1974 | 16 -> 8 (16 pixels) |
| three FCM layers with attributes mixed in | 3928 | 1123 (badline 1622) | |

Three 80-column FCM layers (with attributes) + 12 v2 object parts (six two-row, two-glyph
objects) in one row, V200, GHDL: that row's badline paints in **2073** cycles (row fetch 493,
expansion 245, first object part at 1871), no late pixels; + the sprite fetch (~100) leaves
~420 of 2592 cycles.

## Limits

- Row buffer: 1 KB = 512 tokens per character row after expansion (background + all objects).
- Badline time: expansion reads 2 bytes per token (+8 per entry) from chip RAM at the pixel
  clock; 512 tokens about 13 us of a 64 us line. An expansion still running at the next line
  start is cut off and the row painted plainly.
- Line time (see "First raster line budget"): with list mode on, every pixel a line's painting
  writes before the next line start is shown, also on the first line of a row (the lead). The
  badline's row fetch (1 + 2 x CHRCOUNT cycles) + expansion + painting + the sprite fetch
  (~100 cycles) must end within one line, 2592 pixel cycles, or the next line is not painted
  (upstream's lost line start): 80 tokens + 30 object parts (one-glyph full-colour objects)
  need 2330 + sprites. Heavier rows: DBLRR on the V400 schedule (two lines per painting, the
  two-line lead). Counter 25 shows the longest painting.
- Painting time per raster line is unchanged: objects still cost RRB time (fast RRB, XCTL bit 4,
  doubles it): GOTOX 8 or 16 cycles (16 with X >= 256 or a Y offset sign while the glyph number
  counts as full colour), full-colour glyph 16, normal glyph 8 (11 without fast RRB). With the
  fast token path (bit 7 + bit 4): FCM 4, NCM 8, plain 6, GOTOX ~9 ("Fast token path").
- Raster effects: with list mode on, paint-time registers take effect one raster line later
  (write them one line earlier), see "First raster line budget".
- BRAM: one more 18 Kb block for the colour row buffer (the lead needs none).

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
| 23 | late pixels: raster buffer write cycles (1 or 2 pixels) into the half the display reads, at or left of the pixel it reads, inside the text area, before the line start (must stay 0; with the list mode lead 0 by construction) |
| 24 | `ycounter` at the last late pixel (value) |
| 25 | max pixel cycles from a line start to the end of its painting (`EndOfChargen`; a painting cut off by the next line start counts the cycles it had, ~2592): the RRB time used. Values above 2592 (without DBLRR) come from the vertical blank, where no line starts happen for ~48 lines (the last painting of a frame); a lost line start shows as ~2591 |
| 26 | max pixel cycles from a line start to the dispatch of the first GOTOX of a list row's expanded part (its first object part) |

Expected with HOLD off: 0 == 1 (one per frame); 2 == 3 + 4; 7 == 0 and 19 == 0 always; 10 and
11 compare with one raster line (2592 pixel cycles). 23 == 0 with list mode on (the lead); with
bits 5/6 off it counts real late pixels (e.g. plain RRB objects at the left edge on badlines).
25 < ~2590 means no line was cut off. Without the lead an object part at X is in time only
if it is written before 675 + 3 X cycles (26 says when the first one starts); with the lead
before 2592.

## Tests

- GHDL (`sim/run_sim.sh`, MLAT 3 and 4): the expansion FSM with a fake screen/colour RAM
  (token streams in, buffer contents out), including REL, FLIPX/FLIPY, empty lists, `$FFFF`
  handling, buffer overflow and aborts; v2: copies with count 0/255 and count changes, user
  masks, REL Y, FLIPY, scans at any start index, overflow, a copy with the port only sometimes
  free (`sim/tb_rrb_list_v2.vhdl`).
- GHDL, the whole VIC-IV (`sim/run_viciv_lead.sh`, ~6 min, not part of `run_sim.sh`): the real
  `viciv.vhdl` (report-stripped copy) with a PAL frame, a chip RAM model and fastio register /
  colour RAM writes, V400, fast RRB; frame dumps and the counters per line. Checks: plain rows
  identical with the lead on and off; plain GOTOX reference with the lead == v2 (6 objects at
  X 0/10/600, and 30 parts in one row); no late pixels with the lead; late pixels without it;
  DBLRR on the V400 schedule with `parallax65.bas`-like rows (246 tokens): the two-line lead
  changes only the first line of each pair, no late pixels, v2 with an empty table changes nothing.
  Fast token path (XCTL bit 7): pixel-identical with the old path on FCM layers with attributes,
  parallax rows, v2 objects, 30 parts and the per-token microbenchmarks.
  The same checks pass in V200 (`-gV400=0`, run by hand).
- Board: `scripts/rrb_list_test.py` (pixel-exact comparison of a list-mode screen with the same
  picture built with plain GOTOX tokens; v2 two-row objects, short rows, a sprite in the opened
  top border during the copy; G/G2: V400 objects at the left edge and 30 parts in a row, late
  pixel counter; G3: the same with DBLRR on the V400 schedule; H: three full-colour layers with
  the fast token path against the old path / plain parts), then the demos.
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
  the 8x8 ball shows as an 8x16 ellipse; in V400 (below) it is round. The v1 demos are written
  for the 200-line mode. Before the list mode lead, V400 lost most rows of their objects on
  the left (12 entries per row, hidden ones expanded and painted too, after an 80-token row:
  late on every badline and from the second part on also on the other lines). With the lead
  the budget is the line time only: 12 two-glyph parts after 80 tokens are ~160 + ~300 + 640 +
  12 x 48 = ~1700 of 2592 cycles on a badline, so they can move to V400 (rows, entries and
  `$D031`/`$D07B`/`$D05B` as in the v2 demos); not done yet.
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
  V400. Until the list mode lead ("First raster line budget") an object at the left edge that
  straddles two rows lost the first raster line of the lower row (the badline's fetch +
  expansion delay the painting; the beam passes the left part first). Measured with the CPU
  stopped (bitstream 9719b5145): X 10 lost one 8-pixel row, X 600 never; V200 hid it because
  every glyph row has two lines. With the lead no line is lost.
