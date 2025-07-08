"""Unit Tests for semshi.parser"""

# pylint: disable=protected-access

import sys
from pathlib import Path
from textwrap import dedent

import pytest

from semshi.node import (
    ATTRIBUTE,
    BUILTIN,
    FREE,
    GLOBAL,
    IMPORTED,
    LOCAL,
    PARAMETER,
    PARAMETER_UNUSED,
    SELF,
    UNRESOLVED,
    Node,
    group,
)
from semshi.parser import Parser, UnparsableError

from .conftest import make_parser, make_tree, parse

# top-level functions are parsed as LOCAL in python<3.7,
# but as GLOBAL in Python 3.8.
MODULE_FUNC = GLOBAL if sys.version_info >= (3, 8) else LOCAL

# Python 3.12: comprehensions no longer have their own variable scopes
# https://peps.python.org/pep-0709/
PEP_709 = sys.version_info >= (3, 12)


def test_group():
    assert group('foo') == 'semshiFoo'


def test_basic_name():
    assert [n.name for n in parse('x = 1')] == ['x']


def test_no_names():
    assert parse('') == []
    assert parse('pass') == []


def test_recursion_error():
    with pytest.raises(UnparsableError):
        parse(' + '.join(1000 * ['a']))


def test_syntax_error_fail():
    """Syntax errors which can't be fixed with a single change."""
    parser = Parser()
    with pytest.raises(UnparsableError):
        parser.parse('(\n(')
    with pytest.raises(UnparsableError):
        parser.parse(')\n(')
    # Intentionally no difference to previous one
    with pytest.raises(UnparsableError):
        parser.parse(')\n(')


def test_syntax_error_fail2():
    """Syntax errors which can't be fixed with a single change."""
    parser = make_parser('a\nb/')
    with pytest.raises(UnparsableError):
        parser.parse('a(\nb/')


def test_fixable_syntax_errors():
    """Test syntax errors where we can tokenize the erroneous line."""
    names = parse(r'''
        a  a = b in
        c
    ''')
    assert [n.pos for n in names] == [(2, 0), (2, 3), (2, 7), (3, 0)]


def test_fixable_syntax_errors2():
    """Test syntax errors where we can tokenize the last modified line."""
    parser = make_parser(r'''
        a
        b
    ''')
    parser.parse(dedent(r'''
        c(
        b
    '''))
    assert {n.name for n in parser._nodes} == {'c', 'b'}


@pytest.mark.xfail
def test_fixable_syntax_errors3():
    """Improved syntax fixing should be able to handle a bad symbol at the
    end of the erroneous line."""
    parser = make_parser('def foo(): x=1-')
    print(parser.syntax_errors[-1].offset)
    assert [n.hl_group for n in parser._nodes] == [LOCAL, LOCAL]
    print(parser._nodes)
    raise NotImplementedError()


def test_fixable_syntax_errors_indent():
    parser = make_parser('''def foo():\n \t \tx-''')
    assert parser._nodes[-1].pos == (2, 4)


def test_fixable_syntax_errors_misc():
    fix = Parser._fix_line
    assert fix('') == ''
    assert fix('(') == ''
    assert fix(' (x') == ' +x'
    assert fix(' .x') == ' +x'
    # The trailing whitespace shouldn't be there, but doesn't do any harm
    assert fix(' a .. ') == ' a '


def test_fixable_syntax_errors_attributes():
    fix = Parser._fix_line
    assert fix('foo bar . . baz') == \
               'foo+bar .   baz'
    assert fix('(foo.bar . baz  qux ( . baar') == \
               '+foo.bar . baz++qux   . baar'
    # Doesn't matter that we don't preserve tabs because we only want offsets
    assert fix('def foo.bar( + 1\t. 0 ... .1 spam . ham \t .eggs..') == \
               '++++foo.bar      .          spam . ham   .eggs'


def test_syntax_error_cycle():
    parser = make_parser('')
    assert parser.syntax_errors[-2] is None
    assert parser.syntax_errors[-1] is None
    parser.parse('1+')
    assert parser.syntax_errors[-2] is None
    assert parser.syntax_errors[-1].lineno == 1
    parser.parse('1+1')
    assert parser.syntax_errors[-2].lineno == 1
    assert parser.syntax_errors[-1] is None
    with pytest.raises(UnparsableError):
        parser.parse('\n+\n+')
    assert parser.syntax_errors[-2] is None
    assert parser.syntax_errors[-1].lineno == 2


