10 rem parallax demo for basic 65: three rrb layers per row (vic-iv)
20 rem twin of examples/parallax.bas (b65 basic): same picture, same motion.
30 rem 80 columns, 25 rows (v200). layer 3 (stars, mountains) is painted
40 rem first, then gotox + layer 2 (hills, transparent), then gotox + layer 1
50 rem (trees, ground, transparent). layers 3 and 2 are "behind": sprite 0
60 rem (priority bit) passes between layer 2 and layer 1, sprite 1 is in
70 rem front of all. speeds 1:2:4. fine scroll = gotox x (0..7), coarse scroll
80 rem = a dma list that copies each layer's 81-token window from maps that
90 rem are 128 tokens wide (+81 repeated). on a k325t ($d629=$62) six
100 rem list-mode v2 objects (birds) fly in front of all layers.
110 rem bn>0: stop after bn frames (tests); rs=1: back to text, print timing
120 bn=0:rs=1:db=1:sk=1
130 rem chip ram (bank 5): rows, dma lists, cells, charset, maps, sprites, birds
140 sc=$50000:ls=492:dl=$53100:ce=$53700:tl=$53800:ch=$54000:mp=$54800
150 co=$0800:cb=$ff80000+co:k=(peek($d629)=98)
160 trap 1500
170 print "building the scene..."
175 v0=peek($d054):v1=peek($d058):v2=peek($d059):v3=peek($d05c):v4=peek($d05d):v5=peek($d05e)
176 v6=peek($d060):v7=peek($d061):v8=peek($d062):v9=peek($d063):va=peek($d064):vb=peek($d065)
177 vc=peek($d068):vd=peek($d069):ve=peek($d06a)
180 rem clear rows, charset, maps; colour ram of the rows
190 edma 3,25*ls,0,sc:edma 3,2048,0,ch:edma 3,32*512,0,mp
200 edma 3,25*ls,0,cb
210 rem glyphs: 0 blank 1 solid 2 / 3 \ 4 dither 5 grass 6 crown 7 crown
220 rem 8 trunk 9 ground 10 dot 11 star 12 hilltop 13 post
230 for g=1 to 13:for y=0 to 7:read b:poke ch+8*g+y,b:next y,g
240 data 255,255,255,255,255,255,255,255, 1,3,7,15,31,63,127,255
250 data 128,192,224,240,248,252,254,255, 170,85,170,85,170,85,170,85
260 data 0,0,34,119,255,255,255,255, 24,60,126,255,255,255,255,255
270 data 255,255,126,126,60,24,24,24, 24,24,24,24,24,24,60,126
280 data 255,223,255,251,255,239,255,191, 0,0,0,16,0,0,0,0
290 data 0,16,16,124,16,16,0,0, 0,0,24,60,126,255,255,255
300 data 0,24,24,24,24,24,24,24
310 rem maps: layer 3 rows 0-13 (map 0-13), layer 2 rows 11-19 (14-22),
320 rem layer 1 rows 16-24 (23-31); 128 columns, then the first 81 again
330 for c=0 to 127
340 if mod(c*37+11,13)=0 then y=mod(c,6):g=10+mod(c,2):gosub 1000
350 h=2+int(abs(16-mod(c,32))/3):l=2+int(abs(16-mod(c+127,32))/3):r=2+int(abs(16-mod(c+1,32))/3)
360 g=1:if l<h then g=2:else if r<h then g=3
370 y=13-h+1:gosub 1000:g=1:for y=13-h+2 to 13:gosub 1000:next y
380 h=2+int(abs(12-mod(c,24))/3):l=2+int(abs(12-mod(c+127,24))/3):r=2+int(abs(12-mod(c+1,24))/3)
390 g=12:if l<h then g=2:else if r<h then g=3
400 y=14+19-h+1-11:gosub 1000:g=4:for y=14+19-h+2-11 to 14+19-11:gosub 1000:next y
410 if mod(c,16)=3 then y=24:g=6:gosub 1000:y=25:g=7:gosub 1000:y=26:g=8:gosub 1000:y=27:gosub 1000:else if mod(c,4)=0 then y=27:g=13:gosub 1000
420 y=28:g=5:gosub 1000:g=9:for y=29 to 31:gosub 1000:next y
430 next c
440 for m=0 to 31:edma 0,162,mp+512*m,mp+512*m+256:next m
450 rem colour ram: gotox tokens (flags) and the layers' glyph colours per row
460 for r=0 to 24:a=cb+r*ls
470 poke a,$50:poke a+164,$d0:poke a+328,$90:rem gotox: behind / transp.+behind / transp.
480 c3=0:if r<6 then c3=1:else if r<10 then c3=12:else if r<14 then c3=11
490 c2=5:if r>16 then c2=13
500 c1=9:if r<19 then c1=5:else if r=21 then c1=13:else if r>21 then c1=8
510 a=a+3:c=c3:gosub 1010:a=a+164:c=c2:gosub 1010:a=a+164:c=c1:gosub 1010
520 next r
530 rem dma list per content row: fix the copy's source low byte from the
540 rem coarse cell, copy the fine x into the gotox token, copy the window
550 j=dl:for m=0 to 31
560 if m<14 then r=m:o=0:n=0:else if m<23 then r=m-3:o=164:n=2:else r=m-7:o=328:n=4
570 cm=4:nb=1:s=ce+n+1:d=j+28+5:gosub 1020:j=j+14:s=ce+n:d=sc+r*ls+o:gosub 1020:j=j+14
580 cm=4:if m=31 then cm=0
590 nb=162:s=mp+512*m:d=sc+r*ls+o+2:gosub 1020:j=j+14:next m
600 rem sprite images at $5fc00 (balloon), $5fc40 (plane)
610 edma 3,128,0,$5fc00
620 for i=0 to 20:read b,c:poke $5fc00+3*i+1,b:poke $5fc40+3*i+1,c:next i
630 data 60,0,126,0,255,0,255,0,255,0,255,0,126,129,60,195,24,255,36,255
640 data 36,195,24,129,60,0,60,0,24,0,0,0,0,0,0,0,0,0,0,0,0,0
650 if k then gosub 1100
680 rem vic-iv: hotreg off, chr16 (+ full colour above glyph 255 for the birds),
690 rem 492-byte rows of 246 tokens, screen/colour/charset pointers, side border
700 poke $d05d,peek($d05d) and 127:poke $d054,peek($d054) or 5:wpoke $d058,ls:poke $d05e,246
710 poke $d063,peek($d063) and 192:poke $d060,0,0,5:wpoke $d064,co
720 poke $d068,0,$40,5
730 poke $d020,0:poke $d021,0:poke $d05c,peek($d05c)+8:if db then poke $d051,$c0
735 rem sprites (after the vic-iv set-up: it can reset the pointer address):
736 rem 16-bit pointers at $5fa00; sprite 0 behind layer 1, sprite 1 in front
737 poke $d06c,0,$fa,$85:poke $5fa00,$f0,$17,$f1,$17
738 poke $d027,2,7:poke $d01b,1:poke $d017,0:poke $d01d,0:poke $d01c,0:poke $d015,3
740 if k then poke $ffd71f0,peek($ffd71f0) or $50
760 rem main loop: positions, one dma list, sprites, birds, wait for vsync
770 p=0:t=ti
790 p=p+1:x=p:poke ce+4,7-mod(x,8),mod(int(x/8),128)*2
800 x=int(p/2):poke ce+2,7-mod(x,8),mod(int(x/8),128)*2
810 x=int(p/4):poke ce,7-mod(x,8),mod(int(x/8),128)*2
820 poke $d701,$31,5,0,0,0:rem list at $53100: the trigger ($d705) is written last
830 x=mod(int(p/2),400):poke $d000,mod(x,256):poke $d010,int(x/256):poke $d001,166+mod(int(p/8),8)
840 x=400-mod(p*3,400):poke $d002,mod(x,256):poke $d010,(peek($d010) and 1) or int(x/256)*2:poke $d003,184
850 if k then for i=0 to 5:x=640-mod(p*5+i*110,700):e=$5f800+16*i:wpoke e,x:wpoke e+2,30+i*12+mod(int(p/4)+i*3,8):next i
860 vsync 251
870 if p<>bn then 790
880 t=ti-t
890 if rs then gosub 1070:print bn;"frames in";t;"ti units"
900 end
1000 poke mp+512*y+2*c,g:return
1010 sk=2:cm=3:nb=81:s=c:d=a:gosub 1050:sk=1:return
1020 rem dma job at j: options $0b,0; cmd cm, count nb, source s, target d
1030 s2=s-int(s/1048576)*1048576:d2=d-int(d/1048576)*1048576
1040 poke j,$0b,0,cm,mod(nb,256),int(nb/256),mod(s2,256),mod(int(s2/256),256),int(s2/65536),mod(d2,256),mod(int(d2/256),256),int(d2/65536),0,0,0:return
1050 jj=j:j=tl+6:gosub 1020:j=jj:poke tl,$80,int(s/1048576),$81,int(d/1048576),$85,sk
1060 poke $d701,$38,5,0,0,0:return
1070 poke $d015,0:poke $d051,$80:if k then poke $ffd71f0,peek($ffd71f0) and $af
1080 poke $d054,v0:poke $d058,v1,v2:poke $d05c,v3:poke $d05e,v5:poke $d060,v6,v7,v8,v9,va,vb
1085 poke $d068,vc,vd,ve:poke $d05d,v4
1090 scnclr:return
1100 rem full-colour bird glyphs $17c0 (64 bytes at $5f000), tokens, table
1110 for y=0 to 7:read b:for x=0 to 7:poke $5f000+8*y+x,(int(b/2^(7-x)) and 1)*15:next x,y
1120 data 0,0,195,102,60,24,0,0
1130 for i=0 to 5:a=$5f880+4*i:poke a,$c0,$17,255,255
1140 e=$5f800+16*i:poke e,0,0,0,0,255,1,$45,0:wpoke e+8,$f880+4*i:wpoke e+10,$3c00+4*i:poke e+12,0,0,0,0
1150 poke $ff83c00+4*i,0,0:next i
1160 poke $ffd71f8,0,$f8,5,6:return
1500 gosub 1070:print err$(er);" in";el:end
