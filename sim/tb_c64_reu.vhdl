-- tb_c64_reu.vhdl -- the C64 core's REU memory path on the K325T, end to end:
--   REU model (reu.v's RAM handshake, C64 bus-cycle timing, main_clk 31.5 MHz)
--   -> reu_mapper -> avm_cache -> avm_fifo (main_clk -> hr_clk) -> M2M avm_arbit_general
--   (3 masters, like the framework: ascal-like burst traffic on #2, core on #1, QNICE idle)
--   -> avm_mig (K325T) -> MIG model (in-order reads, 18..33 cycle latency, random ready).
-- STASHes and FETCHes REU blocks and checks the data and that every byte completes.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_c64_reu is
end entity;

architecture sim of tb_c64_reu is
   constant TMAIN : time := 31.72 ns;
   constant THR   : time := 10 ns;
   signal clkm, clkh : std_logic := '0';
   signal done : boolean := false;
   signal rst_m, rst_h : std_logic := '1';

   -- C64 bus cycle (32 main clocks): ext_cycle = DMA0..DMA3 (clocks 4..7)
   signal syscyc    : natural range 0 to 31 := 0;
   signal ext_cycle : std_logic;

   -- REU <-> mapper
   signal reu_cycle : std_logic;
   signal reu_addr  : std_logic_vector(24 downto 0) := (others => '0');
   signal reu_dout  : std_logic_vector(7 downto 0) := (others => '0');
   signal reu_din   : std_logic_vector(7 downto 0);
   signal reu_we, reu_cs : std_logic := '0';

   -- mapper -> cache -> fifo
   signal map_write, map_read, map_rdv, map_wait : std_logic;
   signal map_addr : std_logic_vector(31 downto 0);
   signal map_wdata, map_rdata : std_logic_vector(15 downto 0);
   signal map_be : std_logic_vector(1 downto 0);
   signal map_bc : std_logic_vector(7 downto 0);
   signal c_write, c_read, c_rdv, c_wait : std_logic;
   signal c_addr : std_logic_vector(31 downto 0);
   signal c_wdata, c_rdata : std_logic_vector(15 downto 0);
   signal c_be : std_logic_vector(1 downto 0);
   signal c_bc : std_logic_vector(7 downto 0);

   -- hr_clk side: REU path after its FIFO (r_*), CRT-like master (k_*), core arbiter out (h_*)
   signal r_write, r_read, r_rdv, r_wait : std_logic;
   signal r_addr : std_logic_vector(31 downto 0);
   signal r_wdata, r_rdata : std_logic_vector(15 downto 0);
   signal r_be : std_logic_vector(1 downto 0);
   signal r_bc : std_logic_vector(7 downto 0);
   signal k_write, k_read : std_logic := '0';
   signal k_addr : std_logic_vector(31 downto 0) := (others => '0');
   signal k_wdata : std_logic_vector(15 downto 0) := (others => '0');
   signal k_be : std_logic_vector(1 downto 0) := "11";
   signal k_bc : std_logic_vector(7 downto 0) := x"01";
   signal k_rdata : std_logic_vector(15 downto 0);
   signal k_rdv, k_wait : std_logic;
   signal crt_ok : boolean := true;
   signal crt_done : boolean := false;
   signal h_write, h_read, h_rdv, h_wait : std_logic;
   signal h_addr : std_logic_vector(31 downto 0);
   signal h_wdata, h_rdata : std_logic_vector(15 downto 0);
   signal h_be : std_logic_vector(1 downto 0);
   signal h_bc : std_logic_vector(7 downto 0);

   signal d_write, d_read : std_logic := '0';
   signal d_addr : std_logic_vector(31 downto 0) := (others => '0');
   signal d_wdata : std_logic_vector(15 downto 0) := (others => '0');
   signal d_bc : std_logic_vector(7 downto 0) := x"01";
   signal d_rdata : std_logic_vector(15 downto 0);
   signal d_rdv, d_wait : std_logic;
   signal q_rdata : std_logic_vector(15 downto 0);
   signal q_rdv, q_wait : std_logic;

   signal s_write, s_read, s_rdv, s_wait : std_logic_vector(2 downto 0);
   signal s_addr : std_logic_vector(95 downto 0);
   signal s_wdata, s_rdata : std_logic_vector(47 downto 0);
   signal s_be : std_logic_vector(5 downto 0);
   signal s_bc : std_logic_vector(23 downto 0);

   signal m_write, m_read, m_rdv, m_wait : std_logic;
   signal m_addr : std_logic_vector(31 downto 0);
   signal m_wdata, m_rdata : std_logic_vector(15 downto 0);
   signal m_be : std_logic_vector(1 downto 0);
   signal m_bc : std_logic_vector(7 downto 0);

   signal app_addr : std_logic_vector(27 downto 0);
   signal app_cmd : std_logic_vector(2 downto 0);
   signal app_en, app_wdf_end, app_wdf_wren : std_logic;
   signal app_rdy, app_wdf_rdy : std_logic := '0';
   signal app_wdf_data, app_rd_data : std_logic_vector(127 downto 0) := (others => '0');
   signal app_wdf_mask : std_logic_vector(15 downto 0);
   signal app_rd_data_valid : std_logic := '0';

   constant WORDS : integer := 65536;   -- MIG model: 65536 x 128 bit (1 MB), addresses wrap
   type mem_t is array (0 to WORDS - 1) of std_logic_vector(127 downto 0);
   signal dig_ok : boolean := true;
   signal dig_bursts : natural := 0;
   signal ascal_on : boolean := true;
begin
   clkm <= not clkm after TMAIN / 2 when not done;
   clkh <= not clkh after THR / 2 when not done;

   process (clkm)
   begin
      if rising_edge(clkm) then
         syscyc <= (syscyc + 1) mod 32;
      end if;
   end process;
   ext_cycle <= '1' when syscyc >= 4 and syscyc <= 7 else '0';

   ---------------------------------------------------------------- the core's REU path
   i_map : entity work.reu_mapper
      generic map (G_BASE_ADDRESS => X"0020_0000")
      port map (clk_i => clkm, rst_i => rst_m,
                reu_ext_cycle_i => ext_cycle, reu_ext_cycle_o => reu_cycle,
                reu_addr_i => reu_addr, reu_dout_i => reu_dout, reu_din_o => reu_din,
                reu_we_i => reu_we, reu_cs_i => reu_cs,
                avm_write_o => map_write, avm_read_o => map_read, avm_address_o => map_addr,
                avm_writedata_o => map_wdata, avm_byteenable_o => map_be,
                avm_burstcount_o => map_bc, avm_readdata_i => map_rdata,
                avm_readdatavalid_i => map_rdv, avm_waitrequest_i => map_wait);

   i_cache : entity work.avm_cache
      generic map (G_CACHE_SIZE => 8, G_ADDRESS_SIZE => 32, G_DATA_SIZE => 16)
      port map (clk_i => clkm, rst_i => rst_m,
                s_avm_waitrequest_o => map_wait, s_avm_write_i => map_write,
                s_avm_read_i => map_read, s_avm_address_i => map_addr,
                s_avm_writedata_i => map_wdata, s_avm_byteenable_i => map_be,
                s_avm_burstcount_i => map_bc, s_avm_readdata_o => map_rdata,
                s_avm_readdatavalid_o => map_rdv,
                m_avm_waitrequest_i => c_wait, m_avm_write_o => c_write, m_avm_read_o => c_read,
                m_avm_address_o => c_addr, m_avm_writedata_o => c_wdata,
                m_avm_byteenable_o => c_be, m_avm_burstcount_o => c_bc,
                m_avm_readdata_i => c_rdata, m_avm_readdatavalid_i => c_rdv);

   i_fifo : entity work.avm_fifo
      generic map (G_WR_DEPTH => 16, G_RD_DEPTH => 16, G_FILL_SIZE => 1,
                   G_ADDRESS_SIZE => 32, G_DATA_SIZE => 16)
      port map (s_clk_i => clkm, s_rst_i => rst_m,
                s_avm_waitrequest_o => c_wait, s_avm_write_i => c_write, s_avm_read_i => c_read,
                s_avm_address_i => c_addr, s_avm_writedata_i => c_wdata,
                s_avm_byteenable_i => c_be, s_avm_burstcount_i => c_bc,
                s_avm_readdata_o => c_rdata, s_avm_readdatavalid_o => c_rdv,
                m_clk_i => clkh, m_rst_i => rst_h,
                m_avm_waitrequest_i => r_wait, m_avm_write_o => r_write, m_avm_read_o => r_read,
                m_avm_address_o => r_addr, m_avm_writedata_o => r_wdata,
                m_avm_byteenable_o => r_be, m_avm_burstcount_o => r_bc,
                m_avm_readdata_i => r_rdata, m_avm_readdatavalid_i => r_rdv);

   -- the core's own arbiter (mega65.vhd / sw_cartridge_wrapper): REU path vs cartridge
   i_core_arb : entity work.avm_arbit
      generic map (G_PREFER_SWAP => true, G_FREQ_HZ => 100_000_000, G_ADDRESS_SIZE => 32, G_DATA_SIZE => 16)
      port map (clk_i => clkh, rst_i => rst_h,
                s0_avm_write_i => r_write, s0_avm_read_i => r_read, s0_avm_address_i => r_addr,
                s0_avm_writedata_i => r_wdata, s0_avm_byteenable_i => r_be, s0_avm_burstcount_i => r_bc,
                s0_avm_readdata_o => r_rdata, s0_avm_readdatavalid_o => r_rdv, s0_avm_waitrequest_o => r_wait,
                s1_avm_write_i => k_write, s1_avm_read_i => k_read, s1_avm_address_i => k_addr,
                s1_avm_writedata_i => k_wdata, s1_avm_byteenable_i => k_be, s1_avm_burstcount_i => k_bc,
                s1_avm_readdata_o => k_rdata, s1_avm_readdatavalid_o => k_rdv, s1_avm_waitrequest_o => k_wait,
                m_avm_write_o => h_write, m_avm_read_o => h_read, m_avm_address_o => h_addr,
                m_avm_writedata_o => h_wdata, m_avm_byteenable_o => h_be, m_avm_burstcount_o => h_bc,
                m_avm_readdata_i => h_rdata, m_avm_readdatavalid_i => h_rdv, m_avm_waitrequest_i => h_wait);

   -- cartridge-like master: byte writes (be 01 / 10, like sw_cartridge_csr's qnice2hyperram)
   -- of 4 KB to word 0x210000.., then 16-beat reads of all of it (like crt_parser / crt_cacher)
   crt : process
      type ref_t is array (0 to 2047) of std_logic_vector(15 downto 0);
      variable ref : ref_t := (others => (others => '0'));
      variable b : natural;
      variable bt : natural;
      variable byte : std_logic_vector(7 downto 0);
   begin
      wait until rst_h = '0';
      wait for 3 us;
      for i in 0 to 4095 loop
         byte := std_logic_vector(to_unsigned((i * 37 + 11) mod 256, 8));
         k_addr <= std_logic_vector(to_unsigned(16#210000# + i / 2, 32));
         k_bc <= x"01";
         if i mod 2 = 0 then k_be <= "01"; k_wdata <= x"00" & byte;
         else k_be <= "10"; k_wdata <= byte & x"00"; end if;
         k_write <= '1';
         loop
            wait until rising_edge(clkh);
            exit when k_wait = '0';
         end loop;
         k_write <= '0';
         if i mod 2 = 0 then ref(i / 2)(7 downto 0) := byte; else ref(i / 2)(15 downto 8) := byte; end if;
         for g in 0 to i mod 3 loop wait until rising_edge(clkh); end loop;   -- QNICE pace
      end loop;
      for blk in 0 to 127 loop
         k_addr <= std_logic_vector(to_unsigned(16#210000# + blk * 16, 32));
         k_bc <= x"10";
         k_read <= '1';
         loop
            wait until rising_edge(clkh);
            exit when k_wait = '0';
         end loop;
         k_read <= '0';
         bt := 0;
         while bt < 16 loop
            wait until rising_edge(clkh);
            if k_rdv = '1' then
               if k_rdata /= ref(blk * 16 + bt) then
                  report "CRT master: mismatch at word " & integer'image(blk * 16 + bt) & " got " &
                         integer'image(to_integer(unsigned(k_rdata))) & " expected " &
                         integer'image(to_integer(unsigned(ref(blk * 16 + bt)))) severity error;
                  crt_ok <= false;
               end if;
               bt := bt + 1;
            end if;
         end loop;
      end loop;
      report "CRT master: 4096 byte writes + 128 x 16-beat reads done";
      crt_done <= true;
      wait;
   end process;

   ---------------------------------------------------------------- framework arbiter
   s_write <= d_write & h_write & '0';
   s_read  <= d_read  & h_read  & '0';
   s_addr  <= d_addr  & h_addr  & x"00000000";
   s_wdata <= d_wdata & h_wdata & x"0000";
   s_be    <= "11"    & h_be    & "11";
   s_bc    <= d_bc    & h_bc    & x"01";
   d_rdata <= s_rdata(47 downto 32); d_rdv <= s_rdv(2); d_wait <= s_wait(2);
   h_rdata <= s_rdata(31 downto 16); h_rdv <= s_rdv(1); h_wait <= s_wait(1);

   i_arb : entity work.avm_arbit_general
      generic map (G_NUM_SLAVES => 3, G_FREQ_HZ => 100_000_000, G_ADDRESS_SIZE => 32, G_DATA_SIZE => 16)
      port map (clk_i => clkh, rst_i => rst_h,
                s_avm_write_i => s_write, s_avm_read_i => s_read, s_avm_address_i => s_addr,
                s_avm_writedata_i => s_wdata, s_avm_byteenable_i => s_be,
                s_avm_burstcount_i => s_bc, s_avm_readdata_o => s_rdata,
                s_avm_readdatavalid_o => s_rdv, s_avm_waitrequest_o => s_wait,
                m_avm_write_o => m_write, m_avm_read_o => m_read, m_avm_address_o => m_addr,
                m_avm_writedata_o => m_wdata, m_avm_byteenable_o => m_be,
                m_avm_burstcount_o => m_bc, m_avm_readdata_i => m_rdata,
                m_avm_readdatavalid_i => m_rdv, m_avm_waitrequest_i => m_wait);

   i_mig_if : entity work.avm_mig
      port map (clk => clkh, rst => rst_h, avm_write => m_write, avm_read => m_read,
                avm_address => m_addr, avm_writedata => m_wdata, avm_byteenable => m_be,
                avm_burstcount => m_bc, avm_readdata => m_rdata, avm_readdatavalid => m_rdv,
                avm_waitrequest => m_wait,
                app_addr => app_addr, app_cmd => app_cmd, app_en => app_en, app_rdy => app_rdy,
                app_wdf_data => app_wdf_data, app_wdf_end => app_wdf_end,
                app_wdf_wren => app_wdf_wren, app_wdf_mask => app_wdf_mask,
                app_wdf_rdy => app_wdf_rdy, app_rd_data => app_rd_data,
                app_rd_data_valid => app_rd_data_valid);


   ---------------------------------------------------------------- MIG model
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
      wait until rising_edge(clkh);
      cyc := cyc + 1;
      lfsr := lfsr(14 downto 0) & (lfsr(15) xor lfsr(13) xor lfsr(12) xor lfsr(10));
      if app_en = '1' and app_rdy = '1' then
         if app_cmd = "001" then
            lat := 18 + to_integer(lfsr(3 downto 0));
            if rq_n > 0 and cyc + lat <= rq_due((rq_tail + 63) mod 64) then
               lat := rq_due((rq_tail + 63) mod 64) - cyc + 1;
            end if;
            rq_addr(rq_tail) := to_integer(unsigned(app_addr(27 downto 3))) mod WORDS;
            rq_due(rq_tail) := cyc + lat;
            rq_tail := (rq_tail + 1) mod 64; rq_n := rq_n + 1;
         else
            wq_addr(wq_n) := to_integer(unsigned(app_addr(27 downto 3))) mod WORDS; wq_n := wq_n + 1;
            wc_cyc(wc_n mod 64) := cyc; wc_n := wc_n + 1;
         end if;
      end if;
      if app_wdf_wren = '1' and app_wdf_rdy = '1' then
         if wd_seen < wc_n then
            assert cyc - wc_cyc(wd_seen mod 64) <= 2
               report "MIG rule: write data " & integer'image(cyc - wc_cyc(wd_seen mod 64)) & " cycles after its command" severity failure;
         end if;
         wd_seen := wd_seen + 1;
         dq_data(dq_n) := app_wdf_data;
         dq_mask(dq_n) := to_integer(unsigned(app_wdf_mask)); dq_n := dq_n + 1;
      end if;
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
      app_rdy     <= lfsr(0) or lfsr(6) or lfsr(9);
      app_wdf_rdy <= lfsr(2) or lfsr(11);
   end process;

   ---------------------------------------------------------------- ascal-like master (#2)
   -- write bursts of 32 and read bursts of 64 words in its own region (word 0x60000..),
   -- checked against a reference.  Like a pipelined Avalon master (the real scaler), it often
   -- raises its next write burst while the last beats of a read are still arriving, so the
   -- write's first beat can be accepted in the same cycle as the read's last beat.
   dig : process
      type ref_t is array (0 to 4095) of std_logic_vector(15 downto 0);
      variable ref : ref_t := (others => (others => '0'));
      variable seed : unsigned(31 downto 0) := x"0BADF00D";
      variable a, bt, n, wa, wbt : integer;
      variable pend_w : boolean := false;          -- a write burst already started (pipelined)
      variable wk : integer := 0;                   -- data key of the current write burst
      variable t0 : time;
      impure function rnd(lim : integer) return integer is
      begin
         seed := resize(seed * to_unsigned(1103515245, 32) + 12345, 32);
         return to_integer(seed(30 downto 15)) mod lim;
      end function;
      function wdat(a, b, k : integer) return std_logic_vector is
      begin
         return std_logic_vector(to_unsigned((a * 7 + b * 13 + k) mod 65536, 16));
      end function;
   begin
      wait until rst_h = '0';
      while not done loop
         if not pend_w then
            for g in 0 to rnd(40) loop wait until rising_edge(clkh); end loop;
         end if;
         exit when not ascal_on;
         if pend_w or rnd(2) = 0 then
            -- write burst (possibly already started during the previous read)
            if not pend_w then
               wa := rnd(4096 - 64); wbt := 0; wk := wk + 1;
               d_addr <= std_logic_vector(to_unsigned(16#60000# + wa, 32));
               d_bc <= x"20";
            end if;
            pend_w := false;
            while wbt < 32 loop
               d_write <= '1'; d_wdata <= wdat(wa, wbt, wk);
               wait until rising_edge(clkh);
               if d_wait = '0' then
                  ref(wa + wbt) := wdat(wa, wbt, wk); wbt := wbt + 1;
               end if;
            end loop;
            d_write <= '0';
         else
            a := rnd(4096 - 64);
            n := 64; bt := 0;
            d_addr <= std_logic_vector(to_unsigned(16#60000# + a, 32));
            d_bc <= std_logic_vector(to_unsigned(n, 8));
            d_read <= '1';
            loop
               wait until rising_edge(clkh);
               exit when d_wait = '0';
            end loop;
            d_read <= '0';
            t0 := now;
            while bt < n loop
               -- pipelining: start the next write burst before the last beats are in
               if bt >= n - 2 and not pend_w and rnd(2) = 0 then
                  pend_w := true;
                  -- not overlapping the read still in flight (its result would be ambiguous)
                  if a < 2048 then wa := 2048 + rnd(2048 - 64); else wa := rnd(2048 - 64); end if;
                  wbt := 0; wk := wk + 1;
                  d_addr <= std_logic_vector(to_unsigned(16#60000# + wa, 32));
                  d_bc <= x"20";
                  d_write <= '1'; d_wdata <= wdat(wa, 0, wk);
               end if;
               wait until rising_edge(clkh);
               if pend_w and d_write = '1' and d_wait = '0' then
                  ref(wa + wbt) := wdat(wa, wbt, wk); wbt := wbt + 1;
                  d_wdata <= wdat(wa, wbt, wk);
               end if;
               if d_rdv = '1' then
                  if d_rdata /= ref(a + bt) then
                     report "ascal master: read mismatch at " & integer'image(a + bt) severity error;
                     dig_ok <= false;
                  end if;
                  bt := bt + 1;
               end if;
               assert now - t0 < 100 us report "ascal master: read burst never completed (lost beats)" severity failure;
            end loop;
         end if;
         dig_bursts <= dig_bursts + 1;
      end loop;
      wait;
   end process;

   ---------------------------------------------------------------- REU model + checks
   reu : process
      variable t0 : time;
      variable errors : natural := 0;

      -- one RAM access the way reu.v does it (STATE_EVAL -> STATE_PROC_RAM)
      procedure ram_access(we : std_logic; d : std_logic_vector(7 downto 0);
                           q : out std_logic_vector(7 downto 0)) is
         variable cnt : natural := 0;
      begin
         loop                                   -- EVAL: start when not in a RAM cycle
            wait until rising_edge(clkm);
            exit when reu_cycle = '0';
         end loop;
         reu_cs <= '1'; reu_we <= we; reu_dout <= d;
         t0 := now;
         loop                                   -- PROC_RAM: 4 clocks of ram_cycle
            wait until rising_edge(clkm);
            if reu_cycle = '1' then
               cnt := cnt + 1;
               if cnt mod 4 = 0 then
                  q := reu_din;
                  reu_cs <= '0'; reu_we <= '0';
                  exit;
               end if;
            end if;
            assert now - t0 < 200 us
               report "REU RAM access never completed (addr " &
                      integer'image(to_integer(unsigned(reu_addr))) & ")" severity failure;
         end loop;
      end procedure;

      -- the C64 side of a byte: one DMA (CPU-window) cycle
      procedure c64_access is
      begin
         loop
            wait until rising_edge(clkm);
            exit when syscyc = 31;
         end loop;
      end procedure;

      procedure stash(base, len, salt : natural) is
         variable q : std_logic_vector(7 downto 0);
      begin
         for i in 0 to len - 1 loop
            reu_addr <= '1' & std_logic_vector(to_unsigned(base + i, 24));
            c64_access;
            ram_access('1', std_logic_vector(to_unsigned(((base + i) * 3 + salt) mod 256, 8)), q);
         end loop;
      end procedure;

      procedure fetch(base, len, salt : natural) is
         variable q : std_logic_vector(7 downto 0);
      begin
         for i in 0 to len - 1 loop
            reu_addr <= '1' & std_logic_vector(to_unsigned(base + i, 24));
            ram_access('0', x"00", q);
            if q /= std_logic_vector(to_unsigned(((base + i) * 3 + salt) mod 256, 8)) then
               if errors < 10 then
                  report "FETCH mismatch at REU " & integer'image(base + i) & ": got " &
                         integer'image(to_integer(unsigned(q))) & " expected " &
                         integer'image(((base + i) * 3 + salt) mod 256) severity error;
               end if;
               errors := errors + 1;
            end if;
            c64_access;
         end loop;
      end procedure;
   begin
      wait for 200 ns;
      wait until rising_edge(clkh); rst_h <= '0';
      wait until rising_edge(clkm); rst_m <= '0';
      wait for 1 us;
      -- bank 5 (0x50000), 256 bytes, as in the hardware test
      stash(16#50000#, 256, 0);
      report "stash 256 done";
      fetch(16#50000#, 256, 0);
      report "fetch 256 done, errors " & integer'image(errors);
      -- odd start, odd length, then a jump back (preemptive read path)
      stash(16#12345#, 77, 5);
      fetch(16#12345#, 77, 5);
      fetch(16#50000#, 40, 0);
      stash(16#7FFF0#, 300, 9);
      fetch(16#7FFF0#, 300, 9);
      report "REU blocks done: " & integer'image(errors) & " errors; ascal-like bursts " &
             integer'image(dig_bursts);
      assert errors = 0 report "REU data errors" severity failure;
      assert dig_ok report "ascal-like master saw bad data" severity failure;
      if not crt_done then wait until crt_done; end if;
      assert crt_ok report "CRT-like master saw bad data" severity failure;
      report "TB_C64_REU_PASS";
      done <= true;
      wait;
   end process;
end architecture;
