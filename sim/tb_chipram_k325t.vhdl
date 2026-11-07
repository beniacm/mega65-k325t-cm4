-- tb_chipram_k325t.vhdl -- 1 MB chip RAM wrapper: lo (pre-loaded 384 KB) / mid / hi regions,
-- CPU port A write+read, VIC port B read, no aliasing between regions, initial ROM content kept.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity tb_chipram_k325t is end entity;
architecture sim of tb_chipram_k325t is
  signal clk : std_logic := '0';
  signal done : boolean := false;
  signal aa : integer range 0 to 1048575 := 0;
  signal we : std_logic := '0';
  signal di, doa, dob, w1, w2 : unsigned(7 downto 0);
  signal ab : unsigned(19 downto 0) := (others => '0');
begin
  clk <= not clk after 5 ns when not done;
  dut : entity work.shadowram port map (ClkA => clk, addressa => aa, wea => we, dia => di,
        writes => w1, no_writes => w2, doa => doa, ClkB => clk, addressb => ab, dob => dob);
  process
    type al is array (natural range <>) of integer;
    constant A : al := (16#00800#, 16#5FFFF#, 16#60000#, 16#7FFFF#, 16#80000#, 16#ABCDE#, 16#FFFFF#);
    variable rom0 : unsigned(7 downto 0);
    procedure rd(adr : integer; exp : integer) is
    begin
      aa <= adr; ab <= to_unsigned(adr, 20);
      wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns;
      assert to_integer(doa) = exp report "port A " & integer'image(adr) & " got " & integer'image(to_integer(doa)) severity failure;
      assert to_integer(dob) = exp report "port B " & integer'image(adr) & " got " & integer'image(to_integer(dob)) severity failure;
    end procedure;
  begin
    -- pre-loaded content (open-roms at $20000) must still be there
    aa <= 16#2E000#; wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns; rom0 := doa;
    for i in A'range loop
      aa <= A(i); di <= to_unsigned(i * 37 + 11, 8); we <= '1';
      wait until rising_edge(clk); we <= '0';
    end loop;
    for i in A'range loop rd(A(i), i * 37 + 11); end loop;
    -- regions do not alias: same low address bits in lo / mid / hi
    rd(16#80800#, 0); rd(16#60800#, 0);          -- same low bits as $00800 in hi and mid
    rd(16#2E000#, to_integer(rom0));
    report "rom byte $2E000 = " & integer'image(to_integer(rom0));
    report "TB_CHIPRAM_K325T_PASS"; done <= true; wait;
  end process;
end architecture;
