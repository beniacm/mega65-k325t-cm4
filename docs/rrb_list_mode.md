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

## List mode v2/v3: global object table

Gated by **XCTL bit 6**. Instead of entries per row, one table of up to 1023 objects (level 3;
255 at level 2), each with its own X **and Y**, like a sprite table; the VIC-IV works out the
rows, Y offsets and draw masks. Moving an object = writing its X and Y (4 bytes), nothing else.
Level 3 (the "v3 table", below) changed the entry to 32 bytes: the VIC-IV reads bytes 0..15,
bytes 16..31 belong to software. There is no 16-byte mode any more.

Registers (cleared by reset):

| address | meaning |
|---|---|
| `$FFD71F8-$FFD71FA` | table address in chip RAM (20 bits, little endian) |
| `$FFD71FB` | number of objects, low byte (a write also clears bits 9..8: 0..255 for old programs) |
| `$FFD728A-$FFD728B` | number of objects, 10 bits (0..1023; `$FFD728A` = `$FFD71FB` without clearing the high bits) |
| `$FFD71FC` | bit 0 HOLD: keep the last snapshot (no copy per frame) |
| `$FFD71FD` | read only: K325T extension level (1 = XCTL bits 0-7 incl. bit 3; 2 = also the collides-with mask +15 and painting overlapped with the expansion, 2 KB list rows; 3 = the v3 table: 32-byte entries, 1023 objects, per-row buckets, circle test, pair budget, the `$FFD7200` page) |

The address and the count are several bytes in the CPU's clock domain; the VIC-IV latches them
at the start of each copy. Write them before setting XCTL bit 6, or with HOLD set (then clear
HOLD: the next frame copies with the new values).

Entry (32 bytes; the VIC-IV reads bytes 0..15, see "v3 table" for 16..31):

| offset | meaning |
|---|---|
| +0 | X 9..0 (as GOTOX); bit 10 REL: X = previous table object's X + signed offset |
| +2 | Y 9..0, signed: pixel rows from the top of character row 0; bit 10 REL (as X) |
| +4 | draw mask, ANDed with the computed one (`$FF` normally, `0` hides the object) |
| +5 | bits 5..0: height in character rows, 0..63 (0 = 1); bit 7 circle (collisions, level 3); bit 6 ignored |
| +6 | bits 3..0 screen address 19..16, 4 FLIPX, 5 FLIPY, 6 transparent, 7 behind |
| +7 | collision group mask: one bit per group (0 = never collides; "Collisions") |
| +8 | screen address 15..0 of object row 0's tokens (an RRB token list ending with `$FFFF`) |
| +10 | colour RAM offset of object row 0's tokens |
| +12 | row stride: bytes from one object row's tokens to the next (screen and colour) |
| +14 | collision box width in pixels (0 = 8) |
| +15 | collides-with mask: groups this object collides with (0 = the same as +7; level 2) |

- **Snapshot per frame**: at the top of every frame (unless HOLD is set) the VIC-IV copies the
  table (bytes 0..15 of each entry) into its own table RAM (block RAM, 1024 objects) and
  builds the per-row buckets ("v3 table"). The count is part of the snapshot: every row of the
  frame uses the copied table, so an object beyond a new count never appears, and an object can
  never be shown in two places or in halves.
- **The copy only uses idle chip RAM cycles** of the VIC-IV (after the sprite fetches of a line,
  and the list-mode wait states); it never disturbs the row, glyph or sprite fetches (also in an
  opened top border). 1023 objects (16368 bytes) and their buckets take about 8 lines of the
  top border (GHDL: 21280 cycles with a free port). The fetch of the first text row waits for
  the copy if it is still running; if it is not done by the next line, that row is painted
  without objects for that frame.
- **HOLD** keeps the last snapshot, its count included (no copy at all while set). After reset
  the snapshot is empty: HOLD set before the first copy shows nothing.
- A program may rewrite the table at any time. The copy reads an object's bytes within a few
  cycles, so an X/Y pair written with separate stores can still tear against it (a window of
  roughly 16 cycles per object per frame, also with B65's 4-byte `!`); HOLD makes it atomic.
- **Per character row** the VIC-IV walks the row's bucket (only the objects touching the row,
  in table order, from the table RAM, no chip RAM time) and turns every object into one or two
  entries exactly as in v1:
  the top part of object row k = row - floor(Y/8) (Y offset -(Y mod 8), mask `$FF << (Y mod 8)`)
  and the bottom part of object row k-1 (offset +(8 - Y mod 8), the complementary mask).
  FLIPY reverses the object rows and swaps the offset's sign (the VIC-IV applies the offset to
  the flipped glyph row).
