"""Stage-1 SQLGlot contracts, structural sharing and reference comparisons."""

import copy
from enum import Enum
import importlib.util
import itertools
import os
from pathlib import Path
import random
import sys
import types
import unittest
from unittest.mock import patch

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))

from sqlglot_port import idiomatic as g


def atom(name):
    return g.Expression(name, ())


def render(node):
    if type(node) is g.Expression:
        return node.kind
    if type(node) in (g.And, g.Or):
        return f"({render(node.left)} {'AND' if type(node) is g.And else 'OR'} {render(node.right)})"
    return f"{'NOT' if type(node) is g.Not else ''}({render(node.this)})"


def truth(node, values):
    if type(node) is g.Expression:
        return values[node.kind]
    if type(node) in (g.And, g.Or):
        left, right = truth(node.left, values), truth(node.right, values)
        decisive = False if type(node) is g.And else True
        if left is decisive or right is decisive:
            return decisive
        if left is None or right is None:
            return None
        return not decisive
    if type(node) is g.Not:
        value = truth(node.this, values)
        return None if value is None else not value
    return truth(node.this, values)


def without_parens(node):
    if type(node) is g.Paren:
        return without_parens(node.this)
    return g.map_children(node, without_parens)


def signature(node):
    if hasattr(node, "args"):
        return type(node).__name__, tuple((key, signature(value)) for key, value in node.args.items())
    if isinstance(node, Enum):
        return type(node).__qualname__, node.name
    if isinstance(node, (tuple, list)):
        return type(node).__name__, tuple(signature(value) for value in node)
    return node


def value_signature(expression):
    # Include operator kinds even under namedtuple controls, whose generated
    # equality does not distinguish two tuple classes with the same fields.
    return signature(g.to_sqlglot(without_parens(g.from_sqlglot(expression))))


class RecordTreeTests(unittest.TestCase):
    def test_path_copying_and_snapshot_isolation(self):
        a, b = atom("a"), atom("b")
        root = g.Expression("Select", (g.Argument("expressions", g.Sequence((g.And(a, b),))),))
        self.assertIs(g.transform(root, lambda node: node), root)
        changed = g.transform(root, lambda node: atom("c") if node is a else node)
        old_and = root.args[0].value.items[0]
        new_and = changed.args[0].value.items[0]
        self.assertIs(new_and.right, b)
        self.assertIs(old_and.left, a)
        self.assertEqual(new_and.left, atom("c"))
        with self.assertRaises((AttributeError, TypeError)):
            old_and.left = atom("c")
        with self.assertRaises((AttributeError, TypeError)):
            root.args[0].value = ()
        with self.assertRaises(TypeError):
            g.transform(root, lambda node: None)
        with self.assertRaises(TypeError):
            g.map_children(object(), lambda node: node)

    def test_replacement_not_revisited(self):
        calls = []
        original = atom("a")
        replacement = g.And(original, atom("b"))

        def replace(node):
            calls.append(node)
            return replacement

        self.assertIs(g.transform(original, replace), replacement)
        self.assertEqual(calls, [original])

    def test_distribution_truth_table_and_distance(self):
        a, b, c, d = (atom(name) for name in "abcd")
        cases = [g.Or(g.And(a, b), c), g.Or(g.And(a, b), g.And(c, d)),
                 g.And(g.Or(a, b), g.Or(c, d))]
        rng = random.Random(990)

        def random_tree(depth):
            if depth == 0 or rng.random() < 0.3:
                return rng.choice((a, b, c, d))
            return rng.choice((g.And, g.Or))(random_tree(depth - 1), random_tree(depth - 1))

        cases += [random_tree(3) for _ in range(30)]
        with patch.object(g, "reference", side_effect=AssertionError("core called SQLGlot")):
            self.assertEqual(g.normalization_distance(cases[1]), 4)
            self.assertIs(g.distribute(cases[1], render, max_distance=3), cases[1])
            for original in cases:
                for dnf in (False, True):
                    normalized = g.distribute(original, render, dnf)
                    self.assertTrue(g.normalized(normalized, dnf))
                    for bits in itertools.product((False, True, None), repeat=4):
                        values = dict(zip("abcd", bits))
                        self.assertEqual(truth(original, values), truth(normalized, values))
                    self.assertEqual(g.distribute(normalized, render, dnf), normalized)

    def test_flatten_sort_deduplicate(self):
        a, b = atom("a"), atom("b")
        root = g.And(b, g.Paren(g.And(a, b)))
        result = g.uniq_sort(g.flatten(root), render)
        self.assertEqual(result, g.And(a, b))
        self.assertEqual(root.right.this, g.And(a, b))
        self.assertIs(g.map_children(root, function=lambda node: node), root)


class SQLGlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.sqlglot = g.reference()
        except ModuleNotFoundError as exc:
            if exc.name != "sqlglot":
                raise
            raise unittest.SkipTest("install sqlglot==4.6.0 for adapter/differential tests")
        cls.exp = cls.sqlglot.exp
        cls.normalizer = importlib.import_module("sqlglot.optimizer.normalize")
        cls.simplifier = importlib.import_module("sqlglot.optimizer.simplify")

    def test_version_guard(self):
        g.reference.cache_clear()
        try:
            with patch.dict(sys.modules, {"sqlglot": types.SimpleNamespace(__version__="999")}):
                with self.assertRaisesRegex(RuntimeError, "sqlglot==4.6.0"):
                    g.reference()
        finally:
            g.reference.cache_clear()
        self.assertIs(g.reference(), self.sqlglot)

    def test_parent_backlink_contract(self):
        query = "(((a = 1 OR c IS NULL) AND a = 1) AND ((a = 1 OR c IS NULL) OR (a AND b > 2)))"
        original = self.sqlglot.parse_one(query)
        expected = self.normalizer.normalize(original.copy())
        actual = g.normalize(original)
        # Returning a value through the adapter intentionally discards stale
        # upstream ancestry. Do not emulate backlinks to a discarded tree just
        # to retain redundant rendering parentheses.
        self.assertEqual(expected.sql(), "(a = 1)")
        self.assertEqual(actual.sql(), "a = 1")
        self.assertEqual(value_signature(actual), value_signature(expected))

    def test_roundtrip_and_valid_parent_links(self):
        for query, dialect in (
            ("SELECT a, COUNT(*) OVER (PARTITION BY x) FROM t WHERE a BETWEEN 1 AND 4", None),
            ("WITH q AS (SELECT CAST('2020-01-01' AS DATE) d) SELECT * FROM q", None),
            ("SELECT CASE WHEN a AND b THEN 1 ELSE 0 END, ARRAY(1, 2) FROM t", "spark"),
            ("SELECT `x` FROM `t` WHERE `x` = 1 OR `x` = 2", "mysql"),
        ):
            original = self.sqlglot.parse_one(query, read=dialect)
            before = signature(original)
            tree = g.from_sqlglot(original)
            result = g.to_sqlglot(tree)
            self.assertEqual(signature(result), before)
            self.assertEqual(result.sql(dialect=dialect), original.sql(dialect=dialect))
            for child, parent, key in result.walk():
                self.assertIs(child.parent, parent)
                if parent is not None:
                    self.assertEqual(child.arg_key, key)
            result.set("extra", self.exp.Literal.number(1))
            self.assertEqual(signature(original), before)
            self.assertEqual(signature(g.to_sqlglot(tree)), before)

    def test_dag_aliases_and_rejected_metadata(self):
        shared = self.sqlglot.parse_one("a = 1")
        original = self.exp.And(this=shared, expression=shared)
        tree = g.from_sqlglot(original)
        self.assertIs(tree.left, tree.right)
        materialized = g.to_sqlglot(tree)
        self.assertIsNot(materialized.left, materialized.right)
        self.assertIs(materialized.left.parent, materialized)
        self.assertIs(materialized.right.parent, materialized)
        malformed = self.exp.Column(this={"mutable": []})
        with self.assertRaises(TypeError):
            g.from_sqlglot(malformed)
        cyclic = self.exp.Paren()
        cyclic.set("this", cyclic)
        with self.assertRaises(ValueError):
            g.from_sqlglot(cyclic)
        sequence = []
        sequence.append(sequence)
        with self.assertRaises(ValueError):
            g.from_sqlglot(self.exp.Select(expressions=sequence))
        with self.assertRaises(TypeError):
            g.from_sqlglot(self.exp.And(this="a", expression=shared))

        class Extension(self.exp.Expression):
            pass

        with self.assertRaises(TypeError):
            g.from_sqlglot(Extension(this="x"))
        with self.assertRaises(TypeError):
            g.from_sqlglot("not a tree")
        with self.assertRaises(ValueError):
            g.from_sqlglot(self.exp.And(this=shared))

    def test_single_pass_and_normalization(self):
        rng = random.Random(7781)
        queries = ["(A AND B) OR (C AND D) OR (E AND F) OR (G AND H)",
                   "(a AND b) OR c", "a OR (a AND b)", "NOT (a OR b)",
                   "(a = 1 AND b IN (1,2)) OR (c IS NULL AND d > 4)",
                   "TRUE AND a OR FALSE", "NULL AND FALSE", "a OR NOT a",
                   "SELECT * FROM t WHERE (a AND b) OR (c AND d)",
                   "SELECT CASE WHEN (a AND b) OR c THEN 1 ELSE 0 END FROM t",
                   "WITH q AS (SELECT * FROM t WHERE (a AND b) OR c) SELECT * FROM q",
                   "(a AND b AND c) OR (d AND e)",
                   "(a OR b) AND (c OR d OR e)"]

        def random_condition(depth):
            if depth == 0 or rng.random() < 0.25:
                return rng.choice(("a", "b", "c", "d", "a = 1", "b > 2", "c IS NULL"))
            return f"({random_condition(depth - 1)} {rng.choice(('AND', 'OR'))} {random_condition(depth - 1)})"

        queries += [random_condition(3) for _ in range(50)]
        for query in queries:
            for dnf in (False, True):
                for distance in (0, 128):
                    with self.subTest(query=query, dnf=dnf, distance=distance):
                        original = self.sqlglot.parse_one(query)
                        before = signature(original)
                        tree = g.from_sqlglot(original)
                        self.assertEqual(g.normalization_distance(tree, dnf),
                                         self.normalizer.normalization_distance(original, dnf))
                        self.assertEqual(g.normalized(tree, dnf), self.normalizer.normalized(original, dnf))
                        expected_pass = self.normalizer.distributive_law(original.copy(), dnf, distance)
                        actual_pass = g.distributive_law(tree, dnf, distance, g.canonical_sql)
                        # The mutable implementation shares/reparents operands while
                        # tidying. Immutable path copies need not retain its partial
                        # tidy state or stale parent links. Compare canonical values
                        # after rebuilding fresh occurrences and simplifying both.
                        expected_value = g.to_sqlglot(g.from_sqlglot(expected_pass))
                        expected_value = self.simplifier.simplify(expected_value).unnest()
                        actual_value = self.simplifier.simplify(g.to_sqlglot(actual_pass)).unnest()
                        self.assertEqual(value_signature(actual_value), value_signature(expected_value))
                        expected = self.normalizer.normalize(original.copy(), dnf, distance)
                        actual = g.normalize(original, dnf, distance)
                        # Compare ASTs without Paren wrappers, not SQL with brackets
                        # stripped: binary tree grouping is still compared exactly.
                        # The reference's stale parent links can leave extra wrappers.
                        self.assertEqual(value_signature(actual), value_signature(expected))
                        self.assertEqual(signature(original), before)

    def test_no_reference_distribution_fallback(self):
        original = self.sqlglot.parse_one("(a AND b) OR c")
        with patch.object(self.normalizer, "normalize", side_effect=AssertionError("fallback")), \
             patch.object(self.normalizer, "distributive_law", side_effect=AssertionError("fallback")):
            self.assertEqual(g.normalize(original).sql(), "(a OR c) AND (b OR c)")

    def test_dialects_and_optimizer_pipeline(self):
        for dialect in ("spark", "mysql", "postgres"):
            query = "SELECT * FROM t WHERE (a = 1 AND b = 2) OR c = 3"
            parsed = self.sqlglot.parse_one(query, read=dialect)
            expected = self.normalizer.normalize(parsed.copy()).sql(dialect=dialect)
            self.assertEqual(g.main(query, read=dialect, write=dialect), expected)
        from sqlglot.optimizer import optimize

        schema = {"t": {"a": "int", "b": "int", "c": "int"},
                  "u": {"a": "int", "d": "int"}}
        for query in ("SELECT a FROM t WHERE (a = 1 AND b = 2) OR c = 3",
                      "SELECT t.a FROM t JOIN u ON t.a = u.a WHERE (b = 1 AND c = 2) OR d = 3",
                      "WITH q AS (SELECT a FROM t WHERE b = 1) SELECT * FROM q"):
            parsed = self.sqlglot.parse_one(query)
            before = signature(parsed)
            self.assertEqual(g.optimize(parsed, schema).sql(), optimize(parsed, schema).sql())
            self.assertEqual(signature(parsed), before)

    def test_tpch_workload(self):
        source = BENCH / "sqlglot_port/upstream.py"
        spec = importlib.util.spec_from_file_location("_original_sqlglot_benchmark", source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: module, "pyperf": types.ModuleType("pyperf")}):
            spec.loader.exec_module(module)
        parsed = self.sqlglot.parse_one(module.SQL)
        expected = module.optimize(parsed, module.TPCH_SCHEMA)
        actual = g.optimize(parsed, module.TPCH_SCHEMA)
        self.assertEqual(signature(actual), signature(expected))
        self.assertEqual(actual.sql(), expected.sql())
        self.assertEqual(actual.sql(dialect="spark"), expected.sql(dialect="spark"))


if __name__ == "__main__":
    unittest.main()
