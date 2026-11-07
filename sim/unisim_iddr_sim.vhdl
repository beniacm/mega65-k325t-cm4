-- unisim_iddr_sim.vhdl -- minimal simulation model of the Xilinx IDDR (SAME_EDGE_PIPELINED)
-- for GHDL testbenches (library unisim, package vcomponents).  Q1 = the rising-edge sample,
-- Q2 = the falling-edge sample after it; both presented on the next rising edge.
library ieee;
use ieee.std_logic_1164.all;
package vcomponents is
  component IDDR
    generic (DDR_CLK_EDGE : string := "SAME_EDGE_PIPELINED"; INIT_Q1 : std_logic := '0';
             INIT_Q2 : std_logic := '0'; SRTYPE : string := "SYNC");
    port (Q1, Q2 : out std_logic; C, CE, D, R, S : in std_logic);
  end component;
end package;

library ieee;
use ieee.std_logic_1164.all;
entity IDDR is
  generic (DDR_CLK_EDGE : string := "SAME_EDGE_PIPELINED"; INIT_Q1 : std_logic := '0';
           INIT_Q2 : std_logic := '0'; SRTYPE : string := "SYNC");
  port (Q1, Q2 : out std_logic; C, CE, D, R, S : in std_logic);
end entity;
architecture sim of IDDR is
  signal sr, sf : std_logic := '0';
begin
  process (C) begin
    if rising_edge(C) then Q1 <= sr; Q2 <= sf; sr <= D; end if;
    if falling_edge(C) then sf <= D; end if;
  end process;
end architecture;