- Rows need **no LIST token**: the objects are appended after the row's last token (CHRCOUNT),
  or, in a row ended early by `$FFFF`, at that `$FFFF` (the objects' first GOTOX overwrites it,
  the expansion ends with its own `$FFFF`; the short row keeps its row advance). A row with a
  LIST token gets them at the token, after its own v1 entries if bit 5 is on too. The first of
  LIST / `$FFFF` in the row counts; nothing after an end of row does.
- **Full rows**: if the append point is beyond 2042 (the 2 KB list row buffer minus a GOTOX), the row is
  painted plainly, without objects; an expansion that reaches the buffer limit ends there with
  `$FFFF` (no object part is started that cannot fit).
- Objects later in the table are painted later (on top).
- REL lets a group follow an anchor: write the anchor's X/Y only. A hidden anchor (mask 0) is
  fine. REL is resolved during the copy (the snapshot holds absolute positions), so every
  object, drawn or not, is an anchor for the next one.
- Limits: ~17 cycles per object part drawn (13 for the second part of a two-row object), in
  the badline's time, overlapped with the painting ("Many objects on one row"); objects only
  in character rows 0..63 (the buckets); at most 2048 bucket entries per frame; vertical
  sub-row positioning needs full-colour/NCM glyphs, as in v1.

## v3 table (level 3)

`$FFD71FD` (and `$FFD728C`) read 3. Changes against level 2: 32-byte entries (the VIC-IV reads
bytes 0..15, bytes 16..31 are the software block, never read), up to 1023 objects (10-bit
count), per-row buckets, collisions over the buckets with a circle test and a pair budget, the
register page `$FFD7200-$FFD72FF` (results for all 1023 objects). A flag day: there is no
16-byte table any more.

Entry (32 bytes):

| bytes | owner | content |
|---|---|---|
| 0..15 | VIC-IV | the object (table above): X, Y, draw mask, height / circle, flags and screen address 19..16, group, screen address, colour offset, stride, width, collides-with mask |
| 16..31 | software | the motion block (B65's object statements; documented for user assembler and BASIC 65 programs), below |

Software motion block (bytes 16..31; owned by B65, which may refine it while implementing its
object statements; the hardware never reads it):

| byte | field |
|---|---|
| +16 | mode: 0 static, 1 cartesian, 2 polar, 3 orbit; bit 4 harmonic-to-anchor; bits 5..6 bounds set 0..3; bit 7 pixel-collision hint |
| +17 | exit action X (bits 1..0) and Y (bits 3..2): 0 none, 1 wrap, 2 bounce, 3 kill; bit 4 kill at lifetime 0; bit 5 half restitution on bounce |
| +18 | cartesian VX (16-bit signed, 8.8 px/frame) / polar speed (8.8) / orbit radius |
| +20 | cartesian VY / polar and orbit heading (16-bit, 1/65536 turn) |
| +22 | cartesian AX (1/256 px/frame^2) / polar speed change per frame |
| +24 | cartesian AY / polar and orbit turn rate (16-bit signed, 1/65536 turn per frame) |
| +26 | lifetime in frames (16-bit, 0 = unlimited) |
| +28 | harmonic k (1/256) / orbit anchor object number (10 bits) |
| +30 | X fraction (8 bits) |
| +31 | Y fraction (8 bits) |

Register page `$FFD7200-$FFD72FF` (soft_rtc; reset clears the budget and the count):

| address | meaning |
|---|---|
| `$FFD7200-$FFD727F` | read: collided objects of the last pass, byte n bit b = object 8n + b (0..1023) |
| `$FFD7280` | as `$FFD71C0` (bit 0 latched collision, bit 1 IRQ enable, bit 7 pending; write bit 1 = enable, bit 0 = 1 clears 0 and 7), + read bit 2 = the last pass was cut off by the pair budget, bit 3 = the last bucket build dropped entries (more than 2048) |
| `$FFD7282-$FFD7283` | r/w: object number (10 bits; the same register as `$FFD71C1`, whose write clears bits 9..8) |
| `$FFD7284-$FFD7285` | read: its first partner (lowest number; `$3FF` = none) |
| `$FFD7286-$FFD7287` | read: number of collided objects (0..1023; `$FFD71C3` reads 255 above 255) |
| `$FFD7288-$FFD7289` | r/w: pair budget per pass (0 = no limit) |
| `$FFD728A-$FFD728B` | r/w: number of objects (10 bits) |
| `$FFD728C` | read: K325T extension level (= `$FFD71FD`) |

The `$FFD71A0-$FFD71C3` window keeps serving objects 0..255 (`$FFD71C2` reads `$FF` for a
partner above 255).

**Buckets.** After the copy the VIC-IV lists, for each character row 0..63, the visible objects
(draw mask /= 0) whose rows Y .. Y + 8 H - 1 touch it, in table order: a counting sort (the
counts are made while the copy runs, then prefix sums, then one pass over the table filling
the lists), at most 2048 entries in all (one RAMB36). The row scan walks its bucket instead of
the whole table, and the collision pass tests only objects sharing a bucket. An object above
row 0 or below row 63 is neither drawn nor tested there; entries beyond 2048 are dropped (the
rows filled last lose objects; `$FFD7280` bit 3).

| | GHDL (`tb_rrb_collide`, `tb_rrb_list_v2`) |
|---|---|
| copy + buckets, 1023 objects | 21280 cycles (8.2 raster lines, port free) |
| collision pass, 1023 random objects over 50 rows (1/5 circles) | 52694 cycles (20 lines) |
| collision pass, 255 objects in one place (straddling two rows) | 35753 cycles (13.8 lines) |
| collision pass, 1023 objects in one place | ~523000 cycles (one pair per cycle: ~200 lines) -- use the budget |
| row scan per object part | 17 cycles (unchanged) |

**Pair budget.** `$FFD7288-9` /= 0 limits the pairs one pass tests; a pass that reaches it ends
there (`$FFD7280` bit 2): its results are a subset (the pairs of the upper rows first). 0 = all
pairs. The pass always finishes within a frame (~523000 cycles for all 1023 objects in one
place, a PAL frame has 1.6 million), so the budget is about latency, not correctness.

Hardware: `rrb_list_expander.vhdl` (generic `NOBJ` = 1024 or 512: the snapshot's depth, 4 or
about 2 RAMB36; the bucket RAM one RAMB36), `viciv.vhdl` (generic `rrb_nobj`, port widths),
`soft_rtc.vhdl` (the page), `iomapper.vhdl` (decodes `$FFD72xx` on the K325T: no I2C device
there on this target), `machine.vhdl` (wiring).

## Collisions

Once per frame the VIC-IV compares the pairs of objects of the frame's snapshot that share a
character row (list mode v2/v3, XCTL bit 6) and reports which objects overlap -- like the
VIC-II's sprite collision register, for up to 1023 objects (level 3; all pairs of up to 255 at
level 2).

