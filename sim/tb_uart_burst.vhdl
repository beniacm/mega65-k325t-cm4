-- tb_uart_burst.vhdl -- real monitor transmitter (UART_TX_CTRL, 20 clocks/bit @ cpuclock)
-- sends a back-to-back burst into rk_bridge; a model Rockchip reads it out over SPI
-- (UART_GET) and checks every byte arrives exactly once and in order.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_uart_burst is
end entity;

architecture sim of tb_uart_burst is
  constant TCLK  : time := 24.69 ns;
  constant TFAST : time := TCLK / 4;
  constant TSPI  : time := 20 ns;
  constant N     : integer := 64;
  signal clk, clkf : std_logic := '0';
  signal done : boolean := false;
  signal send, ready, txd : std_logic := '0';
  signal data : std_logic_vector(7 downto 0) := (others => '0');
  signal sck, smosi, smiso : std_logic := '0';
  signal scs : std_logic := '1';
  signal col : std_logic_vector(7 downto 0);
  signal ja, jb : std_logic_vector(4 downto 0);
  signal ctrl : std_logic_vector(7 downto 0);
  signal rbd : std_logic_vector(7 downto 0);
  signal rdy_o, err_o, ack_o, monrx : std_logic;
begin
  clk  <= not clk after TCLK/2 when not done;
  clkf <= not clkf after TFAST/2 when not done;

  tx : entity work.UART_TX_CTRL
    port map (CLK => clk, SEND => send, READY => ready, UART_TX => txd, DATA => unsigned(data),
              BIT_TMR_MAX => to_unsigned(19, 24));

  br : entity work.rk_bridge
    generic map (BIT_CLKS => 80)
    port map (clk => clkf, cpuclk => clk, spi_sck => sck, spi_cs_n => scs, spi_mosi => smosi,
              spi_miso => smiso, sd_rd_req => '0', sd_wr_req => '0', sd_lba => (others => '0'),
              sd_seq => (others => '0'), sd_inited => '0', sd_cmd_cnt => (others => '0'),
              sd_last_cmd => (others => '0'), sd_rd_ready => rdy_o, sd_rd_error => err_o,
              sd_wr_ack => ack_o, rbuf_addr => (others => '0'), rbuf_data => rbd,
              wbuf_we => '0', wbuf_addr => (others => '0'), wbuf_data => (others => '0'),
              mon_rx => monrx, mon_tx => txd, matrix_col_idx => 0, matrix_col => col,
              joya => ja, joyb => jb, ctrl => ctrl, spi_active => open);

  -- monitor: send N bytes as fast as UART_TX_CTRL allows
  mon : process
  begin
    wait for 1 us;
    for i in 0 to N - 1 loop
      wait until rising_edge(clk) and ready = '1';
      data <= std_logic_vector(to_unsigned((i * 37 + 11) mod 256, 8));
      send <= '1';
      wait until rising_edge(clk);
      send <= '0';
      wait until rising_edge(clk);
    end loop;
    wait;
  end process;

  rk : process
    variable got : integer := 0;
    variable b, cnt_hi, cnt_lo : std_logic_vector(7 downto 0);
    variable n_av : integer;
    procedure xfer(tx : in std_logic_vector(7 downto 0); r : out std_logic_vector(7 downto 0)) is
      variable v : std_logic_vector(7 downto 0);
    begin
      for i in 7 downto 0 loop
        smosi <= tx(i); wait for TSPI; sck <= '1'; v(i) := smiso; wait for TSPI; sck <= '0';
      end loop;
      r := v;
    end procedure;
  begin
    wait for 2 us;
    while got < N loop
      -- STATUS: bytes 9/10 (after cmd+dummy) = rx count
      scs <= '0'; wait for TSPI;
      xfer(x"01", b); xfer(x"00", b);
      for i in 0 to 15 loop
        xfer(x"00", b);
        if i = 9 then cnt_hi := b; end if;
        if i = 10 then cnt_lo := b; end if;
      end loop;
      wait for TSPI; scs <= '1'; wait for 4 * TSPI;
      n_av := to_integer(unsigned(cnt_hi(6 downto 0) & cnt_lo));
      if n_av > 0 then
        scs <= '0'; wait for TSPI;
        xfer(x"21", b); xfer(x"00", b);
        for i in 1 to n_av loop
          xfer(x"00", b);
          assert b = std_logic_vector(to_unsigned((got * 37 + 11) mod 256, 8))
            report "byte " & integer'image(got) & " wrong: got " & integer'image(to_integer(unsigned(b)))
            severity failure;
          got := got + 1;
        end loop;
        wait for TSPI; scs <= '1'; wait for 4 * TSPI;
      end if;
      wait for 3 us;
      assert now < 2 ms report "only " & integer'image(got) & " of " & integer'image(N) & " bytes arrived"
        severity failure;
    end loop;
    report "TB_UART_BURST_PASS (" & integer'image(N) & " back-to-back bytes)";
    done <= true;
    wait;
  end process;
end architecture;
