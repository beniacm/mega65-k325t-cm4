`timescale 1ns/1ps
// tb_bt656_video.v -- bt656_video against a synthetic BT.656 stream: W x H active, a grey ramp
// (Cb = Cr = 128, so R = G = B = 1.164 (Y - 16)), then a colour line; checks pixel count, order, values.
// Run (from the repo root; -Ptb.YFIRST=1 / -Ptb.IL=1 for luma-first / interlaced streams):
//   iverilog -g2012 -o /tmp/tb_bt656.vvp sim/bt656/tb_bt656_video.v mega65-core/src/verilog/bt656_video.v
//   vvp /tmp/tb_bt656.vvp          -> TB_BT656_PASS
module tb;
    parameter W = 16, H = 4, HB = 12, VB = 3, YFIRST = 0, IL = 0;
    reg clk = 0; always #9 clk = ~clk;
    reg [7:0] d = 8'h80;
    wire [23:0] rgb; wire de, pv, field, il, locked, y_first;
    bt656_video dut (.pclk(clk), .rst_n(1'b1), .bt_d(d), .uv_swap(1'b0), .rgb(rgb), .de(de), .pv(pv),
                     .field(field), .il(il), .locked(locked), .y_first(y_first));
    task put(input [7:0] b); begin @(negedge clk) d = b; end endtask
    task trs(input f, input v, input h); begin put(8'hFF); put(8'h00); put(8'h00); put({1'b1, f, v, h, 4'h0}); end endtask
    integer fr, ln, x, i, npx, errs, lines, ey;
    integer cnt [0:255];
    function [7:0] grey(input integer y); integer t; begin t = (298 * (y - 16) + 128) >>> 8; grey = t < 0 ? 0 : t > 255 ? 255 : t; end endfunction
    // expected Y sequence of the ramp line: 40 + 5 x
    integer ex; reg colour;
    initial begin
        errs = 0; npx = 0; lines = 0; ex = 0; colour = 0;
        for (fr = 0; fr < 4; fr = fr + 1) begin
            for (ln = 0; ln < VB + H; ln = ln + 1) begin
                // EAV, blanking fill, SAV, active (or fill in the vertical blank)
                trs(IL ? fr[0] : 1'b0, ln < VB, 1'b1);
                for (i = 0; i < HB; i = i + 1) begin
                    if (YFIRST) begin put(8'h10); put(8'h80); end else begin put(8'h80); put(8'h10); end
                end
                trs(IL ? fr[0] : 1'b0, ln < VB, 1'b0);
                for (x = 0; x < W; x = x + 2) begin
                    if (ln < VB) begin
                        if (YFIRST) begin put(8'h10); put(8'h80); put(8'h10); put(8'h80); end
                        else begin put(8'h80); put(8'h10); put(8'h80); put(8'h10); end
                    end else if (ln == VB + H - 1) begin          // colour line: Y 81 Cb 90 Cr 240 = red
                        if (YFIRST) begin put(8'd81); put(8'd90); put(8'd81); put(8'd240); end
                        else begin put(8'd90); put(8'd81); put(8'd240); put(8'd81); end
                    end else begin
                        if (YFIRST) begin put(40 + 5 * x); put(8'h80); put(40 + 5 * (x + 1)); put(8'h80); end
                        else begin put(8'h80); put(40 + 5 * x); put(8'h80); put(40 + 5 * (x + 1)); end
                    end
                end
            end
        end
        trs(1'b0, 1'b1, 1'b1);
        repeat (40) begin if (YFIRST) begin put(8'h10); put(8'h80); end else begin put(8'h80); put(8'h10); end end
        $display("frames 4: pixels %0d (want %0d), lines %0d (want %0d), errors %0d, locked %0d, y_first %0d, il %0d",
                 npx, 4 * W * H, lines, 4 * H, errs, locked, y_first, il);
        if (errs == 0 && npx == 4 * W * H && lines == 4 * H && locked && y_first == YFIRST && il == IL) $display("TB_BT656_PASS");
        else $display("TB_BT656_FAIL");
        $finish;
    end
    // checker: pixels of a line arrive in order; line ends when de falls
    integer lx = 0, lno = 0; reg de_q = 0;
    always @(posedge clk) begin
        de_q <= de;
        if (pv) begin
            npx = npx + 1;
            if ((lno % H) == H - 1) begin
                if (rgb[23:16] < 8'd240 || rgb[15:8] > 8'd20 || rgb[7:0] > 8'd20) begin errs = errs + 1; $display("colour line: rgb %h", rgb); end
            end else begin
                ey = grey(40 + 5 * lx);
                if (rgb !== {ey[7:0], ey[7:0], ey[7:0]}) begin errs = errs + 1; if (errs < 10) $display("line %0d x %0d: rgb %h want %h", lno, lx, rgb, ey[7:0]); end
            end
            lx = lx + 1;
        end
        if (de_q && !de) begin
            if (lx != W) begin errs = errs + 1; $display("line %0d: %0d pixels", lno, lx); end
            lx = 0; lno = lno + 1; lines = lines + 1;
        end
    end
endmodule