- **Box** of an object: X .. X + W - 1 (W = +14, 0 = 8 pixels), Y .. Y + 8 H - 1 (H = +5 bits
  5..0, 0 = 1 row; Y signed, -512..511); X and Y after REL, exactly as the rows are drawn. The box
  does not depend on the glyphs (an object 3 glyphs wide sets W = 24).
- Two objects A and B collide if both have a **draw mask** (+4) /= 0 and a **group mask** (+7)
  /= 0, each one's group mask shares a bit with the other's **collides-with mask** (+15, 0 =
  its +7): (grp_A and with_B) /= 0 and (grp_B and with_A) /= 0, and the boxes overlap
  (touching edges count: X 0..7 and X 7..14 overlap at 7). With +15 = 0 everywhere this is the
  level-1 rule (the group masks share a bit). Example: bullets group 1 with 2, the player group
  2 with 1: a bullet hits the player, bullets never collide with each other (1 and 1 = 0 on the
  with side), and an object with group 4 / with 4 sees neither.
- **Circles** (level 3): +5 bit 7 makes the object a circle of radius W/2 about its box centre
  (X + W/2, Y + 4 H). Circle and circle collide if the centres are at most the sum of the radii
  apart; circle and box if the circle reaches the box (the nearest point of the box: touching
  counts); box and box as above. The pass only meets objects whose boxes share a character
  row, so keep a circle inside its box vertically (W <= 8 H).
- **Rows** (level 3): the pass walks the per-row buckets ("v3 table"): only pairs listed in a
  common row 0..63 are tested, each once (in the first row both are listed in; a row in which
  no object starts is skipped). Objects outside rows 0..63 never collide.
