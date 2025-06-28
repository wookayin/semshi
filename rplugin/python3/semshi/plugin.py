from __future__ import annotations

import sys
from functools import partial, wraps
from typing import TYPE_CHECKING, List, Optional, Sequence, cast

if TYPE_CHECKING:
    from typing import Literal  # for py37

import pynvim
import pynvim.api

from .handler import BufferHandler
from .node import hl_groups

# pylint: disable=consider-using-f-string

_subcommands = {}


def subcommand(func=None, needs_handler=False, silent_fail=True):
    """Decorator to register `func` as a ":Semshi [...]" subcommand.

    If `needs_handler`, the subcommand will fail if no buffer handler is
    currently active. If `silent_fail`, it will fail silently, otherwise an
    error message is printed.
    """
    if func is None:
        return partial(subcommand,
                       needs_handler=needs_handler,
                       silent_fail=silent_fail)

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # pylint: disable=protected-access
        if self._options is None:
            self._init_with_vim()
        if needs_handler and self._cur_handler is None:
            if not silent_fail:
                self.echo_error('Semshi is not enabled in this buffer!')
            return
        func(self, *args, **kwargs)

    _subcommands[func.__name__] = wrapper
    return wrapper


@pynvim.plugin
class Plugin:
    """Semshi Neovim plugin.

    The plugin handles vim events and commands, and delegates them to a buffer
    handler. (Each buffer is handled by a semshi.BufferHandler instance.)
    """

    def __init__(self, vim: pynvim.api.Nvim):
        self._vim = vim

        # A mapping (buffer number -> buffer handler)
        self._handlers = {}
        # The currently active buffer handler
        self._cur_handler: Optional[BufferHandler] = None
        self._options = None

        # Python version check
        if (3, 7) <= sys.version_info <= (3, 13, 9999):
            self._disabled = False
        else:
            self._disabled = True
            self.echom("Semshi currently supports Python 3.7 - 3.13. " +
                       "(Current: {})".format(sys.version.split()[0]))

    def echom(self, msg: str):
        args = ([[msg, "WarningMsg"]], True, {})
        self._vim.api.echo(*args)

    def _init_with_vim(self):
        """Initialize with vim available.

        Initialization code which interacts with vim can't be safely put in
        __init__ because vim itself may not be fully started up.
        """
        self._options = Options(self._vim)

    def echo(self, *msgs):
        msg = ' '.join([str(m) for m in msgs])
        self._vim.out_write(msg + '\n')

    def echo_error(self, *msgs):
        msg = ' '.join([str(m) for m in msgs])
        self._vim.err_write(msg + '\n')

    # Must not be async here because we have to make sure that switching the
    # buffer handler is completed before other events are handled.
    @pynvim.function('SemshiBufEnter', sync=True)
    def event_buf_enter(self, args):
        buf_num, view_start, view_stop = args
        self._select_handler(buf_num)
        assert self._cur_handler is not None
        self._update_viewport(view_start, view_stop)
        self._cur_handler.update()
        self._mark_selected()

    @pynvim.function('SemshiBufLeave', sync=True)
    def event_buf_leave(self, _):
        self._cur_handler = None

    @pynvim.function('SemshiBufWipeout', sync=True)
    def event_buf_wipeout(self, args):
        self._remove_handler(args[0])

    @pynvim.function('SemshiVimResized', sync=False)
    def event_vim_resized(self, args):
        self._update_viewport(*args)
        self._mark_selected()

    @pynvim.function('SemshiCursorMoved', sync=False)
    def event_cursor_moved(self, args):
        if self._cur_handler is None:
            # CursorMoved may trigger before BufEnter, so select the buffer if
            # we didn't enter it yet.
            self.event_buf_enter((self._vim.current.buffer.number, *args))
            return
        self._update_viewport(*args)
        self._mark_selected()

    @pynvim.function('SemshiTextChanged', sync=False)
    def event_text_changed(self, _):
        if self._cur_handler is None:
            return
        # Note: TextChanged event doesn't trigger if text was changed in
        # unfocused buffer via e.g. nvim_buf_set_lines().
        self._cur_handler.update()

    @pynvim.autocmd('VimLeave', sync=True)
    def event_vim_leave(self):
        for handler in self._handlers.values():
            handler.shutdown()

    @pynvim.command(
        'Semshi',
        nargs='*',  # type: ignore
        complete='customlist,SemshiComplete',
        sync=True,
    )
    def cmd_semshi(self, args):
        if not args:
            filetype = cast(pynvim.api.Buffer,
                            self._vim.current.buffer).options.get('filetype')
            py_filetypes = self._vim.vars.get('semshi#filetypes', [])
            if filetype in py_filetypes:  # for python buffers
                self._vim.command('Semshi status')
            else:  # non-python
                self.echo('This is semshi.')
            return

        try:
            func = _subcommands[args[0]]
        except KeyError:
            self.echo_error('Subcommand not found: %s' % args[0])
            return
        func(self, *args[1:])

    @staticmethod
    @pynvim.function('SemshiComplete', sync=True)
    def func_complete(arg):
        lead, *_ = arg
        return [c for c in _subcommands if c.startswith(lead)]

    @pynvim.function('SemshiInternalEval', sync=True)
    def _internal_eval(self, args):
        """Eval Python code in plugin context.

        Only used for testing.
        """
        plugin = self  # noqa pylint: disable=unused-variable
        return eval(args[0])  # pylint: disable=eval-used

    @subcommand
    def enable(self):
        if self._disabled:
            return
        self._attach_listeners()
        self._select_handler(self._vim.current.buffer)
        self._update_viewport(*self._vim.eval('[line("w0"), line("w$")]'))
        self.highlight()

    @subcommand(needs_handler=True)
    def disable(self):
        self.clear()
        self._detach_listeners()
        self._cur_handler = None
        self._remove_handler(self._vim.current.buffer)

    @subcommand
    def toggle(self):
        if self._listeners_attached():
            self.disable()
        else:
            self.enable()

    @subcommand(needs_handler=True)
    def pause(self):
        self._detach_listeners()

    @subcommand(needs_handler=True, silent_fail=False)
    def highlight(self):
        assert self._cur_handler
        self._cur_handler.update(force=True, sync=True)

    @subcommand(needs_handler=True)
    def clear(self):
        assert self._cur_handler
        self._cur_handler.clear_highlights()

    @subcommand(needs_handler=True, silent_fail=False)
    def rename(self, new_name=None):
        assert self._cur_handler
        self._cur_handler.rename(self._vim.current.window.cursor, new_name)

    @subcommand(needs_handler=True, silent_fail=False)
    def goto(self, *args, **kwargs):
        assert self._cur_handler
        self._cur_handler.goto(*args, **kwargs)

    @subcommand(needs_handler=True, silent_fail=False)
    def error(self):
        assert self._cur_handler
        self._cur_handler.show_error()

    @subcommand
    def status(self):
        if self._disabled:
            self.echo('Semshi is disabled: unsupported python version.')
            return

        buffer: pynvim.api.Buffer = self._vim.current.buffer
        attached: bool = buffer.vars.get('semshi_attached', False)

        syntax_error = '(not attached)'
        if self._cur_handler:
            syntax_error = str(self._cur_handler.syntax_error or '(none)')

        self.echo('\n'.join([
            'Semshi is {attached} on (bufnr={bufnr})',
            '- current handler: {handler}',
            '- handlers: {handlers}',
            '- syntax error: {syntax_error}',
        ]).format(
            attached=attached and "attached" or "detached",
            bufnr=str(buffer.number),
            handler=self._cur_handler,
            handlers=self._handlers,
            syntax_error=syntax_error,
        ))

    def _select_handler(self, buf_or_buf_num):
        """Select handler for `buf_or_buf_num`."""
        if isinstance(buf_or_buf_num, int):
            buf = None
            buf_num = buf_or_buf_num
        else:
            buf = buf_or_buf_num
            buf_num = buf.number
        try:
            handler = self._handlers[buf_num]
        except KeyError:
            if buf is None:
                buf = self._vim.buffers[buf_num]
            assert self._options is not None, "must have been initialized"
            handler = BufferHandler(buf, self._vim, self._options)
            self._handlers[buf_num] = handler
        self._cur_handler = handler

    def _remove_handler(self, buf_or_buf_num):
        """Remove handler for buffer with the number `buf_num`."""
        if isinstance(buf_or_buf_num, int):
            buf_num = buf_or_buf_num
        else:
            buf_num = buf_or_buf_num.number
        try:
            handler = self._handlers.pop(buf_num)
        except KeyError:
            return
        else:
            handler.shutdown()

    def _update_viewport(self, start, stop):
        if self._cur_handler:
            self._cur_handler.viewport(start, stop)

    def _mark_selected(self):
        assert self._options is not None, "must have been initialized"
        if not self._options.mark_selected_nodes:
            return
        try:
            handler = self._cur_handler
            if handler:
                cursor = self._vim.current.window.cursor
                handler.mark_selected(cursor)
        except pynvim.api.NvimError as ex:
            # Ignore "Invalid window ID" errors (see wookayin/semshi#3)
            if str(ex).startswith("Invalid window id:"):
                return

            raise ex  # Re-raise other errors.

    def _attach_listeners(self):
        self._vim.call('semshi#buffer_attach')

    def _detach_listeners(self):
        self._vim.call('semshi#buffer_detach')

    def _listeners_attached(self):
        """Return whether event listeners are attached to the current buffer.
        """
        return self._vim.eval('get(b:, "semshi_attached", v:false)')


