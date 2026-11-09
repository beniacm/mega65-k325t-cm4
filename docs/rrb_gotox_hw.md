# RRB GOTOX tokens on the hardware (K325T core, `viciv.vhdl`)

What the VIC-IV does with a GOTOX token and the glyph tokens after it in a CHR16 row (read from
the RTL; the list mode expander builds its object parts from the same tokens, so the GHDL suite
covers these paths pixel-exactly). Xemu models neither the Y offset nor the row mask.

**Scope.** A GOTOX sets everything below for the glyphs that follow it, up to the next GOTOX in
the row. At the start of each row: X = 0, Y offset 0, row mask `$FF`, opaque, not behind.

## 1. Screen word (16 bits)

| bits | meaning |
|---|---|
| 9..0 | X: the next glyph's first pixel goes to X (raster buffer position) |
| 11..10 | unused by GOTOX (REL is a list mode *table* flag, not a token bit) |
| 12 | Y offset sign: 0 = shift the glyphs **up**, 1 = shift them **down** |
| 15..13 | Y offset magnitude, 0..7 pixel rows |

The offset changes only the address of the glyph row that is fetched: for display row r (0..7
inside the character row) the VIC-IV fetches source row `r + offset` (bit 12 = 0) or
`r - offset` (bit 12 = 1) of the glyph, as one row index `glyph * 8 + row`. Past row 7 or below
row 0 it simply reads the **next or previous glyph's data** (glyph number + 1 / - 1, FCM: the
neighbouring 64-byte block). Nothing is blanked automatically; that is the row mask's job.
**Only full-colour and NCM glyphs use the offset**: plain (mono / multicolour) glyphs ignore it.
FLIPY applies before the offset (`7 - r` +/- offset).

## 2. Colour byte 0 of the GOTOX

| bit | meaning for the glyphs that follow |
|---|---|
| 7 | **transparent**: background pixels are not written (1), or written in the background colour (0, opaque) |
| 6 | **behind**: their pixels are marked background (sprites go in front) |
| 4 | GOTOX (this token is a GOTOX) |
| 3 | **ROWMASK**: colour byte 1 is the row mask (0: mask `$FF`) |
| 2 | background pixels are marked foreground (sprite priority only) |
| 5, 1, 0 | no effect |

A *background pixel* is FCM byte 0, NCM nibble 0, a mono 0 bit, or **any pixel of a masked
row** (the row's data is forced to 0). FCM `$FF` / NCM `$F` are the glyph colour, other values
are palette entries. So an **opaque** group (bit 7 = 0) paints the background colour (the screen
colour, or the ECM colour of the glyph) over earlier layers wherever its glyphs are 0 *and* in
all its masked rows. A transparent group leaves those pixels as earlier layers painted them.
Without ROWMASK, colour byte 1 bits 5 + 6 both set switch the following glyphs to the alternate
palette.

## 3. Colour byte 1 = row mask (with bit 3)

Bit n set = pixel row n of the character row is drawn, clear = masked (treated as background,
section 2). The index is the **display** row r, not the shifted source row.

## 4. XCTL bits 3, 4, 7 and XCTL2 bit 0

Bit 4 (2 px), bit 7 (fast token path) and XCTL2 bit 0 (4 px) change only timing: same offset,
mask and transparency rules, pixel-identical in GHDL (FLIPX/Y, alpha, NCM, width trim, offsets,
masks). Bit 3: pixels that **no** token wrote in this line show the screen colour instead of an
earlier line's stale pixels; it changes nothing between layers of the same line.

## 5. A group masked away entirely

Transparent: nothing is written; the earlier layer's pixels stay (with nothing earlier: screen
colour with bit 3, stale buffer content without). Opaque: every glyph cell of the group is
painted in the background colour, also over earlier layers.

## Worked example: 1-pixel vertical scroll of a text overlay by S (1..7)

Base layer first (opaque), then per text line L, in this order:

1. **Set 1**: GOTOX X, colour byte 0 `$98` (transparent + GOTOX + ROWMASK), colour byte 1 =
   rows 0..7-S (`$FF >> S`), screen bits 15..13 = S, bit 12 = 0 (up); then line L's glyphs.
   Rows 0..7-S show glyph rows S..7; rows 8-S..7 would show glyph+1's top rows, masked.
2. **Set 2**: GOTOX X, `$98`, colour byte 1 = rows 8-S..7 (`$FF << (8-S)`), offset 8-S, bit
   12 = 1 (down); then line L+1's glyphs. Rows 8-S..7 show their rows 0..S-1; the rows above
   would show glyph-1's bottom rows, masked.

With both groups **transparent** this is a clean scroll on the hardware, for FCM/NCM glyphs. If
set 2 were opaque, its masked rows 0..7-S would paint the background over set 1's text; an
opaque set 1 would do the same to the base layer in rows 8-S..7. Mono glyphs do not move at all
(they ignore the offset). S = 0 needs set 1 only (offset 0, mask `$FF`). Each set is one GOTOX
plus its glyphs, so a scrolled line costs one extra GOTOX and a second copy of the line's tokens.
