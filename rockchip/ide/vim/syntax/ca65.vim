" Vim syntax: ca65 assembler for the MEGA65 (45GS02 = 6502 + 65CE02/4510 + 32-bit Q instructions)
if exists("b:current_syntax")
  finish
endif
syn case ignore

" 6502
syn keyword ca65Op adc and asl bcc bcs beq bit bmi bne bpl brk bvc bvs clc cld cli clv cmp cpx cpy
syn keyword ca65Op dec dex dey eor inc inx iny jmp jsr lda ldx ldy lsr nop ora pha php pla plp rol
syn keyword ca65Op ror rti rts sbc sec sed sei sta stx sty tax tay tsx txa txs tya
" 65C02 subset present in the 4510
syn keyword ca65Op bra phx phy plx ply stz trb tsb
syn match   ca65Op "\<\(bbr\|bbs\|rmb\|smb\)[0-7]\>"
" 65CE02 / 4510
syn keyword ca65Op4510 ldz cpz inz dez phz plz taz tza tab tba tsy tys cle see neg asr asw row
syn keyword ca65Op4510 dew inw phw bsr map eom rtn
syn keyword ca65Op4510 lbcc lbcs lbeq lbmi lbne lbpl lbra lbvc lbvs
" 45GS02: 32-bit Q = Z:Y:X:A
syn keyword ca65OpQ ldq stq adcq sbcq andq orq eorq cmpq cpq bitq aslq lsrq rolq rorq asrq inq deq

syn match   ca65Directive "\.[a-z_][a-z0-9_]*\>"
syn match   ca65Label     "^\s*[@a-z_][a-z0-9_@]*:"
syn match   ca65Label     "^\s*:"                          " unnamed label
syn match   ca65Register  "\(,\s*\|\[\s*\)\@<=[xyzs]\>"
syn match   ca65Register  "\<a\>\s*$"
syn match   ca65Hex       "\$\x\+\>"
syn match   ca65Bin       "%[01]\+\>"
syn match   ca65Dec       "\<\d\+\>"
syn match   ca65IO        "\$[dD][0-9a-fA-F]\{3}\>"        " MEGA65 I/O
syn match   ca65Imm       "#"
syn region  ca65String    start=+"+ skip=+\\"+ end=+"+ oneline
syn match   ca65Char      "'.'"
syn match   ca65Comment   ";.*$" contains=ca65Todo
syn keyword ca65Todo      contained TODO FIXME XXX NOTE

hi def link ca65Op        Statement
hi def link ca65Op4510    Statement
hi def link ca65OpQ       Special
hi def link ca65Directive PreProc
hi def link ca65Label     Function
hi def link ca65Register  Type
hi def link ca65Hex       Number
hi def link ca65Bin       Number
hi def link ca65Dec       Number
hi def link ca65IO        Constant
hi def link ca65Imm       Operator
hi def link ca65String    String
hi def link ca65Char      Character
hi def link ca65Comment   Comment
hi def link ca65Todo      Todo

let b:current_syntax = "ca65"
