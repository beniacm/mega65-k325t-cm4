-- tb_rrb_list_v2.vhdl -- GHDL test of rrb_list_expander list mode v2 (global object table).
-- Copies object tables from a fake chip RAM into the unit's table (copy_start), then expands
-- character rows with start_scan and compares the row buffer with the expected tokens:
--   * objects straddling two rows (top part / bottom part masks and Y offsets), REL X, a hidden
--     object (mask 0, still an anchor for REL), multi-row objects (stride), FLIPX, FLIPY (rows
--     in reverse order, Y offset sign swapped), a negative Y; a v1 LIST row with scan_en;
--   * no copy yet = empty scan; count 0; count 255 (the last object visible, scan started in the
--     first cycle after copying falls); a count change between two copies (objects of the old
--     snapshot beyond the new count never appear; the live count is not used after the copy);
--   * aborts at many points of a scan, each followed by a clean row;
--   * user draw masks ($3C with Y mod 8 = 2 and 6), REL Y, FLIPY with height 1 on two rows, a
--     scan at an arbitrary out_start (the LIST-token-as-anchor case);
--   * a copy with mfree toggling (pseudo random): identical results, no read while not free;
--   * buffer overflow (255 visible objects, out_start 0/2/4/6).  Invariant checked: the
--     writes form one contiguous run [out_start, out_len); out_len is even and <= LIMIT + 4;
--     the last token is $FFFF and the only one (object tokens never go beyond LIMIT + 1, only
--     the end token may sit at LIMIT + 2 / LIMIT + 3); every GOTOX is followed by at least one
--     token of its object (no part started that cannot fit); the parts appear in table order.
--   * always: no table write while copying = '0'; no copy read while the port was not free.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_rrb_list_v2 is
  generic ( MLAT : integer := 3; BUFW : integer := 10; LIMIT : integer := 1020 );
end entity;

