-- tb_rk_sd.vhdl -- MEGA65 SdCardCtrl  <->  sd_emu  <->  rk_bridge  <->  model Rockchip
-- Checks: card init (CMD0/8/55/41), SDHC probe read at misaligned address,
-- single-block reads served on demand, single + multi-block writes captured
-- and acknowledged, UART bridge in both directions, INPUT register.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_rk_sd is
end entity;

architecture sim of tb_rk_sd is
  constant TCLK : time := 24.69 ns;          -- 40.5 MHz
  constant TSPI : time := 20 ns;             -- 25 MHz SPI half period
  constant TFAST : time := TCLK / 4;         -- 162 MHz bridge clock

  signal clk : std_logic := '0';
  signal clkf : std_logic := '0';
  signal done : boolean := false;

  -- host side of SdCardCtrl
  signal reset_i, rd_i, wr_i : std_logic := '0';
  signal wmulti, wfirst, wlast : std_logic := '0';
  signal addr_i  : std_logic_vector(31 downto 0) := (others => '0');
  signal data_i, data_o : std_logic_vector(7 downto 0) := (others => '0');
  signal busy_o, hs_o : std_logic;
  signal hs_i : std_logic := '0';
  signal err_o : std_logic_vector(15 downto 0);

  signal cs_n, sclk, mosi, miso : std_logic;

  -- emu <-> bridge
  signal rd_req, wr_req, rd_ready, wr_ack, inited, rd_error : std_logic;
  signal lba : unsigned(31 downto 0);
  signal seq, ccnt, lcmd : unsigned(7 downto 0);
  signal rba, wba : unsigned(8 downto 0);
  signal rbd, wbd : std_logic_vector(7 downto 0);
  signal wbwe : std_logic;

  -- rockchip SPI
  signal sck : std_logic := '0';
  signal scs : std_logic := '1';
  signal smosi, smiso : std_logic := '0';

  signal mon_rx, mon_tx : std_logic := '1';
  signal col : std_logic_vector(7 downto 0);
  signal col_idx : integer range 0 to 15 := 0;
  signal joya, joyb : std_logic_vector(4 downto 0);
  signal ctrl : std_logic_vector(7 downto 0);

  type bytes_t is array (natural range <>) of std_logic_vector(7 downto 0);
  signal served, written : integer := 0;

  function pat(l : integer; i : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned((l * 7 + i * 13 + i / 256) mod 256, 8));
  end function;
  function wpat(l : integer; i : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned((l * 3 + i * 5 + 1) mod 256, 8));
  end function;

  signal stop_server : boolean := false;
  signal uart_seen : std_logic_vector(7 downto 0) := x"00";
