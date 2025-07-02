" plugin/semshi.vim
" vim: set ts=4 sts=4 sw=4:

" These options can't be initialized in the Python plugin since they must be
" known immediately.
let g:semshi#filetypes = get(g:, 'semshi#filetypes', ['python'])
let g:semshi#simplify_markup = get(g:, 'semshi#simplify_markup', v:true)
let g:semshi#no_default_builtin_highlight = get(g:, 'semshi#no_default_builtin_highlight', v:true)

function! s:simplify_markup()
    autocmd FileType python call s:simplify_markup_extra()

    " For python-syntax plugin
    let g:python_highlight_operators = 0
endfunction

function! s:simplify_markup_extra()
    hi link pythonConditional pythonStatement
    hi link pythonImport pythonStatement
    hi link pythonInclude pythonStatement
    hi link pythonRaiseFromStatement pythonStatement
    hi link pythonDecorator pythonStatement
    hi link pythonException pythonStatement
    hi link pythonConditional pythonStatement
    hi link pythonRepeat pythonStatement
endfunction

function! s:disable_builtin_highlights()
    autocmd FileType python call s:remove_builtin_extra()
    let g:python_no_builtin_highlight = 1
    hi link pythonBuiltin NONE
    let g:python_no_exception_highlight = 1
    hi link pythonExceptions NONE
    hi link pythonAttribute NONE
    hi link pythonDecoratorName NONE

    " For python-syntax plugin
    let g:python_highlight_class_vars = 0
    let g:python_highlight_builtins = 0
    let g:python_highlight_exceptions = 0
    hi link pythonDottedName NONE
endfunction

function! s:remove_builtin_extra()
    syn keyword pythonKeyword True False None
    hi link pythonKeyword pythonNumber
endfunction

" Ensure the rplugin manifest
function! s:check_rplugin_manifest() abort
    if exists('s:semshi_rplugin_error') > 0
        return v:false
    endif
    if exists(':Semshi') > 0
        return v:true
    endif
    let s:semshi_rplugin_error = 1
    command! -nargs=* Semshi call nvim_err_writeln(":Semshi not found. Run :UpdateRemotePlugins.")

    " notify with an asynchronous error message
    if exists(':lua') && has('nvim-0.5.0') > 0
lua << EOF
      vim.schedule(function()
        vim.notify(":Semshi not found. Run :UpdateRemotePlugins.", 'ERROR', { title = "semshi" })
      end)
EOF
    endif
    return v:false
endfunction

function! s:filetype_changed() abort
    if !s:check_rplugin_manifest()
        " Avoid exceptions inside FileType autocmd, because the stacktrace is ugly.
        " Instead, an asynchronous notification that something is broken will be made.
        return
    endif

    let l:ft = expand('<amatch>')
    if index(g:semshi#filetypes, l:ft) != -1
        if !get(b:, 'semshi_attached', v:false)
            Semshi enable
        endif
    else
        if get(b:, 'semshi_attached', v:false)
            Semshi disable
        endif
    endif
endfunction

lua<<EOF
function _G._semshi_get_viewports()
  local buffer_windows = vim.fn.win_findbuf(vim.fn.bufnr())
  return vim.tbl_map(function(w)
    return vim.api.nvim_win_call(w,
      function()
        return {start=vim.fn.line("w0"), ['end']=vim.fn.line("w$")}
      end)
    end,
    buffer_windows)
end
EOF

function! semshi#buffer_attach()
    if get(b:, 'semshi_attached', v:false)
        return
    endif
    let b:semshi_attached = v:true
    augroup SemshiEvents
        autocmd! * <buffer>
        autocmd BufEnter <buffer> call SemshiBufEnter(+expand('<abuf>'), v:lua._semshi_get_viewports())
        autocmd BufLeave <buffer> call SemshiBufLeave()
        autocmd VimResized <buffer> call SemshiVimResized(v:lua._semshi_get_viewports())
        autocmd TextChanged <buffer> call SemshiTextChanged()
        autocmd TextChangedI <buffer> call SemshiTextChanged()
        autocmd CursorMoved <buffer> call SemshiCursorMoved(v:lua._semshi_get_viewports())
        autocmd CursorMovedI <buffer> call SemshiCursorMoved(v:lua._semshi_get_viewports())
    augroup END
    call SemshiBufEnter(bufnr('%'), v:lua._semshi_get_viewports())
endfunction

function! semshi#buffer_detach()
    let b:semshi_attached = v:false
    augroup SemshiEvents
        autocmd! * <buffer>
    augroup END
endfunction

function! semshi#buffer_wipeout()
    try
        call SemshiBufWipeout(+expand('<abuf>'))
    catch /:E117:/
        " UpdateRemotePlugins probably not done yet, ignore
    endtry
endfunction

function! semshi#init()
    hi def semshiLocal           ctermfg=209 guifg=#ff875f
    hi def semshiGlobal          ctermfg=214 guifg=#ffaf00
    hi def semshiImported        ctermfg=214 guifg=#ffaf00 cterm=bold gui=bold
    hi def semshiParameter       ctermfg=75  guifg=#5fafff
    hi def semshiParameterUnused ctermfg=117 guifg=#87d7ff cterm=underline gui=underline
    hi def semshiFree            ctermfg=218 guifg=#ffafd7
    hi def semshiBuiltin         ctermfg=207 guifg=#ff5fff
    hi def semshiAttribute       ctermfg=49  guifg=#00ffaf
    hi def semshiSelf            ctermfg=249 guifg=#b2b2b2
    hi def semshiUnresolved      ctermfg=226 guifg=#ffff00 cterm=underline gui=underline
    hi def semshiSelected        ctermfg=231 guifg=#ffffff ctermbg=161 guibg=#d7005f

    hi def semshiErrorSign       ctermfg=231 guifg=#ffffff ctermbg=160 guibg=#d70000
    hi def semshiErrorChar       ctermfg=231 guifg=#ffffff ctermbg=160 guibg=#d70000
    sign define semshiError text=E> texthl=semshiErrorSign

    augroup SemshiInit
        autocmd!
        if g:semshi#no_default_builtin_highlight
            call s:disable_builtin_highlights()
        endif
        if g:semshi#simplify_markup
            call s:simplify_markup()
        endif
        autocmd ColorScheme * call semshi#init()
        autocmd FileType * call s:filetype_changed()
        autocmd BufWipeout * call semshi#buffer_wipeout()
    augroup END
endfunction

call semshi#init()
