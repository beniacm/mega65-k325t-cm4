-- tb_viciv_lead.vhdl (run: sim/run_viciv_lead.sh) -- the real VIC-IV (viciv.vhdl, report-stripped sim copy) with a PAL
-- 720x576 frame (864 x 625, pixel strobe every 3 cycles), 1 MB chip RAM model (one cycle
-- registered read, as chipram_k325t port B), registers and colour RAM written through fastio.
-- Per raster line (frame 2) it prints the list mode diagnostic counters 23/25/26/10 (cleared
-- each line) and dumps frame 2's RGB to OUTF.
--   SC 1: reference rows (BG background tokens, then the object parts as plain GOTOX tokens,
--         then a repaint of the last background glyph), XCTL bits 5/6 off
--   SC 2: the same rows, bit 5 on (no LIST token: only the list mode lead)
--   SC 3: plain background rows, v2 object table (bit 6)
--   SC 4: plain background rows only, bits off;  SC 5: the same with bit 5 on
--   SC 6: parallax65.bas rows (V200, 3 layers, 246 tokens), bits off; SC 7: the same + v2
--         objects (OBJS 2: six one-glyph birds); D051: $D051 (DBLRR = $C0); SC 8: the rows
--         with bit 5 only (lead, no LIST token); SC 9: v2 with an empty table; SC 10: the
--         parallax rows with three full-colour layers (glyphs FCM_G..+3), bits off; SC 11: the
--         same with bit 5 (lead).  FT 1: XCTL bit 7 (fast token path).  SC 12-15: rows of 3 x
--         (GOTOX + 81 tokens) for per-token costs: all full colour / plain / GOTOX+FCM / NCM;
--         SC 16: the SC 11 layers with v2 objects instead of bit 5 (OBJS 3: 12 parts in row 11)
--         SC 17: 48 tokens = 6 x 4 x (GOTOX, FCM glyph), LINESTEP 96, v2 (OBJS 4: 64 one-glyph
--         objects in row 10).  SC 18: dense and sparse rows in turn (XCTL bit 3, CU 1: unpainted
--         pixels show as background); SC 19: the sparse rows over an opaque base of spaces.
--         RL 1: XCTL bit 5 (the list mode lead).  SC 20: the SC 17 rows with the objects
--         as plain GOTOX parts in row 10 (bit 5: the lead), the reference of SC 17 (LINESTEP
--         640, or 1020 with V400 2: 25 rows; OBJS 4/5/7, 231 objects = 510 tokens at most)
--         PRE 1: v2 (bit 6) on the rows until frame 1 line 300, then bit 5 only (history)
-- V400 2: V400 timing with 25 rows ($D05B 0, $D07B 24), the schedule DBLRR needs
-- OBJS 0: 6 two-row objects, 2 glyphs wide (case D's object), (X 0/10/600 on rows 10-12/20-22/30-32, all three on 40-42)
-- OBJS 4/5/6/7: 64/128/255/231 one-glyph, one-row objects at Y 80, X = 631 k / n (row 10)
-- OBJS 8: 1023 one-glyph, one-row objects at X = 157 k mod 625, Y = 61 k mod 392 (v3 table)
-- OBJS 1: 15 two-row objects, 1 glyph wide, at Y = 8*20+3, X = 42 k (30 parts in row 21)
-- Demo mirrors (V400, one line per painting, XCTL bits 3/4/6/7; QP 0/1):
--   SC 21 + OBJS 9: B65 examples/swarm2.bas -- rows of 20 plain tokens (spaces, a score in
--     row 0), 1001 objects: the player at (320, 200) + 1000 bullets as the demo leaves them in
--     its steady state (8 a frame from two spawners circling the centre: 6 straight at 3.5 px a
--     frame, 2 curving at 2.5 px turning 1.5 degrees a frame; 110 frames of life, the rest
--     hidden at (700, 600)); positions in the 10-bit space (off-screen X still expands)
--   SC 22 + OBJS 11: the BASIC 65 recipe OBJ65R3 -- rows of nothing but $FFFF at their start
--     (LINESTEP 80, CHRCOUNT 40: VIC-IV short rows of 2 bytes), 32 objects; PRE 2: 40 plain text
--     glyphs a row (screen at $4A000) until frame 1 line 300, then the $FFFF rows; PRE 3: solid
--     red glyphs behind each $FFFF (they get painted: the short-row rule); K2: PRE 4 = rows that
--     paint nothing (39 x GOTOX 640, $FFFF last), after text rows (screen $4A000, colour $2000)
--     until frame 1 line 300; PRE 5 = the same rows without the text (reference); PRE 6 = the
--     fixed recipe (0fa6721): every row one transparent GOTOX (X 0), CHRCOUNT 1, no $FFFF
--   SC 17 + OBJS 10: B65 examples/bounce.bas -- the 48-token star rows (6 x 4 x (GOTOX, FCM
--     glyph)), 64 balls spread over the screen
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;
use std.textio.all;
use work.victypes.all;

entity tb_viciv_lead is
  generic (SC : integer := 3; OBJS : integer := 0; V400 : integer := 1; FAST : integer := 1;
           BG : integer := 80; D051 : integer := 16#80#; FT : integer := 0; CU : integer := 0;
           RL : integer := 0; PRE : integer := 0;
           QP : integer := 0;                       -- XCTL2 bit 0: 4 px painting (step 7)
           OUTF : string := "/dev/null");
end entity;

architecture sim of tb_viciv_lead is
  signal pclk, cclk : std_logic := '0';
  signal reset : std_logic := '0';
  signal x : integer range 0 to 863 := 0;
  signal y : integer range 0 to 624 := 0;
  signal ph : integer range 0 to 2 := 0;
  signal strobe, xzero, yzero, inframe : std_logic := '0';
  signal frame : integer := 0;
  signal caddr : unsigned(19 downto 0);
  signal cdata : unsigned(7 downto 0) := x"00";
  signal cdata32 : unsigned(31 downto 0) := (others => '0');
  signal fa : std_logic_vector(19 downto 0) := (others => '0');
  signal fw, fr, ccs : std_logic := '0';
  signal fwd : std_logic_vector(7 downto 0) := (others => '0');
  signal r, g, b : unsigned(7 downto 0);
  signal rl_en, v2_en, fast_en, ft_en, cu_en, qp_en : std_logic := '0';
  signal tab_addr : unsigned(19 downto 0) := x"58000";   -- (32 KB: 1023 x 32 bytes)
  signal ocount : unsigned(9 downto 0) := (others => '0');
  signal dsel : unsigned(4 downto 0) := (others => '0');
  signal dclr : std_logic := '0';
  signal dval : unsigned(15 downto 0);
  signal setup_done : std_logic := '0';
  signal errata_en, errata_dis : std_logic := '0';
  signal rdata_dummy, crd, chrd : std_logic_vector(7 downto 0);
  signal dat_addrs : sprite_vector_eight;

  type mem_t is array (0 to 1048575) of natural range 0 to 255;
  shared variable mem : mem_t := (others => 0);

  constant SCR : natural := 16#40000#;
  function stepsel(sc : integer; v4 : integer) return natural is
  begin
    if sc = 21 then return 40;                                 -- swarm2.bas rows: 20 tokens
    elsif sc = 22 then return 80;                              -- OBJ65R3: LINESTEP 80
    elsif sc = 20 then
      if v4 = 2 then return 1020; else return 640; end if;    -- 25 x 1020 / 50 x 640 bytes
    elsif sc = 18 or sc = 19 then return 256; elsif sc = 17 then return 96; elsif sc >= 6 then return 492; else return 512; end if;
  end function;
  constant STEP : natural := stepsel(SC, V400);           -- 492: parallax65.bas rows
  constant OBJD : natural := 16#48100#;
  constant OBJDCOL : natural := 16#7F00#;
  constant FCM_G : natural := 16#1400#;
  constant ROWS : natural := 50;
  type objs_t is array (natural range <>) of integer;
  function wsel(o : integer) return natural is
  begin
    if o = 0 or o = 3 then return 2; else return 1; end if;
  end function;
  constant W : natural := wsel(OBJS);               -- glyphs per object row
begin
  pclk <= not pclk after 6 ns;                 -- ~81 MHz
  process (pclk) begin
    if rising_edge(pclk) then cclk <= not cclk; end if;   -- 40.5 MHz, in step
  end process;

  -- PAL frame: 864 x 625, x_zero for 3 pixels at x 819 (frame_generator), y_zero in line 0
  process (pclk) begin
    if rising_edge(pclk) then
      strobe <= '0';
      if ph = 2 then
        ph <= 0; strobe <= '1';
        if x = 863 then
          x <= 0;
          if y = 624 then y <= 0; frame <= frame + 1; else y <= y + 1; end if;
        else
          x <= x + 1;
        end if;
      else
        ph <= ph + 1;
      end if;
    end if;
  end process;
  xzero <= '1' when x >= 819 and x <= 821 else '0';
  yzero <= '1' when y = 0 else '0';
  inframe <= '1' when y >= 17 and y <= 592 else '0';

  -- chip RAM port B: registered read
  process (pclk) begin
    if rising_edge(pclk) then
      cdata <= to_unsigned(mem(to_integer(caddr)), 8);
      for b in 0 to 3 loop                       -- the aligned word (chipram_k325t dob32)
        cdata32(8 * b + 7 downto 8 * b) <= to_unsigned(mem(to_integer(caddr(19 downto 2) & "00") + b), 8);
      end loop;
    end if;
  end process;

  dut : entity work.viciv
    generic map (chipram_size => 1048576, colour_ram_64k => false)
    port map (all_pause => '0', hypervisor_mode => '0', hw_errata_level => x"00",
              hw_errata_enable_toggle => errata_en, hw_errata_disable_toggle => errata_dis,
              pixelclock => pclk, cpuclock => cclk, reset => reset, dd00_bits => "11",
              touch_x => (others => '0'), touch_y => (others => '0'),
              led => '0', motor => '0', xray_mode => '0',
              dat_bitplane_addresses => dat_addrs,
              external_frame_x_zero => xzero, external_frame_y_zero => yzero,
              external_pixel_strobe_in => strobe, vga_in_frame => inframe,
              vgared => r, vgagreen => g, vgablue => b,
              chipram_address => caddr, chipram_datain => cdata, chipram_datain32 => cdata32,
              fastio_addr => fa, fastio_read => fr, fastio_write => fw, fastio_wdata => fwd,
              fastio_rdata => rdata_dummy, colour_ram_fastio_rdata => crd, colour_ram_cs => ccs,
              fast_rrb_en => fast_en, rrb_list_en => rl_en, rrb_v2_en => v2_en, fast_token_en => ft_en,
              clear_unpainted_en => cu_en, quad_px_en => qp_en,
              obj_tab_addr => tab_addr, obj_count => ocount, obj_hold => '0',
              diag_sel => dsel, diag_clr => dclr, diag_val => dval,
              charrom_fastio_rdata => chrd, charrom_cs => '0',
              iomode_set => "11", iomode_set_toggle => '0');

  stim : process
    procedure wr(a : natural; d : natural; colour : boolean := false) is
    begin
      wait until rising_edge(cclk);
      fa <= std_logic_vector(to_unsigned(a, 20)); fwd <= std_logic_vector(to_unsigned(d, 8));
      fw <= '1';
      if colour then ccs <= '1'; else ccs <= '0'; end if;
      wait until rising_edge(cclk);
      fw <= '0'; ccs <= '0';
    end procedure;
    procedure reg(n : natural; d : natural) is
    begin
      wr(16#D3000# + n, d);
    end procedure;
    procedure w16(variable p : inout natural; v : natural) is
    begin
      mem(p) := v mod 256; mem(p + 1) := v / 256; p := p + 2;
    end procedure;
    procedure c16(variable p : inout natural; lo, hi : natural) is
    begin
      wr(16#80000# + p, lo, true); wr(16#80000# + p + 1, hi, true); p := p + 2;
    end procedure;
    variable sp, cp, ntok, nt : natural;
    variable ox, oy : objs_t(0 to 1022);
    variable hgt : integer;
    variable no : natural;
    variable k, o, rr, mask : integer;
    variable tp : natural;
    variable fl : natural;

    variable fr0, ag, ix, cs, ai, bi : integer;
    variable px, py, th : real;
  begin
    -- objects
    if OBJS = 9 then
      -- swarm2.bas at frame 2000: object 0 the player, 1 + 8 a + s the bullet fired a frames ago
      no := 1001;
      ox(0) := 320; oy(0) := 200;
      for a in 0 to 124 loop
        fr0 := 2000 - a;                                -- the frame it was fired in
        cs := (fr0 / 2) mod 64;
        for sv in 0 to 7 loop
          ix := 1 + 8 * a + sv;
          if a >= 110 then
            ox(ix) := 700; oy(ix) := 600;               -- its life is over: hidden
          elsif sv < 6 then                             -- straight, A advances by 11
            ai := (11 * (6 * fr0 + sv + 1)) mod 64;
            px := 320.0 + 40.0 * floor(3.5 * cos(real(cs) * MATH_PI / 32.0) + 0.5)
                  + real(a) * floor(3.5 * cos(real(ai) * MATH_PI / 32.0) + 0.5);
            py := 200.0 + 25.0 * floor(3.5 * sin(real(cs) * MATH_PI / 32.0) + 0.5)
                  + real(a) * floor(3.5 * sin(real(ai) * MATH_PI / 32.0) + 0.5);
            ox(ix) := integer(floor(px)) mod 1024; oy(ix) := integer(floor(py)) mod 1024;
          else                                          -- curving (OBJMOVE 2.5, B * 5.625, 1.5)
            bi := (7 * (2 * fr0 + sv - 5)) mod 64;
            px := 640.0 - (320.0 + 40.0 * floor(3.5 * cos(real(cs) * MATH_PI / 32.0) + 0.5));
            py := 400.0 - (200.0 + 25.0 * floor(3.5 * sin(real(cs) * MATH_PI / 32.0) + 0.5));
            th := real(bi) * 5.625;
            for t in 0 to a - 1 loop
              px := px + 2.5 * cos(th * MATH_PI / 180.0);
              py := py + 2.5 * sin(th * MATH_PI / 180.0);
              th := th + 1.5;
            end loop;
            ox(ix) := integer(floor(px)) mod 1024; oy(ix) := integer(floor(py)) mod 1024;
          end if;
        end loop;
      end loop;
    elsif OBJS = 11 then
      -- OBJ65R3: 32 objects
      no := 32;
      for i in 0 to no - 1 loop ox(i) := 80 + (i * 97) mod 520; oy(i) := 16 + (i * 53) mod 360; end loop;
    elsif OBJS = 10 then
      -- bounce.bas: 64 balls spread over the screen
      no := 64;
      for i in 0 to no - 1 loop ox(i) := (i * 151 + 37) mod 632; oy(i) := (i * 97 + 11) mod 392; end loop;
    elsif OBJS = 8 then
      -- v3: 1023 one-glyph, one-row objects spread over rows 0..49 (about 40 parts per row)
      no := 1023;
      for i in 0 to no - 1 loop ox(i) := (i * 157) mod 625; oy(i) := (i * 61) mod 392; end loop;
    elsif OBJS >= 4 then
      -- 64 / 128 / 255 / 231 one-glyph, one-row objects all in row 10 (a pile on one row)
      if OBJS = 4 then no := 64; elsif OBJS = 5 then no := 128; elsif OBJS = 7 then no := 231;
      else no := 255; end if;
      for i in 0 to no - 1 loop ox(i) := (i * 631) / no; oy(i) := 80; end loop;
    elsif OBJS = 3 then
      -- six two-row objects at Y = 8*10 + 3: 12 parts in row 11 (rrb_list_test.py case H)
      no := 6;
      for i in 0 to 5 loop ox(i) := 40 + 100 * i; oy(i) := 83; end loop;
    elsif OBJS = 2 then
      -- parallax65.bas birds: six one-row, one-glyph objects
      no := 6;
      for i in 0 to 5 loop ox(i) := 40 + 100 * i; oy(i) := 30 + 12 * i; end loop;
    elsif OBJS = 0 then
      no := 6;
      ox(0 to 5) := (0, 10, 600, 0, 10, 600);
      oy(0 to 5) := (8*10+3, 8*20+3, 8*30+3, 8*40+3, 8*40+3, 8*40+3);
    else
      no := 15;
      for i in 0 to 14 loop ox(i) := 42 * i; oy(i) := 8*20+3; end loop;
    end if;
    -- charset at $54000 ($D068): 160 solid, 32 blank, others a pattern per glyph and row
    for gl in 0 to 255 loop
      for yy in 0 to 7 loop
        if gl = 160 then mem(16#54000# + 8 * gl + yy) := 255;
        elsif gl = 32 then mem(16#54000# + 8 * gl + yy) := 0;
        else mem(16#54000# + 8 * gl + yy) := (gl * 37 + yy * 11 + 5) mod 256; end if;
      end loop;
    end loop;
    -- full-colour glyphs FCM_G..+3: every pixel row its own colour
    for gl in 0 to 3 loop
      for yy in 0 to 7 loop
        for xx in 0 to 7 loop
          if SC >= 10 then                       -- per pixel, some transparent (0)
            if (xx + yy + gl) mod 5 = 0 then mem(FCM_G * 64 + 64 * gl + 8 * yy + xx) := 0;
            else mem(FCM_G * 64 + 64 * gl + 8 * yy + xx) := 1 + (3 * gl + yy + 5 * xx) mod 15; end if;
          else
            mem(FCM_G * 64 + 64 * gl + 8 * yy + xx) := 1 + (3 * gl + yy) mod 15;
          end if;
        end loop;
      end loop;
    end loop;
    -- object data: W glyphs per object row (row 0: FCM_G.., row 1: FCM_G+2..), $FFFF each
    tp := OBJD;
    for gl in 0 to W - 1 loop w16(tp, FCM_G + gl); end loop;
    w16(tp, 16#FFFF#);
    for gl in 0 to W - 1 loop w16(tp, FCM_G + 2 + gl); end loop;
    w16(tp, 16#FFFF#);
    -- object table (v3): 32-byte entries, the VIC-IV reads bytes 0..15 (16..31: noise here)
    for i in 0 to no - 1 loop
      for b in 16 to 31 loop mem(16#58000# + 32 * i + b) := (7 * i + b) mod 256; end loop;
      tp := 16#58000# + 32 * i;
      w16(tp, ox(i)); w16(tp, oy(i)); mem(tp) := 255;
      if OBJS = 2 or OBJS >= 4 then mem(tp + 1) := 1; else mem(tp + 1) := 2; end if;   -- height
      mem(tp + 2) := (OBJD / 65536) mod 16; mem(tp + 3) := 0; tp := tp + 4;
      w16(tp, OBJD mod 65536); w16(tp, OBJDCOL); w16(tp, 2 * W + 2); w16(tp, 0);
    end loop;
    ocount <= to_unsigned(no, 10);

    reset <= '0';
    for i in 0 to 20 loop wait until rising_edge(cclk); end loop;
    reset <= '1';
    for i in 0 to 20 loop wait until rising_edge(cclk); end loop;

    -- object colour bytes: glyph colour byte 0 = 0, 1 = 1
    for i in 0 to 2 * (2 * W + 2) - 1 loop
      if i mod 2 = 1 and (i mod (2 * W + 2)) < 2 * W then wr(16#80000# + OBJDCOL + i, 1, true);
      else wr(16#80000# + OBJDCOL + i, 0, true); end if;
    end loop;

    -- rows: screen at SCR + STEP r, colour at STEP r
    ntok := BG;
    if SC = 18 or SC = 19 then
      -- XCTL bit 3: dense rows (80 solid glyphs) and sparse rows (a few glyphs behind GOTOX
      -- tokens) in turn; SC 19 = the sparse rows over an opaque base of 80 spaces (reference)
      ntok := 92;
      for r in 0 to ROWS - 1 loop
        sp := SCR + STEP * r; cp := STEP * r;
        nt := 0;
        if r mod 2 = 0 or SC = 19 then
          for i in 0 to 79 loop
            if r mod 2 = 0 then w16(sp, 160); else w16(sp, 32); end if;
            c16(cp, 0, 1 + (i + r) mod 15); nt := nt + 1;
          end loop;
        end if;
        if r mod 2 = 1 then
          w16(sp, 100); c16(cp, 16#10#, 255);
          for i in 0 to 2 loop w16(sp, 1 + i + r mod 20); c16(cp, 0, 2 + i); end loop;
          w16(sp, 400); c16(cp, 16#10#, 255);
          for i in 0 to 1 loop w16(sp, 160); c16(cp, 0, 5 + i); end loop;
          w16(sp, 632); c16(cp, 16#10#, 255);
          w16(sp, 160); c16(cp, 0, 7);
          nt := nt + 9;
        end if;
        while nt < ntok loop                      -- pad: GOTOX to the row's end (paints nothing)
          w16(sp, 640); c16(cp, 16#10#, 255); nt := nt + 1;
        end loop;
      end loop;
    elsif SC = 22 then
      -- OBJ65R3: every row is $FFFF at its start (no tokens); PRE 2's text rows at $48000
      ntok := 40;
      if PRE = 6 then ntok := 1; end if;          -- (the fixed recipe: one token a row)
      for r in 0 to ROWS - 1 loop
        sp := SCR + STEP * r; cp := STEP * r;
        if PRE = 6 then                            -- one transparent GOTOX (X 0), no $FFFF
          w16(sp, 0); c16(cp, 16#90#, 255);
        end if;
        if PRE = 4 or PRE = 5 then                 -- (probe: 39 x GOTOX 640, $FFFF last)
          for i in 0 to 38 loop w16(sp, 640); end loop;
        end if;
        if PRE /= 6 then w16(sp, 16#FFFF#); end if;
        if PRE = 3 then                            -- (probe: solid glyphs behind the $FFFF)
          for i in 1 to 39 loop w16(sp, 160); end loop;
        end if;
        if PRE = 2 or PRE = 4 then
          sp := 16#4A000# + STEP * r;
          for i in 0 to 39 loop w16(sp, 1 + (i + r) mod 26); end loop;   -- letters
        end if;
      end loop;
      if PRE = 3 then                              -- (probe: colour 2, red, for them)
        for r in 0 to ROWS - 1 loop
          cp := STEP * r;
          for i in 0 to 39 loop c16(cp, 0, 2); end loop;
        end loop;
      end if;
      if PRE = 4 or PRE = 5 then                   -- (probe: the GOTOX colour bytes)
        for r in 0 to ROWS - 1 loop
          cp := STEP * r;
          for i in 0 to 38 loop c16(cp, 16#10#, 255); end loop;
        end loop;
      end if;
      if PRE = 4 then                              -- (probe: the history text's colour, at $2000)
        for r in 0 to ROWS - 1 loop
          cp := 16#2000# + STEP * r;
          for i in 0 to 39 loop c16(cp, 0, 1 + (i + r) mod 15); end loop;
        end loop;
      end if;
      if PRE = 2 then                              -- colour for the text rows (also at STEP r)
        for r in 0 to ROWS - 1 loop
          cp := STEP * r;
          for i in 0 to 39 loop c16(cp, 0, 1 + (i + r) mod 15); end loop;
        end loop;
      end if;
    elsif SC = 21 then
      -- swarm2.bas: 20 plain tokens a row (spaces; row 0: a score), LINESTEP 40, XCTL bit 3
      ntok := 20;
      for r in 0 to ROWS - 1 loop
        sp := SCR + STEP * r; cp := STEP * r;
        for i in 0 to 19 loop
          if r = 0 and i < 12 then w16(sp, 1 + (i * 7) mod 26); else w16(sp, 32); end if;
          c16(cp, 0, 1);
        end loop;
      end loop;
    elsif SC = 17 or SC = 20 then
      -- step 4: 48 tokens = 6 groups of 4 x (GOTOX, one full-colour glyph), LINESTEP 96;
      -- SC 20: + the objects of row 10 as plain parts (GOTOX X, the object's glyph), padded
      ntok := 48;
      if SC = 20 then ntok := 48 + 2 * no; end if;
      for r in 0 to ROWS - 1 loop
        exit when SC = 20 and V400 = 2 and r >= ROWS / 2;
        sp := SCR + STEP * r; cp := STEP * r;
        for g in 0 to 5 loop
          for t in 0 to 3 loop
            w16(sp, 100 * g + 20 * t); c16(cp, 16#10#, 255);
            w16(sp, FCM_G + t); c16(cp, 0, 1 + (g + t + r) mod 15);   -- as rrb_list_test.py case L
          end loop;
        end loop;
        if SC = 20 then
          nt := 48;
          if r = 10 then
            for i in 0 to no - 1 loop
              w16(sp, ox(i) + 4096); c16(cp, 16#10#, 255);    -- top part, Y offset 0
              w16(sp, FCM_G); c16(cp, 0, 1);
              nt := nt + 2;
            end loop;
          end if;
          while nt < ntok loop
            w16(sp, 640); c16(cp, 16#10#, 255); nt := nt + 1;
          end loop;
        end if;
      end loop;
    elsif SC >= 6 then
      -- parallax65.bas rows: 3 layers = GOTOX (fine X) + 81 normal glyphs, 246 tokens, 492 bytes;
      -- GOTOX colour byte 0 $50 (behind), $D0 (transparent + behind), $90 (transparent)
      ntok := 246;
      for r in 0 to 24 loop
        sp := SCR + STEP * r; cp := STEP * r;
        for ly in 0 to 2 loop
          if ly = 0 then w16(sp, 0); else w16(sp, 3 + ly); end if;   -- layer 0 covers X 0..
          if ly = 0 then c16(cp, 16#50#, 255); elsif ly = 1 then c16(cp, 16#D0#, 255);
          else c16(cp, 16#90#, 255); end if;
          for i in 0 to 80 loop
            if SC = 12 then                     -- microbenchmarks: all full colour
              w16(sp, FCM_G + i mod 4); c16(cp, 0, 1);
            elsif SC = 13 then                  -- all plain
              w16(sp, 160); c16(cp, 0, 1);
            elsif SC = 14 then                  -- GOTOX + full-colour glyph pairs
              if i mod 2 = 0 then w16(sp, 8 * i); c16(cp, 16#90#, 255);
              else w16(sp, FCM_G + i mod 4); c16(cp, 0, 1); end if;
            elsif SC = 15 then                  -- all NCM (16 pixels)
              w16(sp, FCM_G + i mod 4); c16(cp, 8, 1);
            elsif SC >= 10 then                 -- full-colour layers, attributes mixed in
              if (i + ly) mod 9 = 8 then
                w16(sp, 160);                   -- a plain glyph
              elsif (i + r) mod 19 = 6 then
                w16(sp, FCM_G + (i + r + ly) mod 4 + 16#4000#);   -- width trim 2
              else
                w16(sp, FCM_G + (i + r + ly) mod 4);
              end if;
              fl := 0;
              if (i + r) mod 5 = 1 then fl := fl + 64; end if;       -- FLIPX
              if (i + r) mod 7 = 2 then fl := fl + 128; end if;      -- FLIPY
              if (i + ly) mod 13 = 4 then fl := fl + 32; end if;     -- alpha
              if (i + r + ly) mod 17 = 3 then fl := fl + 8; end if;  -- NCM (16 pixels)
              c16(cp, fl, 1 + (ly * 5 + r + i) mod 15);
            elsif ly = 0 then w16(sp, 160);                   -- solid
            elsif (i + r + ly) mod 3 = 0 then w16(sp, 1 + (i + r) mod 26);   -- letters
            else w16(sp, 32); end if;                          -- space: transparent
            if SC < 10 then c16(cp, 0, 1 + (ly * 5 + r) mod 15); end if;
          end loop;
        end loop;
      end loop;
    end if;
    -- object rows of the reference parts (one-row objects for OBJS 4..8)
    if OBJS >= 4 then hgt := 1; else hgt := 2; end if;
    if SC <= 2 then
      -- reference rows: count the longest
      for r in 0 to ROWS - 1 loop
        nt := BG + 2;
        for i in 0 to no - 1 loop
          k := r - oy(i) / 8; o := oy(i) mod 8;
          if k >= 0 and k < hgt then nt := nt + 1 + W; end if;
          if o /= 0 and k - 1 >= 0 and k - 1 < hgt then nt := nt + 1 + W; end if;
        end loop;
        if nt > ntok then ntok := nt; end if;
      end loop;
    end if;
    for r in 0 to ROWS - 1 loop
      exit when SC >= 6;
      sp := SCR + STEP * r; cp := STEP * r;
      for i in 0 to BG - 1 loop
        w16(sp, 160);
        if i mod 2 = 1 then c16(cp, 0, 6); else c16(cp, 0, 14); end if;
      end loop;
      if SC <= 2 then
        nt := BG;
        for i in 0 to no - 1 loop
          k := r - oy(i) / 8; o := oy(i) mod 8;
          -- top part of object row k: Y offset o down (sign bit), mask $FF << o
          if k >= 0 and k < hgt then
            mask := (255 * (2 ** o)) mod 256;
            w16(sp, ox(i) + 4096 + o * 8192);
            if mask /= 255 then c16(cp, 16#18#, mask); else c16(cp, 16#10#, mask); end if;
            for gl in 0 to W - 1 loop w16(sp, FCM_G + 2 * k + gl); c16(cp, 0, 1); end loop;
            nt := nt + 1 + W;
          end if;
          -- bottom part of object row k-1: (8-o) up, mask (1 << o) - 1
          if o /= 0 and k - 1 >= 0 and k - 1 < hgt then
            mask := 2 ** o - 1;
            w16(sp, ox(i) + ((8 - o) mod 8) * 8192);
            c16(cp, 16#18#, mask);
            for gl in 0 to W - 1 loop w16(sp, FCM_G + 2 * (k - 1) + gl); c16(cp, 0, 1); end loop;
            nt := nt + 1 + W;
          end if;
        end loop;
        while nt < ntok - 2 loop
          w16(sp, 8 * (BG - 1)); c16(cp, 16#10#, 255); nt := nt + 1;
        end loop;
        w16(sp, 8 * (BG - 1)); c16(cp, 16#10#, 255);
        w16(sp, 160);
        if (BG - 1) mod 2 = 1 then c16(cp, 0, 6); else c16(cp, 0, 14); end if;
      end if;
    end loop;

    -- VIC-IV registers (as the ROM sets them for PAL 80 x 25, then V400 / CHR16)
    reg(16#6F#, 0);                         -- PAL
    reg(16#5D#, 0);                         -- HOTREG off, side border MSB 0
    reg(16#5C#, 80);                        -- side border
    reg(16#4C#, 80); reg(16#4D#, 0);        -- TEXTXPOS
    reg(16#4E#, 104); reg(16#4F#, 0);       -- TEXTYPOS
    reg(16#48#, 104); reg(16#49#, 0);       -- TBDRPOS
    reg(16#4A#, 504 mod 256); reg(16#4B#, 1);   -- BBDRPOS
    reg(16#5A#, 120);                       -- CHRXSCL
    reg(16#11#, 16#1B#); reg(16#16#, 16#C8#);
    reg(16#20#, 0); reg(16#21#, 11);
    if V400 = 1 then
      reg(16#31#, 16#88#); reg(16#5B#, 0); reg(16#7B#, ROWS - 1);
    elsif V400 = 2 then                     -- DBLRR's schedule: V400 timing, 25 rows of 2 x 8
      reg(16#31#, 16#88#); reg(16#5B#, 0); reg(16#7B#, ROWS / 2 - 1);
    else
      reg(16#31#, 16#80#); reg(16#5B#, 1); reg(16#7B#, ROWS / 2 - 1);
    end if;
    reg(16#54#, 16#05#);                    -- CHR16, FCLRHI
    reg(16#58#, STEP mod 256); reg(16#59#, STEP / 256);
    reg(16#5E#, ntok mod 256);
    reg(16#60#, SCR mod 256); reg(16#61#, (SCR / 256) mod 256); reg(16#62#, (SCR / 65536) mod 256);
    reg(16#63#, ((ntok / 256) mod 4) * 16);
    reg(16#64#, 0); reg(16#65#, 0);         -- colour RAM offset 0
    reg(16#68#, 0); reg(16#69#, 16#40#); reg(16#6A#, 5);   -- charset $54000
    if FAST = 1 then fast_en <= '1'; end if;
    if FT = 1 then ft_en <= '1'; end if;
    if QP = 1 then qp_en <= '1'; end if;
    if CU = 1 then cu_en <= '1'; end if;
    if RL = 1 then rl_en <= '1'; end if;
    if SC = 2 or SC = 5 or SC = 8 or (SC >= 11 and SC /= 16 and SC /= 17) then rl_en <= '1'; end if;
    if SC = 3 or SC = 7 or SC = 9 or SC = 16 or SC = 17 or SC = 21 or SC = 22 then v2_en <= '1'; end if;
    if SC = 9 then ocount <= (others => '0'); end if;
    reg(16#51#, D051);                      -- NORRDEL (bit 7), DBLRR (bit 6)
    setup_done <= '1';
    if PRE = 2 or PRE = 4 then
      -- OBJ65R3 history: the text rows (screen at $48000) until frame 1 line 300
      reg(16#60#, 0); reg(16#61#, 16#A0#); reg(16#62#, 4);
      if PRE = 4 then reg(16#64#, 0); reg(16#65#, 16#20#); end if;
      wait until frame = 1 and y = 300;
      if PRE = 4 then reg(16#64#, 0); reg(16#65#, 0); end if;
      reg(16#60#, SCR mod 256); reg(16#61#, (SCR / 256) mod 256); reg(16#62#, (SCR / 65536) mod 256);
    end if;
    if PRE = 1 then
      -- history: v2 (bit 6) on these rows during frame 0 and part of frame 1, then bit 5 only
      v2_en <= '1'; rl_en <= '0';
      wait until frame = 1 and y = 300;
      v2_en <= '0'; rl_en <= '1';
    end if;
    wait;
  end process;

  -- per line (frame 2, before this line's fetch start at x ~677): counters, then clear
  mon : process
    variable l : line;
    variable v23, v25, v26, v10, v7 : natural;
    procedure rd(n : natural; variable v : out natural) is
    begin
      dsel <= to_unsigned(n, 5);
      for i in 0 to 5 loop wait until rising_edge(pclk); end loop;
      v := to_integer(dval);
    end procedure;
  begin
    wait until frame = 2 and y = 1;
    loop
      wait until rising_edge(pclk) and x = 600 and ph = 0;
      rd(23, v23); rd(25, v25); rd(26, v26); rd(10, v10); rd(7, v7);
      dclr <= not dclr;
      write(l, string'("L ")); write(l, y); write(l, string'(" late ")); write(l, v23);
      write(l, string'(" paint ")); write(l, v25); write(l, string'(" obj ")); write(l, v26);
      write(l, string'(" exp ")); write(l, v10);
      write(l, string'(" col ")); write(l, v7);
      writeline(output, l);
      exit when y >= 600;
    end loop;
    wait;
  end process;

  dump : process
    file f : text open write_mode is OUTF;
    variable l : line;
    function hx(v : unsigned(7 downto 0)) return string is
      constant h : string(1 to 16) := "0123456789ABCDEF";
    begin
      return h(to_integer(v(7 downto 4)) + 1) & h(to_integer(v(3 downto 0)) + 1);
    end function;
  begin
    wait until frame = 2 and y = 17;
    while y <= 592 loop
      wait until rising_edge(pclk);
      if strobe = '1' then
        if x = 0 then
          write(l, y); write(l, string'(" "));
        end if;
        write(l, hx(r) & hx(g) & hx(b));
        if x = 863 then writeline(f, l); end if;
      end if;
    end loop;
    assert false report "TB_DONE" severity failure;
    wait;
  end process;
end architecture;