def test_detect_symtable_syntax_error():
    """Some syntax errors (such as duplicate parameter names) aren't directly
    raised when compile() is called on the code, but cause problems later.
    """
    parser = Parser()
    with pytest.raises(UnparsableError):
        parser.parse('def foo(x, x): pass')
    assert parser.syntax_errors[-1].lineno == 1


def test_name_len():
    """Name length needs to be byte length for the correct HL offset."""
    names = parse('asd + äöü')
    assert names[0].end - names[0].col == 3
    assert names[1].end - names[1].col == 6


def test_comprehension_scopes():
    names = parse(r'''
        #!/usr/bin/env python3
        (a for b in c)
        [d for e in f]
        {g for h in i}
        {j:k for l in m}
    ''')
    root = make_tree(names)
    groups = {n.name: n.hl_group for n in names}
    print(f"root = {root}")
    print(f"groups = {groups}")

    if not PEP_709:
        assert root['names'] == ['c', 'f', 'i', 'm']
        assert root['genexpr']['names'] == ['a', 'b']
        assert root['listcomp']['names'] == ['d', 'e']
        assert root['setcomp']['names'] == ['g', 'h']
        assert root['dictcomp']['names'] == ['j', 'k', 'l']

        # generator variables b, e, h, l are local within the scope
        assert [name for name, group in groups.items() if group == LOCAL \
                ] == ['b', 'e', 'h', 'l']
        assert [name for name, group in groups.items() if group == UNRESOLVED
                ] == ['c', 'a', 'f', 'd', 'i', 'g', 'm', 'j', 'k']

    else:
        # PEP-709, Python 3.12+: comprehensions do not have scope of their own.
        # so all the symbol is contained in the root node (ast.Module)
        assert root['names'] == [
            # in the order nodes are visited and evaluated
            'c',  # generators have nested scope !!!
            'f', 'd', 'e',
            'i', 'g', 'h',
            'm', 'j', 'k', 'l'
        ]  # yapf: disable
        # no comprehension children nodes
        assert list(root.keys()) == ['names', 'genexpr']

        # generator variables e, h, l have the scope of the top-level module
        assert [name for name, group in groups.items() if group == GLOBAL
                ] == ['e', 'h', 'l']  # b is defined within the generator scope
        assert [name for name, group in groups.items() if group == UNRESOLVED
                ] == ['c', 'a', 'f', 'd', 'i', 'g', 'm', 'j', 'k']


def test_function_scopes():
    names = parse(r'''
        #!/usr/bin/env python3
        def func(a, b, *c, d=e, f=[g for g in h], **i):
            pass
        def func2(j=k):
            pass
        func(x, y=p, **z)
    ''')
    root = make_tree(names)
    print(f"root = {root}")

    assert root['names'] == [
        'e', 'h',
        *(['g', 'g'] if PEP_709 else []),
        'func', 'k', 'func2', 'func', 'x', 'p', 'z'
    ]  # yapf: disable
    if not PEP_709:
        assert root['listcomp']['names'] == ['g', 'g']
    assert root['func']['names'] == ['a', 'b', 'c', 'd', 'f', 'i']
    assert root['func2']['names'] == ['j']


def test_class_scopes():
    names = parse(r'''
        #!/usr/bin/env python3
        a = 1
        class A(x, y=z):
            a = 2
            def f():
                a
    ''')
    root = make_tree(names)
    assert root['names'] == ['a', 'A', 'x', 'z']


def test_import_scopes_and_positions():
    names = parse(r'''
        #!/usr/bin/env python3
        import aa
        import BB as cc
        from DD import ee
        from FF.GG import hh
        import ii.jj
        import kk, ll
        from MM import NN as oo
        from PP import *
        import qq, RR as tt, UU as vv
        from WW import xx, YY as zz
        import aaa; import bbb
        from CCC import (ddd,
        eee)
        import FFF.GGG as hhh
        from III.JJJ import KKK as lll
        import mmm.NNN.OOO, ppp.QQQ
    ''')
    root = make_tree(names)
    assert root['names'] == [
        'aa', 'cc', 'ee', 'hh', 'ii', 'kk', 'll', 'oo', 'qq', 'tt', 'vv', 'xx',
        'zz', 'aaa', 'bbb', 'ddd', 'eee', 'hhh', 'lll', 'mmm', 'ppp'
    ]
    assert [(name.name, ) + name.pos for name in names] == [
        ('aa', 3, 7),
        ('cc', 4, 13),
        ('ee', 5, 15),
        ('hh', 6, 18),
        ('ii', 7, 7),
        ('kk', 8, 7),
        ('ll', 8, 11),
        ('oo', 9, 21),
        ('qq', 11, 7),
        ('tt', 11, 17),
        ('vv', 11, 27),
        ('xx', 12, 15),
        ('zz', 12, 25),
        ('aaa', 13, 7),
        ('bbb', 13, 19),
        ('ddd', 14, 17),
        ('eee', 15, 0),
        ('hhh', 16, 18),
        ('lll', 17, 27),
        ('mmm', 18, 7),
        ('ppp', 18, 20),
    ]


