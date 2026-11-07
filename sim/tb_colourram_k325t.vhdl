-- tb_colourram_k325t.vhdl -- 64 KB colour RAM: en64=0 mirrors 32 KB (real MEGA65), en64=1 separate halves
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity tb_colourram_k325t is end entity;
architecture sim of tb_colourram_k325t is
  signal clk : std_logic := '0'; signal done : boolean := false;
  signal en64, we : std_logic := '0';
  signal aa, ab : std_logic_vector(15 downto 0) := (others => '0');
  signal di, doa, dob : std_logic_vector(7 downto 0) := x"00";
begin
  clk <= not clk after 5 ns when not done;
  dut : entity work.ram8x64k_k325t port map (en64 => en64, clka => clk, ena => '1', wea(0) => we,
        addra => aa, dina => di, douta => doa, clkb => clk, addrb => ab, doutb => dob);
  process
    procedure wr(a : integer; d : integer) is begin
      aa <= std_logic_vector(to_unsigned(a,16)); di <= std_logic_vector(to_unsigned(d,8)); we <= '1';
      wait until rising_edge(clk); we <= '0'; end procedure;
    procedure rd(a : integer; e : integer) is begin
      aa <= std_logic_vector(to_unsigned(a,16)); ab <= std_logic_vector(to_unsigned(a,16));
      wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns;
      assert to_integer(unsigned(doa)) = e and to_integer(unsigned(dob)) = e
        report "addr " & integer'image(a) & " A=" & integer'image(to_integer(unsigned(doa))) &
               " B=" & integer'image(to_integer(unsigned(dob))) & " exp " & integer'image(e) severity failure;
    end procedure;
  begin
    for i in 0 to 3 loop wait until rising_edge(clk); end loop;
    wr(16#0005#, 16#11#); wr(16#8005#, 16#22#);          -- en64=0: same cell
    rd(16#0005#, 16#22#); rd(16#8005#, 16#22#);
    en64 <= '1'; for i in 0 to 3 loop wait until rising_edge(clk); end loop;
    wr(16#8005#, 16#33#); rd(16#0005#, 16#22#); rd(16#8005#, 16#33#); rd(16#FFFF#, 0);
    report "TB_COLOURRAM_K325T_PASS"; done <= true; wait;
  end process;
end architecture;
