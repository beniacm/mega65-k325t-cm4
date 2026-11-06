-- tb_eth.vhdl -- Ethernet path: RMII MAC model <-> eth_vphy <-> rk_bridge <-> model Rockchip (SPI).
-- MAC->Rockchip: two frames (60 B and 1514 B, > the old 1023-byte bridge index limit) sent
-- as RMII dibits with preamble/SFD; the Rockchip reads them via STATUS + ETH_TXGET.
-- Rockchip->MAC: two frames pushed with ETH_RXPUT; the MAC model decodes preamble, SFD and
-- data from rxd/rxdv and checks every byte and the inter-frame gap.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_eth is
end entity;

architecture sim of tb_eth is
  constant T50  : time := 20 ns;
  constant TCPU : time := 24.69 ns;
  constant TF   : time := TCPU / 4;          -- 162 MHz
  constant TSPI : time := 20 ns;             -- 25 MHz
  signal clk50, cpuclk, clkf : std_logic := '0';
  signal done : boolean := false;

  signal mac_txd, mac_rxd : unsigned(1 downto 0) := "00";
  signal mac_txen, mac_rxdv : std_logic := '0';
  signal tx_avail, tx_pop, rx_we, rx_push : std_logic;
  signal tx_len, tx_raddr, rx_waddr, rx_len : unsigned(10 downto 0);
  signal tx_rdata, rx_wdata : std_logic_vector(7 downto 0);
  signal tx_drops : unsigned(7 downto 0);
  signal rx_free : unsigned(3 downto 0);

  signal sck, smosi, smiso : std_logic := '0';
  signal scs : std_logic := '1';
  signal col : std_logic_vector(7 downto 0);
  signal ja, jb : std_logic_vector(4 downto 0);
  signal ctrl, rbd : std_logic_vector(7 downto 0);
  signal o1, o2, o3, monrx : std_logic;

  function fbyte(f, i : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned((f * 41 + i * 7 + i / 255) mod 256, 8));
  end function;
  type int_arr is array (natural range <>) of integer;
  constant TXLENS : int_arr := (60, 1514);
  constant RXLENS : int_arr := (100, 1514);
  signal rx_frames_ok : integer := 0;
