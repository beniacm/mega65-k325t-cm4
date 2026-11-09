10 rem rrb list mode v2 demo (k325t xctl bit 6): bouncing balls, one x/y per ball
20 n=6:s=$40000:c=$ff84000:l=160:g=$1200:ga=$48000:oa=$49000:oc=$7800:t=$4c000
25 dim p2(8):p2(0)=1:for j=1 to 8:p2(j)=p2(j-1)*2:next j
40 border 0:background 0:poke $d05d,peek($d05d) or 128:poke $d031,peek($d031) or 8:rem v400 (640x400 square pixels, 50 rows), written with hotreg on
50 rem full-colour ball glyphs g+i (64 bytes each at ga) in 6 colours
60 for i=0 to n-1:k=2+i:if k>6 then k=k+1
70 for y=0 to 7:read b:for x=0 to 7:p=0
80 if b and p2(7-x) then p=k:if y<2 then p=1
90 poke ga+64*i+8*y+x,p:next x,y:restore
100 rem object tokens: glyph, end ($ffff); colour bytes 0
110 wpoke oa+16*i,g+i:wpoke oa+16*i+2,65535:wpoke c-$4000+oc+16*i,0
120 rem object table entry (32 bytes, hardware reads 0..15): x, y, mask, height, address 19..16, -, address, colour, stride
130 e=t+32*i:wpoke e,0:wpoke e+2,0:poke e+4,255:poke e+5,1:poke e+6,4:poke e+7,0
140 wpoke e+8,$9000+16*i:wpoke e+10,oc+16*i:wpoke e+12,0:wpoke e+14,0
150 next i
160 data 60,126,255,255,255,255,126,60
170 rem rows (50): 80 stars/spaces -- no list token or entries needed in v2
180 for r=0 to 49:a=s+r*l:ca=c+r*l
190 for x=0 to 79:tk=32:if rnd(1)<.06 then tk=46
200 wpoke a+2*x,tk:poke ca+2*x,0:poke ca+2*x+1,1:next x,r
220 rem vic-iv: hotreg off, chr16 + full colour >$ff, linestep, chrcount, ptrs
230 poke $d05d,peek($d05d) and 127:poke $d054,peek($d054) or 5
240 wpoke $d058,l:poke $d05e,80:poke $d063,peek($d063) and 192
250 poke $d060,0:poke $d061,0:poke $d062,4:wpoke $d064,$4000
255 rem object table at t, n objects; list mode v2 + fast rrb
260 poke $ffd71f8,0:poke $ffd71f9,$c0:poke $ffd71fa,4:poke $ffd71fb,n:poke $ffd71f0,$d0
270 dim x(n),y(n),dx(n),dy(n)
280 for i=0 to n-1:x(i)=20+rnd(1)*590:y(i)=8+rnd(1)*380
290 dx(i)=1+int(rnd(1)*3):dy(i)=1+int(rnd(1)*2):next i
300 rem main loop: move, write x and y of each ball (the vic-iv does the rest)
310 for i=0 to n-1
320 x=x(i)+dx(i):if x<0 or x>630 then dx(i)=-dx(i):x=x(i)+dx(i)
330 y=y(i)+dy(i):if y<0 or y>391 then dy(i)=-dy(i):y=y(i)+dy(i)
340 x(i)=x:y(i)=y:e=t+32*i:wpoke e,x:wpoke e+2,y
350 next i
360 goto 310
