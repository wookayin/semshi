# pylint: disable=unidiomatic-typecheck
import ast
import contextlib
import sys
from itertools import count
from token import NAME, OP
from tokenize import tokenize

from .node import ATTRIBUTE, IMPORTED, PARAMETER_UNUSED, SELF, Node
from .util import debug_time

# PEP-695 type statement (Python 3.12+)
if sys.version_info >= (3, 12):
    TYPE_VARS = (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)
else:
    TYPE_VARS = ()

HAS_PY313 = sys.version_info >= (3, 13)

# Node types which introduce a new scope and child symboltable
BLOCKS = (
    ast.Module,
    ast.Lambda,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.GeneratorExp,
)
if sys.version_info < (3, 12):
    # PEP-709: comprehensions no longer have dedicated stack frames; the
    # comprehension's local will be included in the parent function's symtable
    # (Note: generator expressions are excluded in Python 3.12)
    BLOCKS = tuple([*BLOCKS, ast.ListComp, ast.DictComp, ast.SetComp])

FUNCTION_BLOCKS = (ast.FunctionDef, ast.Lambda, ast.AsyncFunctionDef)

# Node types which don't require any action
if sys.version_info < (3, 8):
    SKIP = (ast.NameConstant, ast.Str, ast.Num)
else:
    from ast import Constant  # pylint: disable=ungrouped-imports
    SKIP = (Constant, )
SKIP += (ast.Store, ast.Load, \
         ast.Eq, ast.Lt, ast.Gt, ast.NotEq, ast.LtE, ast.GtE)


def tokenize_lines(lines):
    return tokenize(((line + '\n').encode('utf-8') for line in lines).__next__)


def advance(tokens, s=None, type=NAME):
    """Advance token stream `tokens`.

    Advances to next token of type `type` with the string representation `s` or
    matching one of the strings in `s` if `s` is an iterable. Without any
    arguments, just advances to next NAME token.
    """
    if s is None:
        cond = lambda token: True
    elif isinstance(s, str):
        cond = lambda token: token.string == s
    else:
        cond = lambda token: token.string in s
    return next(t for t in tokens if t.type == type and cond(t))


@debug_time
def visitor(lines, symtable_root, ast_root):
    visitor = Visitor(lines, symtable_root)
    visitor.visit(ast_root)
    return visitor.nodes