def test_multibyte_import_positions():
    names = parse(r'''
        #!/usr/bin/env python3
        import aaa, bbb
        import äää, ööö
        aaa; import bbb, ccc
        äää; import ööö, üüü
        import äää; import ööö, üüü; from äää import ööö; import üüü as äää
        from x import (
            äää, ööö
        )
        from foo \
            import äää
    ''')
    positions = [(n.col, n.end) for n in names]
    assert positions == [
        (7, 10), (12, 15),
        (7, 13), (15, 21),
        (0, 3), (12, 15), (17, 20),
        (0, 6), (15, 21), (23, 29),
        (7, 13), (22, 28), (30, 36), (57, 63), (82, 88),
        (4, 10), (12, 18),
        (11, 17),  # note the line continuation
    ]  # yapf: disable


def test_name_mangling():
    """Leading double underscores can lead to a different symbol name."""
    names = parse(r'''
        #!/usr/bin/env python3
        __foo = 1
        class A:
            __foo
            class B:
                __foo
                def f():
                    __foo
            class __C:
                pass
        class _A:
            def f():
                __x
        class _A_:
            def f():
                __x
        class ___A_:
            def f():
                __x
    ''')
    root = make_tree(names)
    assert root['names'] == ['__foo', 'A', '_A', '_A_', '___A_']
    assert root['A']['names'] == ['_A__foo', 'B', '_A__C']
    assert root['A']['B']['names'] == ['_B__foo', 'f']
    assert root['A']['B']['f']['names'] == ['_B__foo']
    assert root['_A']['f']['names'] == ['_A__x']
    assert root['_A_']['f']['names'] == ['_A___x']
    assert root['___A_']['f']['names'] == ['_A___x']


def test_self_param():
    """If self/cls appear in a class, they must have a speical group."""
    names = parse(r'''
        #!/usr/bin/env python3
        self
        def x(self):
            pass
        class Foo:
            def x(self):
                pass
                def y():
                    self
                def z(self):
                    self
            def a(foo, self):
                pass
            def b(foo, cls):
                pass
            def c(cls, foo):
                pass
    ''')
    groups = [n.hl_group for n in names if n.name in ['self', 'cls']]
    assert [PARAMETER if g is PARAMETER_UNUSED else g for g in groups] == [
        UNRESOLVED, PARAMETER, SELF, FREE, PARAMETER, PARAMETER, PARAMETER,
        PARAMETER, SELF
    ]


def test_self_with_decorator():
    names = parse(r'''
        #!/usr/bin/env python3
        class Foo:
            @decorator(lambda k: k)
            def x(self):
                self
        ''')
    assert names[-1].hl_group == SELF


def test_self_target():
    """The target of a self with an attribute should be the attribute node."""
    parser = make_parser(r'''
        #!/usr/bin/env python3
        self.abc
        class Foo:
            def x(self):
                self.abc
    ''')
    names = parser._nodes
    assert names[0].target is None
    last_self = names[-1]
    abc = names[-2]
    assert last_self.target is abc
    assert last_self.target.name == 'abc'
    assert list(parser.same_nodes(last_self)) == [abc]


def test_unresolved_name():
    names = parse('def foo(): a')
    assert names[1].hl_group == UNRESOLVED


def test_imported_names():
    names = parse(r'''
        #!/usr/bin/env python3
        import foo
        import abs
        foo, abs
    ''')
    assert [n.hl_group for n in names] == [IMPORTED] * 4


def test_nested_comprehension():
    names = parse(r'''
        #!/usr/bin/env python3
        [a for b in c for d in e for f in g]
        [h for i in [[x for y in z] for k in [l for m in n]]]
        [o for p, q, r in s]
    ''')
    root = make_tree(names)
    if not PEP_709:
        assert root['names'] == ['c', 'n', 's']
        assert root['listcomp']['names'] == [
            'a', 'b', 'd', 'e', 'f', 'g', 'l', 'm', 'z', 'k', 'h', 'i', 'o',
            'p', 'q', 'r'
        ]
    else:
        # Python 3.12: all the 18 symbols are included in the root scope
        assert root['names'] == [
            *['c', 'a', 'b'], *['d', 'e', 'f', 'g'],
            *['n', 'l', 'm'], *['z', 'x', 'y'], 'k', 'h', 'i',
            *['s', 'o', 'p', 'q', 'r']
        ]  # yapf: disable
        assert 'listcomp' not in root


