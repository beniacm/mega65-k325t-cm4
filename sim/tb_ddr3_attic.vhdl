-- tb_ddr3_attic.vhdl -- ddr3_attic against a MIG native-UI model, driven like slow_devices.
-- Checks: busy until calibration, byte writes/reads within one 16-byte word and across
-- words, 8 MB + mirror after reset, the $BFFFFFC size register (unlock 64 MB, read back,
-- registers are not RAM, CPU reset -> 8 MB), all 26 address lines and the 64 MB mirror, read latency inside slow_devices' 128-cycle window,
-- app_rdy / app_wdf_rdy asserting on different cycles (cmd/data desync guard).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.cputypes.all;

entity tb_ddr3_attic is
end entity;

architecture sim of tb_ddr3_attic is
  constant TCPU : time := 12.345 ns;  -- 81 MHz pixelclock (slow_devices domain)
  constant TUI  : time := 10 ns;      -- MIG ui_clk 100 MHz
  signal cpuclk, ui_clk : std_logic := '0';
  signal done : boolean := false;

  signal ex_read, ex_write : std_logic := '0';
  signal ex_address : unsigned(26 downto 0) := (others => '0');
  signal ex_wdata, ex_rdata : unsigned(7 downto 0) := (others => '0');
  signal ex_tog, ex_busy, calibrated : std_logic;
  signal cline : cache_row_t;
  signal caddr : unsigned(26 downto 3);
  signal cvalid : std_logic;
  signal cnext, cprev : std_logic := '0';

  signal ui_rst : std_logic := '1';
  signal calib : std_logic := '0';
  signal app_addr : std_logic_vector(27 downto 0);
  signal app_cmd : std_logic_vector(2 downto 0);
  signal app_en, app_wdf_end, app_wdf_wren : std_logic;
  signal app_rdy, app_wdf_rdy : std_logic := '0';
  signal app_wdf_data, app_rd_data : std_logic_vector(127 downto 0) := (others => '0');
  signal app_wdf_mask : std_logic_vector(15 downto 0);
  signal app_rd_data_valid : std_logic := '0';

  type mem_t is array (0 to 4095) of std_logic_vector(127 downto 0);
  type tag_t is array (0 to 4095) of integer;
  signal reset_n : std_logic := '1';
  constant REG : integer := 16#3FFFFFC#;            -- $BFFFFFC
  constant AB : integer := 26;                    -- ATTIC_BITS as in the K325T top (64 MB)
