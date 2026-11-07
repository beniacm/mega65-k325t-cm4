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

## Tests

- GHDL: the expansion FSM with a fake screen/colour RAM (token streams in, buffer contents out),
  including REL, FLIPX/FLIPY, empty lists, `$FFFF` handling and buffer overflow.
- Board: `scripts/rrb_list_test.py` (pixel-exact comparison of a list-mode screen with the same
  picture built with plain GOTOX tokens), then the demos.
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
  VIC-IV) removes this.
- An expansion still running at the next raster line is cut off and the row painted plainly;
  the colour RAM port is always released at the end of an expansion.
- Tests must stop the CPU (monitor `t1`): BASIC 65 rewrites VIC-IV registers while it runs.

## Demos

- `scripts/demos/rrblist65.bas` -- BASIC 65: 6 full-colour balls over an 80-column starfield,
  two list entries per ball, moved with WPOKE (type it in; m65d cannot type `^`, so powers of
  two come from a table).
- `scripts/demos/rrblist_b65.bas` -- the same in B65 BASIC (type it in **lower case**: B65
  swaps the case of letters like a BBC Micro). Screen rows at `$80000`, ball glyphs at `$60000`
  (B65 uses `$40000-$5FFFF`; full-colour glyphs must stay below 512 KB).
