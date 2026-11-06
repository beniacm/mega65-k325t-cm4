-- tb_kbd_emu.vhdl -- M2M's real keyboard receiver (mega65kbd_to_matrix, on the C64 core
-- clock) <-> m65kbd_emu (100 MHz) answering from a matrix like rk_bridge's (active low).
-- Checks every column of the received matrix for two different key patterns, plus
-- CAPS LOCK, RESTORE and the INST/DEL + RETURN keys (sent at phases 76/77).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_kbd_emu is
end entity;

architecture sim of tb_kbd_emu is
  constant TMAIN : time := 31.80 ns;   -- 31.45 MHz C64 core clock
  constant T100  : time := 10 ns;
  signal clkm, clk100 : std_logic := '0';
  signal done : boolean := false;
  signal kio8, kio9, kio10 : std_logic;
  signal col_idx_host : integer range 0 to 15 := 0;
  signal col_host : std_logic_vector(7 downto 0);
  signal col_idx_emu : integer range 0 to 15;
  signal col_emu : std_logic_vector(7 downto 0);
  signal restore, capslock, del_k, ret_k, fast_k, up_k, left_k : std_logic;
  signal caps_n, rest_n : std_logic := '1';
  signal up_n, left_n : std_logic := '1';
  type mat_t is array (0 to 8) of std_logic_vector(7 downto 0);
  signal mat : mat_t := (others => (others => '1'));
begin
  clkm   <= not clkm after TMAIN / 2 when not done;
  clk100 <= not clk100 after T100 / 2 when not done;

  host : entity work.mega65kbd_to_matrix
    port map (ioclock => clkm, clock_frequency => 31448993,
              powerled_steady => '1', powerled_col => x"00FF00",
              driveled_steady => '0', driveled_blinking => '0', driveled_col => x"000000",
              matrix_col => col_host, matrix_col_idx => col_idx_host,
              delete_out => del_k, return_out => ret_k, fastkey_out => fast_k,
              restore => restore, capslock_out => capslock, leftkey => left_k, upkey => up_k,
              kio8 => kio8, kio9 => kio9, kio10 => kio10);

  emu : entity work.m65kbd_emu
    port map (clk => clk100, kio8 => kio8, kio10 => kio10, matrix_col_idx => col_idx_emu,
              matrix_col => col_emu, capslock_n => caps_n, restore_n => rest_n,
              up_n => up_n, left_n => left_n);

  col_emu <= mat(col_idx_emu) when col_idx_emu <= 8 else (others => '1');

  chk : process
    procedure frames(n : integer) is
    begin
      wait for n * 500 us;                     -- one frame = 141 phases x ~3.2 us
    end procedure;
    procedure check_matrix(tag : string) is
    begin
      for c in 0 to 8 loop
        col_idx_host <= c;
        wait until rising_edge(clkm); wait until rising_edge(clkm); wait until rising_edge(clkm);
        if c = 0 then
          -- rows 0/1 of column 0 come from the INST/DEL / RETURN bits (phases 76/77)
          assert col_host(7 downto 2) = mat(0)(7 downto 2)
            report tag & ": column 0 mismatch" severity failure;
          assert del_k = mat(0)(0) and ret_k = mat(0)(1)
            report tag & ": DEL/RETURN mismatch" severity failure;
        else
          assert col_host = mat(c)
            report tag & ": column " & integer'image(c) & " got " &
                   integer'image(to_integer(unsigned(col_host))) & " expected " &
                   integer'image(to_integer(unsigned(mat(c)))) severity failure;
        end if;
      end loop;
      report tag & ": all 9 columns ok";
    end procedure;
  begin
    frames(3);
    check_matrix("no keys");
    assert restore = '1' and capslock = '1' and up_k = '0' and left_k = '0'
      report "idle special keys wrong" severity failure;
    -- some keys: A (col 1 row 2), RETURN (col 0 row 1), SPACE (col 7 row 4), F9 col 8 row 0
    mat(1) <= "11111011"; mat(0) <= "11111101"; mat(7) <= "11101111"; mat(8) <= "11111110";
    caps_n <= '0'; rest_n <= '0'; up_n <= '0'; left_n <= '0';
    frames(3);
    check_matrix("keys pressed");
    assert restore = '0' and capslock = '0' report "RESTORE / CAPS LOCK not seen" severity failure;
    assert up_k = '1' and left_k = '1' report "cursor up / left keys not seen" severity failure;
    -- release, plus INST/DEL alone
    mat <= (others => (others => '1')); mat(0) <= "11111110";
    caps_n <= '1'; rest_n <= '1'; up_n <= '1'; left_n <= '1';
    frames(3);
    check_matrix("DEL only");
    assert restore = '1' and capslock = '1' report "RESTORE / CAPS LOCK not released" severity failure;
    assert up_k = '0' and left_k = '0' report "cursor up / left keys not released" severity failure;
    report "TB_KBD_EMU_PASS";
    done <= true;
    wait;
  end process;
end architecture;
