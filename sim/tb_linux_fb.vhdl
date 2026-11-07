-- tb_linux_fb.vhdl -- linux_fb with a small picture (16x8): Rockchip frame written via a
-- stand-in for bt1120_video (de/rgb taken from bt_d), read back in the core's timing;
-- checks pixel placement, cropping of a larger input, RGB332 expansion, the select switch and
-- the pass-through.
library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity bt1120_video is
  port (pclk : in std_logic; rst_n : in std_logic; bt_d : in std_logic_vector(15 downto 0);
        rgb : out std_logic_vector(23 downto 0); de : out std_logic; hsync : out std_logic;
        vsync : out std_logic; locked : out std_logic; yc_swapped : out std_logic);
end entity;
architecture stub of bt1120_video is
begin
  process (pclk) begin
    if rising_edge(pclk) then          -- one cycle latency like a real pipeline stage
      de <= bt_d(15);
      rgb <= bt_d(7 downto 0) & bt_d(7 downto 0) & bt_d(7 downto 0);
    end if;
  end process;
  hsync <= '1'; vsync <= '1'; locked <= '1'; yc_swapped <= '0';
end architecture;

library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity tb_linux_fb is end entity;
architecture sim of tb_linux_fb is
  constant W : integer := 16; constant H : integer := 8;
  signal btc, c27 : std_logic := '0'; signal done : boolean := false;
  signal bt_d : std_logic_vector(15 downto 0) := (others => '0');
  signal sel : std_logic := '0';
  signal ide, ihs, ivs : std_logic := '0'; signal ir, ig, ib : unsigned(7 downto 0) := x"00";
  signal ode, ohs, ovs : std_logic; signal o_r, o_g, o_b : unsigned(7 downto 0);
  signal frame : integer := 0;
  function pix(x, y : integer) return integer is begin return (x * 7 + y * 31 + 5) mod 256; end;
begin
  btc <= not btc after 18 ns when not done;     -- ~27 MHz, unrelated phase
  c27 <= not c27 after 18.5 ns when not done;
  dut : entity work.linux_fb generic map (WIDTH => W, HEIGHT => H)
    port map (bt_clk => btc, bt_d => bt_d, clk27 => c27, show_linux => sel,
              in_de => ide, in_hs => ihs, in_vs => ivs, in_r => ir, in_g => ig, in_b => ib,
              out_de => ode, out_hs => ohs, out_vs => ovs, out_r => o_r, out_g => o_g, out_b => o_b,
              linux_locked => open);
  -- Rockchip side: two frames (vblank 2500 cycles, H lines of W pixels + 50 blank)
  process begin
    for f in 0 to 1 loop
      bt_d <= x"0000"; for i in 1 to 4000 loop wait until falling_edge(btc); end loop;
      for y in 0 to H + 2 loop                  -- larger than the buffer: must be cropped
        for x in 0 to W + 4 loop
          wait until falling_edge(btc); bt_d <= x"80" & std_logic_vector(to_unsigned(pix(x, y), 8));
        end loop;
        for i in 1 to 50 loop wait until falling_edge(btc); bt_d <= x"0000"; end loop;
      end loop;
    end loop;
    wait;
  end process;
  -- core side: frame after the writes, once with the core picture, once with Linux
  process
    variable e, got, exp : integer; variable errs : integer := 0;
  begin
    for pass in 0 to 1 loop
      wait for 400 us;
      if pass = 1 then sel <= '1'; else sel <= '0'; end if;
      for i in 1 to 4000 loop wait until rising_edge(c27); ide <= '0'; end loop;
      for y in 0 to H - 1 loop
        for x in 0 to W - 1 loop
          wait until rising_edge(c27); ide <= '1'; ir <= to_unsigned(200, 8); ig <= x"11"; ib <= x"22";
          wait for 1 ns;
          if ode = '1' and x > 0 then null; end if;
        end loop;
        for i in 1 to 40 loop wait until rising_edge(c27); ide <= '0'; end loop;
      end loop;
    end loop;
    wait for 1 us; done <= true; wait;
  end process;
  -- checker on the output side: count output pixels of the Linux frame and compare
  process
    variable n, errs : integer := 0; variable x, y, p : integer;
    variable er, eg, eb : unsigned(7 downto 0);
  begin
    wait until sel = '1';
    loop
      wait until rising_edge(c27);
      exit when done or n = W * H;
      if ode = '1' then
        x := n mod W; y := n / W; p := pix(x, y);
        er := to_unsigned(p, 8)(7 downto 5) & to_unsigned(p, 8)(7 downto 5) & to_unsigned(p, 8)(7 downto 6);
        eg := to_unsigned(p, 8)(7 downto 5) & to_unsigned(p, 8)(7 downto 5) & to_unsigned(p, 8)(7 downto 6);
        eb := to_unsigned(p, 8)(7 downto 6) & to_unsigned(p, 8)(7 downto 6) & to_unsigned(p, 8)(7 downto 6) & to_unsigned(p, 8)(7 downto 6);
        if o_r /= er or o_g /= eg or o_b /= eb then
          if errs < 5 then report "pixel " & integer'image(x) & "," & integer'image(y) & " got " & integer'image(to_integer(o_r)) & "/" & integer'image(to_integer(o_g)) & "/" & integer'image(to_integer(o_b)) & " exp " & integer'image(to_integer(er)) & "/" & integer'image(to_integer(eg)) & "/" & integer'image(to_integer(eb)) & " p=" & integer'image(p) severity error; end if;
          errs := errs + 1;
        end if;
        n := n + 1;
      end if;
    end loop;
    assert n = W * H report "output pixel count " & integer'image(n) severity failure;
    assert errs = 0 report integer'image(errs) & " pixel errors" severity failure;
    report "TB_LINUX_FB_PASS (" & integer'image(n) & " pixels)";
    wait;
  end process;
  -- pass-through check in the first (core) frame
  process begin
    wait until rising_edge(c27) and ode = '1' and sel = '0';
    assert o_r = 200 and o_g = x"11" and o_b = x"22" report "core picture not passed through" severity failure;
    report "core picture passes through";
    wait;
  end process;
end architecture;