def test_try_except_order():
    names = parse(r'''
        #!/usr/bin/env python3
        try:
            def A():
                a
        except ImportError:
            def B():
                b
        else:
            def C():
                c
        finally:
            def D():
                d
    ''')
    root = make_tree(names)
    assert root['A']['names'] == ['a']
    assert root['B']['names'] == ['b']
    assert root['C']['names'] == ['c']
    assert root['D']['names'] == ['d']


def test_except_as():
    names = parse('try: pass\nexcept E as a: pass\nexcept F as\\\n b: pass')
    assert next(n.pos for n in names if n.name == 'a') == (2, 12)
    assert next(n.pos for n in names if n.name == 'b') == (4, 1)


def test_global_nonlocal():
    names = parse(r'''
        #!/usr/bin/env python3
        global ä, ää, \
        b                # Line 4
        def foo():       # Line 5
            c = 1
            def bar():   # Line 7
                nonlocal c
    ''')
    print([(n.name, n.pos) for n in names])
    assert [(n.name, n.pos) for n in names] == [
        ('ä', (3, 7)),
        ('ää', (3, 11)),
        ('b', (4, 0)),
        ('foo', (5, 4)),
        ('c', (6, 4)),
        ('bar', (7, 8)),
        ('c', (8, 17)),
    ]


def test_lambda():
    names = parse(r'''
        #!/usr/bin/env python3
        lambda a: b
        lambda x=y: z
    ''')
    root = make_tree(names)
    assert root['lambda']['names'] == ['a', 'b', 'x', 'z']
    assert root['names'] == ['y']


@pytest.mark.skipif('sys.version_info < (3, 6)')
def test_fstrings():
    assert [n.name for n in parse('f\'{foo}\'')] == ['foo']


@pytest.mark.skipif('sys.version_info < (3, 9, 7)')
def test_fstrings_offsets():
    # There was a Python-internal bug causing expressions with format
    # specifiers in f-strings to give wrong offsets when parsing into AST
    # (https://bugs.python.org/issue35212, https://bugs.python.org/issue44885).
    # The bug was fixed since 3.9.7+ and 3.10+ (numirias/semshi#31).
    s = "f'x{aa}{bbb:y}{cccc}'"
    names = parse("f'x{aa}{bbb:y}{cccc}'")
    offsets = [s.index(x) for x in 'abc']
    assert [n.col for n in names] == offsets


def test_type_hints():
    names = parse(r'''
        #!/usr/bin/env python3
        def f(a:A, b, *c:C, d:D=dd, **e:E) -> z:
            pass
        async def f2(x:X=y):
            pass
    ''')
    root = make_tree(names)
    assert root['names'] == [
        'dd', 'f', 'A', 'D', 'C', 'E', 'z', 'y', 'f2', 'X'
    ]


def test_decorator():
    names = parse(r'''
        #!/usr/bin/env python3
        @d1(a, b=c)
        class A: pass
        @d2(x, y=z)
        def B():
            pass
        @d3
        async def C():
            pass
    ''')
    root = make_tree(names)
    assert root['names'] == [
        'd1', 'a', 'c', 'A', 'd2', 'x', 'z', 'B', 'd3', 'C'
    ]


def test_global_builtin():
    """A builtin name assigned globally should be highlighted as a global, not
    a builtin."""
    names = parse(r'''
        #!/usr/bin/env python3
        len
        set = 1
        def foo(): set, str
    ''')
    assert names[0].hl_group == BUILTIN
    assert names[-2].hl_group == GLOBAL
    assert names[-1].hl_group == BUILTIN


def test_global_statement():
    names = parse(r'''
        #!/usr/bin/env python3
        x = 1
        def foo():
            global x
            x
    ''')
    assert names[-1].hl_group == GLOBAL


def test_positions():
    names = parse(r'''
        #!/usr/bin/env python3
        a = 1             # Line 3
        def func(x=y):    # Line 4
            b = 2
    ''')
    assert [(name.name, ) + name.pos for name in names] == [
        ('a', 3, 0),
        ('y', 4, 11),
        ('func', 4, 4),
        ('x', 4, 9),
        ('b', 5, 4),
    ]