class Options:
    """Plugin options.

    The options will only be read and set once on init.
    """
    _defaults = {
        'filetypes': ['python'],
        'excluded_hl_groups': ['local'],
        'mark_selected_nodes': 1,
        'no_default_builtin_highlight': True,
        'simplify_markup': True,
        'error_sign': True,
        'error_sign_delay': 1.5,
        'always_update_all_highlights': False,
        'tolerate_syntax_errors': True,
        'update_delay_factor': .0,
        'self_to_attribute': True,
    }
    filetypes: List[str]
    excluded_hl_groups: List[str]
    mark_selected_nodes: Literal[0, 1, 2]
    no_default_builtin_highlight: bool
    simplify_markup: bool
    error_sign: bool
    error_sign_delay: float
    always_update_all_highlights: bool
    tolerate_syntax_errors: bool
    update_delay_factor: float
    self_to_attribute: bool

    def __init__(self, vim: pynvim.api.Nvim):
        for key, val_default in Options._defaults.items():
            val = vim.vars.get('semshi#' + key, val_default)
            # vim.vars doesn't support setdefault(), so set value manually
            vim.vars['semshi#' + key] = val
            try:
                converter = getattr(Options, '_convert_' + key)
            except AttributeError:
                pass
            else:
                val = converter(val)
            setattr(self, key, val)

    @staticmethod
    def _convert_excluded_hl_groups(items: Sequence[str]) -> List[str]:
        try:
            return [hl_groups[g] for g in items]
        except KeyError as e:
            # TODO Use err_write instead?
            raise ValueError(
                f'"{e.args[0]}" is an unknown highlight group.') from e
