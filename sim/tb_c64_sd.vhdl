-- tb_c64_sd.vhdl -- C64 core (M2M) SD path:
--   QNICE sdcard (SimpleSDHC, 25 MHz SPI clock domain, CRC on via CMD59)
--   <-> sd_emu (SYNC_IN, 100 MHz) <-> rk_bridge (162.7 MHz) <-> model Rockchip.
-- Checks: init to SDHC, block reads with the CRC16 SimpleSDHC verifies, a read
-- served only after a long (1 ms) server delay, a block write captured byte for
-- byte by the server, an out-of-range read giving the data-error token, and a
-- controller reset + read afterwards (recovery).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_c64_sd is
end entity;

architecture sim of tb_c64_sd is
  constant TQ    : time := 20 ns;        -- QNICE 50 MHz
  constant T100  : time := 10 ns;
  constant TF    : time := 6.146 ns;     -- 162.7 MHz
  constant TSPI  : time := 20 ns;        -- Rockchip SPI 25 MHz
  signal clkq, clk100, clkf : std_logic := '0';
  signal done : boolean := false;

  signal q_rst : std_logic := '1';
  signal en, we : std_logic := '0';
  signal reg : std_logic_vector(2 downto 0) := "000";
  signal din, dout : std_logic_vector(15 downto 0) := (others => '0');
  signal cs_n, sclk, mosi, miso : std_logic;

  signal rd_req, wr_req, rd_ready, wr_ack, inited, rd_error : std_logic;
  signal lba : unsigned(31 downto 0);
  signal seq, ccnt, lcmd : unsigned(7 downto 0);
  signal rba, wba : unsigned(8 downto 0);
  signal rbd, wbd : std_logic_vector(7 downto 0);
  signal wbwe : std_logic;

  signal sck : std_logic := '0';
  signal scs : std_logic := '1';
  signal smosi, smiso : std_logic := '0';
  signal col : std_logic_vector(7 downto 0);
  signal joya, joyb : std_logic_vector(4 downto 0);
  signal ctrl : std_logic_vector(7 downto 0);
  signal monrx : std_logic;

  type bytes_t is array (natural range <>) of std_logic_vector(7 downto 0);
  signal slow_lba : integer := -1;       -- the server delays this LBA by 1 ms
  signal written  : integer := 0;
  function pat(l, i : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned((l * 7 + i * 13 + i / 256) mod 256, 8));
  end function;
  function wpat(l, i : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned((l * 3 + i * 5 + 1) mod 256, 8));
  end function;