def test_class_and_function_positions():
    # Note: did not use r''' to use literal '\t'
    names = parse('''
        #!/usr/bin/env python3
        def aaa(): pass              # Line 3
        async def bbb(): pass
        async  def  ccc(): pass
        class ddd(): pass
        class \t\f eee(): pass       # Line 7
        class \\
                \\
          ggg: pass                  # Line 10
        @deco
        @deco2
        @deco3
        class hhh():                 # Line 14
            def foo():
                pass
    ''')
    assert [name.pos for name in names] == [
        (3, 4),  # aaa
        (4, 10),  # bbb
        (5, 12),  # ccc
        (6, 6),  # ddd
        (7, 9),  # eee
        (10, 2),  # ggg
        (11, 1),  # deco
        (12, 1),  # deco 2
        (13, 1),  # deco 3
        (14, 6),  # hhh
        (15, 8),  # foo
    ]


def test_same_nodes():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        x = 1
        class A:
            x
            def B():
                x
    ''')
    names = parser._nodes
    x, A, A_x, B, B_x = names
    same_nodes = set(parser.same_nodes(x))
    assert same_nodes == {x, A_x, B_x}


def test_base_scope_global():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        x = 1
        def a():
            x = 2
            def b():
                global x
                x
    ''')
    names = parser._nodes
    x, a, a_x, b, b_global_x, b_x = names
    same_nodes = set(parser.same_nodes(x))
    assert same_nodes == {x, b_global_x, b_x}


def test_base_scope_free():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        def a():
            x = 1
            def b():
                x
    ''')
    names = parser._nodes
    a, a_x, b, b_x = names
    same_nodes = set(parser.same_nodes(a_x))
    assert same_nodes == {a_x, b_x}


def test_base_scope_class():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        class A:
            x = 1
            x
    ''')
    names = parser._nodes
    A, x1, x2 = names
    same_nodes = set(parser.same_nodes(x1))
    assert same_nodes == {x1, x2}


def test_base_scope_class_nested():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        def z():
            x = 1
            class A():
                x = 2
                def b():
                    return x
    ''')
    names = parser._nodes
    z, z_x, A, A_x, b, b_x = names
    same_nodes = set(parser.same_nodes(z_x))
    assert same_nodes == {z_x, b_x}


def test_base_scope_nonlocal_free():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        def foo():
            a = 1
            def bar():
                nonlocal a
                a = 1
    ''')
    foo, foo_a, bar, bar_nonlocal_a, bar_a = parser._nodes
    assert set(parser.same_nodes(foo_a)) == {foo_a, bar_nonlocal_a, bar_a}


