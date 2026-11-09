-- tb_rrb_collide.vhdl -- GHDL test of rrb_list_expander's collision pass (list mode v3, 32-byte
-- entries, per-row buckets; docs/rrb_list_mode.md "Collisions").  Object tables in a fake chip
-- RAM are copied (copy_start), the pass runs (col_tick), and the result (col_bits, col_count,
-- the first partner through col_pidx/col_pdata) is compared with a model computed here:
--   box X..X+W-1 (W = +14, 0 = 8), Y..Y+8H-1 (Y signed 10 bit, H = +5 bits 5..0, 0 = 1), REL X/Y
--   (+0 / +2 bit 10) from the previous table object (also a hidden one); objects collide if both
--   draw masks (+4) and group masks (+7) are non-zero, the group masks share a bit and the boxes
--   overlap; first partner = the lowest-numbered partner ($3FF: none).  v3: the pass tests the
--   pairs sharing a character row 0..63 (rows of Y..Y+8H-1), so the model also requires a
--   shared row in 0..63; +5 bit 7 = circle (radius W/2 about the box centre): circle/circle by
--   centre distance, circle/box by the distance to the box, touching counts.
-- +15 = collides-with mask (0 = the group mask): i and j collide if (g_i and w_j) /= 0 and
-- (g_j and w_i) /= 0.
-- Cases: designed boxes (overlap, touching edges, group masks, mask 0, group 0, negative Y,
-- REL chains, widths 1 / 8 / 255, heights), random tables (REL, negative Y, all sizes), 255
-- objects all overlapping (count 255; the pass finishes within its cycle budget), double
-- buffering (the old result stays readable while the next pass runs), a pass on the held
-- snapshot (col_tick without a copy), a copy requested while a pass runs (it waits), count 0/1;
-- v3: circles, overlaps only above row 0 (not reported), 1023 random objects, tall objects
-- sharing many rows (tested once), the pair budget (col_trunc, a subset of the full result).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;

entity tb_rrb_collide is
  generic ( MLAT : integer := 3 );
end entity;

architecture sim of tb_rrb_collide is
  signal clk : std_logic := '0';
  signal start, busy, done, mreq, bwe, copy_start, copying : std_logic := '0';
  signal maddr : unsigned(19 downto 0);
  signal caddr : unsigned(15 downto 0);
  signal mdata : unsigned(7 downto 0) := x"00";
  signal cnt : unsigned(9 downto 0) := (others => '0');
  signal col_tick, col_toggle, col_busy, col_trunc, bk_full : std_logic := '0';
  signal col_bits : std_logic_vector(1023 downto 0);
  signal col_count, col_pidx, col_pdata : unsigned(9 downto 0) := (others => '0');
  signal col_budget : unsigned(15 downto 0) := (others => '0');
  signal out_len : unsigned(10 downto 0);
  signal baddr : unsigned(9 downto 0);
  signal bscreen, bcolour : unsigned(7 downto 0);

  type mem_t is array (0 to 32767) of unsigned(7 downto 0);
  signal tabm : mem_t := (others => x"00");
  type pipe_t is array (1 to 8) of unsigned(7 downto 0);
  signal pipe : pipe_t := (others => x"00");

  -- model
  type ia is array (0 to 1023) of integer;
  type ba is array (0 to 1023) of boolean;
