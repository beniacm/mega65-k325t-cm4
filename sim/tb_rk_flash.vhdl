-- tb_rk_flash.vhdl -- rk_bridge configuration-flash pass-through (command 0x50) and IPROG
-- request (0x60) against a small SPI flash model (mode 0; JEDEC ID, WREN, page program,
-- read, read status).  Checks the answers, that every command sees whole bytes with CS low
-- (WREN exactly 8 clocks), the dummy clocks with CS high, and the IPROG toggle/address.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_rk_flash is
end entity;

architecture sim of tb_rk_flash is
  constant TCLK : time := 24.69 ns;      -- cpuclk 40.5 MHz
  constant TF   : time := 6.146 ns;      -- bridge clk 162.7 MHz
  constant TSPI : time := 50 ns;         -- Rockchip SPI 20 MHz
  signal clk, clkf : std_logic := '0';
  signal done : boolean := false;
  signal sck, smosi : std_logic := '0';
  signal scs : std_logic := '1';
  signal smiso : std_logic;
  signal f_sck, f_cs, f_mosi, f_miso, f_act, ip_tog : std_logic;
  signal ip_addr : std_logic_vector(31 downto 0);
  signal wren_ok : boolean := false;
  signal last_cmd_bits : integer := 0;
  type mem_t is array (0 to 255) of std_logic_vector(7 downto 0);