def test_attributes():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        aa.bb
        cc.self.dd
        self.ee
        def a(self):
            self.ff
        class A:
            def b(self):
                self.gg
        class B:
            def c(self):
                self.gg
            def d(self):
                self.gg
            def e(self):
                self.hh
            def f(foo):
                self.gg
    ''')
    names = parser._nodes
    names = [n for n in names if n.hl_group == ATTRIBUTE]
    b_gg, c_gg, d_gg, e_hh = names
    same_nodes = set(parser.same_nodes(c_gg))
    assert same_nodes == {c_gg, d_gg}


def test_same_nodes_exclude_current():
    parser = make_parser('a, a, a')
    a0, a1, a2 = parser._nodes
    assert set(parser.same_nodes(a0, mark_original=False)) == {a1, a2}


def test_same_nodes_empty():
    parser = make_parser('0, 1')
    assert parser.same_nodes((1, 0)) == []


def test_same_nodes_use_target():
    parser = make_parser(r'''
        #!/usr/bin/env python3
        class Foo:
            def foo(self):
                self.x, self.x
    ''')
    node = parser._nodes[-1]
    assert [n.name for n in list(parser.same_nodes(node, use_target=True))
            ] == ['x', 'x']
    assert [n.name for n in list(parser.same_nodes(node, use_target=False))
            ] == ['self', 'self', 'self']


def test_refresh_names():
    """Clear everything if more than one line changes."""
    # yapf: disable
    parser = Parser()
    add, clear = parser.parse(dedent(r'''
        def foo():
            x = y
    '''))
    assert len(add) == 3
    assert len(clear) == 0
    add, clear = parser.parse(dedent(r'''
        def foo():
            x = y
    '''))
    assert len(add) == 0
    assert len(clear) == 0
    add, clear = parser.parse(dedent(r'''
        def foo():
            z = y
    '''))
    assert len(add) == 1
    assert len(clear) == 1
    add, clear = parser.parse(dedent(r'''
        def foo():
            z = y
        a, b
    '''))
    assert len(add) == 5
    assert len(clear) == 3
    add, clear = parser.parse(dedent(r'''
        def foo():
            z = y
        c, d
    '''))
    assert len(add) == 2
    assert len(clear) == 2
    add, clear = parser.parse(dedent(r'''
        def foo():
            z = y, k
        1, 1
    '''))
    assert len(add) == 4
    assert len(clear) == 5
    # yapf: enable


def test_exclude_types():
    # yapf: disable
    parser = Parser(exclude=[LOCAL])
    add, clear = parser.parse(dedent(r'''
        a = 1
        def f():
            b, c = 1
            a + b
    '''))
    # Python <= 3.7 parses 'a = 1' as the only GLOBAL,
    # but Python >= 3.8 parses three GLOBALS (a, f, a).
    # assert [n.name for n in add] == ['a']
    assert all(n.hl_group != LOCAL for n in add)
    assert clear == []
    add, clear = parser.parse(dedent(r'''
        a = 1
        def f():
            b, c = 1
            a + c
    '''))
    assert add == []
    assert clear == []
    add, clear = parser.parse(dedent(r'''
        a = 1
        def f():
            b, c = 1
            g + c
    '''))
    assert [n.name for n in add] == ['g']
    assert [n.name for n in clear] == ['a']
    add, clear = parser.parse(dedent(r'''
        a = 1
        def f():
            b, c = 1
            0 + c
    '''))
    assert add == []
    assert [n.name for n in clear] == ['g']
    # yapf: enable


def test_exclude_types_same_nodes():
    parser = Parser(exclude=[UNRESOLVED])
    add, clear = parser.parse('a, a')
    assert len(add) == 0
    assert [n.pos for n in parser.same_nodes((1, 0))] == [(1, 0), (1, 3)]


def test_make_nodes():
    """parser._make_nodes should work without a `lines` argument."""
    parser = Parser()
    parser._make_nodes('x')


def test_unused_args():
    names = parse(r'''
        #!/usr/bin/env python3
        def foo(a, b, c, d=1): a, c
        lambda x: 1
        async def bar(y): pass
    ''')
    assert [n.hl_group for n in names] == [
        # foo        a          b                 c          d
        MODULE_FUNC, PARAMETER, PARAMETER_UNUSED, PARAMETER, PARAMETER_UNUSED,
        # a        c
        PARAMETER, PARAMETER,
        # x               bar          y
        PARAMETER_UNUSED, MODULE_FUNC, PARAMETER_UNUSED
    ]  # yapf: disable


def test_unused_args2():
    """Detect unused args in nested scopes correctly."""
    names = parse(r'''
        #!/usr/bin/env python3
        def foo(x): lambda: x
    ''')
    assert [n.hl_group for n in names if n.name == 'x'] == [PARAMETER, FREE]

    names = parse(r'''
        #!/usr/bin/env python3
        def foo(x):
            [[x for a in b] for y in z]
    ''')
    assert [n.hl_group for n in names if n.name == 'x'] == [ \
        PARAMETER,
        PARAMETER if PEP_709 else FREE
    ]


@pytest.mark.skipif('sys.version_info < (3, 8)')
def test_posonlyargs():
    names = parse('def f(x, /): pass')
    assert [n.hl_group for n in names] == [MODULE_FUNC, PARAMETER_UNUSED]


# Fails due to what seems to be an internal bug. See:
# https://stackoverflow.com/q/59066024/5765873
@pytest.mark.xfail
@pytest.mark.skipif('sys.version_info < (3, 8)')
def test_posonlyargs_with_annotation():
    names = parse('def f(x: y, /): pass')
    assert [n.hl_group for n in names] == [
        MODULE_FUNC,
        UNRESOLVED,
        PARAMETER_UNUSED,
    ]


@pytest.mark.skipif('sys.version_info < (3, 8)')
@pytest.mark.parametrize("enable_pep563", (False, True))
def test_postponed_evaluation_of_annotations_pep563(enable_pep563):
    """Tests parsers with __future__ import annotations (PEP 563)."""
    # see https://peps.python.org/pep-0563/
    # see https://github.com/numirias/semshi/issues/116
    names = parse(
        ('from __future__ import annotations' if enable_pep563 else '') +
        dedent(r'''
        #!/usr/bin/env python3

        # globals
        from typing import List, Any, Dict
        a: int = 1  # builtins
        b: UnknownSymbol = 2  # non-builtins
        c: List[Any] = []  # imported

        # nested scope and symtable
        def foo():
           local_var: List[Any] = []  # local variables
        class Foo:
           attr: List[Any] = ()  # class attributes
           def __init__(self, v: Optional[List[Any]], built_in: int) -> Dict:
               temp: Any = built_in
        '''))
    expected = [
        ('annotations', IMPORTED) if enable_pep563 else (),
        ('List', IMPORTED), ('Any', IMPORTED), ('Dict', IMPORTED),
        ('a', GLOBAL), ('int', BUILTIN),
        ('b', GLOBAL), ('UnknownSymbol', UNRESOLVED),
        ('c', GLOBAL), ('List', IMPORTED), ('Any', IMPORTED),
        ('foo', GLOBAL),
        ('local_var', LOCAL), ('List', IMPORTED), ('Any', IMPORTED),
        ('Foo', GLOBAL),
        ('attr', LOCAL), ('List', IMPORTED), ('Any', IMPORTED),
        ('__init__', LOCAL),
        # Note: annotations & returntypes are evaluated first than parameters
        ('Optional', UNRESOLVED), ('List', IMPORTED), ('Any', IMPORTED),
        ('int', BUILTIN), ('Dict', IMPORTED),
        ('self', SELF), ('v', PARAMETER_UNUSED), ('built_in', PARAMETER),
        ('temp', LOCAL), ('Any', IMPORTED), ('built_in', PARAMETER),
    ]  # yapf: disable
    expected = [n for n in expected if len(n) > 0]
    assert [(n.name, n.hl_group) for n in names] == expected


@pytest.mark.skipif('sys.version_info < (3, 8)')
def test_postponed_evaluation_of_annotations_pep563_resolution(request):
    """Additional tests for PEP 563. The code is from the PEP-563 document."""
    path = Path(request.fspath.dirname) / 'data/pep-0563-annotations.py'
    with open(str(path), encoding="utf-8") as f:
        names = parse(f.read())

    # print('\n' + '\n'.join(repr(n) for n in names))

    # Tests the eight type annotations on method.
    def _find_annotations_for_method():
        for i, _ in enumerate(names):
            if names[i].name == 'method':
                yield names[i + 1]

    annos = list(_find_annotations_for_method())
    # print('\n' + '\n'.join(repr(n) for n in annos))

    assert len(annos) == 8

    assert annos[0].name == 'C' and annos[0].hl_group == GLOBAL
    assert annos[1].name == 'D' and annos[1].hl_group == UNRESOLVED
    assert annos[2].name == 'field2' and annos[2].hl_group == LOCAL
    assert annos[3].name == 'field' and annos[3].hl_group == UNRESOLVED

    assert annos[4].name == 'C' and annos[4].hl_group == GLOBAL
    assert annos[5].name == 'field' and annos[5].hl_group == LOCAL
    assert annos[6].name == 'C' and annos[6].hl_group == GLOBAL
    assert annos[7].name == 'D' and annos[7].hl_group == LOCAL


@pytest.mark.skipif('sys.version_info < (3, 10)')
def test_match_case():
    """Tests match/case syntax. see wookayin/semshi#19."""
    parse('''
        #!/usr/bin/env python3
        import sys
        arg = False
        match arg:
            case True: print('boolean')
            case False: print('boolean')
            case 42: print('integer')
            case 3.14: print('float')
            case "string": print('string')
            case b"123": print('bytearray')
            case sys.version: print('expr')
    ''')


@pytest.mark.skipif('sys.version_info < (3, 12)')
def test_generic_syntax():
    names = parse('''
        #!/usr/bin/env python3
        def get_first[T: float](data: list[T]) -> T:
            first: T = data[0]
            return first
    ''')

    expected = [
        ('get_first', MODULE_FUNC),
        *[('T', LOCAL), ('float', BUILTIN)],  # TypeVar with bound (T: float)
        *[('list', BUILTIN), ('T', LOCAL)],  # list[T]
        ('T', LOCAL),  # -> T:
        # for now, arg name is visited *after* params and type annotations
        # because of the way how variable scope is handled
        ('data', PARAMETER),
        *[('first', LOCAL), ('T', FREE), ('data', PARAMETER)],
        ('first', LOCAL),  # return ...
    ]
    assert [(n.name, n.hl_group) for n in names] == expected


@pytest.mark.skipif('sys.version_info < (3, 12)')
def test_type_statement_py312():
    # https://peps.python.org/pep-0695/
    names = parse('''
        #!/usr/bin/env python3
        type IntList = list[int]  # non-generic case
        type MyList[T] = list[T]
        #           ^typevar  ^ a resolved reference (treated like a closure)

        class A:
            pass

        def foo():
            mylist: MyList[int] = [1, 2, 3]
            # ^^^^ -> type statements used to break environment scope
            assert len(mylist) == 3
    ''')
    expected = [
        # non-generic type statement
        *[('IntList', GLOBAL), ('list', BUILTIN), ('int', BUILTIN)],
        # generic type statement
        *[('MyList', GLOBAL), ('T', LOCAL), ('list', BUILTIN), ('T', FREE)],
        # class A:
        ('A', GLOBAL),
        # def foo():
        *[
            ('foo', GLOBAL),
            # mylist: Mylist[int]
            *[('mylist', LOCAL), ('MyList', GLOBAL), ('int', BUILTIN)],
            # assert len(mylist) == 3
            *[('len', BUILTIN), ('mylist', LOCAL)],
        ],
    ]
    assert [(n.name, n.hl_group) for n in names] == expected


@pytest.mark.skipif('sys.version_info < (3, 13)')
def test_type_statement_py313():
    """type statement with bound (3.12+) and default (3.13+) parameters."""
    # https://peps.python.org/pep-0695/
    names = parse('''
        #!/usr/bin/env python3
        type Alias1[T, P] = list[P] | set[T]
        type Alias2[T, P: type[T]] = list[P] | set[T]
        type Alias3[T, P = T] = list[P] | set[T]
        type Alias4[T: int, P: int = bool | T] = list[P] | set[T]

        def foo():
            mylist: list[int] = [1, 2, 3]
            assert len(mylist) == 3
    ''')
    RHS_listP_or_setT = [
        *[('list', BUILTIN), ('P', FREE)],
        *[('set', BUILTIN), ('T', FREE)],
    ]
    expected = [
        # Alias1
        *[('Alias1', GLOBAL), ('T', LOCAL), ('P', LOCAL), *RHS_listP_or_setT],
        # Alias2: bound (P: type[T])
        *[('Alias2', GLOBAL), ('T', LOCAL), ('P', LOCAL), ('type', BUILTIN),
          ('T', FREE), *RHS_listP_or_setT],
        # Alias3: default
        *[('Alias3', GLOBAL), ('T', LOCAL), ('P', LOCAL),
          ('T', FREE), *RHS_listP_or_setT],
        # Alias4: bound and  default
        *[
            ('Alias4', GLOBAL),  # ...
            *[('T', LOCAL), ('int', BUILTIN)],
            *[('P', LOCAL), ('int', BUILTIN), ('bool', BUILTIN), ('T', FREE)],
            *RHS_listP_or_setT
        ],
        # remaining stuff, def foo(): ... should be unaffected
        *[
            ('foo', GLOBAL),
            # mylist: Mylist[int]
            *[('mylist', LOCAL), ('list', BUILTIN), ('int', BUILTIN)],
            # assert len(mylist) == 3
            *[('len', BUILTIN), ('mylist', LOCAL)],
        ],
    ]
    assert [(n.name, n.hl_group) for n in names] == expected


class TestNode:

    def test_node(self):
        # yapf: disable
        class Symbol:
            def __init__(self, name, **kwargs):
                self.name = name
                for k, v in kwargs.items():
                    setattr(self, 'is_' + k, lambda: v)
            def __getattr__(self, item):
                if item.startswith('is_'):
                    return lambda: False
                raise AttributeError(item)

        class Table:
            def __init__(self, symbols, type=None):
                self.symbols = symbols
                self.type = type or 'module'
            def lookup(self, name):
                return next(sym for sym in self.symbols if sym.name == name)
            def get_type(self):
                return self.type
        # yapf: enable

        a = Node('foo', 0, 0, [Table([Symbol('foo', local=True)])])
        b = Node('bar', 0, 10, [Table([Symbol('bar', local=True)])])
        assert a.id + 1 == b.id


def test_diff():
    """The id of a saved name should remain the same so that we can remove
    it later by ID."""
    parser = Parser()
    add0, rem = parser.parse('foo')
    add, rem = parser.parse('foo ')
    add, rem = parser.parse('foo = 1')
    assert add0[0].id == rem[0].id


def test_minor_change():

    def minor_change(c1, c2):
        return Parser._minor_change(c1, c2)

    assert minor_change(list('abc'), list('axc')) == (True, 1)
    assert minor_change(list('abc'), list('xbx')) == (False, None)
    assert minor_change(list('abc'), list('abcedf')) == (False, None)
    assert minor_change(list('abc'), list('abc')) == (True, None)


def test_specific_grammar(request):
    path = Path(request.fspath.dirname) / \
        'data/grammar{0}{1}.py'.format(*sys.version_info[:2])
    with open(str(path), encoding='utf-8') as f:
        parse(f.read())
