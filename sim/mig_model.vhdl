-- mig_model.vhdl -- behavioural model of the MIG 7-series native UI for one x16 DDR3
-- (4:1, BL8: 128-bit words, app_addr in 16-bit units, aligned to 8).
-- Reads are pipelined and returned in order after 18..33 cycles; app_rdy and app_wdf_rdy
-- are random and independent.  Checks the UG586 rule that write data comes no later than
-- two cycles after its command.  Memory: WORDS x 128 bit, addresses wrap.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity mig_model is
   generic (
      WORDS : integer := 65536;
      SEED  : std_logic_vector(15 downto 0) := x"BEEF"
   );
   port (
      clk               : in  std_logic;
      app_addr          : in  std_logic_vector(27 downto 0);
      app_cmd           : in  std_logic_vector(2 downto 0);
      app_en            : in  std_logic;
      app_rdy           : out std_logic := '0';
      app_wdf_data      : in  std_logic_vector(127 downto 0);
      app_wdf_end       : in  std_logic;
      app_wdf_wren      : in  std_logic;
      app_wdf_mask      : in  std_logic_vector(15 downto 0);
      app_wdf_rdy       : out std_logic := '0';
      app_rd_data       : out std_logic_vector(127 downto 0) := (others => '0');
      app_rd_data_valid : out std_logic := '0'
   );
end entity;

architecture sim of mig_model is
begin
   process
      type mem_t is array (0 to WORDS - 1) of std_logic_vector(127 downto 0);
      variable mem : mem_t := (others => (others => '0'));
      variable lfsr : unsigned(15 downto 0) := unsigned(SEED);
      type q_t is array (0 to 63) of integer;
      variable rq_addr, rq_due : q_t;
      variable rq_head, rq_tail, rq_n : integer := 0;
      variable cyc : integer := 0;
      variable wq_addr : q_t;
      variable wq_n : integer := 0;
      type dq_t is array (0 to 63) of std_logic_vector(127 downto 0);
      variable dq_data : dq_t;
      variable dq_mask : q_t;
      variable dq_n : integer := 0;
      variable w : std_logic_vector(127 downto 0);
      variable m : std_logic_vector(15 downto 0);
      variable lat : integer;
      variable wc_cyc : q_t;
      variable wc_n, wd_seen : integer := 0;
      variable rdy_i, wrdy_i : std_logic := '0';
   begin
      wait until rising_edge(clk);
      cyc := cyc + 1;
      lfsr := lfsr(14 downto 0) & (lfsr(15) xor lfsr(13) xor lfsr(12) xor lfsr(10));
      if app_en = '1' and rdy_i = '1' then
         assert app_addr(2 downto 0) = "000" report "app_addr not aligned" severity failure;
         if app_cmd = "001" then
            lat := 18 + to_integer(lfsr(3 downto 0));
            if rq_n > 0 and cyc + lat <= rq_due((rq_tail + 63) mod 64) then
               lat := rq_due((rq_tail + 63) mod 64) - cyc + 1;
            end if;
            rq_addr(rq_tail) := to_integer(unsigned(app_addr(27 downto 3))) mod WORDS;
            rq_due(rq_tail) := cyc + lat;
            rq_tail := (rq_tail + 1) mod 64; rq_n := rq_n + 1;
         elsif app_cmd = "000" then
            wq_addr(wq_n) := to_integer(unsigned(app_addr(27 downto 3))) mod WORDS; wq_n := wq_n + 1;
            wc_cyc(wc_n mod 64) := cyc; wc_n := wc_n + 1;
         else
            report "bad app_cmd" severity failure;
         end if;
      end if;
      if app_wdf_wren = '1' and wrdy_i = '1' then
         assert app_wdf_end = '1' report "wdf without end" severity failure;
         if wd_seen < wc_n then
            assert cyc - wc_cyc(wd_seen mod 64) <= 2
               report "MIG rule: write data " & integer'image(cyc - wc_cyc(wd_seen mod 64)) &
                      " cycles after its command" severity failure;
         end if;
         wd_seen := wd_seen + 1;
         dq_data(dq_n) := app_wdf_data;
         dq_mask(dq_n) := to_integer(unsigned(app_wdf_mask)); dq_n := dq_n + 1;
      end if;
      while wq_n > 0 and dq_n > 0 loop
         w := mem(wq_addr(0)); m := std_logic_vector(to_unsigned(dq_mask(0), 16));
         for i in 0 to 15 loop
            if m(i) = '0' then w(8 * i + 7 downto 8 * i) := dq_data(0)(8 * i + 7 downto 8 * i); end if;
         end loop;
         mem(wq_addr(0)) := w;
         for k in 0 to 62 loop
            wq_addr(k) := wq_addr(k + 1); dq_data(k) := dq_data(k + 1); dq_mask(k) := dq_mask(k + 1);
         end loop;
         wq_n := wq_n - 1; dq_n := dq_n - 1;
      end loop;
      app_rd_data_valid <= '0';
      if rq_n > 0 and rq_due(rq_head) <= cyc then
         app_rd_data <= mem(rq_addr(rq_head));
         app_rd_data_valid <= '1';
         rq_head := (rq_head + 1) mod 64; rq_n := rq_n - 1;
      end if;
      rdy_i  := lfsr(0) or lfsr(6) or lfsr(9);
      wrdy_i := lfsr(2) or lfsr(11);
      app_rdy     <= rdy_i;
      app_wdf_rdy <= wrdy_i;
   end process;
end architecture;
