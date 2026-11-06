-- axi_fifo_sim.vhdl -- behavioural stand-in for M2M's axi_fifo (a wrapper of Xilinx
-- xpm_fifo_axis, which GHDL cannot simulate): independent-clock FIFO, first-word
-- fall-through, pointers seen by the other side two of its clocks late (like the CDC
-- synchronisers of the real FIFO).  Fill-level outputs are not modelled (zero).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity axi_fifo is
   generic (
      G_DEPTH     : natural;
      G_FILL_SIZE : natural;
      G_DATA_SIZE : natural;
      G_USER_SIZE : natural
   );
   port (
      s_aclk_i        : in  std_logic;
      s_aresetn_i     : in  std_logic;
      s_axis_tready_o : out std_logic;
      s_axis_tvalid_i : in  std_logic;
      s_axis_tdata_i  : in  std_logic_vector(G_DATA_SIZE-1 downto 0);
      s_axis_tkeep_i  : in  std_logic_vector(G_DATA_SIZE/8-1 downto 0);
      s_axis_tlast_i  : in  std_logic;
      s_axis_tuser_i  : in  std_logic_vector(G_USER_SIZE-1 downto 0);
      s_fill_o        : out std_logic_vector(G_FILL_SIZE-1 downto 0);
      m_aclk_i        : in  std_logic;
      m_axis_tready_i : in  std_logic;
      m_axis_tvalid_o : out std_logic;
      m_axis_tdata_o  : out std_logic_vector(G_DATA_SIZE-1 downto 0);
      m_axis_tkeep_o  : out std_logic_vector(G_DATA_SIZE/8-1 downto 0);
      m_axis_tlast_o  : out std_logic;
      m_axis_tuser_o  : out std_logic_vector(G_USER_SIZE-1 downto 0);
      m_fill_o        : out std_logic_vector(G_FILL_SIZE-1 downto 0)
   );
end entity axi_fifo;

architecture sim of axi_fifo is
   constant W : natural := G_DATA_SIZE + G_USER_SIZE;
   type mem_t is array (0 to G_DEPTH - 1) of std_logic_vector(W - 1 downto 0);
   signal mem : mem_t;
   signal wp, rp : natural := 0;                 -- free-running counters
   signal wp_m1, wp_m2, rp_s1, rp_s2 : natural := 0;
   signal ready : std_logic;
   signal valid : std_logic;
begin
   s_fill_o <= (others => '0');
   m_fill_o <= (others => '0');
   m_axis_tkeep_o <= (others => '1');
   m_axis_tlast_o <= '1';

   ready <= '1' when s_aresetn_i = '1' and wp - rp_s2 < G_DEPTH else '0';
   s_axis_tready_o <= ready;

   process (s_aclk_i)
   begin
      if rising_edge(s_aclk_i) then
         rp_s1 <= rp; rp_s2 <= rp_s1;
         if s_aresetn_i = '0' then
            null;
         elsif s_axis_tvalid_i = '1' and ready = '1' then
            mem(wp mod G_DEPTH) <= s_axis_tuser_i & s_axis_tdata_i;
            wp <= wp + 1;
         end if;
      end if;
   end process;

   valid <= '1' when rp /= wp_m2 else '0';
   m_axis_tvalid_o <= valid;
   m_axis_tdata_o  <= mem(rp mod G_DEPTH)(G_DATA_SIZE - 1 downto 0);
   m_axis_tuser_o  <= mem(rp mod G_DEPTH)(W - 1 downto G_DATA_SIZE);

   process (m_aclk_i)
   begin
      if rising_edge(m_aclk_i) then
         wp_m1 <= wp; wp_m2 <= wp_m1;
         if valid = '1' and m_axis_tready_i = '1' then
            rp <= rp + 1;
         end if;
      end if;
   end process;
end architecture sim;
