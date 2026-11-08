5 REM Per frame: 25 statements (39 with the K325T birds), 4.5 ms in Xemu without the vsync wait: 50 fps
10 REM Parallax demo for B65 BASIC: three RRB layers per row (VIC-IV)
20 REM 80 columns, 25 rows (V200). Layer 3 (stars, mountains) is painted
30 REM first, then GOTOX + layer 2 (hills, transparent), then GOTOX + layer 1
40 REM (trees, ground, transparent). Layers 3 and 2 are "behind": sprite 0
50 REM (priority bit) passes between layer 2 and layer 1, sprite 1 is in
60 REM front of all. Speeds 1:2:4. Fine scroll = GOTOX X (0..7), coarse scroll
70 REM = a DMA list that copies each layer's 81-token window from maps that
80 REM are 128 tokens wide (+81 repeated). On a confirmed K325T (?&6E1=&FF)
90 REM six list-mode v2 objects (birds) fly in front of all layers.
100 REM BASIC 65 twin: examples/basic65/parallax65.bas (same picture, motion).
110 REM BN%>0: stop after BN% frames (tests); RS%=1: back to text, print timing
120 BN%=0:RS%=1:DB%=1:SK%=1
130 REM chip RAM (bank 5): rows, DMA lists, cells, charset, maps, sprites, birds
140 SC%=&50000:LS%=492:DL%=&53100:CE%=&53700:TL%=&53800:CH%=&54000:MP%=&54800
150 CO%=&0800:CB%=&FF80000+CO%:K%=(?&6E1=&FF)
155 IF K% THEN DB%=0:REM K325T: DBLRR with list mode loses the layers (core issue); its list-mode lead gives the time
160 ON ERROR PROCtext:REPORT:PRINT " at line ";ERL:END
170 PRINT "Building the scene..."
180 REM clear rows, charset, maps; colour RAM of the rows
190 PROCdma(3,25*LS%,0,SC%):PROCdma(3,2048,0,CH%):PROCdma(3,32*512,0,MP%)
200 PROCdma(3,25*LS%,0,CB%)
210 REM glyphs: 0 blank 1 solid 2 / 3 \ 4 dither 5 grass 6 crown 7 crown
220 REM 8 trunk 9 ground 10 dot 11 star 12 hilltop 13 post
230 FOR G%=1 TO 13:FOR Y%=0 TO 7:READ B%:?(CH%+8*G%+Y%)=B%:NEXT:NEXT
240 DATA 255,255,255,255,255,255,255,255, 1,3,7,15,31,63,127,255
250 DATA 128,192,224,240,248,252,254,255, 170,85,170,85,170,85,170,85
260 DATA 0,0,34,119,255,255,255,255, 24,60,126,255,255,255,255,255
270 DATA 255,255,126,126,60,24,24,24, 24,24,24,24,24,24,60,126
280 DATA 255,223,255,251,255,239,255,191, 0,0,0,16,0,0,0,0
290 DATA 0,16,16,124,16,16,0,0, 0,0,24,60,126,255,255,255
300 DATA 0,24,24,24,24,24,24,24
310 REM maps: layer 3 rows 0-13 (map 0-13), layer 2 rows 11-19 (14-22),
320 REM layer 1 rows 16-24 (23-31); 128 columns, then the first 81 again
330 FOR C%=0 TO 127
340 IF (C%*37+11) MOD 13=0 THEN PROCm(C% MOD 6,C%,10+C% MOD 2)
350 H%=2+ABS(16-C% MOD 32) DIV 3:L%=2+ABS(16-(C%+127) MOD 32) DIV 3:R%=2+ABS(16-(C%+1) MOD 32) DIV 3
360 G%=1:IF L%<H% THEN G%=2 ELSE IF R%<H% THEN G%=3
370 PROCm(13-H%+1,C%,G%):FOR Y%=13-H%+2 TO 13:PROCm(Y%,C%,1):NEXT
380 H%=2+ABS(12-C% MOD 24) DIV 3:L%=2+ABS(12-(C%+127) MOD 24) DIV 3:R%=2+ABS(12-(C%+1) MOD 24) DIV 3
390 G%=12:IF L%<H% THEN G%=2 ELSE IF R%<H% THEN G%=3
400 PROCm(14+19-H%+1-11,C%,G%):FOR Y%=19-H%+2 TO 19:PROCm(14+Y%-11,C%,4):NEXT
410 IF C% MOD 16=3 THEN PROCm(23+1,C%,6):PROCm(23+2,C%,7):PROCm(23+3,C%,8):PROCm(23+4,C%,8) ELSE IF C% MOD 4=0 THEN PROCm(23+4,C%,13)
420 PROCm(23+5,C%,5):FOR Y%=6 TO 8:PROCm(23+Y%,C%,9):NEXT
430 NEXT
440 FOR M%=0 TO 31:PROCdma(0,162,MP%+512*M%,MP%+512*M%+256):NEXT
450 REM colour RAM: GOTOX tokens (flags) and the layers' glyph colours per row
460 FOR R%=0 TO 24:A%=CB%+R%*LS%
470 ?A%=&50:?(A%+164)=&D0:?(A%+328)=&90:REM GOTOX: behind / transp.+behind / transp.
480 C3%=0:IF R%<6 THEN C3%=1 ELSE IF R%<10 THEN C3%=12 ELSE IF R%<14 THEN C3%=11
490 C2%=5:IF R%>16 THEN C2%=13
500 C1%=9:IF R%<19 THEN C1%=5 ELSE IF R%=21 THEN C1%=13 ELSE IF R%>21 THEN C1%=8
510 PROCcol(A%+3,C3%):PROCcol(A%+167,C2%):PROCcol(A%+331,C1%)
520 NEXT
530 REM DMA list per content row: fix the copy's source low byte from the
540 REM coarse cell, copy the fine X into the GOTOX token, copy the window
550 J%=DL%:FOR M%=0 TO 31
560 IF M%<14 THEN R%=M%:O%=0:N%=0 ELSE IF M%<23 THEN R%=M%-3:O%=164:N%=2 ELSE R%=M%-7:O%=328:N%=4
570 PROCjob(J%,4,1,CE%+N%+1,J%+28+5):PROCjob(J%+14,4,1,CE%+N%,SC%+R%*LS%+O%)
580 C%=4:IF M%=31 THEN C%=0
590 PROCjob(J%+28,C%,162,MP%+512*M%,SC%+R%*LS%+O%+2):J%=J%+42:NEXT
600 REM sprite images at &5FC00 (balloon), &5FC40 (plane)
610 FOR I%=0 TO 127:?(&5FC00+I%)=0:NEXT
620 FOR I%=0 TO 20:READ B%,C%:?(&5FC00+3*I%+1)=B%:?(&5FC40+3*I%+1)=C%:NEXT
630 DATA 60,0,126,0,255,0,255,0,255,0,255,0,126,129,60,195,24,255,36,255
640 DATA 36,195,24,129,60,0,60,0,24,0,0,0,0,0,0,0,0,0,0,0,0,0
650 IF K% THEN PROCbirds
680 REM VIC-IV: CHR16 (+ full colour above glyph 255 for the birds), 492-byte
690 REM rows of 246 tokens, screen/colour/charset pointers, wider side border
700 ?&D054=?&D054 OR 5:?&D058=LS% AND 255:?&D059=LS% DIV 256:?&D05E=246
710 ?&D063=?&D063 AND &C0:?&D060=0:?&D061=0:?&D062=5:?&D064=CO% AND 255:?&D065=CO% DIV 256
720 ?&D068=CH% AND 255:?&D069=(CH% DIV 256) AND 255:?&D06A=CH% DIV 65536
730 ?&D020=0:?&D021=0:?&D05C=?&D05C+8:IF DB% THEN ?&D051=&C0
735 REM sprites (after the VIC-IV set-up: it can reset the pointer address):
736 REM 16-bit pointers at &5FA00; sprite 0 behind layer 1, sprite 1 in front
737 ?&D06C=0:?&D06D=&FA:?&D06E=&85:?&5FA00=&F0:?&5FA01=&17:?&5FA02=&F1:?&5FA03=&17
738 ?&D027=2:?&D028=7:?&D01B=1:?&D017=0:?&D01D=0:?&D01C=0:?&D015=3
740 IF K% THEN ?&FFD71F0=?&FFD71F0 OR &50
760 REM main loop: positions, one DMA list, sprites, birds, wait for vsync
765 REM v2 X/Y must be 0..1023 (10-bit; Y signed): negative values are masked with AND 1023
770 P%=0:T%=TIME
780 REPEAT
790 P%=P%+1:X%=P%:?(CE%+4)=7-(X% AND 7):?(CE%+5)=(X% DIV 8 AND 127)*2
800 X%=P% DIV 2:?(CE%+2)=7-(X% AND 7):?(CE%+3)=(X% DIV 8 AND 127)*2
810 X%=P% DIV 4:?CE%=7-(X% AND 7):?(CE%+1)=(X% DIV 8 AND 127)*2
820 ?&D702=DL% DIV 65536:?&D704=0:?&D701=(DL% DIV 256) AND 255:?&D705=DL% AND 255:REM (the list address advances: set it each time)
830 X%=(P% DIV 2) MOD 400:?&D000=X% AND 255:?&D010=X% DIV 256:?&D001=166+(P% DIV 8) MOD 8
840 X%=400-(P%*3) MOD 400:?&D002=X% AND 255:?&D010=?&D010 AND 1 OR (X% DIV 256)*2:?&D003=184
850 IF K% THEN FOR I%=0 TO 5:X%=(640-(P%*5+I%*110) MOD 700) AND 1023:!(&5F800+16*I%)=X%+((30+I%*12+(P% DIV 4+I%*3) MOD 8) AND 1023)*65536:NEXT
860 WAIT
870 UNTIL P%=BN%
880 T%=TIME-T%
890 IF RS% THEN PROCtext:PRINT BN%;" frames in ";T%;" cs: ";BN%*100 DIV T%;" frames per second"
900 END
1000 DEF PROCm(Y%,C%,G%):?(MP%+512*Y%+2*C%)=G%:ENDPROC
1010 DEF PROCcol(A%,C%):SK%=2:PROCdma(3,81,C%,A%):SK%=1:ENDPROC
1020 DEF PROCjob(J%,C%,N%,S%,D%)
1030 ?J%=&0B:?(J%+1)=0:?(J%+2)=C%:?(J%+3)=N% AND 255:?(J%+4)=N% DIV 256
1040 !(J%+5)=S% AND &FFFFF:!(J%+8)=D% AND &FFFFF:?(J%+12)=0:?(J%+13)=0:ENDPROC
1050 DEF PROCdma(C%,N%,S%,D%):?TL%=&80:?(TL%+1)=S% DIV &100000:?(TL%+2)=&81:?(TL%+3)=D% DIV &100000
1055 ?(TL%+4)=&85:?(TL%+5)=SK%:PROCjob(TL%+6,C%,N%,S%,D%)
1060 ?&D702=TL% DIV 65536:?&D704=0:?&D701=(TL% DIV 256) AND 255:?&D705=TL% AND 255:ENDPROC
1070 DEF PROCtext
1080 ?&D015=0:?&D051=&80:?&D05C=?&D05C-8:IF K% THEN ?&FFD71F0=?&FFD71F0 AND &AF
1090 MODE 0:ENDPROC
1100 DEF PROCbirds
1110 REM full-colour bird glyphs &17C0 (64 bytes at &5F000), tokens, table
1120 FOR Y%=0 TO 7:READ B%:FOR X%=0 TO 7:?(&5F000+8*Y%+X%)=((B%>>(7-X%)) AND 1)*15:NEXT:NEXT
1130 DATA 0,0,195,102,60,24,0,0
1140 FOR I%=0 TO 5:A%=&5F880+4*I%:?A%=&C0:?(A%+1)=&17:?(A%+2)=255:?(A%+3)=255
1150 E%=&5F800+16*I%:!E%=0:?(E%+4)=255:?(E%+5)=1:?(E%+6)=&45:?(E%+7)=0
1160 !(E%+8)=(&F880+4*I%)+(&3C00+4*I%)*65536:!(E%+12)=0
1170 ?(CB%-CO%+&3C00+4*I%)=0:?(CB%-CO%+&3C01+4*I%)=0:NEXT
1180 ?&FFD71F8=0:?&FFD71F9=&F8:?&FFD71FA=5:?&FFD71FB=6:ENDPROC
