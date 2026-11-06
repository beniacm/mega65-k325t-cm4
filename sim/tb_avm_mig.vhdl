-- tb_avm_mig.vhdl -- avm_mig (M2M Avalon-MM memory bus -> MIG native UI) against a MIG model
-- with pipelined in-order reads (variable latency) and independent random app_rdy /
-- app_wdf_rdy.  Random write and read bursts (any start offset, 1..255 beats, partial
-- byte enables) are checked against a reference memory; back-to-back bursts and the
-- read-FIFO credit limit (long bursts, slow consumer is not possible on Avalon, so long
-- MIG latency) are exercised.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_avm_mig is
end entity;

architecture sim of tb_avm_mig is
  constant T : time := 10 ns;
  signal clk : std_logic := '0';
  signal rst : std_logic := '1';
  signal done : boolean := false;

  signal avm_write, avm_read : std_logic := '0';
  signal avm_address : std_logic_vector(31 downto 0) := (others => '0');
  signal avm_writedata : std_logic_vector(15 downto 0) := (others => '0');
  signal avm_byteenable : std_logic_vector(1 downto 0) := "11";
  signal avm_burstcount : std_logic_vector(7 downto 0) := x"01";
  signal avm_readdata : std_logic_vector(15 downto 0);
  signal avm_readdatavalid, avm_waitrequest : std_logic;

  signal app_addr : std_logic_vector(27 downto 0);
  signal app_cmd : std_logic_vector(2 downto 0);
  signal app_en, app_wdf_end, app_wdf_wren : std_logic;
  signal app_rdy, app_wdf_rdy : std_logic := '0';
  signal app_wdf_data, app_rd_data : std_logic_vector(127 downto 0) := (others => '0');
  signal app_wdf_mask : std_logic_vector(15 downto 0);
  signal app_rd_data_valid : std_logic := '0';

  constant WORDS : integer := 4096;                  -- model: 4096 x 128 bit (32K Avalon words)
  type mem_t is array (0 to WORDS - 1) of std_logic_vector(127 downto 0);
  signal max_inflight : integer := 0;
