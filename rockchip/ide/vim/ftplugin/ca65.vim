" ca65 / MEGA65 cross-development in vim
"   F5  build and run on the MEGA65 (make run -> m65run; back here: Ctrl+Alt+T)
"   F7  build                  F8 / F9  next / previous error      K  45GS02 instruction help
if exists("b:did_ftplugin")
  finish
endif
let b:did_ftplugin = 1

setlocal makeprg=make
" ca65: 'file.s:12: Error: ...' / 'Warning: ...'; ld65 messages without a line are kept as text
setlocal errorformat=%f:%l:\ %trror:\ %m,%f:%l:\ %tarning:\ %m,%+Gld65:%m,%+Gmake:%m,%-G%.%#
setlocal keywordprg=m65doc
setlocal commentstring=;\ %s
setlocal iskeyword+=.,@-@

function! s:M65Make(target) abort
  silent wall
  execute 'silent make ' . a:target
  redraw!
  " not v:shell_error: :make pipes through tee, whose exit status that is
  if !empty(filter(getqflist(), 'v:val.valid'))
    copen
    echohl ErrorMsg | echo 'build failed (F8/F9: errors)' | echohl None
  else
    cclose
    echo a:target ==# 'run' ? 'running on the MEGA65 -- Ctrl+Alt+T comes back here' : 'build ok'
  endif
endfunction

nnoremap <buffer> <silent> <F5> :call <SID>M65Make('run')<CR>
nnoremap <buffer> <silent> <F7> :call <SID>M65Make('')<CR>
nnoremap <buffer> <silent> <F8> :cnext<CR>
nnoremap <buffer> <silent> <F9> :cprevious<CR>
inoremap <buffer> <silent> <F5> <Esc>:call <SID>M65Make('run')<CR>
inoremap <buffer> <silent> <F7> <Esc>:call <SID>M65Make('')<CR>
