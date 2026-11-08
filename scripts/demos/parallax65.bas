5 rem per frame: 19 statements (46 with the k325t birds), 3.9 ms in xemu without the vsync wait: 50 fps. rrb: 3 gotox + 123 fcm glyphs a line, ~1970 of 2592 cycles (16 per fcm glyph); dblrr on the v400 schedule doubles that
10 rem parallax demo for basic 65: three overlapping rrb layers (vic-iv)
20 rem twin of examples/parallax.bas (b65 basic): same picture, same motion.
30 rem 40 columns (h320), 25 rows (v200), every glyph full colour (fcm) from an
40 rem own 256-colour palette (0-15 kept). layer 3: sky gradient and snowy
50 rem mountains (opaque, painted first); gotox + layer 2: rolling hills
60 rem (transparent): the mountains show in the dips; gotox + layer 1: trees,
70 rem fence and ground (transparent): the hills show between the trunks and
80 rem under the crowns. layers 3 and 2 are "behind", so the 16-colour balloon
90 rem sprite (priority bit) passes behind the trees but in front of the hills;
95 rem the 16-colour plane flies in front of all. speeds 1:2:6. fine scroll =
100 rem gotox x, coarse scroll = one self-modifying dma list per frame (41-token
105 rem windows of 128-tile maps). k325t ($d629=$62): six shaded, flapping
107 rem list-mode v2 birds in front of all. dblrr on the v400 schedule: line 620.
110 rem bn>0: stop after bn frames (tests); rs=1: back to text, print timing
120 bn=0:rs=1:db=1:sk=1
130 sc=$50000:ls=252:dl=$53100:ce=$53700:tl=$53800:mp=$54800:tg=$5a000
140 co=$0800:cb=$ff80000+co:k=(peek($d629)=98)
150 trap 2900
155 v0=peek($d054):v1=peek($d058):v2=peek($d059):v3=peek($d05c):v4=peek($d05d):v5=peek($d05e)
156 v6=peek($d060):v7=peek($d061):v8=peek($d062):v9=peek($d063):va=peek($d064):vb=peek($d065)
157 vc=peek($d031):vd=peek($d016):ve=peek($d021):d5=peek($d05b):d7=peek($d07b):gosub 2840
160 print "building the scene...":gosub 2700
170 cm=3:s=0:nb=25*ls:d=sc:gosub 2600:d=cb:gosub 2600:nb=160*64:d=tg:gosub 2600
180 sk=2:nb=36*256:s=$80:d=mp:gosub 2600:s=$16:d=mp+1:gosub 2600:sk=1
190 rem full-colour tiles $1680+n at tg+64*n: 0 blank; 1+6*row+kind: layer 3
200 rem (rows 0-17; kind 0 sky, 1 lit, 2 shade, 3 rise, 4 fall, 5 top);
210 rem 109+5*band+kind-1: hills; 124-132 crown, 133 trunk, 134 post,
220 rem 135 rail, 136 grass, 137 earth; birds $17c0-$17c3 at $5f000
230 gosub 1100
235 rem all periods divide 128 (mountains and hills 32, trees 16, posts 4): seamless wrap
240 rem maps (36 rows of 512 bytes): layer 3 rows 5-17 (map 0-12), layer 2
250 rem rows 11-21 (13-23), layer 1 rows 13-24 (24-35); 128 columns, then the
260 rem first 41 again
270 for c=0 to 127
280 u=mod(c,32):t=5+int(abs(u-16)/2):l=5+int(abs(mod(c+127,32)-16)/2):r=5+int(abs(mod(c+1,32)-16)/2)
290 g=5:if l>t then g=3:else if r>t then g=4
300 s=1:if u>=16 then s=2
310 for y=5 to 17:v=0:if y=t then v=g:else if y>t then v=s
320 my=y-5:n=1+6*y+v:gosub 1000:next y
330 u=mod(c+8,32):t=11+int(abs(u-16)/4):l=11+int(abs(mod(c+7,32)-16)/4):r=11+int(abs(mod(c+9,32)-16)/4)
340 g=5:if l>t then g=3:else if r>t then g=4
350 s=1:if u>=16 then s=2
360 for y=t to 21:v=s:if y=t then v=g
370 my=y+2:n=109+5*int((y-11)/4)+v-1:gosub 1000:next y
380 if mod(c,4)=0 then n=134:for my=30 to 32:gosub 1000:next my:else n=135:for my=30 to 31:gosub 1000:next my
390 u=mod(c+1,16):if u<3 then for y=0 to 2:my=24+y:n=124+3*y+u:gosub 1000:next y
400 if u=1 then n=133:for my=27 to 32:gosub 1000:next my
410 my=33:n=136:gosub 1000:n=137:for my=34 to 35:gosub 1000:next my
420 next c
430 cm=0:nb=82:for m=0 to 35:s=mp+512*m:d=s+256:gosub 2600:next m
440 rem rows: layer 3 sky above the mountains and rock below, layers 2/1 blank;
450 rem gotox colour flags: behind / transparent+behind / transparent
460 cm=3:for r=0 to 24:a=sc+r*ls:n=1+6*r:if r>17 then n=1+6*17+2
470 sk=2:nb=41:s=(128+n) and 255:d=a+2:gosub 2600:s=22+int((128+n)/256):d=a+3:gosub 2600
480 s=$80:d=a+86:gosub 2600:s=$16:d=a+87:gosub 2600:s=$80:d=a+170:gosub 2600:s=$16:d=a+171:gosub 2600:sk=1
490 poke cb+r*ls,$50:poke cb+r*ls+84,$d0:poke cb+r*ls+168,$90:next r
500 rem dma list per content row: fix the copy's source low byte from the
510 rem coarse cell, copy the fine x into the gotox token, copy the window
520 j=dl:for m=0 to 35
530 if m<13 then r=m+5:o=0:n=0:else if m<24 then r=m-2:o=84:n=2:else r=m-11:o=168:n=4
540 cm=4:nb=1:s=ce+n+1:d=j+28+5:gosub 1020:j=j+14:s=ce+n:d=sc+r*ls+o:gosub 1020:j=j+14
550 cm=4:if m=35 then cm=0
560 nb=82:s=mp+512*m:d=sc+r*ls+o+2:gosub 1020:j=j+14:next m
570 gosub 1400:if k then gosub 1700
580 rem vic-iv: h320 (hotreg recomputes the borders), chr16 + full colour,
590 rem 252-byte rows of 126 tokens, screen and colour pointers, sky behind. dblrr
595 rem needs the v400 row schedule (v400, one line per glyph row, 25 rows):
597 rem each row is painted once, shown twice
600 poke $d05d,peek($d05d) or 128:poke $d031,vc and 127:poke $d016,$c8:poke $d011,$1b:poke $d05d,peek($d05d) and 127
605 poke $d054,peek($d054) or 7:wpoke $d058,ls:poke $d05e,126:poke $d063,peek($d063) and 192
610 poke $d060,0,0,5:wpoke $d064,co
620 poke $d020,0:poke $d021,16:if db then poke $d031,peek($d031) or 8:poke $d05b,0:poke $d07b,24:poke $d051,$c0
625 poke $d05c,peek($d05c)+16:rem wider side border: hides the fine-scroll gap at the left edge
630 if k then poke $ffd71f0,peek($ffd71f0) or $50
640 gosub 1600
760 rem main loop: positions, one dma list, sprites, birds, wait for vsync
765 rem v2 x/y must be 0..1023 (10-bit; y signed): negative values are masked with and 1023
770 p=0:t=ti
790 p=p+1:x=p*3:poke ce+4,7-mod(x,8),mod(int(x/8),128)*2
800 x=p:poke ce+2,7-mod(x,8),mod(int(x/8),128)*2
810 x=int(p/2):poke ce,7-mod(x,8),mod(int(x/8),128)*2
820 poke $d701,$31,5,0,0,0:rem list at $53100: the trigger ($d705) is written last
830 x=mod(int(p/3),400):poke $d00c,x and 255:poke $d010,int(x/256)*64:poke $d00d,150+mod(int(p/10),6)
840 x=400-mod(p*2,400):poke $d00e,x and 255:poke $d010,peek($d010) or int(x/256)*128:poke $d00f,80
850 if k then poke $5f880,$c0+mod(int(p/4),4):poke $5f884,$c0+mod(int(p/4)+2,4):for i=0 to 5:e=$5f800+16*i:wpoke e,(1664-mod(p*5+i*110,700)) and 1023:wpoke e+2,(30+i*12+mod(int(p/4)+i*3,8)) and 1023:next i
860 vsync 251
870 if p<>bn then 790
880 t=ti-t
890 if rs then gosub 1070:print bn;"frames in";t;"seconds:";int(bn/t);"frames per second"
900 end
1000 poke mp+512*my+2*c,(128+n) and 255,22+int((128+n)/256):return
1020 rem dma job at j: options $0b,0; cmd cm, count nb, source s, target d
1030 s2=s-int(s/1048576)*1048576:d2=d-int(d/1048576)*1048576
1040 poke j,$0b,0,cm,mod(nb,256),int(nb/256),mod(s2,256),mod(int(s2/256),256),int(s2/65536),mod(d2,256),mod(int(d2/256),256),int(d2/65536),0,0,0:return
1070 poke $d015,0:poke $d06b,0:poke $d051,$80:poke $d05b,d5:poke $d07b,d7:if k then poke $ffd71f0,peek($ffd71f0) and $af
1080 poke $d05d,v4 or 128:poke $d031,vc:poke $d016,vd:poke $d05d,v4
1085 poke $d054,v0:poke $d058,v1,v2:poke $d05c,v3:poke $d05e,v5:poke $d060,v6,v7,v8,v9,va,vb:poke $d021,ve
1090 gosub 2860:scnclr:return
1100 rem layer 3: per row the sky colours of its 8 lines; rock (lit, shade), snow
1110 for r=0 to 17:for kk=0 to 5:if r<5 and kk>0 then 1180
1120 n=1+6*r+kk:for y=0 to 7:s=16+int((r*8+y)*31/199):if kk=0 then cm=3:nb=8:d=tg+64*n+8*y:gosub 2600:goto 1170
1130 for x=0 to 7:m=-1:if kk=3 then m=(x+y>=7):else if kk=4 then m=(x<=y):else if kk=5 then m=(y>=2)
1140 v=s:if m then v=76-int((r-8)/2)+mod(x*5+y*3+r,3):if r<=7 then v=180+mod(x+y,2)
1150 if m and (kk=2 or kk=4) then v=v-5
1160 poke tg+64*n+8*y+x,v:next x
1170 next y
1180 next kk,r
1200 rem hills: 3 height bands x 5 kinds, transparent outside, darker lower
1210 for b=0 to 2:for kk=1 to 5:n=109+5*b+kk-1:for y=0 to 7:for x=0 to 7
1220 m=-1:if kk=3 then m=(x+y>=7):else if kk=4 then m=(x<=y):else if kk=5 then m=(y>=2)
1230 v=0:if m then v=165-4*b-int(y/3)+mod(x*3+y*5,3):if mod(x*3+y*5+b,7)=0 then v=v-4
1240 if m and (kk=2 or kk=4) then v=v-4
1250 poke tg+64*n+8*y+x,v:next x,y,kk,b
1260 rem tree crown (3x3 tiles, a sphere lit from the top left), trunk, fence,
1270 rem grass, earth
1280 for n=124 to 137:for y=0 to 7:for x=0 to 7:v=0
1290 if n<133 then u=mod(n-124,3)*8+x-12:w=int((n-124)/3)*8+y-11:if u*u+w*w<=121 then v=94-int((u+w+22)/4)+3*(mod(x*7+y*3,5)=0)
1300 if n=133 and x>=3 and x<=4 then v=210-(x-3)*4-(mod(y,3)=0)
1310 if n=134 then if (x>=3 and x<=4 and y>0) or y=2 or y=5 then v=207-4*(x=3)
1320 if n=135 and (y=2 or y=5) then v=206
1330 if n=136 then v=58+mod(x*5+y*3,3):if y<3 or (y=3 and mod(x,2)=0) then v=92-y
1340 if n=137 then v=54+mod(x*5+y*7,5):if mod(x*7+y*3,11)=0 then v=50
1350 poke tg+64*n+8*y+x,v:next x,y,n
1360 return
1400 rem 16-colour sprites (4 bits per pixel, 16x21): 6 = balloon (palette
1410 rem 96-111: 1-11 a red ramp, 12 rope, 14-15 basket), 7 = plane (112-127)
1420 cm=3:nb=384:s=0:d=$5fc00:gosub 2600:for y=0 to 20:for x=0 to 15:v=0
1430 u=2*x-15:w=2*y-14:if y<15 and u*u+w*w<=224 then v=11-int(((2*x-9)*(2*x-9)+(2*y-8)*(2*y-8))/48):if v<1 then v=1
1440 if (y=15 or y=16 or y=17) and (x=5 or x=10) then v=12
1450 if y>=18 and x>=5 and x<=10 then v=14:if y=20 or x=5 or x=10 then v=15
1460 a=$5fc00+8*y+int(x/2):if x and 1 then poke a,peek(a) or v:else poke a,v*16
1470 next x,y
1480 restore 1510:h$="0123456789abcdef":for y=0 to 20:read a$:for x=0 to 7
1490 poke $5fcc0+8*y+x,(instr(h$,mid$(a$,2*x+1,1))-1)*16+instr(h$,mid$(a$,2*x+2,1))-1:next x,y:return
1510 data "0000000000000000","0000000000000000","0000000000000000","0000000000000000","0000000000000000"
1520 data "0000000000000000","0000000000000000","00009a0000000000","0000ea9000000000","eeeeeeeeeeeeeee0"
1530 data "e8788d8d8d878e00","0e2345566654e000","00ee33456eeee000","0000ea9000000000","00009a0000000000"
1540 data "0000000000000000","0000000000000000","0000000000000000","0000000000000000","0000000000000000"
1550 data "0000000000000000"
1600 rem 16-bit sprite pointers at $5fa00 (vic-iv set-up can reset them),
1610 rem sprites 6 and 7 in 16-colour mode, colour 0 transparent
1620 poke $d06c,0,$fa,$85:poke $5fa0c,$f0,$17,$f3,$17
1630 poke $d06b,$c0:poke $d02d,0,0:poke $d01b,$40:poke $d017,0:poke $d01d,0:poke $d01c,0:poke $d015,$c0:return
1700 rem four flapping frames (8x8 full colour) at $5f000 = glyphs $17c0-$17c3;
1710 rem birds 0,2,4 use token list $5f880, birds 1,3,5 $5f884 (opposite phase)
1720 restore 1790:k$=".obBwy":for f=0 to 3:for y=0 to 7:read a$:for x=0 to 7
1730 poke $5f000+64*f+8*y+x,val(mid$("000144197188175141",3*instr(k$,mid$(a$,x+1,1))-2,3)):next x,y,f
1740 poke $5f880,$c0,$17,255,255,$c2,$17,255,255
1750 for i=0 to 5:e=$5f800+16*i:poke e,0,0,0,0,255,1,$45,0:wpoke e+8,$f880+4*(i and 1):wpoke e+10,$3c00:poke e+12,0,0,0,0:next i
1760 poke cb-co+$3c00,0,0:poke $ffd71f8,0,$f8,5,6:return
1790 data "ow....wo",".owbbwo.","..obbo..","...bBy..","...BB...","........","........","........"
1800 data "........","owwbbwwo","..obbo..","...bBy..","...BB...","........","........","........"
1810 data "........","........","..obbo..",".owbBy..","owwBBwwo","........","........","........"
1820 data "........","owwbbwwo","..obbo..","...bBy..","...BB...","........","........","........"
2600 rem dma job at tl: options mb, skip sk, $0b; cmd cm, count nb, source s, target d
2610 s2=s-int(s/1048576)*1048576:d2=d-int(d/1048576)*1048576
2620 poke tl,$80,int(s/1048576),$81,int(d/1048576),$85,sk,$0b,0,cm,mod(nb,256),int(nb/256)
2630 poke tl+11,mod(s2,256),mod(int(s2/256),256),int(s2/65536),mod(d2,256),mod(int(d2/256),256),int(d2/65536),0,0,0
2640 poke $d701,$38,5,0,0,0:return
2700 rem own palette: ramps from 16 on (0-15 stay the standard colours)
2710 restore 2760
2720 read ps:if ps<0 then return
2730 read pc,r0,g0,b0,r1,g1,b1:dd=pc-1:if dd<1 then dd=1
2740 for ii=0 to pc-1:qi=ps+ii:qr=r0+int((r1-r0)*ii/dd):qg=g0+int((g1-g0)*ii/dd):qb=b0+int((b1-b0)*ii/dd):gosub 2820:next ii
2750 goto 2720
2760 data 16,32,14,18,70,255,190,140, 48,16,40,22,10,190,140,80, 64,16,30,35,60,175,185,215
2770 data 80,16,5,35,10,110,200,70, 97,11,90,0,0,255,140,120, 108,2,60,40,20,120,90,50
2780 data 110,2,110,60,20,170,110,50, 113,8,50,55,70,235,240,250, 121,4,200,150,0,255,230,90
2790 data 125,1,90,170,255,90,170,255, 126,2,30,30,40,220,30,30, 128,16,90,30,0,255,240,150
2800 data 144,8,0,0,0,40,35,55, 152,16,20,60,15,200,235,110, 168,16,130,150,190,255,255,255
2810 data 184,16,20,25,70,140,170,240, 200,16,45,25,10,190,130,70, 216,40,0,0,0,255,255,255, -1
2820 rem the vic-iv stores palette bytes nibble-swapped
2830 poke $d100+qi,mod(qr,16)*16+int(qr/16):poke $d200+qi,mod(qg,16)*16+int(qg/16):poke $d300+qi,mod(qb,16)*16+int(qb/16):return
2840 for ii=16 to 255:poke $5f400+ii,peek($d100+ii):poke $5f500+ii,peek($d200+ii):poke $5f600+ii,peek($d300+ii):next ii:return
2860 for ii=16 to 255:poke $d100+ii,peek($5f400+ii):poke $d200+ii,peek($5f500+ii):poke $d300+ii,peek($5f600+ii):next ii:return
2900 gosub 1070:print err$(er);" in";el:end
