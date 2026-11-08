5 REM Per frame: 25 statements (46 with the K325T birds), 4.6 ms in Xemu without the vsync wait: 50 fps. RRB: 3 GOTOX + 123 FCM glyphs a line, ~1970 of 2592 cycles (16 per FCM glyph); DBLRR on the V400 schedule doubles that
10 REM Parallax demo for B65 BASIC: three overlapping RRB layers (VIC-IV)
20 REM 40 columns (H320), 25 rows (V200), every glyph full colour (FCM) from an
30 REM own 256-colour palette (0-15 kept). Layer 3: sky gradient and snowy
40 REM mountains (opaque, painted first); GOTOX + layer 2: rolling hills
50 REM (transparent): the mountains show in the dips; GOTOX + layer 1: trees,
60 REM fence and ground (transparent): the hills show between the trunks and
70 REM under the crowns. Layers 3 and 2 are "behind", so the 16-colour balloon
80 REM sprite (priority bit) passes behind the trees but in front of the hills;
90 REM the 16-colour plane flies in front of all. Speeds 1:2:6. Fine scroll =
95 REM GOTOX X, coarse scroll = one self-modifying DMA list per frame (41-token
100 REM windows of 128-tile maps). K325T (?&6E1=&FF): six shaded, flapping
105 REM list-mode v2 birds in front of all. DBLRR on the V400 schedule: line 620.
110 REM BASIC 65 twin: examples/basic65/parallax65.bas (same picture, motion).
115 REM BN%>0: stop after BN% frames (tests); RS%=1: back to text, print timing
120 BN%=0:RS%=1:DB%=1:SK%=1:XS%=1:SB%=?&D05C:D5%=?&D05B:D7%=?&D07B
130 SC%=&50000:LS%=252:DL%=&53100:CE%=&53700:TL%=&53800:MP%=&54800:TG%=&5A000
140 CO%=&0800:CB%=&FF80000+CO%:K%=(?&6E1=&FF)
150 ON ERROR PROCtext:REPORT:PRINT " at line ";ERL:END
155 PROCpalsave
160 PRINT "Building the scene...":MODE 1:PROCpalette
170 PROCdma(3,25*LS%,0,SC%):PROCdma(3,25*LS%,0,CB%):PROCdma(3,160*64,0,TG%)
180 SK%=2:PROCdma(3,36*256,&80,MP%):PROCdma(3,36*256,&16,MP%+1):SK%=1
190 REM full-colour tiles &1680+n at TG%+64*n: 0 blank; 1+6*row+kind: layer 3
200 REM (rows 0-17; kind 0 sky, 1 lit, 2 shade, 3 rise, 4 fall, 5 top);
210 REM 109+5*band+kind-1: hills; 124-132 crown, 133 trunk, 134 post,
220 REM 135 rail, 136 grass, 137 earth; birds &17C0-&17C3 at &5F000
230 PROCtiles
235 REM all periods divide 128 (mountains and hills 32, trees 16, posts 4): seamless wrap
240 REM maps (36 rows of 512 bytes): layer 3 rows 5-17 (map 0-12), layer 2
250 REM rows 11-21 (13-23), layer 1 rows 13-24 (24-35); 128 columns, then the
260 REM first 41 again
270 FOR C%=0 TO 127
280 U%=C% MOD 32:T%=5+ABS(U%-16) DIV 2:L%=5+ABS((C%+127) MOD 32-16) DIV 2:R%=5+ABS((C%+1) MOD 32-16) DIV 2
290 G%=5:IF L%>T% THEN G%=3 ELSE IF R%>T% THEN G%=4
300 S%=1:IF U%>=16 THEN S%=2
310 FOR Y%=5 TO 17:V%=0:IF Y%=T% THEN V%=G% ELSE IF Y%>T% THEN V%=S%
320 PROCm(Y%-5,C%,1+6*Y%+V%):NEXT
330 U%=(C%+8) MOD 32:T%=11+ABS(U%-16) DIV 4:L%=11+ABS((C%+7) MOD 32-16) DIV 4:R%=11+ABS((C%+9) MOD 32-16) DIV 4
340 G%=5:IF L%>T% THEN G%=3 ELSE IF R%>T% THEN G%=4
350 S%=1:IF U%>=16 THEN S%=2
360 FOR Y%=T% TO 21:V%=S%:IF Y%=T% THEN V%=G%
370 PROCm(Y%+2,C%,109+5*((Y%-11) DIV 4)+V%-1):NEXT
380 IF C% MOD 4=0 THEN PROCm(30,C%,134):PROCm(31,C%,134):PROCm(32,C%,134) ELSE PROCm(30,C%,135):PROCm(31,C%,135)
390 U%=(C%+1) MOD 16:IF U%<3 THEN FOR Y%=0 TO 2:PROCm(24+Y%,C%,124+3*Y%+U%):NEXT
400 IF U%=1 THEN FOR Y%=3 TO 8:PROCm(24+Y%,C%,133):NEXT
410 PROCm(33,C%,136):PROCm(34,C%,137):PROCm(35,C%,137)
420 NEXT
430 FOR M%=0 TO 35:PROCdma(0,82,MP%+512*M%,MP%+512*M%+256):NEXT
440 REM rows: layer 3 sky above the mountains and rock below, layers 2/1 blank;
450 REM GOTOX colour flags: behind / transparent+behind / transparent
460 FOR R%=0 TO 24:A%=SC%+R%*LS%:N%=1+6*R%:IF R%>17 THEN N%=1+6*17+2
470 SK%=2:PROCdma(3,41,(&80+N%) AND 255,A%+2):PROCdma(3,41,&16+(&80+N%) DIV 256,A%+3)
480 PROCdma(3,41,&80,A%+86):PROCdma(3,41,&16,A%+87):PROCdma(3,41,&80,A%+170):PROCdma(3,41,&16,A%+171):SK%=1
490 ?(CB%+R%*LS%)=&50:?(CB%+R%*LS%+84)=&D0:?(CB%+R%*LS%+168)=&90:NEXT
500 REM DMA list per content row: fix the copy's source low byte from the
510 REM coarse cell, copy the fine X into the GOTOX token, copy the window
520 J%=DL%:FOR M%=0 TO 35
530 IF M%<13 THEN R%=M%+5:O%=0:N%=0 ELSE IF M%<24 THEN R%=M%-2:O%=84:N%=2 ELSE R%=M%-11:O%=168:N%=4
540 PROCjob(J%,4,1,CE%+N%+1,J%+28+5):PROCjob(J%+14,4,1,CE%+N%,SC%+R%*LS%+O%)
550 C%=4:IF M%=35 THEN C%=0
560 PROCjob(J%+28,C%,82,MP%+512*M%,SC%+R%*LS%+O%+2):J%=J%+42:NEXT
570 PROCsprites:IF K% THEN PROCbirds
580 REM VIC-IV (MODE 1 set H320 and full colour): 252-byte rows of 126 tokens,
590 REM screen and colour pointers, sky behind. DBLRR needs the V400 row schedule
595 REM (V400, one line per glyph row, 25 rows): each row is painted once, shown twice
600 ?&D058=LS% AND 255:?&D059=LS% DIV 256:?&D05E=126:?&D063=?&D063 AND &C0
610 ?&D060=0:?&D061=0:?&D062=5:?&D064=CO% AND 255:?&D065=CO% DIV 256
620 ?&D020=0:?&D021=16:IF DB% THEN ?&D031=?&D031 OR 8:?&D05B=0:?&D07B=24:?&D051=&C0
625 SB%=?&D05C:?&D05C=SB%+16:REM wider side border: hides the fine-scroll gap at the left edge
630 IF K% THEN ?&FFD71F0=?&FFD71F0 OR &50
640 PROCspriteregs
760 REM main loop: positions, one DMA list, sprites, birds, wait for vsync
765 REM v2 X/Y must be 0..1023 (10-bit; Y signed): negative values are masked with AND 1023
770 P%=0:T%=TIME
780 REPEAT
790 P%=P%+1:X%=P%*3:?(CE%+4)=XS%*(7-(X% AND 7)):?(CE%+5)=(X% DIV 8 AND 127)*2
800 X%=P%:?(CE%+2)=XS%*(7-(X% AND 7)):?(CE%+3)=(X% DIV 8 AND 127)*2
810 X%=P% DIV 2:?CE%=XS%*(7-(X% AND 7)):?(CE%+1)=(X% DIV 8 AND 127)*2
820 ?&D702=DL% DIV 65536:?&D704=0:?&D701=(DL% DIV 256) AND 255:?&D705=DL% AND 255:REM (the list address advances: set it each time)
830 X%=(P% DIV 3) MOD 400:?&D00C=X% AND 255:?&D010=(X% DIV 256)*64:?&D00D=150+(P% DIV 10) MOD 6
840 X%=400-(P%*2) MOD 400:?&D00E=X% AND 255:?&D010=?&D010 OR (X% DIV 256)*128:?&D00F=80
850 IF K% THEN ?&5F880=&C0+(P% DIV 4) MOD 4:?&5F884=&C0+(P% DIV 4+2) MOD 4:FOR I%=0 TO 5:X%=(640-(P%*5+I%*110) MOD 700) AND 1023:!(&5F800+16*I%)=X%+((30+I%*12+(P% DIV 4+I%*3) MOD 8) AND 1023)*65536:NEXT
860 WAIT
870 UNTIL P%=BN%
880 T%=TIME-T%
890 IF RS% THEN PROCtext:PRINT BN%;" frames in ";T%;" cs: ";BN%*100 DIV T%;" frames per second"
900 END
1000 DEF PROCm(Y%,C%,N%):?(MP%+512*Y%+2*C%)=(&80+N%) AND 255:?(MP%+512*Y%+2*C%+1)=&16+(&80+N%) DIV 256:ENDPROC
1020 DEF PROCjob(J%,C%,N%,S%,D%)
1030 ?J%=&0B:?(J%+1)=0:?(J%+2)=C%:?(J%+3)=N% AND 255:?(J%+4)=N% DIV 256
1040 !(J%+5)=S% AND &FFFFF:!(J%+8)=D% AND &FFFFF:?(J%+12)=0:?(J%+13)=0:ENDPROC
1050 DEF PROCdma(C%,N%,S%,D%):?TL%=&80:?(TL%+1)=S% DIV &100000:?(TL%+2)=&81:?(TL%+3)=D% DIV &100000
1055 ?(TL%+4)=&85:?(TL%+5)=SK%:PROCjob(TL%+6,C%,N%,S%,D%)
1060 ?&D702=TL% DIV 65536:?&D704=0:?&D701=(TL% DIV 256) AND 255:?&D705=TL% AND 255:ENDPROC
1070 DEF PROCtext
1080 ?&D015=0:?&D06B=0:?&D051=&80:IF DB% THEN ?&D031=?&D031 AND &F7:?&D05B=D5%:?&D07B=D7%
1085 ?&D05C=SB%:IF K% THEN ?&FFD71F0=?&FFD71F0 AND &AF
1090 PROCpalrest:MODE 0:ENDPROC
1100 DEF PROCtiles:LOCAL N%,R%,K%,X%,Y%,V%,M%,S%
1110 REM layer 3: per row the sky colours of its 8 lines; rock (lit, shade), snow
1120 FOR R%=0 TO 17:FOR K%=0 TO 5:IF R%<5 AND K%>0 THEN 1190
1130 N%=1+6*R%+K%:FOR Y%=0 TO 7:S%=16+(R%*8+Y%)*31 DIV 199:IF K%=0 THEN PROCdma(3,8,S%,TG%+64*N%+8*Y%):GOTO 1180
1140 FOR X%=0 TO 7:M%=-1:IF K%=3 THEN M%=(X%+Y%>=7) ELSE IF K%=4 THEN M%=(X%<=Y%) ELSE IF K%=5 THEN M%=(Y%>=2)
1150 V%=S%:IF M% THEN V%=76-(R%-8) DIV 2+(X%*5+Y%*3+R%) MOD 3:IF R%<=7 THEN V%=180+(X%+Y%) MOD 2
1160 IF M% AND (K%=2 OR K%=4) THEN V%=V%-5
1170 ?(TG%+64*N%+8*Y%+X%)=V%:NEXT
1180 NEXT
1190 NEXT:NEXT
1200 REM hills: 3 height bands x 5 kinds, transparent outside, darker lower
1210 FOR B%=0 TO 2:FOR K%=1 TO 5:N%=109+5*B%+K%-1:FOR Y%=0 TO 7:FOR X%=0 TO 7
1220 M%=-1:IF K%=3 THEN M%=(X%+Y%>=7) ELSE IF K%=4 THEN M%=(X%<=Y%) ELSE IF K%=5 THEN M%=(Y%>=2)
1230 V%=0:IF M% THEN V%=165-4*B%-Y% DIV 3+(X%*3+Y%*5) MOD 3:IF (X%*3+Y%*5+B%) MOD 7=0 THEN V%=V%-4
1240 IF M% AND (K%=2 OR K%=4) THEN V%=V%-4
1250 ?(TG%+64*N%+8*Y%+X%)=V%:NEXT:NEXT:NEXT:NEXT
1260 REM tree crown (3x3 tiles, a sphere lit from the top left), trunk, fence,
1270 REM grass, earth
1280 FOR N%=124 TO 137:FOR Y%=0 TO 7:FOR X%=0 TO 7:V%=0
1290 IF N%<133 THEN U%=((N%-124) MOD 3)*8+X%-12:W%=((N%-124) DIV 3)*8+Y%-11:IF U%*U%+W%*W%<=121 THEN V%=94-(U%+W%+22) DIV 4-((X%*7+Y%*3) MOD 5=0)*-3
1300 IF N%=133 AND X%>=3 AND X%<=4 THEN V%=210-(X%-3)*4-(Y% MOD 3=0)
1310 IF N%=134 THEN IF (X%>=3 AND X%<=4 AND Y%>0) OR Y%=2 OR Y%=5 THEN V%=207+(X%=3)*-4
1320 IF N%=135 AND (Y%=2 OR Y%=5) THEN V%=206
1330 IF N%=136 THEN V%=58+(X%*5+Y%*3) MOD 3:IF Y%<3 OR (Y%=3 AND X% MOD 2=0) THEN V%=92-Y%
1340 IF N%=137 THEN V%=54+(X%*5+Y%*7) MOD 5:IF (X%*7+Y%*3) MOD 11=0 THEN V%=50
1350 ?(TG%+64*N%+8*Y%+X%)=V%:NEXT:NEXT:NEXT
1360 ENDPROC
1400 DEF PROCsprites:LOCAL Y%,X%,A$,V%,U%,W%
1410 REM 16-colour sprites (4 bits per pixel, 16x21): 6 = balloon (palette
1420 REM 96-111: 1-11 a red ramp, 12 rope, 14-15 basket), 7 = plane (112-127)
1430 PROCdma(3,384,0,&5FC00):FOR Y%=0 TO 20:FOR X%=0 TO 15:V%=0
1440 U%=2*X%-15:W%=2*Y%-14:IF Y%<15 AND U%*U%+W%*W%<=224 THEN V%=11-((2*X%-9)*(2*X%-9)+(2*Y%-8)*(2*Y%-8)) DIV 48:IF V%<1 THEN V%=1
1450 IF (Y%=15 OR Y%=16 OR Y%=17) AND (X%=5 OR X%=10) THEN V%=12
1460 IF Y%>=18 AND X%>=5 AND X%<=10 THEN V%=14:IF Y%=20 OR X%=5 OR X%=10 THEN V%=15
1470 A%=&5FC00+8*Y%+X% DIV 2:IF X% AND 1 THEN ?A%=?A% OR V% ELSE ?A%=V%*16
1480 NEXT:NEXT
1490 RESTORE 1510:FOR Y%=0 TO 20:READ A$:FOR X%=0 TO 7
1500 ?(&5FCC0+8*Y%+X%)=(INSTR("0123456789abcdef",MID$(A$,2*X%+1,1))-1)*16+INSTR("0123456789abcdef",MID$(A$,2*X%+2,1))-1:NEXT:NEXT:ENDPROC
1510 DATA "0000000000000000","0000000000000000","0000000000000000","0000000000000000","0000000000000000"
1520 DATA "0000000000000000","0000000000000000","00009a0000000000","0000ea9000000000","eeeeeeeeeeeeeee0"
1530 DATA "e8788d8d8d878e00","0e2345566654e000","00ee33456eeee000","0000ea9000000000","00009a0000000000"
1540 DATA "0000000000000000","0000000000000000","0000000000000000","0000000000000000","0000000000000000"
1550 DATA "0000000000000000"
1600 DEF PROCspriteregs
1610 REM 16-bit sprite pointers at &5FA00 (VIC-IV set-up can reset them),
1620 REM sprites 6 and 7 in 16-colour mode, colour 0 transparent
1630 ?&D06C=0:?&D06D=&FA:?&D06E=&85:?&5FA0C=&F0:?&5FA0D=&17:?&5FA0E=&F3:?&5FA0F=&17
1640 ?&D06B=&C0:?&D02D=0:?&D02E=0:?&D01B=&40:?&D017=0:?&D01D=0:?&D01C=0:?&D015=&C0:ENDPROC
1700 DEF PROCbirds:LOCAL F%,Y%,X%,A$,K$,I%,E%,A%
1710 REM four flapping frames (8x8 full colour) at &5F000 = glyphs &17C0-&17C3;
1720 REM birds 0,2,4 use token list &5F880, birds 1,3,5 &5F884 (opposite phase)
1730 RESTORE 1790:K$=".obBwy":FOR F%=0 TO 3:FOR Y%=0 TO 7:READ A$:FOR X%=0 TO 7
1740 ?(&5F000+64*F%+8*Y%+X%)=VAL(MID$("000144197188175141",3*INSTR(K$,MID$(A$,X%+1,1))-2,3)):NEXT:NEXT:NEXT
1750 ?&5F880=&C0:?&5F881=&17:?&5F882=255:?&5F883=255:?&5F884=&C2:?&5F885=&17:?&5F886=255:?&5F887=255
1760 FOR I%=0 TO 5:E%=&5F800+16*I%:!E%=0:?(E%+4)=255:?(E%+5)=1:?(E%+6)=&45:?(E%+7)=0
1770 !(E%+8)=(&F880+4*(I% AND 1))+&3C00*65536:!(E%+12)=0:NEXT
1780 ?(CB%-CO%+&3C00)=0:?(CB%-CO%+&3C01)=0:?&FFD71F8=0:?&FFD71F9=&F8:?&FFD71FA=5:?&FFD71FB=6:ENDPROC
1790 DATA "ow....wo",".owbbwo.","..obbo..","...bBy..","...BB...","........","........","........"
1800 DATA "........","owwbbwwo","..obbo..","...bBy..","...BB...","........","........","........"
1810 DATA "........","........","..obbo..",".owbBy..","owwBBwwo","........","........","........"
1820 DATA "........","owwbbwwo","..obbo..","...bBy..","...BB...","........","........","........"
2000 DEF PROCpalette:LOCAL S%,C%,D%,I%,R0%,G0%,B0%,R1%,G1%,B1%
2010 REM own palette: ramps from 16 on (0-15 stay the standard colours)
2020 RESTORE 2060
2030 READ S%:IF S%<0 THEN ENDPROC
2040 READ C%,R0%,G0%,B0%,R1%,G1%,B1%:D%=C%-1:IF D%<1 THEN D%=1
2045 FOR I%=0 TO C%-1
2050 PROCpal(S%+I%,R0%+(R1%-R0%)*I% DIV D%,G0%+(G1%-G0%)*I% DIV D%,B0%+(B1%-B0%)*I% DIV D%):NEXT:GOTO 2030
2060 DATA 16,32,14,18,70,255,190,140, 48,16,40,22,10,190,140,80, 64,16,30,35,60,175,185,215
2070 DATA 80,16,5,35,10,110,200,70, 97,11,90,0,0,255,140,120, 108,2,60,40,20,120,90,50
2080 DATA 110,2,110,60,20,170,110,50, 113,8,50,55,70,235,240,250, 121,4,200,150,0,255,230,90
2090 DATA 125,1,90,170,255,90,170,255, 126,2,30,30,40,220,30,30, 128,16,90,30,0,255,240,150
2100 DATA 144,8,0,0,0,40,35,55, 152,16,20,60,15,200,235,110, 168,16,130,150,190,255,255,255
2110 DATA 184,16,20,25,70,140,170,240, 200,16,45,25,10,190,130,70, 216,40,0,0,0,255,255,255, -1
2120 DEF PROCpal(I%,R%,G%,B%):REM the VIC-IV stores palette bytes nibble-swapped
2130 ?(&D100+I%)=(R% MOD 16)*16+R% DIV 16:?(&D200+I%)=(G% MOD 16)*16+G% DIV 16:?(&D300+I%)=(B% MOD 16)*16+B% DIV 16:ENDPROC
2140 DEF PROCpalsave:LOCAL I%
2150 FOR I%=16 TO 255:?(&5F400+I%)=?(&D100+I%):?(&5F500+I%)=?(&D200+I%):?(&5F600+I%)=?(&D300+I%):NEXT:ENDPROC
2160 DEF PROCpalrest:LOCAL I%
2170 FOR I%=16 TO 255:?(&D100+I%)=?(&5F400+I%):?(&D200+I%)=?(&5F500+I%):?(&D300+I%)=?(&5F600+I%):NEXT:ENDPROC
