-- tb_soft_rtc.vhdl -- soft_rtc calendar: leap years, month ends, year 2099 -> 2000, register reads
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
begin
  clk <= not clk after 5 ns when not done;
  dut : entity work.soft_rtc generic map (clock_frequency => F)
    port map (clock => clk, cs => cs, fastio_addr => addr, fastio_write => wr,
              fastio_read => rd, fastio_wdata => wdata, fastio_rdata => rdata);
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
    report "TB_SOFT_RTC_PASS";
    done <= true; wait;
  end process;
end architecture;
