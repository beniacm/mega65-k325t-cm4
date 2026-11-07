-- tb_rrb_list_expander.vhdl -- GHDL test of rrb_list_expander (RRB list mode, K325T).
-- Fixed-latency chip RAM (MLAT 4) and colour RAM (CLAT 1) models, two entries referencing the
-- same object (absolute X + FLIPX + draw mask; relative X + FLIPY + transparent), garbage after
-- the object's $FFFF (must be discarded), then the end-of-lists word.  Run with -gLIMIT=24 for
-- the buffer-full case.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_rrb_list_expander is
  generic ( LIMIT : integer := 1020 );
end entity;

architecture sim of tb_rrb_list_expander is
  constant MLAT : integer := 4;
  signal clk : std_logic := '0';
  signal start, busy, done, mreq, bwe : std_logic := '0';
  signal ent_addr : unsigned(19 downto 0) := x"10000";
  signal out_start : unsigned(9 downto 0) := to_unsigned(10, 10);
  signal out_len, baddr : unsigned(9 downto 0);
  signal maddr : unsigned(19 downto 0);
  signal caddr : unsigned(15 downto 0);
  signal mdata, cdata, bscreen, bcolour : unsigned(7 downto 0) := x"00";

  type mem_t is array (0 to 255) of unsigned(7 downto 0);
  -- chip RAM: entries at $10000 (index 0..), object at $20000 (index 128..)
  function chip(a : unsigned(19 downto 0)) return unsigned is
    type r_t is array (natural range <>) of integer;
    constant E : r_t := (16#64#, 16#40#, 16#F0#, 16#12#, 0, 0, 0, 16#40#,   -- X 100, Y+2, mask F0, FLIPX
                         16#28#, 16#04#, 16#FF#, 16#62#, 0, 0, 0, 16#40#,   -- REL +40, FLIPY, transparent
                         16#FF#, 16#FF#, 0, 0, 0, 0, 0, 0);                 -- end of lists
    constant O : r_t := (16#23#, 16#01#, 16#08#, 16#00#, 16#24#, 16#01#,    -- glyph, GOTOX 8, glyph
                         16#FF#, 16#FF#, 16#AA#, 16#AA#, 16#AA#, 16#AA#, 16#AA#, 16#AA#);
    variable i : integer;
  begin
    if a(19 downto 8) = x"100" then
      i := to_integer(a(7 downto 0));
      if i < E'length then return to_unsigned(E(i), 8); end if;
    elsif a(19 downto 8) = x"200" then
      i := to_integer(a(7 downto 0));
      if i < O'length then return to_unsigned(O(i), 8); end if;
    end if;
    return x"EE";
  end function;
  function col(a : unsigned(15 downto 0)) return unsigned is
    type r_t is array (natural range <>) of integer;
    constant C : r_t := (16#00#, 16#05#, 16#10#, 16#00#, 16#40#, 16#07#, 16#00#, 16#00#,
                         16#BB#, 16#BB#, 16#BB#, 16#BB#, 16#BB#, 16#BB#);
    variable i : integer;
  begin
    if a(15 downto 8) = x"40" then
      i := to_integer(a(7 downto 0));
      if i < C'length then return to_unsigned(C(i), 8); end if;
    end if;
    return x"CC";
  end function;

  type buf_t is array (0 to 1023) of unsigned(7 downto 0);
  signal bs, bc : buf_t := (others => x"55");
  signal maxw : integer := 0;
  type pipe_t is array (1 to MLAT) of unsigned(7 downto 0);
  signal mp : pipe_t := (others => x"00");
begin
  clk <= not clk after 5 ns;

  dut : entity work.rrb_list_expander
    generic map (MLAT => MLAT, CLAT => 1, LIMIT => LIMIT)
    port map (clk => clk, start => start, ent_addr => ent_addr, out_start => out_start,
              busy => busy, done => done, out_len => out_len,
              mreq => mreq, maddr => maddr, mdata => mdata, caddr => caddr, cdata => cdata,
              bwe => bwe, baddr => baddr, bscreen => bscreen, bcolour => bcolour);

  -- memories: chip RAM data MLAT cycles after the request (registered request + pipeline),
  -- colour RAM one cycle after the address
  process (clk) begin
    if rising_edge(clk) then
      mp(1) <= chip(maddr);
      for n in 2 to MLAT - 1 loop mp(n) <= mp(n - 1); end loop;
      cdata <= col(caddr);
      if bwe = '1' then
        bs(to_integer(baddr)) <= bscreen; bc(to_integer(baddr)) <= bcolour;
        if to_integer(baddr) > maxw then maxw <= to_integer(baddr); end if;
      end if;
    end if;
  end process;
  mdata <= mp(MLAT - 1);       -- request registered by the DUT (+1) + MLAT-1 stages = MLAT

  process
    type exp_t is array (natural range <>) of integer;
    -- buffer from index 10: (screen, colour) per byte
    constant XS : exp_t := (16#64#, 16#40#,  16#23#, 16#01#, 16#08#, 16#00#, 16#24#, 16#01#,
                            16#8C#, 16#00#,  16#23#, 16#01#, 16#08#, 16#00#, 16#24#, 16#01#,
                            16#FF#, 16#FF#);
    constant XC : exp_t := (16#18#, 16#F0#,  16#40#, 16#05#, 16#10#, 16#00#, 16#00#, 16#07#,
                            16#90#, 16#FF#,  16#80#, 16#05#, 16#10#, 16#00#, 16#C0#, 16#07#,
                            16#00#, 16#00#);
    variable errs : integer := 0;
  begin
    wait for 50 ns;
    wait until rising_edge(clk); start <= '1';
    wait until rising_edge(clk); start <= '0';
    for t in 1 to 5000 loop
      wait until rising_edge(clk);
      exit when done = '1';
    end loop;
    assert done = '1' report "no done" severity failure;
    wait until rising_edge(clk);
    if LIMIT >= 1000 then
      for n in XS'range loop
        if bs(10 + n) /= XS(n) or bc(10 + n) /= XC(n) then
          report "byte " & integer'image(10 + n) & ": screen $" & to_hstring(bs(10 + n))
                 & " colour $" & to_hstring(bc(10 + n)) & ", expected $"
                 & to_hstring(to_unsigned(XS(n), 8)) & " $" & to_hstring(to_unsigned(XC(n), 8))
                 severity error;
          errs := errs + 1;
        end if;
      end loop;
      if to_integer(out_len) /= 10 + XS'length then
        report "out_len " & integer'image(to_integer(out_len)) severity error; errs := errs + 1;
      end if;
      -- nothing written beyond the end marker (the $AA/$BB garbage was discarded)
      if maxw /= 10 + XS'length - 1 then
        report "highest write " & integer'image(maxw) severity error; errs := errs + 1;
      end if;
    else
      -- buffer full: ends with $FFFF, no write after it, nothing beyond LIMIT + 3
      if bs(to_integer(out_len) - 2) /= x"FF" or bs(to_integer(out_len) - 1) /= x"FF"
         or maxw /= to_integer(out_len) - 1 or maxw > LIMIT + 3 then
        report "limit case: out_len " & integer'image(to_integer(out_len)) & ", highest write "
               & integer'image(maxw) severity error;
        errs := errs + 1;
      end if;
    end if;
    if errs = 0 then
      report "TB_RRB_LIST_EXPANDER_PASS (LIMIT " & integer'image(LIMIT) & ", out_len "
             & integer'image(to_integer(out_len)) & ")";
    else
      report "TB_RRB_LIST_EXPANDER_FAIL " & integer'image(errs) & " errors" severity failure;
    end if;
    std.env.stop;
    wait;
  end process;
end architecture;