begin
  clk  <= not clk after TCLK / 2 when not done;
  clkf <= not clkf after TF / 2 when not done;

  br : entity work.rk_bridge
    generic map (BIT_CLKS => 80, CORE_ID => x"02")
    port map (clk => clkf, cpuclk => clk, spi_sck => sck, spi_cs_n => scs, spi_mosi => smosi,
              spi_miso => smiso, sd_rd_req => '0', sd_wr_req => '0', sd_lba => (others => '0'),
              sd_seq => (others => '0'), sd_inited => '0', sd_cmd_cnt => (others => '0'),
              sd_last_cmd => (others => '0'), sd_rd_ready => open, sd_rd_error => open,
              sd_wr_ack => open, rbuf_addr => (others => '0'), rbuf_data => open, wbuf_we => '0',
              wbuf_addr => (others => '0'), wbuf_data => (others => '0'), mon_rx => open,
              mon_tx => '1', matrix_col_idx => 0, matrix_col => open, joya => open, joyb => open,
              ctrl => open, eth_tx_raddr => open, eth_tx_pop => open, eth_rx_we => open,
              eth_rx_waddr => open, eth_rx_wdata => open, eth_rx_push => open, eth_rx_len => open,
              flash_active => f_act, flash_sck => f_sck, flash_cs_n => f_cs, flash_mosi => f_mosi,
              flash_miso => f_miso, iprog_toggle => ip_tog, iprog_addr => ip_addr,
              spi_active => open);

  -- SPI flash model (mode 0): commands 9F, 06, 05, 02, 03
  flash : process
    variable mem : mem_t := (others => x"FF");
    variable sr, cmd : std_logic_vector(7 downto 0);
    variable nbits, nbytes : integer;
    variable addr : integer;
    variable wel : boolean := false;
    variable outb : std_logic_vector(7 downto 0);
    variable obit : integer;
  begin
    f_miso <= 'Z';
    wait until f_cs = '0';
    nbits := 0; nbytes := 0; outb := x"FF"; obit := 7;
    while f_cs = '0' loop
      wait until rising_edge(f_sck) or f_cs = '1';
      exit when f_cs = '1';
      sr := sr(6 downto 0) & f_mosi;
      nbits := nbits + 1;
      if nbits mod 8 = 0 then
        nbytes := nbytes + 1;
        if nbytes = 1 then
          cmd := sr;
          case cmd is
            when x"9F" => outb := x"01";
            when x"05" => if wel then outb := x"02"; else outb := x"00"; end if;
            when others => null;
          end case;
        elsif cmd = x"9F" then
          if nbytes = 2 then outb := x"60"; elsif nbytes = 3 then outb := x"18"; end if;
        elsif (cmd = x"02" or cmd = x"03") and nbytes = 4 then
          addr := to_integer(unsigned(sr));         -- low address byte only (model)
          if cmd = x"03" then outb := mem(addr); end if;
        elsif cmd = x"02" and nbytes > 4 then
          assert wel report "page program without WREN" severity failure;
          mem(addr) := mem(addr) and sr; addr := (addr + 1) mod 256;
        elsif cmd = x"03" and nbytes > 4 then
          addr := (addr + 1) mod 256; outb := mem(addr);
        end if;
        obit := 7;
      end if;
      -- output the next bit after the falling edge
      wait until falling_edge(f_sck) or f_cs = '1';
      exit when f_cs = '1';
      if nbits mod 8 = 0 then
        f_miso <= outb(7); obit := 6;
      elsif cmd = x"9F" or cmd = x"05" or (cmd = x"03" and nbytes >= 4) then
        f_miso <= outb(obit); if obit > 0 then obit := obit - 1; end if;
      end if;
    end loop;
    last_cmd_bits <= nbits;
    if cmd = x"06" then
      assert nbits = 8 report "WREN with " & integer'image(nbits) & " clocks" severity failure;
      wel := true;
    elsif cmd = x"02" then
      wel := false;
    end if;
    assert nbits mod 8 = 0 report "CS released mid-byte (" & integer'image(nbits) & " clocks)" severity failure;
  end process;

  rk : process
    type bytes_t is array (0 to 300) of std_logic_vector(7 downto 0);
    variable tx, rx : bytes_t;
    procedure xfer(tx_b : in std_logic_vector(7 downto 0); r : out std_logic_vector(7 downto 0)) is
      variable v : std_logic_vector(7 downto 0);
    begin
      for i in 7 downto 0 loop
        smosi <= tx_b(i); wait for TSPI / 2;
        sck <= '1'; v(i) := smiso; wait for TSPI / 2;
        sck <= '0';
      end loop;
      r := v;
    end procedure;
    -- one 0x50 frame: n data bytes from tx(0..n-1); answers to rx(0..n-1)
    procedure frame(n : integer) is
      variable all_rx : bytes_t;
      variable b : std_logic_vector(7 downto 0);
    begin
      scs <= '0'; wait for TSPI;
      xfer(x"50", b); xfer(std_logic_vector(to_unsigned(n / 256, 8)), b);
      xfer(std_logic_vector(to_unsigned(n mod 256, 8)), b);
      for k in 0 to n + 1 loop
        if k < n then xfer(tx(k), all_rx(k + 3)); else xfer(x"00", all_rx(k + 3)); end if;
      end loop;
      wait for TSPI; scs <= '1'; wait for 2 us;
      for k in 0 to n - 1 loop rx(k) := all_rx(k + 5); end loop;
    end procedure;
    variable b : std_logic_vector(7 downto 0);
    variable st : bytes_t;
  begin
    wait for 1 us;
    -- core id in STATUS byte 52
    scs <= '0'; wait for TSPI;
    xfer(x"01", b); xfer(x"00", b);
    for i in 0 to 52 loop xfer(x"00", st(i)); end loop;
    scs <= '1'; wait for 1 us;
    assert st(52) = x"02" report "core id wrong" severity failure;
    -- JEDEC ID
    tx(0) := x"9F"; tx(1) := x"00"; tx(2) := x"00"; tx(3) := x"00";
    frame(4);
    assert rx(1) = x"01" and rx(2) = x"60" and rx(3) = x"18"
      report "JEDEC ID wrong: " & to_hstring(rx(1)) & to_hstring(rx(2)) & to_hstring(rx(3)) severity failure;
    report "JEDEC ID 01 60 18 ok";
    -- WREN (exactly 8 clocks), status shows WEL
    tx(0) := x"06"; frame(1);
    assert last_cmd_bits = 8 report "WREN clocks" severity failure;
    tx(0) := x"05"; tx(1) := x"00"; frame(2);
    assert rx(1)(1) = '1' report "WEL not set" severity failure;
    -- page program 16 bytes at address 0x20, read back
    tx(0) := x"02"; tx(1) := x"00"; tx(2) := x"00"; tx(3) := x"20";
    for i in 0 to 15 loop tx(4 + i) := std_logic_vector(to_unsigned(16#A0# + i, 8)); end loop;
    frame(20);
    tx(0) := x"03"; tx(1) := x"00"; tx(2) := x"00"; tx(3) := x"1F";
    for i in 0 to 17 loop tx(4 + i) := x"00"; end loop;
    frame(22);
    assert rx(4) = x"FF" report "byte before the page data changed" severity failure;
    for i in 0 to 15 loop
      assert rx(5 + i) = std_logic_vector(to_unsigned(16#A0# + i, 8))
        report "read back " & integer'image(i) & ": " & to_hstring(rx(5 + i)) severity failure;
    end loop;
    report "page program + read back ok";
    -- IPROG request
    b := ip_tog & "0000000";
    scs <= '0'; wait for TSPI;
    xfer(x"60", st(0)); xfer(x"00", st(0)); xfer(x"80", st(0)); xfer(x"00", st(0)); xfer(x"00", st(0));
    xfer(x"A5", st(0));
    wait for TSPI; scs <= '1'; wait for 1 us;
    assert ip_tog /= b(7) and ip_addr = x"00800000" report "IPROG request" severity failure;
    report "TB_RK_FLASH_PASS";
    done <= true;
    wait;
  end process;
end architecture;
