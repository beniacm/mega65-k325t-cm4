" ca65 / MEGA65 cross-development in vim
"   F5  build and run on the MEGA65 (make run -> m65run; back here: Ctrl+Alt+T)
"   F7  build                  F8 / F9  next / previous error      K  45GS02 instruction help
if exists("b:did_ftplugin")
  finish
endif
let b:did_ftplugin = 1

setlocal makeprg=make
" ca65: 'file.s:12: Error: ...' / 'Warning: ...'; ld65 messages without a line are kept as text
" only make's failure lines ('make: *** [...] Error 1') count, not 'is up to date' or 'Nothing to be done'
setlocal errorformat=%f:%l:\ %trror:\ %m,%f:%l:\ %tarning:\ %m,%+Gld65:%m,%+Gmake:%.%#Error%.%#,%-G%.%#
setlocal keywordprg=m65doc
setlocal commentstring=;\ %s
setlocal iskeyword+=.,@-@

nnoremap <buffer> <silent> <F5> :call M65Make('run')<CR>
nnoremap <buffer> <silent> <F7> :call M65Make('')<CR>
nnoremap <buffer> <silent> <F8> :cnext<CR>
nnoremap <buffer> <silent> <F9> :cprevious<CR>
inoremap <buffer> <silent> <F5> <Esc>:call M65Make('run')<CR>
inoremap <buffer> <silent> <F7> <Esc>:call M65Make('')<CR>