begin
  clk <= not clk after T / 2 when not done;

  dut : entity work.avm_mig
    port map (clk => clk, rst => rst, avm_write => avm_write, avm_read => avm_read,
              avm_address => avm_address, avm_writedata => avm_writedata,
              avm_byteenable => avm_byteenable, avm_burstcount => avm_burstcount,
              avm_readdata => avm_readdata, avm_readdatavalid => avm_readdatavalid,
              avm_waitrequest => avm_waitrequest,
              app_addr => app_addr, app_cmd => app_cmd, app_en => app_en, app_rdy => app_rdy,
              app_wdf_data => app_wdf_data, app_wdf_end => app_wdf_end, app_wdf_wren => app_wdf_wren,
              app_wdf_mask => app_wdf_mask, app_wdf_rdy => app_wdf_rdy, app_rd_data => app_rd_data,
              app_rd_data_valid => app_rd_data_valid);

  mig : process
    variable mem : mem_t := (others => (others => '0'));
    variable lfsr : unsigned(15 downto 0) := x"BEEF";
    type q_t is array (0 to 63) of integer;
    variable rq_addr, rq_due : q_t;
    variable rq_head, rq_tail, rq_n : integer := 0;
    variable cyc : integer := 0;
    variable wq_addr : q_t;
    variable wq_n : integer := 0;
    type dq_t is array (0 to 63) of std_logic_vector(127 downto 0);
    variable dq_data : dq_t;
    variable dq_mask : q_t;
    variable dq_n : integer := 0;
    variable w : std_logic_vector(127 downto 0);
    variable m : std_logic_vector(15 downto 0);
    variable lat : integer;
    variable wc_cyc : q_t;                    -- cycle each write command was accepted
    variable wc_n, wd_seen : integer := 0;
  begin
    wait until rising_edge(clk);
    cyc := cyc + 1;
    lfsr := lfsr(14 downto 0) & (lfsr(15) xor lfsr(13) xor lfsr(12) xor lfsr(10));
    if app_en = '1' and app_rdy = '1' then
      assert app_addr(2 downto 0) = "000" report "app_addr not aligned" severity failure;
      if app_cmd = "001" then
        lat := 18 + to_integer(lfsr(3 downto 0));            -- 18..33 cycles, in order
        if rq_n > 0 and cyc + lat <= rq_due((rq_tail + 63) mod 64) then
          lat := rq_due((rq_tail + 63) mod 64) - cyc + 1;
        end if;
        rq_addr(rq_tail) := to_integer(unsigned(app_addr(27 downto 3))) mod WORDS;
        rq_due(rq_tail) := cyc + lat;
        rq_tail := (rq_tail + 1) mod 64; rq_n := rq_n + 1;
        if rq_n > max_inflight then max_inflight <= rq_n; end if;
      elsif app_cmd = "000" then
        wq_addr(wq_n) := to_integer(unsigned(app_addr(27 downto 3))) mod WORDS; wq_n := wq_n + 1;
        wc_cyc(wc_n mod 64) := cyc; wc_n := wc_n + 1;
      else
        report "bad app_cmd" severity failure;
      end if;
    end if;
    if app_wdf_wren = '1' and app_wdf_rdy = '1' then
      if wd_seen < wc_n then   -- UG586: data at most two cycles after its command
        assert cyc - wc_cyc(wd_seen mod 64) <= 2
          report "MIG rule: write data " & integer'image(cyc - wc_cyc(wd_seen mod 64)) & " cycles after its command" severity failure;
      end if;
      wd_seen := wd_seen + 1;
      assert app_wdf_end = '1' report "wdf without end" severity failure;
      dq_data(dq_n) := app_wdf_data;
      dq_mask(dq_n) := to_integer(unsigned(app_wdf_mask)); dq_n := dq_n + 1;
    end if;
    -- pair write commands with write data (in order)
    while wq_n > 0 and dq_n > 0 loop
      w := mem(wq_addr(0)); m := std_logic_vector(to_unsigned(dq_mask(0), 16));
      for i in 0 to 15 loop
        if m(i) = '0' then w(8 * i + 7 downto 8 * i) := dq_data(0)(8 * i + 7 downto 8 * i); end if;
      end loop;
      mem(wq_addr(0)) := w;
      for k in 0 to 62 loop
        wq_addr(k) := wq_addr(k + 1); dq_data(k) := dq_data(k + 1); dq_mask(k) := dq_mask(k + 1);
      end loop;
      wq_n := wq_n - 1; dq_n := dq_n - 1;
    end loop;
    app_rd_data_valid <= '0';
    if rq_n > 0 and rq_due(rq_head) <= cyc then
      app_rd_data <= mem(rq_addr(rq_head));
      app_rd_data_valid <= '1';
      rq_head := (rq_head + 1) mod 64; rq_n := rq_n - 1;
    end if;
    app_rdy     <= lfsr(0) or lfsr(6) or lfsr(9);     -- ~87 %
    app_wdf_rdy <= lfsr(2) or lfsr(11);               -- ~75 %, independent
  end process;

  drv : process
    type ref_t is array (0 to WORDS * 8 - 1) of std_logic_vector(15 downto 0);
    variable ref : ref_t := (others => (others => '0'));
    variable seed : unsigned(31 downto 0) := x"12345679";
    variable a, n, beats, nreads, nwrites : integer := 0;
    variable be : std_logic_vector(1 downto 0);
    variable d, g : std_logic_vector(15 downto 0);
    variable t0 : time;
    impure function rnd(lim : integer) return integer is
    begin
      seed := resize(seed * to_unsigned(1103515245, 32) + 12345, 32);
      return to_integer(seed(30 downto 15)) mod lim;
    end function;
    procedure write_burst(addr, len : integer; partial : boolean) is
      variable bt : integer := 0;
    begin
      avm_address <= std_logic_vector(to_unsigned(addr, 32));
      avm_burstcount <= std_logic_vector(to_unsigned(len, 8));
      while bt < len loop
        d := std_logic_vector(to_unsigned(rnd(65536), 16));
        if partial then be := std_logic_vector(to_unsigned(1 + rnd(3), 2)); else be := "11"; end if;
        avm_write <= '1'; avm_writedata <= d; avm_byteenable <= be;
        wait until rising_edge(clk);
        if avm_waitrequest = '0' then
          if be(0) = '1' then ref(addr + bt)(7 downto 0) := d(7 downto 0); end if;
          if be(1) = '1' then ref(addr + bt)(15 downto 8) := d(15 downto 8); end if;
          bt := bt + 1;
        end if;
      end loop;
      avm_write <= '0';
    end procedure;
    procedure read_burst(addr, len : integer) is
      variable bt : integer := 0;
    begin
      avm_address <= std_logic_vector(to_unsigned(addr, 32));
      avm_burstcount <= std_logic_vector(to_unsigned(len, 8));
      avm_read <= '1';
      loop
        wait until rising_edge(clk);
        exit when avm_waitrequest = '0';
      end loop;
      avm_read <= '0';
      t0 := now;
      while bt < len loop
        wait until rising_edge(clk);
        if avm_readdatavalid = '1' then
          g := avm_readdata;
          assert g = ref(addr + bt)
            report "READ MISMATCH addr " & integer'image(addr + bt) & " beat " & integer'image(bt) &
                   " of " & integer'image(len) severity failure;
          bt := bt + 1;
        end if;
        assert now - t0 < 20 us report "read burst stalled" severity failure;
      end loop;
    end procedure;
  begin
    wait for 100 ns;
    wait until rising_edge(clk);
    rst <= '0';
    -- fixed cases: single words, unaligned starts, word-boundary crossings, max burst
    write_burst(0, 1, false);  read_burst(0, 1);
    write_burst(5, 3, false);  read_burst(5, 3);   -- inside one 128-bit word
    write_burst(6, 4, false);  read_burst(6, 4);   -- crosses a word boundary
    write_burst(100, 255, false); read_burst(100, 255);
    read_burst(7, 1); read_burst(8, 16); read_burst(1, 255);
    write_burst(300, 9, true); read_burst(296, 20);
    -- random mix
    for k in 0 to 399 loop
      n := 1 + rnd(255);
      if rnd(4) = 0 then n := 1 + rnd(8); end if;
      a := rnd(WORDS * 8 - 256);
      if rnd(2) = 0 then
        write_burst(a, n, rnd(3) = 0); nwrites := nwrites + 1;
      else
        read_burst(a, n); nreads := nreads + 1;
      end if;
      beats := beats + n;
    end loop;
    -- full read-back
    for k in 0 to (WORDS * 8) / 256 - 1 loop
      read_burst(k * 256, 255); read_burst(k * 256 + 255, 1);
    end loop;
    report "random bursts: " & integer'image(nwrites) & " writes, " & integer'image(nreads) &
           " reads, " & integer'image(beats) & " beats; full read-back ok; max MIG reads in flight " &
           integer'image(max_inflight);
    report "TB_AVM_MIG_PASS";
    done <= true;
    wait;
  end process;
end architecture;
