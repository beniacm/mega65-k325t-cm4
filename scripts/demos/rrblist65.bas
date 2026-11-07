10 rem rrb list mode demo (k325t xctl bit 5): bouncing balls
20 n=6:s=$40000:c=$ff84000:l=512:g=$1200:ga=$48000:oa=$49000:oc=$7800
25 dim p2(8):p2(0)=1:for j=1 to 8:p2(j)=p2(j-1)*2:next j
30 poke $ffd71f0,$30:rem list mode + fast rrb
40 border 0:background 0
50 rem full-colour ball glyphs g+i (64 bytes each at ga) in 6 colours
60 for i=0 to n-1:k=2+i:if k>6 then k=k+1
70 for y=0 to 7:read b:for x=0 to 7:p=0
80 if b and p2(7-x) then p=k:if y<2 then p=1
90 poke ga+64*i+8*y+x,p:next x,y:restore
100 rem object tokens: glyph, end ($ffff); colour bytes 0
110 wpoke oa+16*i,g+i:wpoke oa+16*i+2,65535:wpoke c-$4000+oc+16*i,0
120 next i
130 data 60,126,255,255,255,255,126,60
140 rem rows: 80 stars/spaces, list token, 2n slots, end
150 for r=0 to 24:a=s+r*l:ca=c+r*l
160 for x=0 to 79:t=32:if rnd(1)<.06 then t=46
170 wpoke a+2*x,t:poke ca+2*x,0:poke ca+2*x+1,1:next x
180 wpoke a+160,65532
190 for k=0 to 2*n-1:e=a+162+8*k:i=int(k/2)
200 wpoke e,0:wpoke e+2,17408:wpoke e+4,$9000+16*i:wpoke e+6,oc+16*i:next k
210 wpoke a+162+16*n,65535:next r
220 rem vic-iv: hotreg off, chr16 + full colour >$ff, linestep, chrcount, ptrs
230 poke $d05d,peek($d05d) and 127:poke $d054,peek($d054) or 5
240 wpoke $d058,l:poke $d05e,81:poke $d063,peek($d063) and 192
250 poke $d060,0:poke $d061,0:poke $d062,4:wpoke $d064,$4000
260 dim x(n),y(n),dx(n),dy(n),q(n)
270 for i=0 to n-1:x(i)=20+rnd(1)*590:y(i)=8+rnd(1)*180
280 dx(i)=1+int(rnd(1)*3):dy(i)=1+int(rnd(1)*2):q(i)=-1:next i
290 rem main loop: move, write the two list entries of each ball
300 for i=0 to n-1
310 x=x(i)+dx(i):if x<0 or x>630 then dx(i)=-dx(i):x=x(i)+dx(i)
320 y=y(i)+dy(i):if y<0 or y>191 then dy(i)=-dy(i):y=y(i)+dy(i)
330 x(i)=x:y(i)=y:r=int(y/8):o=y-8*r
340 if r<>q(i) and q(i)>=0 then e=s+q(i)*l+162+16*i:wpoke e+2,17408:if q(i)<24 then wpoke e+l+10,17408
350 e=s+r*l+162+16*i:wpoke e,x+4096+8192*o:wpoke e+2,17408+256-p2(o)
360 if r<24 and o>0 then wpoke e+l+8,x+8192*(8-o):wpoke e+l+10,17408+p2(o)-1
365 if r<24 and o=0 then wpoke e+l+10,17408
370 q(i)=r:next i
380 goto 300