class Visitor:
    """The visitor visits the AST recursively to extract relevant name nodes in
    their context.
    """

    def __init__(self, lines, root_table):
        self._lines = lines
        self._table_stack = [root_table]
        self._env = []
        # Holds a copy of the current environment to avoid repeated copying
        self._cur_env = None
        self.nodes = []

    def visit(self, node):
        """Recursively visit the node to build a list of names in their scopes.

        In some contexts, nodes appear in a different order than the scopes are
        nested. In that case, attributes of a node might be visitied before
        creating a new scope and deleted afterwards so they are not revisited
        later.
        """
        # Use type() because it's faster than the more idiomatic isinstance()
        type_ = type(node)
        if type_ is ast.Name:
            self._new_name(node)
            return
        if type_ in TYPE_VARS:  # handle type variables (Python 3.12+)
            self._visit_typevar(node)
            return
        if type_ is ast.Attribute:
            self._add_attribute(node)
            self.visit(node.value)
            return
        if type_ in SKIP:
            return

        if type_ is ast.Try:
            self._visit_try(node)
        elif type_ is ast.ExceptHandler:
            self._visit_except(node)
        elif type_ in (ast.Import, ast.ImportFrom):
            self._visit_import(node)
        elif type_ is ast.arg:
            self._visit_arg(node)
        elif type_ in FUNCTION_BLOCKS:
            self._visit_arg_defaults(node)
        elif type_ in (ast.ListComp, ast.SetComp, ast.DictComp,
                       ast.GeneratorExp):
            self._visit_comp(node)
        elif type_ in (ast.Global, ast.Nonlocal):
            keyword = 'global' if type_ is ast.Global else 'nonlocal'
            self._visit_global_nonlocal(node, keyword)
        elif type_ is ast.keyword:
            pass
        elif TYPE_VARS and type_ is ast.TypeAlias:  # Python 3.12+
            self._visit_type(node)
            return  # scope already handled

        if type_ in (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef):
            self._visit_class_function_definition(node)

        # Either make a new block scope...
        if type_ in BLOCKS:
            with self._enter_scope() as current_table:
                if type_ in FUNCTION_BLOCKS:
                    current_table.unused_params = {}
                    self._iter_node(node)
                    # Set the hl group of all parameters that didn't appear in the
                    # function body to "unused parameter".
                    for param in current_table.unused_params.values():
                        if param.hl_group == SELF:
                            # SELF args should never be shown as unused
                            continue
                        param.hl_group = PARAMETER_UNUSED
                        param.update_tup()
                else:
                    self._iter_node(node)
        # ...or just iterate through the node's (remaining) attributes.
        else:
            self._iter_node(node)

    @contextlib.contextmanager
    def _enter_scope(self):
        # Enter a local lexical variable scope (env represented by symtables).
        current_table = self._table_stack.pop()
        # The order of children symtables is not guaranteed and in fact
        # differs between CPython 3.13+ and prior versions. Sorting them in
        # the order they appear ensures consistency with AST visitation.
        children = sorted(current_table.get_children(),
                          key=lambda st: st.get_lineno())
        self._table_stack += reversed(children)
        self._env.append(current_table)
        self._cur_env = self._env[:]
        yield current_table
        self._env.pop()
        self._cur_env = self._env[:]

    def _new_name(self, node):
        self.nodes.append(Node(
            node.id,
            node.lineno,
            node.col_offset,
            self._cur_env,
            # Using __dict__.get() is faster than getattr()
            node.__dict__.get('_target'),
        )) # yapf: disable

    def _visit_arg(self, node):
        """Visit function argument."""
        node = Node(node.arg, node.lineno, node.col_offset, self._cur_env)
        self.nodes.append(node)
        # Register as unused parameter for now. The entry is removed if it's
        # found to be used later.
        self._env[-1].unused_params[node.name] = node

    def _visit_arg_defaults(self, node):
        """Visit argument default values."""
        for arg_ in node.args.defaults + node.args.kw_defaults:
            self.visit(arg_)
        del node.args.defaults
        del node.args.kw_defaults

    def _visit_try(self, node):
        """Visit try-except."""
        for child in node.body:
            self.visit(child)
        del node.body
        for child in node.handlers:
            self.visit(child)
        del node.handlers
        for child in node.orelse:
            self.visit(child)
        del node.orelse
        for child in node.finalbody:
            self.visit(child)
        del node.finalbody

    def _visit_except(self, node):
        """Visit except branch."""
        if node.name is None:
            # There is no "as ..." branch, so don't do anything.
            return
        # We can't really predict the line for "except-as", so we must always
        # tokenize.
        line_idx = node.lineno - 1
        tokens = tokenize_lines(self._lines[i] for i in count(line_idx))
        advance(tokens, 'as')
        token = advance(tokens)
        lineno = token.start[0] + line_idx
        cur_line = self._lines[lineno - 1]
        self.nodes.append(Node(
            node.name,
            lineno,
            len(cur_line[:token.start[1]].encode('utf-8')),
            self._cur_env,
        ))  # yapf: disable

    def _visit_comp(self, node):
        """Visit set/dict/list comprehension or generator expression."""
        generator = node.generators[0]
        self.visit(generator.iter)
        del generator.iter

    def _visit_class_meta(self, node):
        """Visit class bases and keywords."""
        for base in node.bases:
            self.visit(base)
        del node.bases
        for keyword in node.keywords:
            self.visit(keyword)
        del node.keywords

    def _visit_args(self, node):
        """Visit function arguments."""
        # We'd want to visit args.posonlyargs, but it appears an internal bug
        # is preventing that. See: https://stackoverflow.com/q/59066024/5765873
        for arg in node.args.posonlyargs:
            del arg.annotation
        self._visit_args_pre38(node)

    def _visit_args_pre38(self, node):
        # args: ast.arguments
        args = node.args
        for arg in args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
            if arg is None:
                continue
            self.visit(arg.annotation)
            del arg.annotation
        self.visit(node.returns)
        del node.returns

    def _visit_import(self, node):
        """Visit import statement.

        Unlike other nodes in the AST, names in import statements don't come
        with a specified line number and column. Therefore, we need to use the
        tokenizer on that part of the code to get the exact position. Since
        using the tokenize module is slow, we only use it where absolutely
        necessary.
        """
        line_idx = node.lineno - 1
        # We first try to guess the import line to avoid having to use the
        # tokenizer. This will fail in some cases as we just cover the most
        # common import syntax.
        name = node.names[0].name
        asname = node.names[0].asname
        target = asname or name
        if target != '*' and '.' not in target:
            guess = 'import ' + name + (' as ' + asname if asname else '')
            if isinstance(node, ast.ImportFrom):
                guess = 'from ' + (node.module or node.level * '.') + ' ' + \
                        guess
            if self._lines[line_idx] == guess:
                self.nodes.append(Node(
                    target,
                    node.lineno,
                    len(guess.encode('utf-8')) - len(target.encode('utf-8')),
                    self._cur_env,
                    None,
                    IMPORTED,
                ))  # yapf: disable
                return
        # Guessing the line failed, so we need to use the tokenizer
        tokens = tokenize_lines(self._lines[i] for i in count(line_idx))
        while True:
            # Advance to next "import" keyword
            token = advance(tokens, 'import')
            cur_line = self._lines[line_idx + token.start[0] - 1]
            # Determine exact byte offset. token.start[1] just holds the char
            # index which may give a wrong position.
            offset = len(cur_line[:token.start[1]].encode('utf-8'))
            # ...until we found the matching one.
            if offset >= node.col_offset:
                break
        for alias, more in zip(node.names, count(1 - len(node.names))):
            if alias.name == '*':
                continue
            # If it's an "as" alias import...
            if alias.asname is not None:
                # ...advance to "as" keyword.
                advance(tokens, 'as')
            token = advance(tokens)
            cur_line = self._lines[line_idx + token.start[0] - 1]
            self.nodes.append(Node(
                token.string,
                token.start[0] + line_idx,
                # Exact byte offset of the token
                len(cur_line[:token.start[1]].encode('utf-8')),
                self._cur_env,
                None,
                IMPORTED,
            ))  # yapf: disable

            # If there are more imports in that import statement...
            if more:
                # ...they must be comma-separated, so advance to next comma.
                advance(tokens, ',', OP)

    def _visit_class_function_definition(self, node):
        """Visit class or function definition.

        We need to use the tokenizer here for the same reason as in
        _visit_import (no line/col for names in class/function definitions).
        """
        # node: ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef
        decorators = node.decorator_list
        for decorator in decorators:
            self.visit(decorator)
        del node.decorator_list
        line_idx = node.lineno - 1
        # Guess offset of the name (length of the keyword + 1)
        start = node.col_offset + (6 if type(node) is ast.ClassDef else 4)
        stop = start + len(node.name)
        # If the node has no decorators and its name appears directly after the
        # definition keyword, we found its position and don't need to tokenize.
        if not decorators and self._lines[line_idx][start:stop] == node.name:
            lineno = node.lineno
            column = start
        else:
            tokens = tokenize_lines(self._lines[i] for i in count(line_idx))
            advance(tokens, ('class', 'def'))
            token = advance(tokens)
            lineno = token.start[0] + line_idx
            column = token.start[1]
        self.nodes.append(Node(node.name, lineno, column, self._cur_env))

        # Handling type parameters & generic syntax (Python 3.12+)
        # When generic type vars are present, a new scope is added
        _type_params = node.type_params if TYPE_VARS else None
        with (self._enter_scope() if _type_params  # ...
              else contextlib.nullcontext()):
            if _type_params:
                for p in _type_params:
                    self.visit(p)
                del node.type_params  # Don't visit again later

            # Visit class meta (parent class), argument type hints, etc.
            if type(node) is ast.ClassDef:
                self._visit_class_meta(node)
            else:
                self._visit_args(node)
                self._mark_self(node)

    def _visit_global_nonlocal(self, node, keyword):
        line_idx = node.lineno - 1
        line = self._lines[line_idx]
        indent = line[:-len(line.lstrip())]
        if line == indent + keyword + ' ' + ', '.join(node.names):
            offset = len(indent) + len(keyword) + 1
            for name in node.names:
                self.nodes.append(Node(
                    name,
                    node.lineno,
                    offset,
                    self._cur_env,
                )) # yapf: disable
                # Add 2 bytes for the comma and space
                offset += len(name.encode('utf-8')) + 2
            return
        # Couldn't guess line, so we need to tokenize.
        tokens = tokenize_lines(self._lines[i] for i in count(line_idx))
        # Advance to global/nonlocal statement
        advance(tokens, keyword)
        for name, more in zip(node.names, count(1 - len(node.names))):
            token = advance(tokens)
            cur_line = self._lines[line_idx + token.start[0] - 1]
            self.nodes.append(Node(
                token.string,
                token.start[0] + line_idx,
                len(cur_line[:token.start[1]].encode('utf-8')),
                self._cur_env,
            )) # yapf: disable
            # If there are more declared names...
            if more:
                # ...advance to next comma.
                advance(tokens, ',', OP)

    def _visit_type(self, node):
        """Visit type statement (PEP-695)."""
        # e.g. type MyList[T_var] = list[T_var]
        #           ^^^^^^ ^^^^^         ^ reference to typevar
        #           name   typevar
        # Visit alias name in the outer scope
        self.visit(node.name)

        # The type statement has two variable scopes: one for typevar (if any),
        # and another one (a child scope) for the rhs
        maybe_scope = (self._enter_scope() if node.type_params \
                       else contextlib.nullcontext())
        with maybe_scope:
            for p in node.type_params:
                self.visit(p)
            with self._enter_scope():
                self.visit(node.value)

    def _visit_typevar(self, node):
        # node: ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple
        self.nodes.append(
            Node(
                node.name,
                node.lineno,
                node.col_offset,
                self._cur_env,
            ))

        # When a TypeVar has a bound or a default value,
        # e.g. `T: T_Bound = T_Default`, each expression (bound and/or default)
        # introduces a new inner lexical scope for the type variable.
        bound = node.bound if type(node) is ast.TypeVar else None
        default_value = node.default_value if HAS_PY313 else None

        if bound:
            with self._enter_scope():
                self.visit(bound)

        if default_value:
            with self._enter_scope():
                self.visit(default_value)

    def _mark_self(self, node):
        """Mark self/cls argument if the current function has one.

        Determine if an argument is a self argument (the first argument of a
        method called "self" or "cls") and add a reference in the function's
        symtable.
        """
        # The first argument...
        try:
            # TODO Does this break with posonlyargs?
            arg = node.args.args[0]
        except IndexError:
            return
        # ...with a special name...
        if arg.arg not in ('self', 'cls'):
            return
        # ...and a class as parent scope is a self_param.
        if not self._env[-1].get_type() == 'class':
            return
        # Let the table for the current function scope remember the param
        self._table_stack[-1].self_param = arg.arg

    def _add_attribute(self, node):
        """Add node as an attribute.

        The only relevant attributes are attributes to self or cls in a
        method (e.g. "self._name").
        """
        # Node must be an attribute of a name (foo.attr, but not [].attr)
        if type(node.value) is not ast.Name:
            return
        target_name = node.value.id
        # Redundant, but may spare us the getattr() call in the next step
        if target_name not in ('self', 'cls'):
            return
        # Only register attributes of self/cls parameter
        if target_name != getattr(self._env[-1], 'self_param', None):
            return
        new_node = Node(
            node.attr,
            node.value.lineno,
            node.value.col_offset + len(target_name) + 1,
            self._env[:-1],
            None,  # target
            ATTRIBUTE,
        )
        node.value._target = new_node  # pylint: disable=protected-access
        self.nodes.append(new_node)

    def _iter_node(self, node):
        """Iterate through fields of the node."""
        if node is None:
            return
        for field in node._fields:
            value = node.__dict__.get(field, None)
            if value is None:
                continue
            value_type = type(value)
            if value_type is list:
                for item in value:
                    if isinstance(item, str):
                        continue
                    self.visit(item)
            # We would want to use isinstance(value, AST) here. Not sure how
            # much more expensive that is, though.
            elif value_type not in (str, int, bytes, bool):
                self.visit(value)


if sys.version_info < (3, 8):
    # pylint: disable=protected-access
    Visitor._visit_args = Visitor._visit_args_pre38
