" *.s / *.asm / *.inc / *.mac are ca65 sources (MEGA65 cross-development)
" filetype.vim's own rules run before this one (*.s -> asm).  vim 9.0's ftplugin/asm.vim (Debian 12)
" sets b:did_ftplugin without a b:undo_ftplugin, so the flag survives the switch to ca65 and
" ftplugin/ca65.vim would skip itself (no F5 / F7 / K): drop it before switching
au BufRead,BufNewFile *.s,*.asm,*.inc,*.mac unlet! b:did_ftplugin | set filetype=ca65
