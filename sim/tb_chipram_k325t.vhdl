-- tb_chipram_k325t.vhdl -- 1 MB chip RAM wrapper: lo (pre-loaded 384 KB) / mid / hi regions,
-- CPU port A write+read, VIC port B read, no aliasing between regions, initial ROM content kept;
-- port B's 32-bit word (dob32, byte lanes) against byte reads, in every region.
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
  signal dob32 : unsigned(31 downto 0);
begin
  clk <= not clk after 5 ns when not done;
  dut : entity work.shadowram port map (ClkA => clk, addressa => aa, wea => we, dia => di,
        writes => w1, no_writes => w2, doa => doa, ClkB => clk, addressb => ab, dob => dob, dob32 => dob32);
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
    -- 32-bit word reads: write 4 distinct bytes per word in each region (and in the ROM area,
    -- read only), compare the word with the bytes read through port A
    for reg in 0 to 2 loop
      for w in 0 to 3 loop
        for b in 0 to 3 loop
          aa <= 16#10000# * (reg * 7 + 1) + 16#3A0# + 4 * w + b;
          if reg = 1 then aa <= 16#60000# + 16#3A0# + 4 * w + b; end if;
          if reg = 2 then aa <= 16#C0000# + 16#3A0# + 4 * w + b; end if;
          di <= to_unsigned((reg * 16 + w * 4 + b) * 13 + 7, 8); we <= '1';
          wait until rising_edge(clk); we <= '0';
        end loop;
      end loop;
    end loop;
    for reg in 0 to 3 loop
      for w in 0 to 3 loop
        for b in 0 to 3 loop
          case reg is
            when 0 => aa <= 16#10000# + 16#3A0# + 4 * w + b; ab <= to_unsigned(16#10000# + 16#3A0# + 4 * w, 20);
            when 1 => aa <= 16#60000# + 16#3A0# + 4 * w + b; ab <= to_unsigned(16#60000# + 16#3A0# + 4 * w, 20);
            when 2 => aa <= 16#C0000# + 16#3A0# + 4 * w + b; ab <= to_unsigned(16#C0000# + 16#3A0# + 4 * w, 20);
            when others => aa <= 16#2E000# + 4 * w + b; ab <= to_unsigned(16#2E000# + 4 * w, 20);
          end case;
          wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns;
          assert dob32(8 * b + 7 downto 8 * b) = doa
            report "dob32 region " & integer'image(reg) & " word " & integer'image(w) & " byte " & integer'image(b) severity failure;
        end loop;
      end loop;
    end loop;
    report "TB_CHIPRAM_K325T_PASS"; done <= true; wait;
  end process;
end architecture;
