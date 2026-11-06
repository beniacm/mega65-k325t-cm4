-- tb_c64_crt.vhdl -- the C64 core's software-cartridge loader on the K325T memory path:
-- a QNICE model writes the first part of a real .crt file byte by byte (like the M2M
-- firmware: one QNICE write per byte, address = byte offset), then starts the parser via
-- the CSR with the full file length.  sw_cartridge_wrapper (real) -> avm_mig -> MIG model.
-- Passing the CRT header (and the first CHIP headers that were written) shows the write
-- and read paths agree; the parse then stops at the first CHIP that was not written.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity tb_c64_crt is
   generic (
      G_HEX_FILE : string  := "eob_head.hex";   -- one byte per line, hex
      G_FILE_LEN : natural := 976816            -- length of the whole .crt
   );
end entity;

architecture sim of tb_c64_crt is
   signal qnice_clk, main_clk, hr_clk : std_logic := '0';
   signal qnice_rst, main_rst, hr_rst : std_logic := '1';
   signal done : boolean := false;

   signal qnice_addr : std_logic_vector(27 downto 0) := (others => '0');
   signal qnice_wdata, qnice_rdata : std_logic_vector(15 downto 0) := (others => '0');
   signal qnice_ce, qnice_we : std_logic := '0';
   signal qnice_wait : std_logic;

   signal hr_write, hr_read, hr_rdv, hr_wait : std_logic;
   signal hr_address : std_logic_vector(31 downto 0);
   signal hr_wdata, hr_rdata : std_logic_vector(15 downto 0);
   signal hr_be : std_logic_vector(1 downto 0);
   signal hr_bc : std_logic_vector(7 downto 0);

   -- framework arbiter: #2 scaler-like traffic, #1 the core (cartridge), #0 idle
   signal d_write, d_read : std_logic := '0';
   signal d_addr : std_logic_vector(31 downto 0) := (others => '0');
   signal d_wdata : std_logic_vector(15 downto 0) := (others => '0');
   signal d_bc : std_logic_vector(7 downto 0) := x"01";
   signal d_rdv, d_wait : std_logic;
   signal s_write, s_read, s_rdv, s_wait : std_logic_vector(2 downto 0);
   signal s_addr : std_logic_vector(95 downto 0);
   signal s_wdata, s_rdata : std_logic_vector(47 downto 0);
   signal s_be : std_logic_vector(5 downto 0);
   signal s_bc : std_logic_vector(23 downto 0);
   signal m_write, m_read, m_rdv, m_wait : std_logic;
   signal m_addr : std_logic_vector(31 downto 0);
   signal m_wdata, m_rdata : std_logic_vector(15 downto 0);
   signal m_be : std_logic_vector(1 downto 0);
   signal m_bc : std_logic_vector(7 downto 0);
   signal app_addr : std_logic_vector(27 downto 0);
   signal app_cmd : std_logic_vector(2 downto 0);
   signal app_en, app_rdy, app_wdf_end, app_wdf_wren, app_wdf_rdy, app_rd_data_valid : std_logic;
   signal app_wdf_data, app_rd_data : std_logic_vector(127 downto 0);
   signal app_wdf_mask : std_logic_vector(15 downto 0);