begin
  clk  <= not clk after TCLK/2 when not done;
  clkf <= not clkf after TFAST/2 when not done;

  ctl : entity work.SdCardCtrl
    port map (sdhc_i => '1', clk_i => clk, reset_i => reset_i, rd_i => rd_i, wr_i => wr_i,
              write_multi => wmulti, write_multi_first => wfirst, write_multi_last => wlast,
              addr_i => addr_i, data_i => data_i, data_o => data_o, busy_o => busy_o,
              hndShk_i => hs_i, hndShk_o => hs_o, error_o => err_o,
              cs_bo => cs_n, sclk_o => sclk, mosi_o => mosi, miso_i => miso);

  emu : entity work.sd_emu
    port map (clk => clk, sd_cs_n => cs_n, sd_sclk => sclk, sd_mosi => mosi, sd_miso => miso,
              rd_req => rd_req, wr_req => wr_req, req_lba => lba, req_seq => seq,
              rd_ready => rd_ready, rd_error => rd_error, wr_ack => wr_ack, rbuf_addr => rba, rbuf_data => rbd,
              wbuf_we => wbwe, wbuf_addr => wba, wbuf_data => wbd, inited => inited,
              cmd_count => ccnt, last_cmd => lcmd);

  br : entity work.rk_bridge
    generic map (BIT_CLKS => 80)
    port map (clk => clkf, cpuclk => clk, spi_sck => sck, spi_cs_n => scs, spi_mosi => smosi, spi_miso => smiso,
              sd_rd_req => rd_req, sd_wr_req => wr_req, sd_lba => lba, sd_seq => seq,
              sd_inited => inited, sd_cmd_cnt => ccnt, sd_last_cmd => lcmd,
              sd_rd_ready => rd_ready, sd_rd_error => rd_error, sd_wr_ack => wr_ack, rbuf_addr => rba, rbuf_data => rbd,
              wbuf_we => wbwe, wbuf_addr => wba, wbuf_data => wbd,
              mon_rx => mon_rx, mon_tx => mon_tx,
              matrix_col_idx => col_idx, matrix_col => col, joya => joya, joyb => joyb,
              ctrl => ctrl, spi_active => open);

  -- Model Rockchip: SPI master + sector server + UART checks
  rk : process
    variable rx : bytes_t(0 to 17);
    variable b  : std_logic_vector(7 downto 0);
    variable l  : integer;

    procedure xfer(tx : in std_logic_vector(7 downto 0); r : out std_logic_vector(7 downto 0)) is
      variable v : std_logic_vector(7 downto 0);
    begin
      for i in 7 downto 0 loop
        smosi <= tx(i);
        wait for TSPI;
        sck <= '1';
        v(i) := smiso;
        wait for TSPI;
        sck <= '0';
      end loop;
      r := v;
    end procedure;

    procedure status(r : out bytes_t) is
      variable v : std_logic_vector(7 downto 0);
    begin
      scs <= '0'; wait for TSPI;
      xfer(x"01", v); xfer(x"00", v);
      for i in 0 to 15 loop
        xfer(x"00", v); r(i) := v;
      end loop;
      wait for TSPI; scs <= '1'; wait for 4*TSPI;
    end procedure;

    procedure cmd1(c : std_logic_vector(7 downto 0)) is
      variable v : std_logic_vector(7 downto 0);
    begin
      scs <= '0'; wait for TSPI; xfer(c, v); wait for TSPI; scs <= '1'; wait for 4*TSPI;
    end procedure;
  begin
    wait for 2 us;
    -- INPUT register: ctrl=0, joyA=0x11, joyB=0x02, col0=0x80 ... col8=0x01
    scs <= '0'; wait for TSPI;
    xfer(x"30", b); xfer(x"00", b); xfer(x"11", b); xfer(x"02", b);
    for i in 0 to 8 loop
      if i = 0 then xfer(x"80", b); elsif i = 8 then xfer(x"01", b); else xfer(x"00", b); end if;
    end loop;
    wait for TSPI; scs <= '1'; wait for 4*TSPI;
    assert joya = "10001" and joyb = "00010" report "INPUT joy FAIL" severity failure;

    -- UART_PUT one byte to the monitor
    scs <= '0'; wait for TSPI; xfer(x"20", b); xfer(x"A5", b); wait for TSPI; scs <= '1'; wait for 4*TSPI;

    status(rx);
    for i in 0 to 15 loop report "st[" & integer'image(i) & "]=" & integer'image(to_integer(unsigned(rx(i)))); end loop;
    assert rx(0) = x"4D" and rx(1) = x"65" report "STATUS magic FAIL" severity failure;
    report "STATUS magic ok, version " & integer'image(to_integer(unsigned(rx(2))));

    -- sector server loop
    while not stop_server loop
      status(rx);
      l := to_integer(unsigned(std_logic_vector'(rx(5) & rx(6) & rx(7) & rx(8))));
      if rx(3)(0) = '1' and rx(3)(1) = '0' and rx(3)(5) = '0' and l >= 1000000 then
        cmd1(x"13");                         -- beyond the "card": error token
        report "rejected read lba " & integer'image(l);
      elsif rx(3)(0) = '1' and rx(3)(1) = '0' and rx(3)(5) = '0' then
        scs <= '0'; wait for TSPI;
        xfer(x"10", b);
        for i in 0 to 511 loop xfer(pat(l, i), b); end loop;
        wait for TSPI; scs <= '1'; wait for 4*TSPI;
        served <= served + 1;
        report "served read lba " & integer'image(l);
      elsif rx(3)(2) = '1' then
        scs <= '0'; wait for TSPI;
        xfer(x"11", b); xfer(x"00", b);
        for i in 0 to 511 loop
          xfer(x"00", b);
          assert b = wpat(l, i) report "WRITE DATA MISMATCH lba " & integer'image(l) &
            " byte " & integer'image(i) severity failure;
        end loop;
        wait for TSPI; scs <= '1'; wait for 4*TSPI;
        cmd1(x"12");
        written <= written + 1;
        report "write captured+acked lba " & integer'image(l);
      end if;
      -- monitor output -> Rockchip
      if rx(10) /= x"00" then
        scs <= '0'; wait for TSPI; xfer(x"21", b); xfer(x"00", b); xfer(x"00", b);
        wait for TSPI; scs <= '1'; wait for 4*TSPI;
        uart_seen <= b;
      end if;
      wait for 2 us;
    end loop;
    wait;
  end process;

  -- MEGA65 monitor model: echo check of mon_rx and send a byte on mon_tx
  mon : process
    variable v : std_logic_vector(7 downto 0);
    constant TXB : std_logic_vector(7 downto 0) := x"3C";
  begin
    wait until mon_rx = '0';
    wait for 10 * TCLK;                 -- middle of start bit
    for i in 0 to 7 loop
      wait for 20 * TCLK; v(i) := mon_rx;
    end loop;
    assert v = x"A5" report "UART bridge RK->monitor FAIL" severity failure;
    report "UART RK->monitor byte ok (A5)";
    wait for 5 us;
    -- send 0x3C to the bridge at 20 clocks/bit
    mon_tx <= '0'; wait for 20 * TCLK;
    for i in 0 to 7 loop
      mon_tx <= TXB(i); wait for 20 * TCLK;
    end loop;
    mon_tx <= '1';
    wait;
  end process;

  -- MEGA65 side: use the SD controller like sdcardio does
  host : process
    variable got : std_logic_vector(7 downto 0);
    procedure wait_ready is
    begin
      wait until rising_edge(clk) and busy_o = '0';
    end procedure;
    procedure read_block(a : integer; l : integer) is
    begin
      addr_i <= std_logic_vector(to_unsigned(a, 32));
      wait until rising_edge(clk);
      rd_i <= '1';
      wait until rising_edge(clk) and busy_o = '1';
      rd_i <= '0';
      for i in 0 to 511 loop
        wait until rising_edge(clk) and hs_o = '1';
        got := data_o;
        assert got = pat(l, i) report "READ MISMATCH lba " & integer'image(l) & " byte " &
          integer'image(i) & " got " & integer'image(to_integer(unsigned(got))) severity failure;
        hs_i <= '1';
        wait until rising_edge(clk) and hs_o = '0';
        hs_i <= '0';
      end loop;
      wait_ready;
      report "host read OK lba " & integer'image(l);
    end procedure;
    procedure write_block(a : integer; l : integer; m, f, last : std_logic) is
    begin
      addr_i <= std_logic_vector(to_unsigned(a, 32));
      wmulti <= m; wfirst <= f; wlast <= last;
      wait until rising_edge(clk);
      wr_i <= '1';
      wait until rising_edge(clk) and busy_o = '1';
      wr_i <= '0';
      for i in 0 to 511 loop
        wait until rising_edge(clk) and hs_o = '1';
        data_i <= wpat(l, i);
        hs_i <= '1';
        wait until rising_edge(clk) and hs_o = '0';
        hs_i <= '0';
      end loop;
      wait_ready;
      report "host write done lba " & integer'image(l);
    end procedure;
  begin
    reset_i <= '1'; wait for 200 ns; reset_i <= '0';
    wait for 1 us;
    wait_ready;
    assert err_o = x"0000" report "init error" severity failure;
    report "SD init OK (busy low, no error)";
    read_block(2, 2);          -- HYPPO's misaligned SDHC probe
    read_block(0, 0);
    read_block(12345, 12345);
    write_block(77, 77, '0', '0', '0');
    write_block(100, 100, '1', '1', '0');   -- CMD25 first block
    write_block(101, 101, '1', '0', '0');
    write_block(102, 102, '1', '0', '1');   -- last block + stop token
    read_block(5, 5);
    -- read past the end of the card: controller must report an error
    addr_i <= std_logic_vector(to_unsigned(2000000, 32));
    wait until rising_edge(clk);
    rd_i <= '1';
    wait until rising_edge(clk) and busy_o = '1';
    rd_i <= '0';
    wait_ready;
    assert err_o /= x"0000" report "out-of-range read did not error" severity failure;
    report "out-of-range read -> error " & integer'image(to_integer(unsigned(err_o)));
    -- recover the way sdcardio does: reset the controller, then read again
    reset_i <= '1'; wait for 200 ns; reset_i <= '0'; wait for 1 us;
    wait_ready;
    read_block(7, 7);
    wait for 20 us;
    assert uart_seen = x"3C" report "UART monitor->RK FAIL" severity failure;
    report "UART monitor->RK byte ok (3C)";
    assert served = 5 and written = 4 report "count mismatch" severity failure;
    report "TB_RK_SD_PASS";
    stop_server <= true;
    done <= true;
    wait;
  end process;

end architecture;