begin
  clkq   <= not clkq   after TQ / 2   when not done;
  clk100 <= not clk100 after T100 / 2 when not done;
  clkf   <= not clkf   after TF / 2   when not done;

  ctl : entity work.sdcard
    port map (clk => clkq, reset => q_rst, en => en, we => we, reg => reg, data_in => din,
              data_out => dout, sd_reset => cs_n, sd_clk => sclk, sd_mosi => mosi, sd_miso => miso);

  emu : entity work.sd_emu
    generic map (SYNC_IN => true)
    port map (clk => clk100, sd_cs_n => cs_n, sd_sclk => sclk, sd_mosi => mosi, sd_miso => miso,
              rd_req => rd_req, wr_req => wr_req, req_lba => lba, req_seq => seq,
              rd_ready => rd_ready, rd_error => rd_error, wr_ack => wr_ack,
              rbuf_addr => rba, rbuf_data => rbd, wbuf_we => wbwe, wbuf_addr => wba, wbuf_data => wbd,
              inited => inited, cmd_count => ccnt, last_cmd => lcmd);

  br : entity work.rk_bridge
    generic map (BIT_CLKS => 1412)
    port map (clk => clkf, cpuclk => clk100, spi_sck => sck, spi_cs_n => scs, spi_mosi => smosi,
              spi_miso => smiso, sd_rd_req => rd_req, sd_wr_req => wr_req, sd_lba => lba,
              sd_seq => seq, sd_inited => inited, sd_cmd_cnt => ccnt, sd_last_cmd => lcmd,
              sd_rd_ready => rd_ready, sd_rd_error => rd_error, sd_wr_ack => wr_ack,
              rbuf_addr => rba, rbuf_data => rbd, wbuf_we => wbwe, wbuf_addr => wba, wbuf_data => wbd,
              mon_rx => monrx, mon_tx => '1', matrix_col_idx => 0, matrix_col => col,
              joya => joya, joyb => joyb, ctrl => ctrl, spi_active => open);

  -- model Rockchip: sector server
  rk : process
    variable rx : bytes_t(0 to 15);
    variable b  : std_logic_vector(7 downto 0);
    variable l  : integer;
    procedure xfer(tx : in std_logic_vector(7 downto 0); r : out std_logic_vector(7 downto 0)) is
      variable v : std_logic_vector(7 downto 0);
    begin
      for i in 7 downto 0 loop
        smosi <= tx(i); wait for TSPI; sck <= '1'; v(i) := smiso; wait for TSPI; sck <= '0';
      end loop;
      r := v;
    end procedure;
    procedure status(r : out bytes_t) is
      variable v : std_logic_vector(7 downto 0);
    begin
      scs <= '0'; wait for TSPI; xfer(x"01", v); xfer(x"00", v);
      for i in 0 to 15 loop xfer(x"00", v); r(i) := v; end loop;
      wait for TSPI; scs <= '1'; wait for 4 * TSPI;
    end procedure;
    procedure cmd1(c : std_logic_vector(7 downto 0)) is
      variable v : std_logic_vector(7 downto 0);
    begin
      scs <= '0'; wait for TSPI; xfer(c, v); wait for TSPI; scs <= '1'; wait for 4 * TSPI;
    end procedure;
  begin
    wait for 2 us;
    loop
      status(rx);
      l := to_integer(unsigned(std_logic_vector'(rx(5) & rx(6) & rx(7) & rx(8))));
      if rx(3)(0) = '1' and rx(3)(1) = '0' and rx(3)(5) = '0' then
        if l >= 1000000 then
          cmd1(x"13");
          report "server: rejected read lba " & integer'image(l);
        else
          if l = slow_lba then
            wait for 1 ms;                       -- Rockchip busy / idle back-off
          end if;
          scs <= '0'; wait for TSPI; xfer(x"10", b);
          for i in 0 to 511 loop xfer(pat(l, i), b); end loop;
          wait for TSPI; scs <= '1'; wait for 4 * TSPI;
          report "server: served read lba " & integer'image(l);
        end if;
      elsif rx(3)(2) = '1' then
        scs <= '0'; wait for TSPI; xfer(x"11", b); xfer(x"00", b);
        for i in 0 to 511 loop
          xfer(x"00", b);
          assert b = wpat(l, i) report "WRITE DATA MISMATCH lba " & integer'image(l) &
            " byte " & integer'image(i) severity failure;
        end loop;
        wait for TSPI; scs <= '1'; wait for 4 * TSPI;
        cmd1(x"12");
        written <= written + 1;
        report "server: write captured+acked lba " & integer'image(l);
      end if;
      wait for 2 us;
    end loop;
  end process;

  -- QNICE side: register accesses as the firmware does them
  host : process
    variable v : std_logic_vector(15 downto 0);
    procedure wr(r : integer; d : integer) is
    begin
      wait until rising_edge(clkq);
      reg <= std_logic_vector(to_unsigned(r, 3)); din <= std_logic_vector(to_unsigned(d, 16));
      en <= '1'; we <= '1';
      wait until rising_edge(clkq);           -- registers latch on the falling edge
      en <= '0'; we <= '0';
      wait until rising_edge(clkq);
      wait until rising_edge(clkq);
    end procedure;
    procedure rd(r : integer; d : out std_logic_vector(15 downto 0)) is
    begin
      wait until rising_edge(clkq);
      reg <= std_logic_vector(to_unsigned(r, 3)); en <= '1'; we <= '0';
      wait until falling_edge(clkq);
      d := dout;
      wait until rising_edge(clkq);
      en <= '0';
    end procedure;
    procedure wait_not_busy(tmo : time) is
      variable s : std_logic_vector(15 downto 0);
      variable t0 : time := now;
    begin
      for k in 0 to 4 loop wait until rising_edge(clkq); end loop;
      loop
        rd(5, s);
        exit when s(15) = '0';
        assert now - t0 < tmo report "timeout waiting for SD controller" severity failure;
        wait for 2 us;
      end loop;
    end procedure;
    procedure read_block(l : integer) is
      variable s : std_logic_vector(15 downto 0);
    begin
      wr(0, l mod 65536); wr(1, l / 65536);
      wr(5, 1);
      wait_not_busy(20 ms);
      rd(5, s);
      assert s(14) = '0' report "read error at lba " & integer'image(l) severity failure;
      for i in 0 to 511 loop
        wr(2, i);
        rd(3, s);
        assert s(7 downto 0) = pat(l, i) report "READ MISMATCH lba " & integer'image(l) &
          " byte " & integer'image(i) severity failure;
      end loop;
      report "host: read OK (data + CRC16) lba " & integer'image(l);
    end procedure;
  begin
    wait for 200 ns;
    q_rst <= '0';
    wait for 1 us;
    wr(5, 0);                                  -- reset/init the card
    wait_not_busy(50 ms);
    rd(5, v);
    assert v(14) = '0' report "init error" severity failure;
    assert v(13 downto 12) = "11" report "card type not SDHC" severity failure;
    report "host: SD init OK, SDHC";

    read_block(5);
    read_block(123456);
    slow_lba <= 77;
    read_block(77);                            -- served after a 1 ms delay
    report "host: delayed read OK";

    -- write a block: fill the buffer, then write it
    for i in 0 to 511 loop
      wr(2, i); wr(3, to_integer(unsigned(wpat(9, i))));
    end loop;
    wr(0, 9); wr(1, 0);
    wr(5, 2);
    wait_not_busy(20 ms);
    rd(5, v);
    assert v(14) = '0' report "write error" severity failure;
    wait for 50 us;
    assert written = 1 report "server did not capture the write" severity failure;
    report "host: write OK";

    -- out of range: the controller must report an error (data error token)
    wr(0, 16#4240#); wr(1, 16#F#);             -- 1000000
    wr(5, 1);
    for k in 0 to 4 loop wait until rising_edge(clkq); end loop;
    loop
      rd(5, v);
      exit when v(15) = '0' or v(14) = '1';
      wait for 2 us;
    end loop;
    assert v(14) = '1' report "out-of-range read did not fail" severity failure;
    rd(4, v);
    report "host: out-of-range read -> error, code " & integer'image(to_integer(unsigned(v(7 downto 0))));

    -- recovery: reset the controller and read again
    wr(5, 0);
    wait_not_busy(50 ms);
    read_block(6);
    report "TB_C64_SD_PASS";
    done <= true;
    wait;
  end process;
end architecture;