begin
  cpuclk <= not cpuclk after TCPU / 2 when not done;
  ui_clk <= not ui_clk after TUI / 2 when not done;

  dut : entity work.ddr3_attic
    generic map (ATTIC_BITS => AB, COMPAT_BITS => 23)
    port map (cpuclk => cpuclk, reset_n => reset_n, ex_read => ex_read, ex_write => ex_write, ex_address => ex_address,
              ex_wdata => ex_wdata, ex_rdata => ex_rdata, ex_data_ready_toggle => ex_tog,
              ex_busy => ex_busy, ui_clk => ui_clk, ui_rst => ui_rst, init_calib_complete => calib,
              app_addr => app_addr, app_cmd => app_cmd, app_en => app_en, app_rdy => app_rdy,
              app_wdf_data => app_wdf_data, app_wdf_end => app_wdf_end, app_wdf_wren => app_wdf_wren,
              app_wdf_mask => app_wdf_mask, app_wdf_rdy => app_wdf_rdy, app_rd_data => app_rd_data,
              app_rd_data_valid => app_rd_data_valid,
              cache_line => cline, cache_line_addr => caddr, cache_line_valid => cvalid,
              cache_next_toggle => cnext, cache_prev_toggle => cprev,
              calibrated => calibrated);

  -- MIG model: independent pseudo-random ready patterns, masked writes, 20-cycle reads.
  -- Sparse memory over the FULL app_addr (open hashing with tags, 4096 words), so aliasing
  -- can only come from the DUT; never-written words read 0.
  mig : process
    variable mem : mem_t := (others => (others => '0'));
    variable tag : tag_t := (others => -1);
    variable rd_slot : integer;
    impure function slot(wa : integer; claim : boolean) return integer is
      variable i : integer := wa mod 4093;
    begin
      for n in 0 to 4095 loop
        if tag(i) = wa then return i; end if;
        if tag(i) = -1 then
          if claim then tag(i) := wa; return i; else return -1; end if;
        end if;
        i := (i + 1) mod 4096;
      end loop;
      report "MIG model full" severity failure; return -1;
    end function;
    variable lfsr : unsigned(15 downto 0) := x"ACE1";
    variable pend_cmd, pend_dat : boolean := false;
    variable cmd_addr : integer;
    variable rd_q : integer := -1;
    variable rd_cnt : integer := 0;
    variable wdat : std_logic_vector(127 downto 0);
    variable wmask : std_logic_vector(15 downto 0);
    variable w : std_logic_vector(127 downto 0);
    variable cyc, wcmd_cyc : integer := 0;
  begin
    wait until rising_edge(ui_clk);
    lfsr := lfsr(14 downto 0) & (lfsr(15) xor lfsr(13) xor lfsr(12) xor lfsr(10));
    cyc := cyc + 1;
    -- app_rdy / app_wdf_rdy for THIS cycle (decided last cycle -> registered like MIG)
    if app_en = '1' and app_rdy = '1' then
      assert app_addr(2 downto 0) = "000" report "app_addr not word aligned" severity failure;
      cmd_addr := to_integer(unsigned(app_addr(27 downto 3)));
      if app_cmd = "001" then
        rd_q := cmd_addr; rd_cnt := 20;
      else
        pend_cmd := true;
        wcmd_cyc := cyc;
      end if;
    end if;
    if app_wdf_wren = '1' and app_wdf_rdy = '1' then
      assert app_wdf_end = '1' report "wdf without end" severity failure;
      -- UG586: write data at most two cycles after its command
      assert not pend_cmd or cyc - wcmd_cyc <= 2
        report "MIG rule: write data " & integer'image(cyc - wcmd_cyc) & " cycles after its command" severity failure;
      wdat := app_wdf_data; wmask := app_wdf_mask; pend_dat := true;
    end if;
    if pend_cmd and pend_dat then
      cmd_addr := slot(cmd_addr, true);
      w := mem(cmd_addr);
      for i in 0 to 15 loop
        if wmask(i) = '0' then w(8 * i + 7 downto 8 * i) := wdat(8 * i + 7 downto 8 * i); end if;
      end loop;
      mem(cmd_addr) := w;
      pend_cmd := false; pend_dat := false;
    end if;
    app_rd_data_valid <= '0';
    if rd_cnt > 0 then
      rd_cnt := rd_cnt - 1;
      if rd_cnt = 0 then
        rd_slot := slot(rd_q, false);
        if rd_slot < 0 then app_rd_data <= (others => '0'); else app_rd_data <= mem(rd_slot); end if;
        app_rd_data_valid <= '1';
      end if;
    end if;
    app_rdy     <= lfsr(0) or lfsr(5);          -- ~75 %
    app_wdf_rdy <= lfsr(3) and not lfsr(8);     -- ~25 %, independent of app_rdy
  end process;

  drv : process
    variable t0 : time;
    variable cycles : integer;
    variable maxcyc : integer := 0;
    type addr_list is array (natural range <>) of integer;
    constant A : addr_list := (0, 1, 2, 15, 16, 17, 31, 4095, 16#12345#, 16#7FFFFF#, 16#7FFFF0#,
                               16#3FFFFEE#, 16#3FFFFE0#, 16#2000000#, 16#2123456#);
    procedure wr(a : integer; d : integer) is
    begin
      wait until rising_edge(cpuclk) and ex_busy = '0';
      ex_address <= to_unsigned(a, 27); ex_wdata <= to_unsigned(d, 8); ex_write <= '1';
      wait until rising_edge(cpuclk);
      ex_write <= '0';
    end procedure;
    procedure rd(a : integer; expect : integer) is
      variable tog : std_logic;
    begin
      wait until rising_edge(cpuclk) and ex_busy = '0';
      tog := ex_tog;
      ex_address <= to_unsigned(a, 27); ex_read <= '1';
      wait until rising_edge(cpuclk);
      ex_read <= '0';
      cycles := 0;
      while ex_tog = tog loop
        wait until rising_edge(cpuclk);
        cycles := cycles + 1;
        assert cycles < 128 report "read exceeded slow_devices' 128-cycle timeout" severity failure;
      end loop;
      if cycles > maxcyc then maxcyc := cycles; end if;
      assert to_integer(ex_rdata) = expect
        report "addr " & integer'image(a) & ": got " & integer'image(to_integer(ex_rdata)) &
               " expected " & integer'image(expect) severity failure;
    end procedure;
  begin
    wait for 200 ns; ui_rst <= '0';
    wait for 1 us;
    assert ex_busy = '1' report "busy must stay high before calibration" severity failure;
    calib <= '1';
    wait for 200 ns;
    assert ex_busy = '0' report "not ready after calibration" severity failure;
    -- ---- after reset: 8 MB with mirrors, like a real MEGA65 ----
    rd(REG, 8);
    wr(16#10#, 16#11#); rd(16#800010#, 16#11#); rd(16#3800010#, 16#11#);
    wr(16#1000020#, 16#22#); rd(16#20#, 16#22#);
    rd(16#30#, 0);                                      -- publishes line $30
    wr(16#800031#, 16#33#);                             -- via the 8 MB mirror: write-through
    wait until rising_edge(cpuclk) and ex_busy = '0';
    assert cline(1) = x"33" report "8 MB mirror write not coherent" severity failure;
    wr(REG, 16); rd(REG, 8);                            -- other values keep 8 MB
    report "8 MB compat: mirrors, register, write-through ok";
    -- ---- unlock 64 MB ----
    wr(REG, 64);
    wait until rising_edge(cpuclk) and ex_busy = '0';
    assert cvalid = '0' report "cache line not invalidated on size change" severity failure;
    rd(REG, 64);
    rd(16#800010#, 0);                                  -- no longer the mirror of $10
    -- distinct value per address, written in one pass, then read back
    for i in A'range loop wr(A(i), (i * 29 + 7) mod 256); end loop;
    for i in A'range loop rd(A(i), (i * 29 + 7) mod 256); end loop;
    -- neighbours inside the same 16-byte word were not disturbed by masked writes
    rd(3, 0); rd(14, 0); rd(18, 0);
    -- every address line: $2^k + 9 (k = 4..AB-1) gets its own byte, base untouched
    for k in 4 to AB - 1 loop wr(2 ** k + 9, 100 + k); end loop;
    for k in 4 to AB - 1 loop rd(2 ** k + 9, 100 + k); end loop;
    rd(0, 7); rd(9, 0);
    -- 8 MB is no longer a mirror; 64 MB is: $4000000 aliases $000000
    rd(16#800000#, 0);
    rd(16#4000000#, 7); rd(16#4000001#, 36);
    wr(16#4000010#, 16#5A#); rd(16#10#, 16#5A#);
    report "64 MB: " & integer'image(AB) & " address lines distinct, mirror at 64 MB ok";
    -- dense sequential pattern across several words
    for i in 0 to 63 loop wr(16#2000# + i, (i * 7 + 3) mod 256); end loop;
    for i in 0 to 63 loop rd(16#2000# + i, (i * 7 + 3) mod 256); end loop;
    -- ---- CPU fast path: published line, write-through, mirror, prefetch ----
    rd(16#2008#, (8 * 7 + 3) mod 256);                 -- line $2008-$200F
    assert cvalid = '1' and caddr = to_unsigned(16#2008#, 27)(26 downto 3)
      report "line not published after read" severity failure;
    for i in 0 to 7 loop
      assert to_integer(cline(i)) = ((8 + i) * 7 + 3) mod 256 report "line byte wrong" severity failure;
    end loop;
    wr(16#200A#, 16#C3#);                               -- write into the cached line
    wait until rising_edge(cpuclk) and ex_busy = '0';
    assert cline(2) = x"C3" and cvalid = '1' report "write-through failed" severity failure;
    wr(16#400200B#, 16#3C#);                            -- same physical line via the 64 MB mirror
    wait until rising_edge(cpuclk) and ex_busy = '0';
    assert cline(3) = x"3C" report "mirror write not coherent" severity failure;
    t0 := now;
    cnext <= not cnext;                                 -- CPU: prefetch next line ($2010)
    wait until rising_edge(cpuclk) and caddr = to_unsigned(16#2010#, 27)(26 downto 3) for 5 us;
    wait until rising_edge(cpuclk) and ex_busy = '0';
    assert caddr = to_unsigned(16#2010#, 27)(26 downto 3) report "next-line prefetch failed" severity failure;
    for i in 0 to 7 loop
      assert to_integer(cline(i)) = ((16 + i) * 7 + 3) mod 256 report "prefetched byte wrong" severity failure;
    end loop;
    cprev <= not cprev;                                 -- back to $2008 (with the written bytes)
    wait until rising_edge(cpuclk) and caddr = to_unsigned(16#2008#, 27)(26 downto 3) for 5 us;
    wait until rising_edge(cpuclk) and ex_busy = '0';
    assert cline(2) = x"C3" and cline(3) = x"3C" report "prev-line prefetch / DDR content wrong" severity failure;
    report "cache line: publish, write-through, mirror coherence, next/prev prefetch ok";
    -- registers are not RAM; the RAM line just below is, but no prefetch into the registers
    wr(16#3FFFFF5#, 16#77#); rd(16#3FFFFF5#, 0);
    wr(16#3FFFFEF#, 16#EF#); rd(16#3FFFFEF#, 16#EF#);   -- publishes line $3FFFFE8
    cnext <= not cnext;
    for i in 0 to 200 loop wait until rising_edge(cpuclk); end loop;
    assert caddr = to_unsigned(16#3FFFFE8#, 27)(26 downto 3) and ex_busy = '0'
      report "prefetch went into the register line" severity failure;
    rd(REG, 64);
    -- ---- CPU reset: back to 8 MB ----
    reset_n <= '0';
    for i in 0 to 9 loop wait until rising_edge(cpuclk); end loop;
    reset_n <= '1';
    for i in 0 to 3 loop wait until rising_edge(cpuclk); end loop;
    assert cvalid = '0' report "cache line not invalidated by reset" severity failure;
    rd(REG, 8);
    rd(16#800010#, 16#5A#);                             -- mirror of $10 again
    report "size register: unlock, read back, not RAM, no register prefetch, reset -> 8 MB ok";
    report "max read latency " & integer'image(maxcyc) & " cpu cycles (limit 128)";
    report "TB_DDR3_ATTIC_PASS";
    done <= true;
    wait;
  end process;
end architecture;