begin
  clk <= not clk after 5 ns;

  -- chip RAM: the byte at maddr returns MLAT cycles after the request
  process (clk) begin
    if rising_edge(clk) then
      pipe(1) <= tabm(to_integer(maddr(14 downto 0)));
      for n in 2 to 8 loop pipe(n) <= pipe(n - 1); end loop;
    end if;
  end process;
  mdata <= pipe(MLAT - 1) when MLAT >= 2 else pipe(1);

  dut : entity work.rrb_list_expander
    generic map (MLAT => MLAT, CLAT => 1, LIMIT => 1020)
    port map (clk => clk, start => start, ent_addr => x"10000", out_start => (others => '0'),
              busy => busy, done => done, out_len => out_len, mreq => mreq, maddr => maddr,
              mdata => mdata, caddr => caddr, cdata => x"00", bwe => bwe, baddr => baddr,
              bscreen => bscreen, bcolour => bcolour, obj_count => cnt, copy_start => copy_start,
              tab_addr => x"30000", mfree => '1', copying => copying,
              col_tick => col_tick, col_bits => col_bits, col_count => col_count,
              col_toggle => col_toggle, col_pidx => col_pidx, col_pdata => col_pdata,
              col_busy => col_busy, col_budget => col_budget, col_trunc => col_trunc,
              bk_full => bk_full);

  process
    variable seed1, seed2 : positive := 7;
    variable passes : integer := 0;

    procedure tick is
    begin
      wait until rising_edge(clk);
    end procedure;

    -- object k: X (10 bit, + REL bit 10), Y (10 bit two's complement, + REL), draw mask,
    -- height, group mask, width
    procedure put(k, x, xrel, y, yrel, mask, h, grp, wid : integer; wth : integer := 0;
                  circ : integer := 0) is
      variable b : integer := 32 * k;
      variable yy : integer;
    begin
      yy := y mod 1024;
      tabm(b) <= to_unsigned(x mod 256, 8);
      tabm(b + 1) <= to_unsigned((x / 256) mod 4 + 4 * xrel, 8);
      tabm(b + 2) <= to_unsigned(yy mod 256, 8);
      tabm(b + 3) <= to_unsigned(yy / 256 + 4 * yrel, 8);
      tabm(b + 4) <= to_unsigned(mask, 8);
      tabm(b + 5) <= to_unsigned(h + 128 * circ, 8);
      tabm(b + 6) <= x"02";
      tabm(b + 7) <= to_unsigned(grp, 8);
      for i in 8 to 13 loop tabm(b + i) <= x"00"; end loop;
      tabm(b + 14) <= to_unsigned(wid, 8);
      tabm(b + 15) <= to_unsigned(wth, 8);       -- collides-with mask (0 = the group mask)
      for i in 16 to 31 loop tabm(b + i) <= x"5A"; end loop;   -- software block (never read)
    end procedure;

    function rd8(m : mem_t; a : integer) return integer is
    begin
      return to_integer(m(a));
    end function;

    -- the expected result for objects 0..n-1 of the table in tabm
    procedure model(n : integer; variable bits : out ba; variable part : out ia;
                    variable count : out integer) is
      variable x0, x1, y0, y1, g, wm, wd, hh, fr, lr : ia;
      variable act, cir : ba;
      variable ax, ay, v, h, w, e, dx, dy, rr, f2, l2 : integer;
      variable hit : boolean;
      function outside(c, l, r : integer) return integer is
      begin
        if c < l then return l - c; elsif c > r then return c - r; else return 0; end if;
      end function;
    begin
      ax := 0; ay := 0;
      for k in 0 to n - 1 loop
        e := 32 * k;
        v := rd8(tabm, e) + 256 * (rd8(tabm, e + 1) mod 4);
        if (rd8(tabm, e + 1) / 4) mod 2 = 1 then ax := (ax + v) mod 1024; else ax := v; end if;
        v := rd8(tabm, e + 2) + 256 * (rd8(tabm, e + 3) mod 4);
        if (rd8(tabm, e + 3) / 4) mod 2 = 1 then ay := (ay + v) mod 1024; else ay := v; end if;
        h := rd8(tabm, e + 5) mod 64; if h = 0 then h := 1; end if;
        w := rd8(tabm, e + 14); if w = 0 then w := 8; end if;
        x0(k) := ax; x1(k) := ax + w - 1; wd(k) := w; hh(k) := h;
        if ay >= 512 then y0(k) := ay - 1024; else y0(k) := ay; end if;
        y1(k) := y0(k) + 8 * h - 1;
        -- character rows (floor), clamped to the bucket rows 0..63 below
        if y0(k) >= 0 then fr(k) := y0(k) / 8; else fr(k) := -((-y0(k) + 7) / 8); end if;
        if y1(k) >= 0 then lr(k) := y1(k) / 8; else lr(k) := -((-y1(k) + 7) / 8); end if;
        g(k) := rd8(tabm, e + 7);
        wm(k) := rd8(tabm, e + 15); if wm(k) = 0 then wm(k) := g(k); end if;
        act(k) := rd8(tabm, e + 4) /= 0 and g(k) /= 0;
        cir(k) := rd8(tabm, e + 5) >= 128;
      end loop;
      count := 0;
      for k in 0 to 1023 loop bits(k) := false; part(k) := 1023; end loop;
      for i in 0 to n - 1 loop
        for j in i + 1 to n - 1 loop
          if act(i) and act(j)
             and (to_unsigned(g(i), 8) and to_unsigned(wm(j), 8)) /= x"00"
             and (to_unsigned(g(j), 8) and to_unsigned(wm(i), 8)) /= x"00" then
            -- a shared character row 0..63
            f2 := maximum(maximum(fr(i), fr(j)), 0); l2 := minimum(minimum(lr(i), lr(j)), 63);
            if f2 <= l2 then
              if cir(i) and cir(j) then
                dx := (2 * x0(i) + wd(i)) - (2 * x0(j) + wd(j));
                dy := (2 * y0(i) + 8 * hh(i)) - (2 * y0(j) + 8 * hh(j));
                rr := wd(i) + wd(j);
                hit := dx * dx + dy * dy <= rr * rr;
              elsif cir(i) then
                dx := outside(2 * x0(i) + wd(i), 2 * x0(j), 2 * x0(j) + 2 * wd(j));
                dy := outside(2 * y0(i) + 8 * hh(i), 2 * y0(j), 2 * y0(j) + 16 * hh(j));
                hit := dx * dx + dy * dy <= wd(i) * wd(i);
              elsif cir(j) then
                dx := outside(2 * x0(j) + wd(j), 2 * x0(i), 2 * x0(i) + 2 * wd(i));
                dy := outside(2 * y0(j) + 8 * hh(j), 2 * y0(i), 2 * y0(i) + 16 * hh(i));
                hit := dx * dx + dy * dy <= wd(j) * wd(j);
              else
                hit := x0(i) <= x1(j) and x0(j) <= x1(i) and y0(i) <= y1(j) and y0(j) <= y1(i);
              end if;
              if hit then
                if part(i) = 1023 or j < part(i) then part(i) := j; end if;
                if part(j) = 1023 or i < part(j) then part(j) := i; end if;
                bits(i) := true; bits(j) := true;
              end if;
            end if;
          end if;
        end loop;
      end loop;
      for k in 0 to n - 1 loop
        if bits(k) then count := count + 1; end if;
      end loop;
    end procedure;

    procedure wait_result(limit : integer) is
      variable t0 : std_logic;
      variable c : integer := 0;
    begin
      t0 := col_toggle;
      while col_toggle = t0 loop
        tick; c := c + 1;
        assert c < limit report "no collision result after " & integer'image(c) & " cycles"
          severity failure;
      end loop;
      passes := passes + 1;
    end procedure;

    -- copy the table (n objects), run the pass, wait for its result
    procedure copy_pass(n : integer) is
    begin
      cnt <= to_unsigned(n, 10);
      tick; copy_start <= '1'; col_tick <= '1';
      tick; copy_start <= '0'; col_tick <= '0';
      wait_result(800000);
    end procedure;

    procedure check(name : string; n : integer) is
      variable bits : ba;
      variable part : ia;
      variable count : integer;
    begin
      model(n, bits, part, count);
      assert to_integer(col_count) = count
        report name & ": count " & integer'image(to_integer(col_count)) & ", expected "
          & integer'image(count) severity failure;
      assert col_trunc = '0' report name & ": truncated" severity failure;
      for k in 0 to 1023 loop
        assert (col_bits(k) = '1') = bits(k)
          report name & ": object " & integer'image(k) & " collided bit wrong" severity failure;
        col_pidx <= to_unsigned(k, 10);
        wait for 1 ns;
        assert to_integer(col_pdata) = part(k)
          report name & ": object " & integer'image(k) & " partner "
            & integer'image(to_integer(col_pdata)) & ", expected " & integer'image(part(k))
          severity failure;
      end loop;
      report name & ": OK (" & integer'image(count) & " collided)";
    end procedure;

    procedure rnd(lo, hi : integer; variable r : out integer) is
      variable x : real;
    begin
      uniform(seed1, seed2, x);
      r := lo + integer(trunc(x * real(hi - lo + 1)));
      if r > hi then r := hi; end if;
    end procedure;

    variable bits_a : std_logic_vector(1023 downto 0);
    variable cnt_a : unsigned(9 downto 0);
    variable mbits : ba;
    variable mpart : ia;
    variable mcount : integer;
    variable a, b, c, d, e, f, g, h : integer;
    variable t : integer;
  begin
    for i in 0 to 9 loop tick; end loop;

    -- designed boxes
    put(0, 10, 0, 10, 0, 255, 1, 1, 0);       -- 10..17 x 10..17, group 1
    put(1, 14, 0, 14, 0, 255, 1, 1, 0);       -- overlaps 0
    put(2, 14, 0, 14, 0, 255, 1, 2, 0);       -- same box, group 2: not with 0/1
    put(3, 16, 0, 16, 0, 255, 1, 3, 0);       -- groups 1|2: with 0, 1, 2
    put(4, 12, 0, 12, 0, 0, 1, 1, 0);         -- draw mask 0: never
    put(5, 2, 1, 0, 1, 255, 1, 1, 0);         -- REL from 4 (hidden anchor): X 14, Y 12 -> with 0, 1, 3
    put(6, 300, 0, -5, 0, 255, 1, 4, 0);      -- Y -5..2, group 4
    put(7, 300, 0, 2, 0, 255, 1, 4, 0);       -- Y 2..9: touches -5..2 at 2 -> collides
    put(8, 300, 0, 3, 0, 255, 1, 0, 0);       -- group 0: never
    put(9, 500, 0, 100, 0, 255, 1, 8, 1);     -- width 1: X 500..500
    put(10, 501, 0, 100, 0, 255, 1, 8, 0);    -- X 501..508: no overlap with 9
    put(11, 499, 0, 107, 0, 255, 1, 8, 2);    -- X 499..500, Y 107..114: overlaps 9 (Y 100..107)
    put(12, 700, 0, 200, 0, 255, 4, 16, 255); -- 700..954 x 200..231 (height 4)
    put(13, 950, 0, 231, 0, 255, 1, 16, 0);   -- corner touch: collides
    put(14, 955, 0, 231, 0, 255, 1, 16, 0);   -- just right of 12: no
    put(15, 1020, 0, 500, 0, 255, 63, 32, 8); -- X 1020..1027, Y 500..1003
    put(16, 1, 1, 503, 0, 255, 1, 32, 0);     -- REL +1: X 1021: overlaps 15
    wait for 1 ns;
    copy_pass(17);
    check("designed", 17);
    -- collides-with masks (+15): bullets (group 1, with 2) never pair with each other, but with
    -- the player (group 2, with 1); an object with with-mask 4 meets nobody here; 0 = legacy
    for k in 0 to 7 loop put(k, 100 + 2 * k, 0, 50, 0, 255, 1, 1, 0, 2); end loop;   -- bullets
    put(8, 104, 0, 52, 0, 255, 1, 2, 0, 1);       -- the player, over all bullets
    put(9, 104, 0, 52, 0, 255, 1, 2, 0, 4);       -- group 2, but collides only with group 4
    put(10, 104, 0, 52, 0, 255, 1, 1, 0, 0);      -- legacy (+15 = 0): with group 1 objects? no:
                                                  -- bullets' with-mask is 2 (not group 1)
    put(11, 104, 0, 52, 0, 255, 1, 3, 0, 0);      -- legacy groups 1|2: with the player (2/1)
    wait for 1 ns;
    copy_pass(12);
    check("collides-with masks", 12);
    -- count 0 and 1: no pairs
    copy_pass(1);
    check("one object", 1);
    copy_pass(0);
    check("no objects", 0);

    -- random tables
    for round in 1 to 6 loop
      for k in 0 to 59 loop
        rnd(0, 1023, a); rnd(0, 7, b); rnd(-512, 511, c); rnd(0, 7, d);
        rnd(0, 255, e); rnd(0, 3, f); rnd(0, 255, g); rnd(0, 40, h); rnd(0, 255, t);
        if t < 128 then t := 0; end if;                              -- half without +15
        if b /= 0 then b := 0; else b := 1; a := a mod 64; end if;   -- 1 in 8 REL X
        if d /= 0 then d := 0; else d := 1; c := c mod 64; end if;   -- 1 in 8 REL Y
        if e < 20 then e := 0; end if;                              -- some hidden
        if g < 30 then g := 0; end if;                              -- some without group
        if round <= 3 then a := a mod 300; c := c mod 120; end if;   -- denser
        put(k, a, b, c, d, e, f, g, h, t);
      end loop;
      wait for 1 ns;
      copy_pass(60);
      check("random " & integer'image(round), 60);
    end loop;

    -- 255 objects in one place: all collide; the pass must finish within its budget
    for k in 0 to 254 loop put(k, 100 + k mod 3, 0, 50, 0, 255, 1, 1, 0); end loop;
    wait for 1 ns;
    cnt <= to_unsigned(255, 10);
    tick; copy_start <= '1'; col_tick <= '1';
    tick; copy_start <= '0'; col_tick <= '0';
    t := 0;
    while col_busy = '0' loop tick; t := t + 1; end loop;   -- (the copy runs first)
    t := 0;
    bits_a := col_bits; cnt_a := col_count;
    while col_busy = '1' loop
      -- double buffering: the last result stays readable while the pass runs (it changes at
      -- the edge that ends the pass)
      assert col_bits = bits_a and col_count = cnt_a report "result changed during the pass" severity failure;
      tick; t := t + 1;
    end loop;
    report "255 objects: pass " & integer'image(t) & " cycles";
    assert t <= 255 * 254 / 2 + 16 * 255 + 200 report "pass too slow" severity failure;
    tick; tick;
    check("255 objects", 255);

    -- pass on the held snapshot (no copy): the same result, a new toggle
    put(0, 900, 0, 900, 0, 255, 1, 1, 0);     -- (chip RAM changes; the snapshot does not)
    tick; col_tick <= '1'; tick; col_tick <= '0';
    wait_result(800000);
    for k in 0 to 254 loop put(k, 100 + k mod 3, 0, 50, 0, 255, 1, 1, 0); end loop;
    wait for 1 ns;
    check("held snapshot", 255);

    -- a copy requested while a pass runs waits for it (the boxes do not change under the pass)
    for k in 0 to 254 loop put(k, 4 * k, 0, 10, 0, 255, 1, 1, 0); end loop;    -- 4 apart, width 8
    wait for 1 ns;
    tick; col_tick <= '1'; tick; col_tick <= '0';        -- pass on the old snapshot
    while col_busy = '0' loop tick; end loop;
    for i in 0 to 99 loop tick; end loop;
    cnt <= to_unsigned(255, 10);
    copy_start <= '1'; tick; copy_start <= '0';          -- copy of the new table, during the pass
    while col_busy = '1' loop
      assert mreq = '0' report "copy read while the pass runs" severity failure;
      tick;
    end loop;
    tick; tick;
    assert col_count = 255 report "pass before the waiting copy: not the old snapshot's result"
      severity failure;
    report "pass before a waiting copy: old snapshot's result (255), no copy read meanwhile";
    while copying = '1' loop tick; end loop;
    tick; col_tick <= '1'; tick; col_tick <= '0';
    wait_result(800000);
    check("after the waiting copy", 255);

    -- circles (+5 bit 7, radius W/2 about the box centre)
    for k in 0 to 15 loop put(k, 0, 0, 0, 0, 0, 1, 0, 0); end loop;
    put(0, 100, 0, 100, 0, 255, 2, 1, 16, 0, 1);    -- circle r 8, centre (108, 108)
    put(1, 124, 0, 100, 0, 255, 2, 1, 16, 0, 1);    -- centre (132, 108): distance 24 > 16: no
    put(2, 116, 0, 100, 0, 255, 2, 1, 16, 0, 1);    -- centre (124, 108): distance 16 = r + r: yes
    put(3, 300, 0, 100, 0, 255, 2, 2, 16, 0, 1);    -- circle r 8, centre (308, 108)
    put(4, 314, 0, 114, 0, 255, 1, 2, 8, 0, 0);     -- box 314..321 x 114..121: corner beyond r
    put(5, 314, 0, 104, 0, 255, 1, 2, 8, 0, 0);     -- box 314..321 x 104..111: side touches
    put(6, 500, 0, 100, 0, 255, 2, 4, 16, 0, 1);    -- circle centre (508, 108)
    put(7, 520, 0, 110, 0, 255, 2, 4, 16, 0, 1);    -- centre (528, 118): 20, 10: 500 > 256: no
    put(8, 510, 0, 103, 0, 255, 2, 4, 16, 0, 1);    -- centre (518, 111): 10, 3: yes
    put(9, 700, 0, 100, 0, 255, 2, 8, 16, 0, 1);    -- circle vs a box it covers: yes
    put(10, 704, 0, 104, 0, 255, 1, 8, 4, 0, 0);
    wait for 1 ns;
    copy_pass(11);
    check("circles", 11);
    -- overlaps above row 0 only (rows < 0 have no bucket): not reported; tall objects that share
    -- many rows: one pair
    for k in 0 to 15 loop put(k, 0, 0, 0, 0, 0, 1, 0, 0); end loop;
    put(0, 100, 0, -100, 0, 255, 2, 1, 8);          -- Y -100..-85
    put(1, 104, 0, -90, 0, 255, 2, 1, 8);           -- overlaps 0 at Y -90..-85: no shared row
    put(2, 104, 0, -90, 0, 255, 12, 1, 8);          -- Y -90..5: shares row 0 with 3
    put(3, 100, 0, 0, 0, 255, 1, 1, 8);
    put(4, 400, 0, 0, 0, 255, 63, 2, 8);            -- rows 0..62
    put(5, 404, 0, 4, 0, 255, 63, 2, 8);            -- rows 0..63: share 63 rows
    wait for 1 ns;
    copy_pass(6);
    check("rows above 0, tall objects", 6);

    -- 1023 random objects (also circles), spread over 50 rows; one or two rows each (about
    -- 1900 bucket entries: within the 2048)
    for k in 0 to 1022 loop
      rnd(0, 1000, a); rnd(0, 399, c); rnd(0, 255, e); rnd(0, 1, f); rnd(1, 15, g); rnd(0, 24, h);
      rnd(0, 255, t);
      if e < 20 then e := 0; end if;
      if t < 200 then t := 0; else t := 1; end if;     -- about 1 in 5 a circle
      put(k, a, 0, c, 0, e, f, g, h, 0, t);
    end loop;
    wait for 1 ns;
    tick; cnt <= to_unsigned(1023, 10);
    tick; copy_start <= '1'; col_tick <= '1';
    tick; copy_start <= '0'; col_tick <= '0';
    t := 0;
    while col_busy = '0' loop tick; t := t + 1; end loop;
    report "1023 objects: copy + buckets " & integer'image(t) & " cycles";
    t := 0;
    while col_busy = '1' loop tick; t := t + 1; end loop;
    report "1023 objects: pass " & integer'image(t) & " cycles";
    assert bk_full = '0' report "1023 objects: buckets full" severity failure;
    tick; tick;
    check("1023 random objects", 1023);

    -- the pair budget: 255 objects in one place, 100 pairs: truncated, a subset of the result
    for k in 0 to 254 loop put(k, 100 + k mod 3, 0, 50, 0, 255, 1, 1, 0); end loop;
    wait for 1 ns;
    col_budget <= to_unsigned(100, 16);
    copy_pass(255);
    model(255, mbits, mpart, mcount);
    assert col_trunc = '1' report "budget: not truncated" severity failure;
    assert to_integer(col_count) < mcount and to_integer(col_count) > 0
      report "budget: count " & integer'image(to_integer(col_count)) severity failure;
    for k in 0 to 254 loop
      assert col_bits(k) = '0' or mbits(k) report "budget: not a subset" severity failure;
    end loop;
    report "budget 100 pairs: truncated, " & integer'image(to_integer(col_count)) & " of "
      & integer'image(mcount) & " collided";
    col_budget <= to_unsigned(40000, 16);
    tick; col_tick <= '1'; tick; col_tick <= '0';
    wait_result(800000);
    check("budget 40000 (enough)", 255);
    col_budget <= (others => '0');

    report "TB_RRB_COLLIDE_PASS (MLAT " & integer'image(MLAT) & ", " & integer'image(passes)
      & " passes)";
    std.env.stop;
    wait;
  end process;
end architecture;