architecture sim of tb_rrb_list_v2 is
  constant BUFN : integer := 2 ** BUFW;                 -- row buffer bytes (VIC-IV: 2 KB)
  signal clk : std_logic := '0';
  signal start, busy, done, mreq, bwe, start_scan, scan_en, copy_start, copying : std_logic := '0';
  signal abort : std_logic := '0';
  signal mfree : std_logic := '1';
  signal mfree_tog : boolean := false;
  signal ent_addr : unsigned(19 downto 0) := x"10000";
  signal out_start : unsigned(BUFW - 1 downto 0) := to_unsigned(0, BUFW);
  signal out_len : unsigned(BUFW downto 0);
  signal baddr : unsigned(BUFW - 1 downto 0);
  signal maddr : unsigned(19 downto 0);
  signal caddr : unsigned(15 downto 0);
  signal mdata, cdata, bscreen, bcolour : unsigned(7 downto 0) := x"00";
  signal row : unsigned(7 downto 0) := x"00";
  signal cnt : unsigned(7 downto 0) := x"00";
  signal part_s : std_logic := '0';

  type r_t is array (natural range <>) of integer;
  -- object table 1: X lo/hi, Y lo/hi, mask, height, flags, res, addr lo/hi, col lo/hi,
  -- stride lo/hi, res, res
  constant T : r_t := (
    100, 0,  13, 0,  16#FF#, 1, 16#02#, 0,  0, 0,  0, 16#40#,  0, 0,  0, 0,     -- 0: X100 Y13
     50, 4,  16, 0,  16#FF#, 2, 16#12#, 0,  0, 0,  0, 16#40#, 16, 0,  0, 0,     -- 1: REL+50 Y16 H2 FLIPX
    244, 1,   8, 0,  16#00#, 1, 16#02#, 0,  0, 0,  0, 16#40#,  0, 0,  0, 0,     -- 2: hidden (X500)
     44, 1, 253, 3,  16#FF#, 1, 16#02#, 0,  0, 0,  0, 16#40#,  0, 0,  0, 0,     -- 3: X300 Y-3
    144, 1,   8, 0,  16#FF#, 2, 16#22#, 0,  0, 0,  0, 16#40#, 16, 0,  0, 0,     -- 4: X400 Y8 H2 FLIPY
     88, 2,  12, 0,  16#FF#, 2, 16#22#, 0,  0, 0,  0, 16#40#, 16, 0,  0, 0);    -- 5: X600 Y12 H2 FLIPY
  -- object data at $20000 (row 0), $20010 (row 1), $20020 (3 glyphs); colour at $4000/10/20
  constant O0 : r_t := (16#01#, 16#01#, 16#FF#, 16#FF#, 16#AA#, 16#AA#, 16#AA#, 16#AA#);
  constant O1 : r_t := (16#02#, 16#02#, 16#FF#, 16#FF#, 16#AA#, 16#AA#, 16#AA#, 16#AA#);
  constant O2 : r_t := (16#03#, 16#03#, 16#04#, 16#04#, 16#05#, 16#05#, 16#FF#, 16#FF#, 16#AA#, 16#AA#);
  constant C0 : r_t := (16#00#, 16#05#, 16#00#, 16#00#, 16#BB#, 16#BB#);
  constant C1 : r_t := (16#00#, 16#06#, 16#00#, 16#00#, 16#BB#, 16#BB#);
  constant C2 : r_t := (16#00#, 16#07#, 16#00#, 16#07#, 16#00#, 16#07#, 16#BB#, 16#BB#);
  -- v1 entries at $10000: one entry X 20 -> object row 0, then end of lists
  constant E : r_t := (20, 0, 16#FF#, 16#02#, 0, 0, 0, 16#40#,  16#FF#, 16#FF#, 0, 0, 0, 0, 0, 0);

  -- the object table in chip RAM ($30000-$30FFF), written by the test
  type mem_t is array (0 to 4095) of unsigned(7 downto 0);
  signal tabm : mem_t := (others => x"00");

  impure function chip(a : unsigned(19 downto 0)) return unsigned is
    variable i : integer;
  begin
    i := to_integer(a(7 downto 0));
    if a(19 downto 12) = x"30" then return tabm(to_integer(a(11 downto 0))); end if;
    if a(19 downto 8) = x"100" and i < E'length then return to_unsigned(E(i), 8); end if;
    if a(19 downto 4) = x"2000" and i mod 16 < O0'length then return to_unsigned(O0(i mod 16), 8); end if;
    if a(19 downto 4) = x"2001" and i mod 16 < O1'length then return to_unsigned(O1(i mod 16), 8); end if;
    if a(19 downto 4) = x"2002" and i mod 16 < O2'length then return to_unsigned(O2(i mod 16), 8); end if;
    return x"EE";
  end function;
  function col(a : unsigned(15 downto 0)) return unsigned is
    variable i : integer;
  begin
    i := to_integer(a(3 downto 0));
    if a(15 downto 4) = x"400" and i < C0'length then return to_unsigned(C0(i), 8); end if;
    if a(15 downto 4) = x"401" and i < C1'length then return to_unsigned(C1(i), 8); end if;
    if a(15 downto 4) = x"402" and i < C2'length then return to_unsigned(C2(i), 8); end if;
    return x"CC";
  end function;

  type buf_t is array (0 to BUFN - 1) of unsigned(7 downto 0);
  signal bs, bc : buf_t := (others => x"55");
  type wr_t is array (0 to BUFN - 1) of boolean;
  signal wr : wr_t := (others => false);                    -- written since the last clear
  type pipe_t is array (1 to MLAT) of unsigned(7 downto 0);
  signal mp : pipe_t := (others => x"00");
  signal clr : std_logic := '0';
  signal port_errs : integer := 0;
begin
  clk <= not clk after 5 ns;

  dut : entity work.rrb_list_expander
    generic map (MLAT => MLAT, CLAT => 1, LIMIT => LIMIT, BUFW => BUFW)
    port map (clk => clk, start => start, abort => abort, ent_addr => ent_addr,
              out_start => out_start, busy => busy, done => done, out_len => out_len,
              mreq => mreq, maddr => maddr, mdata => mdata, caddr => caddr, cdata => cdata,
              bwe => bwe, baddr => baddr, bscreen => bscreen, bcolour => bcolour,
              scan_en => scan_en, start_scan => start_scan, row => row, obj_count => cnt,
              copy_start => copy_start, tab_addr => x"30000", mfree => mfree, copying => copying,
              part => part_s);

  -- memories, buffer and port checks
  process (clk)
    alias tab_we is << signal .tb_rrb_list_v2.dut.tab_we : std_logic >>;
    variable lfsr : unsigned(15 downto 0) := x"ACE1";
    variable mfree_d : std_logic := '1';
  begin
    if rising_edge(clk) then
      mp(1) <= chip(maddr);
      for n in 2 to MLAT - 1 loop mp(n) <= mp(n - 1); end loop;
      cdata <= col(caddr);
      if clr = '1' then
        bs <= (others => x"55"); bc <= (others => x"55"); wr <= (others => false);
      elsif bwe = '1' then
        bs(to_integer(baddr)) <= bscreen; bc(to_integer(baddr)) <= bcolour;
        wr(to_integer(baddr)) <= true;
      end if;
      -- a copy read (the FSM is idle) on the port now was decided at the previous edge, with
      -- the mfree seen there
      if mreq = '1' and busy = '0' and (mfree_d = '0' or copying = '0') then
        report "copy read without a free port" severity error;
        port_errs <= port_errs + 1;
      end if;
      if tab_we = '1' and copying = '0' then
        report "table write after copying fell" severity error;
        port_errs <= port_errs + 1;
      end if;
      mfree_d := mfree;
      if mfree_tog then
        lfsr := lfsr(14 downto 0) & (lfsr(15) xor lfsr(13) xor lfsr(12) xor lfsr(10));
        mfree <= lfsr(0) and (lfsr(3) or lfsr(5));   -- free about 3/8 of the cycles, in bursts
      else
        mfree <= '1';
      end if;
    end if;
  end process;
  mdata <= mp(MLAT - 1);

  process
    variable errs : integer := 0;
    variable tcnt, tfirst, tlast, nparts : integer := 0;
    -- 4 numbers per token: screen lo, screen hi, colour 0, colour 1
    constant REMPTY : r_t := (16#FF#, 16#FF#, 0, 0);
    constant R0 : r_t := (16#2C#, 16#61#, 16#18#, 16#1F#,  1, 1, 0, 5,                -- 3 bottom
                          16#FF#, 16#FF#, 0, 0);
    constant R1 : r_t := (16#64#, 16#B0#, 16#18#, 16#E0#,  1, 1, 0, 5,                -- 0 top
                          16#90#, 16#01#, 16#10#, 16#FF#,  2, 2, 16#80#, 6,           -- 4 row 1 (flip)
                          16#58#, 16#82#, 16#18#, 16#F0#,  2, 2, 16#80#, 6,           -- 5 top: row 1
                          16#FF#, 16#FF#, 0, 0);
    constant R2 : r_t := (16#64#, 16#60#, 16#18#, 16#1F#,  1, 1, 0, 5,                -- 0 bottom
                          16#96#, 16#10#, 16#10#, 16#FF#,  1, 1, 16#40#, 5,           -- 1 row 0 FLIPX
                          16#90#, 16#01#, 16#10#, 16#FF#,  1, 1, 16#80#, 5,           -- 4 row 0 (flip)
                          16#58#, 16#82#, 16#18#, 16#F0#,  1, 1, 16#80#, 5,           -- 5 top: row 0
                          16#58#, 16#92#, 16#18#, 16#0F#,  2, 2, 16#80#, 6,           -- 5 bottom: row 1
                          16#FF#, 16#FF#, 0, 0);
    constant R3 : r_t := (16#96#, 16#10#, 16#10#, 16#FF#,  2, 2, 16#40#, 6,           -- 1 row 1 FLIPX
                          16#58#, 16#92#, 16#18#, 16#0F#,  1, 1, 16#80#, 5,           -- 5 bottom: row 0
                          16#FF#, 16#FF#, 0, 0);
    -- v1 LIST row (row 0) + scan: the entry, then the global objects of row 0
    constant RL : r_t := (16#14#, 16#00#, 16#10#, 16#FF#,  1, 1, 0, 5,
                          16#2C#, 16#61#, 16#18#, 16#1F#,  1, 1, 0, 5,
                          16#FF#, 16#FF#, 0, 0);
    -- table 1 with count 3: row 1 has object 0 only (4 and 5 are beyond the count)
    constant R1C3 : r_t := (16#64#, 16#B0#, 16#18#, 16#E0#,  1, 1, 0, 5,
                            16#FF#, 16#FF#, 0, 0);
    -- table 2 (masks, REL Y, FLIPY height 1)
    constant R1B : r_t := (16#64#, 16#50#, 16#18#, 16#3C#,  1, 1, 0, 5,              -- 0 top, $3C
                           16#D2#, 16#70#, 16#18#, 16#F8#,  1, 1, 0, 5,              -- 3 REL X/Y top
                           16#2C#, 16#A1#, 16#18#, 16#E0#,  1, 1, 16#80#, 5,         -- 4 FLIPY top
                           16#FF#, 16#FF#, 0, 0);
    constant R2B : r_t := (16#78#, 16#40#, 16#18#, 16#3C#,  1, 1, 0, 5,              -- 1 bottom, $3C
                           16#D2#, 16#A0#, 16#18#, 16#07#,  1, 1, 0, 5,              -- 3 bottom
                           16#2C#, 16#71#, 16#18#, 16#1F#,  1, 1, 16#80#, 5,         -- 4 FLIPY bottom
                           16#FF#, 16#FF#, 0, 0);
    -- 255 objects, only the last one visible
    constant R254 : r_t := (16#4D#, 16#10#, 16#10#, 16#FF#,  1, 1, 0, 5,
                            16#FF#, 16#FF#, 0, 0);

    procedure tick is
    begin
      wait until rising_edge(clk);
    end procedure;

    -- one 16-byte object of the table in chip RAM (x, y: 11 bits incl. REL)
    procedure put(k, x, y, mask, h, flags, addr, colo, stride : integer) is
      constant b : integer := 16 * k;
    begin
      tabm(b + 0) <= to_unsigned(x mod 256, 8);      tabm(b + 1) <= to_unsigned(x / 256, 8);
      tabm(b + 2) <= to_unsigned(y mod 256, 8);      tabm(b + 3) <= to_unsigned(y / 256, 8);
      tabm(b + 4) <= to_unsigned(mask, 8);           tabm(b + 5) <= to_unsigned(h, 8);
      tabm(b + 6) <= to_unsigned(flags, 8);          tabm(b + 7) <= x"00";
      tabm(b + 8) <= to_unsigned(addr mod 256, 8);   tabm(b + 9) <= to_unsigned(addr / 256, 8);
      tabm(b + 10) <= to_unsigned(colo mod 256, 8);  tabm(b + 11) <= to_unsigned(colo / 256, 8);
      tabm(b + 12) <= to_unsigned(stride mod 256, 8); tabm(b + 13) <= to_unsigned(stride / 256, 8);
      tabm(b + 14) <= x"00";                         tabm(b + 15) <= x"00";
    end procedure;

    procedure load_t1 is
    begin
      tabm <= (others => x"00");
      wait for 0 ns;
      for i in T'range loop tabm(i) <= to_unsigned(T(i), 8); end loop;
    end procedure;

    procedure copy(n : integer; tog : boolean) is
    begin
      cnt <= to_unsigned(n, 8); mfree_tog <= tog;
      tick; copy_start <= '1';
      tick; copy_start <= '0';
      wait for 1 ns;
      assert copying = '1' report "copying not set" severity failure;
      for c in 1 to 100000 loop
        tick; wait for 1 ns;
        exit when copying = '0';
      end loop;
      assert copying = '0' report "copy does not end" severity failure;
      mfree_tog <= false;
      for c in 1 to 4 loop tick; end loop;
    end procedure;

    procedure wait_done is
    begin
      for n in 1 to 20000 loop
        tick;
        exit when done = '1';
      end loop;
      assert done = '1' report "no done" severity failure;
      tick;
    end procedure;

    procedure expand(r : integer; scan_only : std_logic; os : integer := 0) is
    begin
      clr <= '1'; tick; clr <= '0';
      row <= to_unsigned(r, 8); start_scan <= scan_only; scan_en <= '1';
      out_start <= to_unsigned(os, BUFW);
      tick; start <= '1';
      tick; start <= '0';
      wait_done;
    end procedure;

    procedure check(name : string; x : r_t; os : integer := 0) is
      variable k : integer;
    begin
      for n in 0 to x'length / 4 - 1 loop
        for b in 0 to 1 loop
          k := os + 2 * n + b;
          if bs(k) /= x(4 * n + b) or bc(k) /= x(4 * n + 2 + b) then
            report name & " byte " & integer'image(k) & ": screen $" & to_hstring(bs(k))
                   & " colour $" & to_hstring(bc(k)) & ", expected $"
                   & to_hstring(to_unsigned(x(4 * n + b), 8)) & " $"
                   & to_hstring(to_unsigned(x(4 * n + 2 + b), 8)) severity error;
            errs := errs + 1;
          end if;
        end loop;
      end loop;
      if to_integer(out_len) /= os + x'length / 2 then
        report name & ": out_len " & integer'image(to_integer(out_len)) severity error;
        errs := errs + 1;
      end if;
      for i in 0 to BUFN - 1 loop
        if wr(i) and (i < os or i >= os + x'length / 2) then
          report name & ": written outside the expected range at " & integer'image(i) severity error;
          errs := errs + 1;
          exit;
        end if;
      end loop;
    end procedure;

    -- buffer overflow invariant (see the header)
    procedure check_full(name : string; os : integer; per : integer := 1) is
      variable l, k, x : integer;
    begin
      l := to_integer(out_len);
      if l mod 2 /= 0 or l > LIMIT + 4 or l < os + 2 then
        report name & ": out_len " & integer'image(l) severity error;
        errs := errs + 1;
        return;
      end if;
      for i in 0 to BUFN - 1 loop
        if wr(i) /= (i >= os and i < l) then
          report name & ": write map wrong at " & integer'image(i) severity error;
          errs := errs + 1;
          exit;
        end if;
      end loop;
      if bs(l - 2) /= x"FF" or bs(l - 1) /= x"FF" then
        report name & ": no end token" severity error; errs := errs + 1;
      end if;
      k := os; x := 0;
      while k < l - 2 loop
        if bs(k) = x"FF" and bs(k + 1) = x"FF" then
          report name & ": early end token at " & integer'image(k) severity error;
          errs := errs + 1;
        end if;
        if bc(k)(4) = '1' then                    -- a GOTOX: the parts in table order
          if to_integer(bs(k)) /= x / per then
            report name & ": GOTOX at " & integer'image(k) & " is X " & integer'image(to_integer(bs(k)))
                   & ", expected " & integer'image(x / per) severity error;
            errs := errs + 1;
          end if;
          x := x + 1;
          -- every part has tokens after its GOTOX (no part is started that cannot fit)
          if k + 2 >= l - 2 or bc(k + 2)(4) = '1' then
            report name & ": GOTOX at " & integer'image(k) & " without tokens" severity error;
            errs := errs + 1;
          end if;
        end if;
        k := k + 2;
      end loop;
      if x < 100 then
        report name & ": only " & integer'image(x) & " parts" severity error; errs := errs + 1;
      end if;
      report name & ": out_len " & integer'image(l) & ", " & integer'image(x) & " parts";
    end procedure;
  begin
    load_t1;
    cnt <= x"06";
    wait for 50 ns;
    -- no copy yet: the (empty) snapshot, not the live count
    expand(1, '1'); check("no snapshot", REMPTY);

    copy(6, false);
    expand(0, '1'); check("row 0", R0);
    expand(1, '1'); check("row 1", R1);
    expand(2, '1'); check("row 2", R2);
    expand(3, '1'); check("row 3", R3);
    expand(0, '0'); check("LIST row 0", RL);

    -- abort at many points of a scan (scan, part, object data), then a clean row
    for d in 0 to 24 loop
      clr <= '1'; tick; clr <= '0';
      row <= to_unsigned(2, 8); start_scan <= '1'; scan_en <= '1'; out_start <= to_unsigned(0, BUFW);
      tick; start <= '1';
      tick; start <= '0';
      for n in 1 to 1 + 4 * d loop tick; end loop;
      -- restart right after the abort: reads in flight / the scan pipeline must not leak
      abort <= '1'; clr <= '1'; tick; abort <= '0'; clr <= '0';
      wait for 1 ns;
      assert busy = '0' report "busy after abort" severity error;
      start <= '1'; tick; start <= '0';
      wait_done;
      check("row 2 after abort at " & integer'image(1 + 4 * d), R2);
    end loop;

    -- count change: the second snapshot has 3 objects; 3..5 of the first must not appear,
    -- and the live count is not used for scans
    copy(3, false);
    expand(0, '1'); check("count 3 row 0", REMPTY);
    expand(1, '1'); check("count 3 row 1", R1C3);
    cnt <= x"06";
    expand(1, '1'); check("count 3 row 1, live count 6", R1C3);

    -- copy with the port free only sometimes: the same snapshot
    copy(6, true);
    expand(0, '1'); check("mfree row 0", R0);
    expand(1, '1'); check("mfree row 1", R1);
    expand(2, '1'); check("mfree row 2", R2);
    expand(3, '1'); check("mfree row 3", R3);

    -- table 2: user masks, REL Y, FLIPY height 1 on two rows, arbitrary out_start
    tabm <= (others => x"00");
    wait for 0 ns;
    put(0, 100, 10, 16#3C#, 1, 16#02#, 0, 16#4000#, 0);           -- Y mod 8 = 2: top only
    put(1, 120, 14, 16#3C#, 1, 16#02#, 0, 16#4000#, 0);           -- Y mod 8 = 6: bottom only
    put(2, 200, 16, 0, 1, 16#02#, 0, 16#4000#, 0);                -- hidden anchor
    put(3, 1024 + 10, 1024 + 1019, 16#FF#, 1, 16#02#, 0, 16#4000#, 0);   -- REL +10, REL -5
    put(4, 300, 13, 16#FF#, 1, 16#22#, 0, 16#4000#, 0);           -- FLIPY, height 1
    copy(5, true);
    expand(0, '1'); check("table 2 row 0", REMPTY);
    expand(1, '1'); check("table 2 row 1", R1B);
    expand(2, '1'); check("table 2 row 2", R2B);
    expand(1, '1', 74); check("table 2 row 1 at 74", R1B, 74);
    expand(2, '1', 1000); check("table 2 row 2 at 1000", R2B, 1000);

    -- count 0
    copy(0, false);
    expand(1, '1'); check("count 0 row 1", REMPTY);
    expand(2, '1', 74); check("count 0 row 2 at 74", REMPTY, 74);

    -- count 255, only the last object visible; the scan starts in the first cycle after
    -- copying falls
    tabm <= (others => x"00");
    wait for 0 ns;
    for k in 0 to 253 loop put(k, 0, 0, 0, 1, 16#02#, 0, 16#4000#, 0); end loop;
    put(254, 77, 0, 16#FF#, 1, 16#02#, 0, 16#4000#, 0);
    clr <= '1'; tick; clr <= '0';
    row <= x"00"; start_scan <= '1'; scan_en <= '1'; out_start <= to_unsigned(0, BUFW);
    cnt <= x"FF";
    tick; copy_start <= '1';
    tick; copy_start <= '0';
    for n in 1 to 100000 loop
      tick; wait for 1 ns;
      exit when copying = '0';
    end loop;
    start <= '1'; tick; start <= '0';
    wait_done;
    check("count 255, scan right after the copy", R254);

    -- buffer overflow: 255 visible objects (3 glyphs each) on row 0
    tabm <= (others => x"00");
    wait for 0 ns;
    for k in 0 to 254 loop put(k, k, 0, 16#FF#, 1, 16#02#, 16#0020#, 16#4020#, 0); end loop;
    copy(255, false);
    for os in 0 to 3 loop
      expand(0, '1', 2 * os); check_full("overflow at " & integer'image(2 * os), 2 * os);
    end loop;
    -- the same with two parts per object (Y 3, height 2, row 1: top part, then bottom part)
    tabm <= (others => x"00");
    wait for 0 ns;
    for k in 0 to 254 loop put(k, k, 3, 16#FF#, 2, 16#02#, 16#0020#, 16#4020#, 0); end loop;
    copy(255, false);
    for os in 0 to 3 loop
      expand(1, '1', 2 * os); check_full("overflow, 2 parts, at " & integer'image(2 * os), 2 * os, 2);
    end loop;

    -- timing (no check): object parts appended after a 48-token row (out_start 96), one-glyph
    -- objects; cycles from start to done, to the first part, and per part
    for tcase in 0 to 5 loop
      tabm <= (others => x"00");
      wait for 0 ns;
      if tcase = 0 then        -- 64 objects inside row 5
        for k in 0 to 63 loop put(k, 10 * k, 40, 16#FF#, 1, 16#02#, 0, 16#4000#, 0); end loop;
        copy(64, false);
      elsif tcase = 1 then     -- 64 objects at Y = 43: row 5 has their top parts
        for k in 0 to 63 loop put(k, 10 * k, 43, 16#FF#, 1, 16#02#, 0, 16#4000#, 0); end loop;
        copy(64, false);
      elsif tcase = 2 then     -- 32 two-row objects at Y = 35: two parts each in row 5
        for k in 0 to 31 loop put(k, 10 * k, 35, 16#FF#, 2, 16#02#, 0, 16#4000#, 16); end loop;
        copy(32, false);
      elsif tcase = 3 then     -- 255 objects inside row 5 (1 KB: the buffer ends the expansion)
        for k in 0 to 254 loop put(k, 4 * k, 40, 16#FF#, 1, 16#02#, 0, 16#4000#, 0); end loop;
        copy(255, false);
      elsif tcase = 4 then     -- 128 objects inside row 5
        for k in 0 to 127 loop put(k, 5 * k, 40, 16#FF#, 1, 16#02#, 0, 16#4000#, 0); end loop;
        copy(128, false);
      else                     -- 255 objects inside row 5 again (all of them fit 2 KB)
        for k in 0 to 254 loop put(k, 2 * k, 40, 16#FF#, 1, 16#02#, 0, 16#4000#, 0); end loop;
        copy(255, false);
      end if;
      clr <= '1'; tick; clr <= '0';
      row <= to_unsigned(5, 8); start_scan <= '1'; scan_en <= '1'; out_start <= to_unsigned(96, BUFW);
      tick; start <= '1';
      tick; start <= '0';
      tcnt := 0; tfirst := -1; nparts := 0;
      for n in 1 to 20000 loop
        tick; tcnt := tcnt + 1;
        if part_s = '1' then
          nparts := nparts + 1;
          if tfirst < 0 then tfirst := tcnt; end if;
          tlast := tcnt;
        end if;
        exit when done = '1';
      end loop;
      report "TIMING case " & integer'image(tcase) & ": " & integer'image(nparts) & " parts, "
        & integer'image(tcnt) & " cycles start -> done, first part at " & integer'image(tfirst)
        & ", per part " & integer'image((tlast - tfirst) / maximum(nparts - 1, 1))
        & " (MLAT " & integer'image(MLAT) & ")";
      tick;
    end loop;

    if port_errs /= 0 then
      report integer'image(port_errs) & " port / table write errors" severity error;
      errs := errs + port_errs;
    end if;
    if errs = 0 then
      report "TB_RRB_LIST_V2_PASS (MLAT " & integer'image(MLAT) & ")";
    else
      report "TB_RRB_LIST_V2_FAIL " & integer'image(errs) & " errors" severity failure;
    end if;
    std.env.stop;
    wait;
  end process;
end architecture;
