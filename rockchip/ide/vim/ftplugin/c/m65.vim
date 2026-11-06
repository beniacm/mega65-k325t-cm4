" C files of MEGA65 projects (Makefile uses mos-mega65-clang / m65run): F5 build + run on the
" MEGA65, F7 build, F8/F9 errors (vim's default errorformat reads clang's file:line:col: error:)
if !filereadable('Makefile') || match(readfile('Makefile'), 'mos-mega65\|m65run') < 0
  finish
endif
setlocal makeprg=make
nnoremap <buffer> <silent> <F5> :call M65Make('run')<CR>
nnoremap <buffer> <silent> <F7> :call M65Make('')<CR>
nnoremap <buffer> <silent> <F8> :cnext<CR>
nnoremap <buffer> <silent> <F9> :cprevious<CR>
inoremap <buffer> <silent> <F5> <Esc>:call M65Make('run')<CR>
inoremap <buffer> <silent> <F7> <Esc>:call M65Make('')<CR>
