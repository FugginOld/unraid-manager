"""Pins facts about collector.py's SOURCE that no behavioural test can observe.

A stripped key and a key that was never read produce the identical stored
output, so nothing about the OUTPUT of parse_smart or _disk_row can tell
"stripped" from "never there" apart. A banned GraphQL keyword (a mutation, an
introspection field) would round-trip through every mocked transport in this
suite unchanged, because nothing here talks to a real server that would
reject it. The only place any of these four facts is observable at all is
the source itself - so these four checks read collector.py as an AST instead
of asserting on behaviour.

This used to live in tests/php/policy_test.php, scanning source TEXT with
regexes for two helpers, py_function() (find a function's body) and
py_code_only() (strip comments and docstrings so prose can't satisfy a pin
meant for code). That approach went through three rounds and failed each
time: a docstring that quoted the guarded line satisfied a pin with the code
deleted; stripping any triple-quoted span also stripped a triple-quoted
GraphQL query VALUE before the no-mutation pin ever scanned it; anchoring the
strip to a docstring's line position closed that hole but a multi-line
docstring's closing delimiter is still line-anchored and paired with the
next unrelated line-terminal triple-quote downstream, deleting real code
between them. Pairing string delimiters correctly is a lexing problem, and a
regex is not a lexer.

Python's own parser does not have this problem. ast.get_docstring() finds a
docstring by what it structurally IS - a string used as a STATEMENT, the
first line of a function/class/module body - not by pattern-matching its
delimiters. A string used as a VALUE (a dict key argument, a query text
passed to a call) is a different node shape by construction, so a docstring
that quotes or describes a guard can never be mistaken for the guard, and a
multi-line triple-quoted query value is never at risk of being paired away.
"""
import ast
import os
import unittest

import context

COLLECTOR_PATH = os.path.join(context.DAEMON, 'collector.py')


def _tree():
    with open(COLLECTOR_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()
    return ast.parse(source, filename=COLLECTOR_PATH)


def _find_function(tree, name):
    """The FunctionDef node named `name`, anywhere in the module.

    Fails loudly (raises) rather than returning something falsy: a pin that
    silently reads as satisfied because it could not find its target is the
    exact failure class this file exists to end.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        'collector.py has no function named %r - the guard could not '
        'find what it is supposed to check' % name)


def _docstring_constant_ids(node):
    """id() of every Constant that IS a docstring, anywhere under node.

    Uses ast.get_docstring() - the same test the language itself uses to
    recognise a docstring (a bare string as a def/class/module's first
    statement) - rather than a heuristic re-implementation of it.
    """
    ids = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(n, clean=False) is not None:
                ids.add(id(n.body[0].value))
    return ids


def _non_docstring_strings(node):
    """Every string Constant's value under node, excluding docstrings."""
    skip = _docstring_constant_ids(node)
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


def _pop_keys(func_node):
    """The first-argument string constant of every `<expr>.pop(...)` call
    inside func_node's body - i.e. every key a dict.pop() call in this
    function removes."""
    keys = []
    for node in ast.walk(func_node):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'pop'
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.append(node.args[0].value)
    return keys


class TestCollectorSourcePolicy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tree = _tree()

    def test_parse_smart_strips_serial_number_and_logical_unit_id(self):
        func = _find_function(self.tree, 'parse_smart')
        keys = _pop_keys(func)
        self.assertIn('serial_number', keys)
        self.assertIn('logical_unit_id', keys)

    def test_disk_row_does_not_leak_serial_num(self):
        func = _find_function(self.tree, '_disk_row')
        strings = _non_docstring_strings(func)
        # Sanity that this found the right function at all, not an empty body.
        self.assertIn('smart_status', strings)
        self.assertFalse(any('serial' in s.lower() for s in strings),
                          'a serial-shaped key reached _disk_row: %r' % strings)

    def test_no_domain_query_contains_a_mutation(self):
        strings = _non_docstring_strings(self.tree)
        self.assertFalse(any('mutation' in s.lower() for s in strings))

    def test_no_domain_query_contains_an_introspection_query(self):
        strings = _non_docstring_strings(self.tree)
        self.assertFalse(any('__schema' in s or '__type' in s for s in strings))


if __name__ == '__main__':
    unittest.main()
