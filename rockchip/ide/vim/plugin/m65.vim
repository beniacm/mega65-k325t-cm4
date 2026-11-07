" MEGA65 cross-development: build (make) or build + run on the MEGA65 (make run -> m65run)
function! M65Make(target) abort
  silent wall
  execute 'silent make ' . a:target
  redraw!
  " not v:shell_error: :make pipes through tee, whose exit status that is
  if !empty(filter(getqflist(), 'v:val.valid'))
    copen
    echohl ErrorMsg | echo 'build failed (F8/F9: errors)' | echohl None
  else
    cclose
    echo a:target ==# 'run' ? 'running on the MEGA65 -- Ctrl+Alt+Insert comes back here' : 'build ok'
  endif
endfunction
