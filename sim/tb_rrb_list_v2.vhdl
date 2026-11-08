-- tb_rrb_list_v2.vhdl -- GHDL test of rrb_list_expander list mode v2 (global object table).
-- Copies a 6-object table from a fake chip RAM into the unit's BRAM (copy_start), then expands
-- character rows 0..3 with start_scan and compares the row buffer with the expected tokens:
-- objects straddling two rows (top part / bottom part masks and Y offsets), REL X, a hidden
-- object (mask 0, still an anchor for REL), multi-row objects (stride), FLIPX, FLIPY (rows in
-- reverse order, Y offset sign swapped), a negative Y.  Also: a v1 LIST row with scan_en (v2
-- objects follow the row's own entries).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_rrb_list_v2 is
  generic ( MLAT : integer := 3 );
end entity;

architecture sim of tb_rrb_list_v2 is
  signal clk : std_logic := '0';
  signal start, busy, done, mreq, bwe, start_scan, scan_en, copy_start, copying : std_logic := '0';
  signal ent_addr : unsigned(19 downto 0) := x"10000";
  signal out_start : unsigned(9 downto 0) := to_unsigned(0, 10);
  signal out_len, baddr : unsigned(9 downto 0);
  signal maddr : unsigned(19 downto 0);
  signal caddr : unsigned(15 downto 0);
  signal mdata, cdata, bscreen, bcolour : unsigned(7 downto 0) := x"00";
  signal row : unsigned(7 downto 0) := x"00";

  type r_t is array (natural range <>) of integer;
  -- object table at $30000: X lo/hi, Y lo/hi, mask, height, flags, res, addr lo/hi, col lo/hi,
  -- stride lo/hi, res, res
  constant T : r_t := (
    100, 0,  13, 0,  16#FF#, 1, 16#02#, 0,  0, 0,  0, 16#40#,  0, 0,  0, 0,     -- 0: X100 Y13
     50, 4,  16, 0,  16#FF#, 2, 16#12#, 0,  0, 0,  0, 16#40#, 16, 0,  0, 0,     -- 1: REL+50 Y16 H2 FLIPX
    244, 1,   8, 0,  16#00#, 1, 16#02#, 0,  0, 0,  0, 16#40#,  0, 0,  0, 0,     -- 2: hidden (X500)
     44, 1, 253, 3,  16#FF#, 1, 16#02#, 0,  0, 0,  0, 16#40#,  0, 0,  0, 0,     -- 3: X300 Y-3
    144, 1,   8, 0,  16#FF#, 2, 16#22#, 0,  0, 0,  0, 16#40#, 16, 0,  0, 0,     -- 4: X400 Y8 H2 FLIPY
     88, 2,  12, 0,  16#FF#, 2, 16#22#, 0,  0, 0,  0, 16#40#, 16, 0,  0, 0);    -- 5: X600 Y12 H2 FLIPY
  -- object data at $20000 (row 0) and $20010 (row 1), colour at $4000 / $4010
  constant O0 : r_t := (16#01#, 16#01#, 16#FF#, 16#FF#, 16#AA#, 16#AA#, 16#AA#, 16#AA#);
  constant O1 : r_t := (16#02#, 16#02#, 16#FF#, 16#FF#, 16#AA#, 16#AA#, 16#AA#, 16#AA#);
  constant C0 : r_t := (16#00#, 16#05#, 16#00#, 16#00#, 16#BB#, 16#BB#);
  constant C1 : r_t := (16#00#, 16#06#, 16#00#, 16#00#, 16#BB#, 16#BB#);
  -- v1 entries at $10000: one entry X 20 -> object row 0, then end of lists
  constant E : r_t := (20, 0, 16#FF#, 16#02#, 0, 0, 0, 16#40#,  16#FF#, 16#FF#, 0, 0, 0, 0, 0, 0);

  function chip(a : unsigned(19 downto 0)) return unsigned is
    variable i : integer;
  begin
    i := to_integer(a(7 downto 0));
    if a(19 downto 8) = x"300" and i < T'length then return to_unsigned(T(i), 8); end if;
    if a(19 downto 8) = x"100" and i < E'length then return to_unsigned(E(i), 8); end if;
    if a(19 downto 4) = x"2000" and i mod 16 < O0'length then return to_unsigned(O0(i mod 16), 8); end if;
    if a(19 downto 4) = x"2001" and i mod 16 < O1'length then return to_unsigned(O1(i mod 16), 8); end if;
    return x"EE";
  end function;
  function col(a : unsigned(15 downto 0)) return unsigned is
    variable i : integer;
  begin
    i := to_integer(a(3 downto 0));
    if a(15 downto 4) = x"400" and i < C0'length then return to_unsigned(C0(i), 8); end if;
    if a(15 downto 4) = x"401" and i < C1'length then return to_unsigned(C1(i), 8); end if;
    return x"CC";
  end function;

  type buf_t is array (0 to 1023) of unsigned(7 downto 0);
  signal bs, bc : buf_t := (others => x"55");
  type pipe_t is array (1 to MLAT) of unsigned(7 downto 0);
  signal mp : pipe_t := (others => x"00");
  signal clr : std_logic := '0';
begin
  clk <= not clk after 5 ns;

  dut : entity work.rrb_list_expander
    generic map (MLAT => MLAT, CLAT => 1, LIMIT => 1020)
    port map (clk => clk, start => start, ent_addr => ent_addr, out_start => out_start,
              busy => busy, done => done, out_len => out_len,
              mreq => mreq, maddr => maddr, mdata => mdata, caddr => caddr, cdata => cdata,
              bwe => bwe, baddr => baddr, bscreen => bscreen, bcolour => bcolour,
              scan_en => scan_en, start_scan => start_scan, row => row, obj_count => x"06",
              copy_start => copy_start, tab_addr => x"30000", copying => copying);

  process (clk) begin
    if rising_edge(clk) then
      mp(1) <= chip(maddr);
      for n in 2 to MLAT - 1 loop mp(n) <= mp(n - 1); end loop;
      cdata <= col(caddr);
      if clr = '1' then
        bs <= (others => x"55"); bc <= (others => x"55");
      elsif bwe = '1' then
        bs(to_integer(baddr)) <= bscreen; bc(to_integer(baddr)) <= bcolour;
      end if;
    end if;
  end process;
  mdata <= mp(MLAT - 1);

  process
    variable errs : integer := 0;
    -- 4 numbers per token: screen lo, screen hi, colour 0, colour 1
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

    procedure expand(r : integer; scan_only : std_logic) is
    begin
      clr <= '1'; wait until rising_edge(clk); clr <= '0';
      row <= to_unsigned(r, 8); start_scan <= scan_only; scan_en <= '1';
      wait until rising_edge(clk); start <= '1';
      wait until rising_edge(clk); start <= '0';
      for n in 1 to 5000 loop
        wait until rising_edge(clk);
        exit when done = '1';
      end loop;
      assert done = '1' report "no done" severity failure;
      wait until rising_edge(clk);
    end procedure;

    procedure check(name : string; x : r_t) is
      variable k : integer;
    begin
      for n in 0 to x'length / 4 - 1 loop
        for b in 0 to 1 loop
          k := 2 * n + b;
          if bs(k) /= x(4 * n + b) or bc(k) /= x(4 * n + 2 + b) then
            report name & " byte " & integer'image(k) & ": screen $" & to_hstring(bs(k))
                   & " colour $" & to_hstring(bc(k)) & ", expected $"
                   & to_hstring(to_unsigned(x(4 * n + b), 8)) & " $"
                   & to_hstring(to_unsigned(x(4 * n + 2 + b), 8)) severity error;
            errs := errs + 1;
          end if;
        end loop;
      end loop;
      if to_integer(out_len) /= x'length / 2 then
        report name & ": out_len " & integer'image(to_integer(out_len)) severity error;
        errs := errs + 1;
      end if;
      if bs(x'length / 2) /= x"55" then
        report name & ": written beyond the end" severity error; errs := errs + 1;
      end if;
    end procedure;
  begin
    wait for 50 ns;
    wait until rising_edge(clk); copy_start <= '1';
    wait until rising_edge(clk); copy_start <= '0';
    wait until rising_edge(clk);
    wait for 1 ns;
    assert copying = '1' report "copying not set" severity failure;
    while copying = '1' loop wait until rising_edge(clk); wait for 1 ns; end loop;
    for n in 1 to 4 loop wait until rising_edge(clk); end loop;
    expand(0, '1'); check("row 0", R0);
    expand(1, '1'); check("row 1", R1);
    expand(2, '1'); check("row 2", R2);
    expand(3, '1'); check("row 3", R3);
    expand(0, '0'); check("LIST row 0", RL);
    if errs = 0 then
      report "TB_RRB_LIST_V2_PASS (MLAT " & integer'image(MLAT) & ")";
    else
      report "TB_RRB_LIST_V2_FAIL " & integer'image(errs) & " errors" severity failure;
    end if;
    std.env.stop;
    wait;
  end process;
end architecture;
