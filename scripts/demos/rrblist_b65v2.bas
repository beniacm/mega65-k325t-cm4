10 REM RRB list mode v2 demo for B65 BASIC (K325T XCTL bit 6): bouncing balls
20 N%=6:S%=&80000:C%=&FF84000:L%=160:G%=&1800:GA%=&60000:OA%=&89000:OC%=&7800:T%=&8C000
25 DIM P%(8):P%(0)=1:FOR J%=1 TO 8:P%(J%)=P%(J%-1)*2:NEXT
50 REM full-colour ball glyphs G%+I% (64 bytes each at GA%; full-colour glyphs must be in the first 512 KB)
60 FOR I%=0 TO N%-1:K%=2+I%:IF K%>6 THEN K%=K%+1
70 RESTORE:FOR Y%=0 TO 7:READ B%:FOR X%=0 TO 7:P%=0
80 IF B% AND P%(7-X%) THEN P%=K%:IF Y%<2 THEN P%=1
90 ?(GA%+64*I%+8*Y%+X%)=P%:NEXT:NEXT
100 PROCw(OA%+16*I%,G%+I%):PROCw(OA%+16*I%+2,&FFFF):PROCw(C%-&4000+OC%+16*I%,0)
110 REM object table entry: X/Y, mask, height, address 19..16, address, colour offset, stride
120 E%=T%+16*I%:!E%=0:?(E%+4)=255:?(E%+5)=1:?(E%+6)=8:?(E%+7)=0
130 !(E%+8)=&9000+16*I%+(OC%+16*I%)*65536:!(E%+12)=0
140 NEXT
150 DATA 60,126,255,255,255,255,126,60
160 REM rows (50): 80 stars/spaces -- no LIST token or entries needed in v2
170 FOR R%=0 TO 49:A%=S%+R%*L%:CA%=C%+R%*L%
180 FOR X%=0 TO 79:T=32:IF RND(1)<.06 THEN T=46
190 PROCw(A%+2*X%,T):?(CA%+2*X%)=0:?(CA%+2*X%+1)=1:NEXT:NEXT
210 REM VIC-IV: HOTREG off (B65 keeps it off: a hot-register recompute would move CHARPTR), V400 = $D031 bit 3 + 50 rows ($D07B) + 1 line per glyph row ($D05B); CHR16 + full colour >$FF, LINESTEP, CHRCOUNT, pointers
220 ?&FFD305D=?&FFD305D AND 127:?&FFD3031=?&FFD3031 OR 8:?&FFD307B=49:?&FFD305B=0:?&FFD3054=?&FFD3054 OR 5
230 PROCw(&FFD3058,L%):?&FFD305E=80:?&FFD3063=?&FFD3063 AND 192
240 ?&FFD3020=0:?&FFD3021=0:?&FFD3060=0:?&FFD3061=0:?&FFD3062=8:PROCw(&FFD3064,&4000)
245 REM object table at T%, N% objects; list mode v2 + fast RRB (keep 1 MB chip RAM)
250 ?&FFD71F8=0:?&FFD71F9=&C0:?&FFD71FA=8:?&FFD71FB=N%:?&FFD71F0=?&FFD71F0 OR &50
260 DIM X%(N%),Y%(N%),DX%(N%),DY%(N%)
270 FOR I%=0 TO N%-1:X%(I%)=20+RND(590):Y%(I%)=8+RND(380)
280 DX%(I%)=RND(3):DY%(I%)=RND(2):NEXT
290 REPEAT
300 FOR I%=0 TO N%-1
310 X%=X%(I%)+DX%(I%):IF X%<0 OR X%>630 THEN DX%(I%)=-DX%(I%):X%=X%(I%)+DX%(I%)
320 Y%=Y%(I%)+DY%(I%):IF Y%<0 OR Y%>391 THEN DY%(I%)=-DY%(I%):Y%=Y%(I%)+DY%(I%)
330 X%(I%)=X%:Y%(I%)=Y%:!(T%+16*I%)=X%+Y%*65536:REM X and Y in one 4-byte write
340 NEXT
350 UNTIL FALSE
360 DEF PROCw(A%,V%):?A%=V% AND 255:?(A%+1)=(V% DIV 256) AND 255:ENDPROC