begin
  clk50  <= not clk50 after T50 / 2 when not done;
  cpuclk <= not cpuclk after TCPU / 2 when not done;
  clkf   <= not clkf after TF / 2 when not done;

  phy : entity work.eth_vphy
    port map (clk50 => clk50, clkb => clkf, mac_txd => mac_txd, mac_txen => mac_txen,
              mac_rxd => mac_rxd, mac_rxdv => mac_rxdv, tx_avail => tx_avail, tx_len => tx_len,
              tx_raddr => tx_raddr, tx_rdata => tx_rdata, tx_pop => tx_pop, tx_drops => tx_drops,
              rx_free => rx_free, rx_we => rx_we, rx_waddr => rx_waddr, rx_wdata => rx_wdata,
              rx_push => rx_push, rx_len_in => rx_len);

  br : entity work.rk_bridge
    generic map (BIT_CLKS => 80)
    port map (clk => clkf, cpuclk => cpuclk, spi_sck => sck, spi_cs_n => scs, spi_mosi => smosi,
              spi_miso => smiso, sd_rd_req => '0', sd_wr_req => '0', sd_lba => (others => '0'),
              sd_seq => (others => '0'), sd_inited => '0', sd_cmd_cnt => (others => '0'),
              sd_last_cmd => (others => '0'), sd_rd_ready => o1, sd_rd_error => o2, sd_wr_ack => o3,
              rbuf_addr => (others => '0'), rbuf_data => rbd, wbuf_we => '0',
              wbuf_addr => (others => '0'), wbuf_data => (others => '0'), mon_rx => monrx,
              mon_tx => '1', matrix_col_idx => 0, matrix_col => col, joya => ja, joyb => jb,
              ctrl => ctrl, eth_tx_avail => tx_avail, eth_tx_len => tx_len, eth_tx_raddr => tx_raddr,
              eth_tx_rdata => tx_rdata, eth_tx_pop => tx_pop, eth_tx_drops => tx_drops,
              eth_rx_free => rx_free, eth_rx_we => rx_we, eth_rx_waddr => rx_waddr,
              eth_rx_wdata => rx_wdata, eth_rx_push => rx_push, eth_rx_len => rx_len,
              spi_active => open);

  -- MAC TX model: RMII dibits, LSB first, 7 x 0x55 + 0xD5 preamble, 12-byte gap
  mactx : process
    variable b : std_logic_vector(7 downto 0);
  begin
    wait for 3 us;
    for f in TXLENS'range loop
      wait until rising_edge(clk50);
      for i in 0 to 31 loop
        mac_txen <= '1';
        if i = 31 then mac_txd <= "11"; else mac_txd <= "01"; end if;
        wait until rising_edge(clk50);
      end loop;
      for i in 0 to TXLENS(f) - 1 loop
        b := fbyte(f, i);
        for d in 0 to 3 loop
          mac_txd <= unsigned(b(2 * d + 1 downto 2 * d));
          wait until rising_edge(clk50);
        end loop;
      end loop;
      mac_txen <= '0'; mac_txd <= "00";
      for g in 1 to 48 loop wait until rising_edge(clk50); end loop;
    end loop;
    wait;
  end process;

  -- MAC RX model: decode rxdv/rxd
  macrx : process
    variable b : std_logic_vector(7 downto 0);
    variable n, pre : integer;
  begin
    for f in RXLENS'range loop
      wait until rising_edge(clk50) and mac_rxdv = '1';
      pre := 0;
      while mac_rxd = "01" loop
        pre := pre + 1;
        wait until rising_edge(clk50);
      end loop;
      assert mac_rxd = "11" and pre = 31
        report "bad preamble/SFD (" & integer'image(pre) & " dibits)" severity failure;
      n := 0;
      wait until rising_edge(clk50);
      while mac_rxdv = '1' loop
        for d in 0 to 3 loop
          b(2 * d + 1 downto 2 * d) := std_logic_vector(mac_rxd);
          wait until rising_edge(clk50);
        end loop;
        assert b = fbyte(10 + f, n) report "RX frame " & integer'image(f) & " byte " &
          integer'image(n) & " wrong" severity failure;
        n := n + 1;
      end loop;
      assert n = RXLENS(f) report "RX frame " & integer'image(f) & " length " & integer'image(n)
        severity failure;
      report "MAC received frame " & integer'image(f) & ": " & integer'image(n) & " bytes ok";
      rx_frames_ok <= rx_frames_ok + 1;
    end loop;
    wait;
  end process;

  -- model Rockchip
  rk : process
    variable b : std_logic_vector(7 downto 0);
    variable st : std_logic_vector(159 downto 0);
    variable got, len : integer;
    procedure xfer(tx : in std_logic_vector(7 downto 0); r : out std_logic_vector(7 downto 0)) is
      variable v : std_logic_vector(7 downto 0);
    begin
      for i in 7 downto 0 loop
        smosi <= tx(i); wait for TSPI; sck <= '1'; v(i) := smiso; wait for TSPI; sck <= '0';
      end loop;
      r := v;
    end procedure;
    procedure status is
      variable v : std_logic_vector(7 downto 0);
    begin
      scs <= '0'; wait for TSPI; xfer(x"01", v); xfer(x"00", v);
      for i in 0 to 19 loop xfer(x"00", v); st(8 * i + 7 downto 8 * i) := v; end loop;
      wait for TSPI; scs <= '1'; wait for 4 * TSPI;
    end procedure;
  begin
    wait for 2 us;
    status;
    assert st(23 downto 16) = x"03" report "bridge version" severity failure;
    assert unsigned(st(151 downto 144)) = 8 report "expected 8 free RX slots" severity failure;
    -- push two frames to the MAC
    for f in RXLENS'range loop
      scs <= '0'; wait for TSPI;
      xfer(x"41", b);
      xfer(std_logic_vector(to_unsigned(RXLENS(f) / 256, 8)), b);
      xfer(std_logic_vector(to_unsigned(RXLENS(f) mod 256, 8)), b);
      for i in 0 to RXLENS(f) - 1 loop xfer(fbyte(10 + f, i), b); end loop;
      wait for TSPI; scs <= '1'; wait for 4 * TSPI;
    end loop;
    -- collect the two MAC frames
    got := 0;
    while got < TXLENS'length loop
      status;
      len := to_integer(unsigned(st(130 downto 128) & st(143 downto 136)));
      if len /= 0 then
        assert len = TXLENS(got) report "TX frame length " & integer'image(len) severity failure;
        scs <= '0'; wait for TSPI;
        xfer(x"40", b); xfer(x"00", b);
        for i in 0 to len - 1 loop
          xfer(x"00", b);
          assert b = fbyte(got, i) report "TX frame " & integer'image(got) & " byte " &
            integer'image(i) & " wrong" severity failure;
        end loop;
        wait for TSPI; scs <= '1'; wait for 4 * TSPI;
        report "Rockchip got MAC frame " & integer'image(got) & ": " & integer'image(len) & " bytes ok";
        got := got + 1;
      end if;
      wait for 5 us;
    end loop;
    status;
    assert unsigned(st(135 downto 128) & st(143 downto 136)) = 0 report "frame not released" severity failure;
    wait until rx_frames_ok = 2 for 2 ms;
    assert rx_frames_ok = 2 report "MAC did not receive both frames" severity failure;
    status;
    assert unsigned(st(151 downto 144)) = 8 report "RX slots not freed" severity failure;
    assert st(159 downto 152) = x"00" report "unexpected drops" severity failure;
    report "TB_ETH_PASS";
    done <= true;
    wait;
  end process;
end architecture;
