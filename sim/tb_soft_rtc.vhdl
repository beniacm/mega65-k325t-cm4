-- tb_soft_rtc.vhdl -- soft_rtc calendar: leap years, month ends, year 2099 -> 2000, register reads;
-- list mode v2 collision registers ($FFD71A0-C3): copy on the toggle, partner, status, IRQ
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_soft_rtc is
end entity;

architecture sim of tb_soft_rtc is
  constant F : integer := 4;                  -- 4 clocks per "second"
  signal clk : std_logic := '0';
  signal done : boolean := false;
  signal cs, wr, rd : std_logic := '0';
  signal addr : unsigned(19 downto 0) := (others => '0');
  signal wdata, rdata : unsigned(7 downto 0);
  signal col_bits : std_logic_vector(255 downto 0) := (others => '0');
  signal col_count, col_pidx, col_pdata : unsigned(7 downto 0) := x"00";
  signal col_toggle, irq_n, rst_n : std_logic := '1';
begin
  clk <= not clk after 5 ns when not done;
  dut : entity work.soft_rtc generic map (clock_frequency => F)
    port map (clock => clk, reset_n => rst_n, cs => cs, fastio_addr => addr, fastio_write => wr,
              fastio_read => rd, fastio_wdata => wdata, fastio_rdata => rdata,
              col_bits_in => col_bits, col_count_in => col_count, col_toggle_in => col_toggle,
              col_pidx_out => col_pidx, col_pdata_in => col_pdata, irq_n => irq_n);
  -- the VIC-IV's partner RAM, as seen through col_pidx: partner = 255 - index
  col_pdata <= 255 - col_pidx;
  process
    procedure poke(a : integer; v : unsigned(7 downto 0)) is
    begin
      addr <= x"D71" & to_unsigned(a, 8); wdata <= v; cs <= '1'; wr <= '1';
      wait until rising_edge(clk); cs <= '0'; wr <= '0';
    end procedure;
    procedure peek(a : integer; v : out unsigned(7 downto 0)) is
    begin
      addr <= x"D71" & to_unsigned(a, 8); cs <= '1'; rd <= '1'; wait for 1 ns;
      v := rdata; cs <= '0'; rd <= '0'; wait for 1 ns;
    end procedure;
    procedure set(y, mo, d, h, mi, s, w : unsigned(7 downto 0)) is
    begin
      poke(16#15#, y); poke(16#14#, mo); poke(16#13#, d); poke(16#12#, h);
      poke(16#11#, mi); poke(16#16#, w); poke(16#10#, s);
    end procedure;
    procedure expect(y, mo, d, h, mi, s, w : unsigned(7 downto 0); what : string) is
      variable v : unsigned(7 downto 0);
      type arr is array (0 to 6) of unsigned(7 downto 0);
      variable e : arr := (s, mi, h or x"80", d, mo, y, w);
    begin
      for i in 0 to 6 loop
        peek(16#10# + i, v);
        assert v = e(i) report what & ": reg $" & to_hstring(to_unsigned(16#10# + i, 8)) &
          " = " & to_hstring(v) & ", expected " & to_hstring(e(i)) severity failure;
      end loop;
      report what & " ok";
    end procedure;
    procedure seconds(n : integer) is
    begin
      for i in 1 to n * F loop wait until rising_edge(clk); end loop;
    end procedure;
    variable v : unsigned(7 downto 0);
  begin
    wait until rising_edge(clk);
    peek(16#13#, v); assert v = x"00" report "day not 0 before set" severity failure;
    set(x"24", x"02", x"28", x"23", x"59", x"58", x"03");
    seconds(1); expect(x"24", x"02", x"28", x"23", x"59", x"59", x"03", "23:59:59");
    seconds(1); expect(x"24", x"02", x"29", x"00", x"00", x"00", x"04", "2024 leap day");
    set(x"23", x"02", x"28", x"23", x"59", x"59", x"06");
    seconds(1); expect(x"23", x"03", x"01", x"00", x"00", x"00", x"00", "2023 Feb -> Mar 1");
    set(x"26", x"04", x"30", x"23", x"59", x"59", x"02");
    seconds(1); expect(x"26", x"05", x"01", x"00", x"00", x"00", x"03", "Apr 30 -> May 1");
    set(x"99", x"12", x"31", x"23", x"59", x"59", x"04");
    seconds(1); expect(x"00", x"01", x"01", x"00", x"00", x"00", x"05", "2099 -> 2000");
    set(x"26", x"11", x"06", x"12", x"09", x"59", x"05");
    seconds(1); expect(x"26", x"11", x"06", x"12", x"10", x"00", x"05", "12:09:59 -> 12:10:00");
    peek(16#00#, v); assert v = x"00" report "other register not 0" severity failure;

    -- collisions: nothing yet
    peek(16#C0#, v); assert v = x"00" report "collision status not 0 after start" severity failure;
    peek(16#C3#, v); assert v = x"00" report "count not 0" severity failure;
    poke(16#C0#, x"02");                          -- IRQ enable
    peek(16#C0#, v); assert v = x"02" report "IRQ enable does not read back" severity failure;
    assert irq_n = '1' report "IRQ without a collision" severity failure;
    -- a result: objects 0, 9, 200 collided; changes of the inputs without a toggle are ignored
    col_bits <= (0 => '1', 9 => '1', 200 => '1', others => '0'); col_count <= x"03";
    for i in 1 to 4 loop wait until rising_edge(clk); end loop;
    peek(16#A0#, v); assert v = x"00" report "table changed without a toggle" severity failure;
    col_toggle <= not col_toggle;
    for i in 1 to 4 loop wait until rising_edge(clk); end loop;
    peek(16#A0#, v); assert v = x"01" report "byte 0" severity failure;
    peek(16#A1#, v); assert v = x"02" report "byte 1 (object 9)" severity failure;
    peek(16#B9#, v); assert v = x"01" report "byte 25 (object 200)" severity failure;
    peek(16#BF#, v); assert v = x"00" report "byte 31" severity failure;
    peek(16#C3#, v); assert v = x"03" report "count" severity failure;
    peek(16#C0#, v); assert v = x"83" report "status not $83: " & to_hstring(v) severity failure;
    assert irq_n = '0' report "no IRQ" severity failure;
    -- partner: $FF for objects without a collision, else the VIC-IV's value
    poke(16#C1#, x"09"); peek(16#C1#, v); assert v = x"09" report "index read back" severity failure;
    peek(16#C2#, v); assert v = x"F6" report "partner of 9" severity failure;
    poke(16#C1#, x"0A"); peek(16#C2#, v); assert v = x"FF" report "partner of 10 (none)" severity failure;
    -- acknowledge (keep enabled): bits 0 and 7 clear, the IRQ goes away, the table stays
    poke(16#C0#, x"03");
    peek(16#C0#, v); assert v = x"02" report "status after clear" severity failure;
    assert irq_n = '1' report "IRQ after clear" severity failure;
    peek(16#A1#, v); assert v = x"02" report "table cleared by the acknowledge" severity failure;
    -- a pass without a collision: table and count follow, bit 0 / IRQ stay off
    col_bits <= (others => '0'); col_count <= x"00"; col_toggle <= not col_toggle;
    for i in 1 to 4 loop wait until rising_edge(clk); end loop;
    peek(16#C0#, v); assert v = x"02" report "status after a pass without collision" severity failure;
    peek(16#A0#, v); assert v = x"00" report "table after a pass without collision" severity failure;
    assert irq_n = '1' report "IRQ without collision" severity failure;
    -- with the IRQ disabled: pending is set, the line stays off; reset clears status and enable
    poke(16#C0#, x"01");
    col_bits(5) <= '1'; col_count <= x"01"; col_toggle <= not col_toggle;
    for i in 1 to 4 loop wait until rising_edge(clk); end loop;
    peek(16#C0#, v); assert v = x"81" report "status with the IRQ disabled" severity failure;
    assert irq_n = '1' report "IRQ while disabled" severity failure;
    rst_n <= '0'; wait until rising_edge(clk); rst_n <= '1'; wait until rising_edge(clk);
    peek(16#C0#, v); assert v = x"00" report "status after reset" severity failure;
    peek(16#FD#, v); assert v = x"02" report "feature level $FFD71FD not 2" severity failure;
    report "collision registers ok";
    report "TB_SOFT_RTC_PASS";
    done <= true; wait;
  end process;
end architecture;