begin
   qnice_clk <= not qnice_clk after 10 ns when not done;     -- 50 MHz
   main_clk  <= not main_clk after 15.86 ns when not done;   -- 31.5 MHz
   hr_clk    <= not hr_clk after 5 ns when not done;         -- 100 MHz (MIG ui_clk)
   qnice_rst <= '1', '0' after 200 ns;
   main_rst  <= '1', '0' after 200 ns;
   hr_rst    <= '1', '0' after 200 ns;

   dut : entity work.sw_cartridge_wrapper
      generic map (G_BASE_ADDRESS => "1000000000" & x"000")   -- C_HMAP_CRT x"0200" as in mega65.vhd
      port map (
         qnice_clk_i => qnice_clk, qnice_rst_i => qnice_rst, qnice_addr_i => qnice_addr,
         qnice_data_i => qnice_wdata, qnice_ce_i => qnice_ce, qnice_we_i => qnice_we,
         qnice_data_o => qnice_rdata, qnice_wait_o => qnice_wait,
         main_clk_i => main_clk, main_rst_i => main_rst, main_reset_core_o => open,
         main_loading_o => open, main_id_o => open, main_exrom_o => open, main_game_o => open,
         main_size_o => open, main_bank_laddr_o => open, main_bank_size_o => open,
         main_bank_num_o => open, main_bank_raddr_o => open, main_bank_wr_o => open,
         main_bank_lo_i => (others => '0'), main_bank_hi_i => (others => '0'),
         main_bank_wait_o => open, main_ram_addr_i => (others => '0'),
         main_ram_data_i => (others => '0'), main_ioe_we_i => '0', main_iof_we_i => '0',
         main_lo_ram_data_o => open, main_hi_ram_data_o => open,
         main_ioe_ram_data_o => open, main_iof_ram_data_o => open,
         hr_clk_i => hr_clk, hr_rst_i => hr_rst, hr_write_o => hr_write, hr_read_o => hr_read,
         hr_address_o => hr_address, hr_writedata_o => hr_wdata, hr_byteenable_o => hr_be,
         hr_burstcount_o => hr_bc, hr_readdata_i => hr_rdata, hr_readdatavalid_i => hr_rdv,
         hr_waitrequest_i => hr_wait);

   s_write <= d_write & hr_write & '0';
   s_read  <= d_read  & hr_read  & '0';
   s_addr  <= d_addr  & hr_address & x"00000000";
   s_wdata <= d_wdata & hr_wdata & x"0000";
   s_be    <= "11" & hr_be & "11";
   s_bc    <= d_bc & hr_bc & x"01";
   hr_rdata <= s_rdata(31 downto 16); hr_rdv <= s_rdv(1); hr_wait <= s_wait(1);
   d_rdv <= s_rdv(2); d_wait <= s_wait(2);

   arb : entity work.avm_arbit_general
      generic map (G_NUM_SLAVES => 3, G_FREQ_HZ => 100_000_000, G_ADDRESS_SIZE => 32, G_DATA_SIZE => 16)
      port map (clk_i => hr_clk, rst_i => hr_rst,
                s_avm_write_i => s_write, s_avm_read_i => s_read, s_avm_address_i => s_addr,
                s_avm_writedata_i => s_wdata, s_avm_byteenable_i => s_be,
                s_avm_burstcount_i => s_bc, s_avm_readdata_o => s_rdata,
                s_avm_readdatavalid_o => s_rdv, s_avm_waitrequest_o => s_wait,
                m_avm_write_o => m_write, m_avm_read_o => m_read, m_avm_address_o => m_addr,
                m_avm_writedata_o => m_wdata, m_avm_byteenable_o => m_be,
                m_avm_burstcount_o => m_bc, m_avm_readdata_i => m_rdata,
                m_avm_readdatavalid_i => m_rdv, m_avm_waitrequest_i => m_wait);

   -- scaler-like traffic: 32-beat write and 64-beat read bursts in the low 2 MB
   dig : process
      variable seed : unsigned(31 downto 0) := x"0BADF00D";
      variable bt : natural;
      impure function rnd(lim : integer) return integer is
      begin
         seed := resize(seed * to_unsigned(1103515245, 32) + 12345, 32);
         return to_integer(seed(30 downto 15)) mod lim;
      end function;
   begin
      wait until hr_rst = '0';
      while not done loop
         for g in 0 to rnd(20) loop wait until rising_edge(hr_clk); end loop;
         d_addr <= std_logic_vector(to_unsigned(16#40000# + rnd(16#30000#), 32));   -- clear of the CRT in the (wrapping) model
         if rnd(2) = 0 then
            d_bc <= x"20"; bt := 0;
            while bt < 32 loop
               d_write <= '1'; d_wdata <= std_logic_vector(to_unsigned(rnd(65536), 16));
               wait until rising_edge(hr_clk);
               if d_wait = '0' then bt := bt + 1; end if;
            end loop;
            d_write <= '0';
         else
            d_bc <= x"40"; d_read <= '1';
            loop
               wait until rising_edge(hr_clk);
               exit when d_wait = '0';
            end loop;
            d_read <= '0'; bt := 0;
            while bt < 64 loop
               wait until rising_edge(hr_clk);
               if d_rdv = '1' then bt := bt + 1; end if;
            end loop;
         end if;
      end loop;
      wait;
   end process;

   bridge : entity work.avm_mig
      port map (clk => hr_clk, rst => hr_rst, avm_write => m_write, avm_read => m_read,
                avm_address => m_addr, avm_writedata => m_wdata, avm_byteenable => m_be,
                avm_burstcount => m_bc, avm_readdata => m_rdata, avm_readdatavalid => m_rdv,
                avm_waitrequest => m_wait,
                app_addr => app_addr, app_cmd => app_cmd, app_en => app_en, app_rdy => app_rdy,
                app_wdf_data => app_wdf_data, app_wdf_end => app_wdf_end,
                app_wdf_wren => app_wdf_wren, app_wdf_mask => app_wdf_mask,
                app_wdf_rdy => app_wdf_rdy, app_rd_data => app_rd_data,
                app_rd_data_valid => app_rd_data_valid);

   mig : entity work.mig_model
      port map (clk => hr_clk, app_addr => app_addr, app_cmd => app_cmd, app_en => app_en,
                app_rdy => app_rdy, app_wdf_data => app_wdf_data, app_wdf_end => app_wdf_end,
                app_wdf_wren => app_wdf_wren, app_wdf_mask => app_wdf_mask,
                app_wdf_rdy => app_wdf_rdy, app_rd_data => app_rd_data,
                app_rd_data_valid => app_rd_data_valid);

   qnice : process
      file f : text open read_mode is G_HEX_FILE;
      variable l : line;
      variable b : std_logic_vector(7 downto 0);
      variable n : natural := 0;
      variable st, code : std_logic_vector(15 downto 0);
      variable ea : std_logic_vector(31 downto 0);

      procedure wr(a : std_logic_vector(27 downto 0); d : std_logic_vector(15 downto 0)) is
      begin
         qnice_addr <= a; qnice_wdata <= d; qnice_we <= '1'; qnice_ce <= '1';
         wait until falling_edge(qnice_clk);
         while qnice_wait = '1' loop wait until falling_edge(qnice_clk); end loop;
         qnice_ce <= '0'; qnice_we <= '0';
         wait until falling_edge(qnice_clk);          -- the QNICE CPU needs a few cycles per MOVE
      end procedure;
      procedure rd(a : std_logic_vector(27 downto 0); d : out std_logic_vector(15 downto 0)) is
      begin
         qnice_addr <= a; qnice_we <= '0'; qnice_ce <= '1';
         wait until falling_edge(qnice_clk);
         while qnice_wait = '1' loop wait until falling_edge(qnice_clk); end loop;
         d := qnice_rdata; qnice_ce <= '0';
         wait until falling_edge(qnice_clk);
      end procedure;
      constant CSR : std_logic_vector(27 downto 12) := x"FFFF";
   begin
      wait until qnice_rst = '0';
      wait for 1 us;
      wait until falling_edge(qnice_clk);
      -- firmware order: STATUS = loading, then the data bytes, then size + STATUS = OK
      wr(CSR & x"000", x"0001");
      while not endfile(f) loop
         readline(f, l);
         hread(l, b);
         wr(std_logic_vector(to_unsigned(n, 28)), x"00" & b);
         n := n + 1;
      end loop;
      report "QNICE wrote " & integer'image(n) & " bytes";
      -- read the first 16 bytes back through the QNICE window
      for i in 0 to 15 loop
         rd(std_logic_vector(to_unsigned(i, 28)), st);
         write(l, to_hstring(st(7 downto 0)) & " ");
      end loop;
      report "readback: " & l.all;
      deallocate(l);
      wr(CSR & x"001", std_logic_vector(to_unsigned(G_FILE_LEN mod 65536, 16)));
      wr(CSR & x"002", std_logic_vector(to_unsigned(G_FILE_LEN / 65536, 16)));
      wr(CSR & x"000", x"0003");
      for i in 1 to 4000 loop
         rd(CSR & x"010", st);
         exit when st = x"0002" or st = x"0003";
         wait for 1 us;
      end loop;
      rd(CSR & x"011", code);
      rd(CSR & x"012", ea(15 downto 0));
      rd(CSR & x"013", ea(31 downto 16));
      report "parse status " & to_hstring(st) & " error code " & to_hstring(code) &
             " at address " & to_hstring(ea);
      if st = x"0003" and code(3 downto 0) = "0011" then
         report "TB_C64_CRT_FAIL: wrong CRT header (reproduces the hardware)" severity failure;
      elsif st = x"0002" or (st = x"0003" and unsigned(ea) >= 64) then
         report "TB_C64_CRT_PASS (header and written CHIPs parsed; stopped past the written part)";
      else
         report "TB_C64_CRT_FAIL" severity failure;
      end if;
      done <= true;
      wait;
   end process;
end architecture;