- The **pass** runs after the frame's table copy and bucket build (with HOLD set: on the held
  snapshot, every frame): per row, the pairs of its bucket, one pair per pixel cycle (a 6-stage
  pipeline: bucket read, table read, shape, tests, squares, result) plus ~12 cycles per object:
  255 objects in one place 35753 cycles = 13.8 raster lines, 1023 objects spread over 50 rows
  52694 (20 lines). The copy and the pass start at the top of the frame, so for spread objects
  the result of frame N's positions is complete before frame N's first text row. It reads the
  snapshot through the table RAM's second port, so it never delays a row. The pair budget
  (`$FFD7288`, "v3 table") cuts a long pass off. The next copy waits for a pass still running.
- **Results**, double buffered: the pass writes the working set; at its end it becomes the
  readable one in one step (a pass's results are never mixed with another's), and the CPU
  side copies it: a bit per object (collided this pass), the **first partner** per object (the
  lowest-numbered object it collides with), the number of collided objects. Objects 0..255 at
  `$FFD71A0-C3` (below), all 1023 at `$FFD7200-87` ("v3 table").

| address | meaning |
|---|---|
| `$FFD71A0-$FFD71BF` | read: collided objects of the last pass, byte n bit b = object 8n + b |
| `$FFD71C0` | read: bit 0 = a pass found a collision (latched until cleared), bit 1 = IRQ enable, bit 7 = IRQ pending (set together with bit 0); write: bit 1 = IRQ enable, bit 0 = 1 clears bits 0 and 7 |
| `$FFD71C1` | write: object number; read: it back |
| `$FFD71C2` | read: first partner of that object ($FF: none, or above 255) |
| `$FFD71C3` | read: number of collided objects in the last pass (0..255; 255 if more) |

- **IRQ**: with bit 1 set, the IRQ line is active while bit 7 is set, i.e. from the end of a
  pass with a collision until the program writes bit 0 = 1. It is one more input of the
  iomapper's IRQ (like the CIAs, Ethernet, UART), so the CPU takes it like any peripheral IRQ;
  the handler checks `$FFD71C0` bit 7 and acknowledges by writing `$03` (keep enabled) or `$01`.
- The table and the count change only when a pass ends (once per frame, ~line 15). Reading the
  32 bytes across that moment can mix two passes (read them right after the IRQ, or poll bit 0,
  read, then clear). `$FFD71C2` reads the partner from the VIC-IV's readable buffer, which
  switches a few CPU cycles before the CPU-side copy of the table.
- From BASIC (polling): `IF PEEK($FFD71C0) AND 1 THEN` read `$FFD71A0+N/8` bit `N AND 7`, or
  `POKE $FFD71C1,N : P = PEEK($FFD71C2)`; then `POKE $FFD71C0,1`.

Hardware: `rrb_list_expander.vhdl` (the pass over the buckets, reading the shapes from the table RAM; the partner RAMs, 1024 x 10 bits each; double buffers),
`viciv.vhdl` (a tick per frame), `soft_rtc.vhdl` (registers, CPU-side copy on a toggle, IRQ),
`iomapper.vhdl` (IRQ input), `machine.vhdl` (wiring).

## How the VIC-IV does it

Code: `viciv.vhdl` keeps the list mode states and branches of its raster FSM (ListWait, ListRun,
FrontWait, the fast token decoder, the lead's shifted buffer halves) and instantiates
`rrb_list_expander.vhdl` (lists, the object table, buckets, collisions) and
`rrb_list_frontend.vhdl` (the XCTL bit 3 valid bits, the colour row buffer, the diagnostic
counters: the parts that sit beside the FSM, moved out of `viciv.vhdl` unchanged).

Today: at the badline the VIC-IV copies the screen row (screen RAM) into the 1 KB row buffer
(`screen_ram_buffer`); while painting each raster line it reads tokens from that buffer, and the
colour bytes **directly from colour RAM**, sequentially from `colour_ram_base + first_card`.

List mode expands the lists **at the badline**:

1. A second buffer, the **colour row buffer** (2 KB, as the screen row buffer of list rows), is
   filled in step with the screen row buffer: ordinary tokens get their colour RAM bytes (the
   VIC's colour RAM port is idle during the badline fetch), list data gets the bytes at its own
   colour offset.
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
| list mode: expansion (counter 10) | v2 scan ~7 + 1 per table object (13 for 6, 22 for 15 objects without a part in the row); per object part ~16-36 (1 part 36, 6 parts 128 -- the board measured 137 --, 15 parts 335, 30 parts 545; these are before step 4's expander speed-up: now 17 per one-glyph part, 13 for a two-row object's second part) |
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
  is the limit for FCM, and the token rate matches it. (Step 7 added it anyway, for plain/NCM glyphs, GOTOX
  pairs and object piles: XCTL2 bit 0, "4 px painting" below.)

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

## 4 px painting (XCTL2 bit 0, step 7)

**XCTL2 `$FFD71F1`** (read/write, cleared by reset; bits 7..1 read 0 and are reserved). Bit 0
works only together with XCTL bits 4 and 7 (fast RRB painting + fast token path); with bit 0
off, or without bits 4 and 7, every picture and every cycle is as before (GHDL: the 71
`run_viciv_lead.sh` scenarios with the bit off are byte-identical to step 6's outputs).

What it changes (all of it gated by the bit):

- **raster buffer**: four banks by X mod 4 in `rrb_list_frontend.vhdl` (4 x 512 x 18, the same
  block RAM as the old pair); the painter can write 4 consecutive pixels per cycle.
- **painter**: FCM (8 px = 2 cycles), NCM (16 px = 4), plain mono (8 px = 2) and multicolour
  (two double-wide pixels) in 4-pixel steps; the last 1..3 pixels of a glyph whose width is not
  a multiple of 4 take a 2-pixel step.
- **GOTOX**: dispatched as soon as the painter is idle (its last write is out); the decoder keeps
  its own copy of the GOTOX's Y offset, sign and draw mask, so the token behind it is decoded
  without waiting for the dispatch.
- **expander** (list mode v2/v3, object parts from the table scan): a one-row hit goes straight
  to emitting (no multiply); the object's first token reads go out during its GOTOX; the next
  table word is read ahead while a part is fetched; a part ends when its `$FFFF` comes back,
  and when the token data is 4-byte aligned the 32-bit word shows the `$FFFF` behind the glyph
  token two cycles earlier. The GOTOX's PaintMemWait/PaintMemWait2 port cycles go to the
  expander (counter 7 below).

Falls back to the 2-pixel timing (**same speed as with the bit off**):

| case | cycles |
|---|---|
| lines not on the fast token path (bitmap, 8-bit characters, hyperram glyphs, bit 7 or 4 off) | as before: the bit has no effect |
| pixels at a raster buffer X above 1018 without RRB wrap-around | 2 px per cycle for those steps |
| the last 1..3 pixels of a glyph trimmed to a width that is not a multiple of 4 | one 2 px step |
| object token data not 4-byte aligned | 9 cycles per part instead of 7 (unit test, MLAT 3) |
| v1 list entries (XCTL bit 5): no table scan | the read-ahead and the early GOTOX reads are scan-only; the `$FFFF` end detection applies |

### Measured (GHDL)

`sim/run_viciv_lead.sh` (71 scenarios, TB_VICIV_LEAD_PASS: every 4 px picture identical to its
2 px pair, no late pixels, port collisions 0) and `sim/tb_rrb_list_v2.vhdl` (timing cases).
Text rows = 3 x (GOTOX + 81 tokens), non-badline painting cycles; piles = one-glyph objects all
on row 10, painting end (expansion end) in cycles from the line start; DBLRR has 5184 per
painting, otherwise 2592.

| case | bit off (2 px, bit 7) | 4 px, painter only (parts 1-2) | + piece 1 (expander) + piece 2 (GOTOX) |
|---|---|---|---|
| all FCM | 1006 | 1002 | **993** |
| all plain | 1490 | 1006 | **995** |
| all NCM | 1974 | 1006 | **995** |
| GOTOX + FCM pairs | 1611 | 1371 | **993** |
| three FCM layers with attributes | 1123 | 1004 | **994** |
| expansion per object part (unit test, MLAT 3) | 17 | 17 | **7** (9 if the token data is not 4-byte aligned; MLAT 4: 8) |
| expansion per part in the VIC-IV (64 -> 255 objects) | 17.0 | 17.0 | **8.0** |
| 64 objects, no DBLRR | 1371 (1258) | 1278 | **816** (515) |
| 64 objects, DBLRR | - | - | **816** (515) |
| 128 objects, no DBLRR | 2459 (2346) | - | **1328** (1027) |
| 128 objects, DBLRR | 2459 (2346) | - | **1328** (1027) |
| 231 objects, DBLRR | 4210 (4097) | 4117 | **2152** (1851) |
| 255 objects, DBLRR | 4618 (4505) | - | **2344** (2043) |
| 255 objects in one line, no DBLRR | does not fit | does not fit | **2344** (2043) of 2592 |
| 1023 objects spread over 50 rows | 1189 (939) | 976 | **825** (334) |

DBLRR changes no cycle count here (it only gives two lines per painting). On the way, piece 1
without its last two parts (the 32-bit `$FFFF` look-ahead and the GOTOX's port cycles) gave 9
cycles per part in the unit test and 64/128/231 piles of 1080/1784/2917; with piece 2 887/1655/
2891 and 255 on DBLRR 3179.  The remaining limits: the token pipeline (4 cycles per glyph, the
decoder) for text rows, and for piles the decoder + the expander's chip RAM port (two tokens per
part, ~8 cycles).

## Unpainted pixels (XCTL bit 3)

The VIC-IV never clears its raster line buffer: a pixel that no token of a line paints keeps
whatever an earlier line painted into that buffer half, and the line is shown up to its last
painted pixel. Sparse rows (a few glyphs behind GOTOX tokens, objects appended) therefore show
streaks of other rows -- unless an opaque base layer is painted first (~330 cycles per line for
80 glyphs, 640 with bit 7 off).

With **XCTL bit 3** a pixel the line's painting did not write shows as a background pixel
(screen colour `$D021`, alpha `$FF`, not foreground, primary palette: exactly what a painted
background pixel of the screen colour shows), and every line runs to the full text width (no
"ends at the last painted pixel" cut). Bit 3 off: upstream behaviour.

- One valid bit per buffer pixel (2 halves x 1024 positions, per even/odd bank), in flip-flops:
  4 cycles after each line start the new write half's bits are cleared in one cycle (after the
  display has passed the end of the text area -- with the lead the write half is the one just
  shown --, before the painting's first write ~10 cycles after the line start); every raster
  buffer write sets its bit one cycle later; the display reads the bit with the pixel. No block
  RAM (the raster buffer banks stay 18 bits wide; a 19th generation bit would cost 2 RAMB36 and
  would still repeat every two paintings of a half), ~2048 flip-flops and their decode/mux.
- Lead (bits 5/6), DBLRR, the fast token path: the clear follows the write half chosen at the
  line start, so all of them work unchanged (GHDL: identical to the base-of-spaces reference
  with the lead, bit 7, and DBLRR + lead). A line whose start is lost (overtime) shows
  background instead of stale content.
- The painter never reads the buffer: transparent glyph pixels simply do not write, so they
  stay unpainted (background) unless something below painted them in the same line.
- Tests: GHDL (`run_viciv_lead.sh`, u0-u7), board `rrb_list_test.py` case K.

## Limits

- Objects (level 3): 1023 per table (`NOBJ` = 1024; 511 with `NOBJ` = 512); drawn and tested
  for collisions in character rows 0..63 only; 2048 bucket entries per frame (an object counts
  once per row it touches: 1023 one-row objects at random Y need ~1918, 2046 if all straddle
  two rows); the collision pass one pair per cycle (budget `$FFD7288`).
- Row buffer: rows with list mode (bit 5 or 6 on) 2 KB = 1022 tokens per character row after
  expansion (background + all objects; the row's own tokens still come from the first 1 KB, at
  most 512 as before); plain rows 1 KB = 512 tokens (the upstream wrap). 255 one-glyph objects
  after a 48-token row are 558 tokens.
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
- **Stale pixels**: the raster line buffer is never cleared. A pixel no token of the line
  paints shows what an earlier line painted into that buffer half, and the line ends at its
  last painted pixel. Sparse rows (glyphs behind GOTOX tokens with gaps) therefore need an
  opaque base layer first (e.g. GOTOX 0 + 80 glyphs, ~330 cycles with bit 7, ~640 without), or
  XCTL bit 3 ("Unpainted pixels"); tests comparing two different rows must make them opaque
  (cases E, J) because the gaps' history differs between the two.
- **Many objects on one row** (a pile on the floor; level 2). The badline of such a row does
  the row fetch (1 + 2 x CHRCOUNT, with the row's colour bytes copied into the colour row
  buffer in the same cycles: the colour RAM port is idle there), then starts the expansion and
  **paints at the same time**: the row's own tokens at once (they are in the buffer), and every
  object part as soon as the expander has written it. Pipeline:
  - the expander reads chip RAM only in cycles where the VIC-IV's port is idle in the next
    cycle too (`exp_ofree`: the token states that issue no read -- FetchNextCharacter, the
    high byte, the waits, a full-colour glyph's second word, EndOfChargen -- and no row fetch),
    so it never collides with the painting's glyph reads (counter 7 stays 0);
  - the mono glyph byte is latched when it arrives (`mono_l`) instead of held on the port, so
    the expander may use the port during the painting of a plain glyph;
  - the token readers wait at the expander's write position: a buffer index at or beyond it is
    read only after the expander has written it plus one (`rd_ok`: the old path waits in
    FrontWait, the fast token path's decoder stalls in D0 on the write position one cycle
    later, `exp_wpos_d`: an expander byte is in the row buffer two cycles after its write
    strobe, and D0 reads in the cycle it decides), and the end of the expansion's `$FFFF` ends
    the row as before.
  Expansion per part is unchanged (17 cycles per one-glyph object, 13 for a second part), but
  it is now hidden behind painting: the badline ends ~fetch + expansion + the last part's
  painting. Measured in GHDL (`tb_viciv_lead`, V400, 48-token row of 24 GOTOX + 24 FCM, bits 4
  and 7, one-glyph full-colour objects all in row 10):

  | objects | expansion | badline painting (counter 25) | other lines | plain parts | was (step 4) |
  |---|---|---|---|---|---|
  | 64 | 1258 | **1371** | 1151 | 1576 | 2350 |
  | 128 | 2346 | **2459** (one line, 2592) | 1983 | 2728 (DBLRR; loses badlines in one line) | lost line |
  | 231 (DBLRR V400) | 4097 | **4210** (two lines, 5184) | 3322 | 4582 | -- |
  | 255 (DBLRR V400, 2 KB) | 4505 | **4618** | 3634 | (does not fit 1 KB) | -- |

  So a pile of objects now costs less than the same parts written as plain GOTOX tokens (the
  expansion runs in the reader's idle cycles); pictures are identical (GHDL, all four sizes).
  Expander alone (`tb_rrb_list_v2` timing cases, MLAT 3): 64 parts 1095 cycles, 128 parts
  2183, 231 parts 3933, 255 parts 4342 (2 KB; MLAT 4: 4597). Without the fast token path (bit 7
  off) the painting itself is the limit: 64 objects need 2591 in one line (lost; on DBLRR 2847,
  pixel-exact), 255 objects on DBLRR 5183 (cut off) -- use bit 7 for piles.
- **Colour bytes of list rows** come from the colour row buffer, also the row's own tokens (copied
  at the badline, like the screen bytes): a colour RAM write to the row after its badline shows
  on the next frame, as the screen bytes always did. Rows without list mode read colour RAM
  while painting, as upstream.
- Raster effects: with list mode on, paint-time registers take effect one raster line later
  (write them one line earlier), see "First raster line budget".
- BRAM: the colour row buffer and the 2 KB list-row screen buffer cost no new block RAM
  (build after step 5: 421.5 of 445 tiles, 63.2k LUTs, WNS +0.028 ns); the lead needs none.
  Level 3: the table snapshot (1024 objects in three block RAMs by reader: X/Y/mask/height
  read by both the scan and the pass, 1 RAMB36; the collision fields, 1; the drawing fields,
  2) and the buckets (2048 x 10 bits, 1 RAMB36), replacing two LUT RAMs (the 512 x 64 table,
  the 256 x 64 box RAM): build after step 6 425.5 of 445 tiles (+4), 69.2k LUTs, WNS +0.275 ns.

## Diagnostic counters

Hardware counters in the VIC-IV (pixel clock, 81 MHz; `rrb_list_frontend.vhdl`) for the list mode path, read through the
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
| 7 | port collisions: expander read in a cycle where the VIC-IV reads too (must stay 0). With 4 px painting (XCTL2 bit 0) a GOTOX's PaintMemWait/PaintMemWait2 on the fast token path are not VIC-IV reads: its glyph data is never used, and the expander's address always wins the port in those cycles (`exp_mreq` overrides `next_ramaddress`) |
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

- GHDL collisions (`sim/tb_rrb_collide.vhdl` in `run_sim.sh`, MLAT 3 and 4): the pass against a
  model -- designed boxes (overlap, touching edges, groups, mask 0, group 0, negative Y, REL,
  widths, heights), six random tables of 60 objects, 255 overlapping objects (and the pass's
  cycle budget), double buffering (the old result stays readable during a pass), a pass on the
  held snapshot, a copy requested during a pass (waits), 0 and 1 objects, collides-with masks
  (+15: asymmetric masks, 0 = legacy; random tables with +15); level 3: circles (circle/circle,
  circle/box side and corner), overlaps only above row 0 (not reported), tall objects sharing
  63 rows (one pair), 1023 random objects with circles, the pair budget (truncated: a subset).
  `sim/tb_soft_rtc.vhdl` also: the `$FFD72xx` page (1024 bits, 10-bit index / partner / count,
  budget, count registers, status bits 2/3, level 3, no aliasing of `$FFD71xx`).
  `sim/tb_soft_rtc.vhdl`: the CPU side (copy on the toggle, `$FFD71A0-C3`, partner `$FF`,
  latched bit 0, IRQ line, acknowledge, reset).
- GHDL (`sim/run_sim.sh`, MLAT 3 and 4): the expansion FSM with a fake screen/colour RAM
  (token streams in, buffer contents out), including REL, FLIPX/FLIPY, empty lists, `$FFFF`
  handling, buffer overflow and aborts; v2: copies with count 0/255 and count changes, user
  masks, REL Y, FLIPY, scans at any start index, overflow, a copy with the port only sometimes
  free; timing cases 64 / 128 / 231 / 255 parts on one row, also with the 2 KB buffer
  (`-gBUFW=11 -gLIMIT=2044`); level 3: 32-byte entries with a noise software block (never
  read), 1023 objects, rows beyond 63, the bucket capacity (bk_full) (`sim/tb_rrb_list_v2.vhdl`).
- GHDL, the whole VIC-IV (`sim/run_viciv_lead.sh`, ~6 min, not part of `run_sim.sh`): the real
  `viciv.vhdl` (report-stripped copy) with a PAL frame, a chip RAM model and fastio register /
  colour RAM writes, V400, fast RRB; frame dumps and the counters per line. Checks: plain rows
  identical with the lead on and off; plain GOTOX reference with the lead == v2 (6 objects at
  X 0/10/600, and 30 parts in one row); no late pixels with the lead; late pixels without it;
  DBLRR on the V400 schedule with `parallax65.bas`-like rows (246 tokens): the two-line lead
  changes only the first line of each pair, no late pixels, v2 with an empty table changes nothing.
  Fast token path (XCTL bit 7): pixel-identical with the old path on FCM layers with attributes,
  parallax rows, v2 objects, 30 parts and the per-token microbenchmarks.
  Bullet hell (level 2): 64 one-glyph objects on one row (V400, bit 7), 128 and 231 on DBLRR's
  schedule, 64 with bit 7 off == the same objects as plain GOTOX parts (XCTL bit 3 on: the rows
  are sparse), 128 objects within one line (bit 7), 255 within DBLRR's two lines; no late pixels,
  no port collisions (counter 7). The plain references need LINESTEP 640 / 1020: the sim copy
  widens `row_advance` to its 10 hardware bits (GHDL checks the `0 to 512` range).
  The same checks pass in V200 (`-gV400=0`, run by hand).
- Board: `scripts/rrb_list_test.py` (pixel-exact comparison of a list-mode screen with the same
  picture built with plain GOTOX tokens; v2 two-row objects, short rows, a sprite in the opened
  top border during the copy; G/G2: V400 objects at the left edge and 30 parts in a row, late
  pixel counter; G3: the same with DBLRR on the V400 schedule; H: three full-colour layers with
  the fast token path against the old path / plain parts; I: collisions against a model, the
  registers and the IRQ pending bit; I2: collides-with masks (+15); J: 64 objects on one row
  against plain parts, counter 25 < 2590; K: XCTL bit 3; L: 231 objects on one row on DBLRR's
  schedule against plain parts, 255 objects (2 KB list row) counter 25 < 5180; M: level 3 --
  1023 objects spread over the screen against plain parts and their collisions (+15 masks,
  circles) through `$FFD7200`, multi-row objects against plain parts, the pair budget, designed
  circles), then the demos.
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
- The VIC-IV reads the token after a row's last one (CHRCOUNT + 1; never painted), and upstream
  takes a `$FFFF` there as the end of a short row: the row advance becomes that position instead
  of LINESTEP. With the 2 KB list rows the slot after a plain row can hold an earlier v2
  expansion's `$FFFF` (case L's reference after its v2 picture: every row 2 bytes further on, a
  diagonal of shifted glyphs). With XCTL bit 5 or 6 on, a `$FFFF` in that slot of a plain row is
  ignored (both token paths); with both off, nothing changes. GHDL: `r231h` (PRE 1).

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
- Level 3 (32-byte entries): the v2 demos write entry n at table + 32 n (bytes 16..31 left to
  software): `rrblist65v2.bas` and `parallax65.bas` (BASIC 65; parallax65's token lists moved
  to `$5F900`), and B65's twins in b65-basic (05b3ac7). A level-2 bitstream would read them as
  16-byte objects: run them on level 3 (`PEEK($FFD71FD)` = 3).
