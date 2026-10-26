"""Record-native simplifier rules, occurrence isolation and integration."""

from enum import Enum
import importlib
import itertools
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlglot_port import idiomatic as g
from sqlglot_port import simplify as s
from test_sqlglot_rewrite import value_signature


def atom(name):
    return g.Expression(name, ())


def signature(node):
    if hasattr(node, "args"):
        return type(node).__name__, tuple((k, signature(v)) for k, v in node.args.items())
    if isinstance(node, Enum):
        return type(node).__qualname__, node.name
    if isinstance(node, (tuple, list)):
        return type(node).__name__, tuple(signature(v) for v in node)
    return node


class ContextTests(unittest.TestCase):
    def test_occurrence_paths_and_sharing(self):
        a, b = atom("a"), atom("b")
        shared = g.And(a, b)
        sequence = g.Sequence((shared, (shared, "metadata")))
        root = g.Expression("Select", (g.Argument("expressions", sequence),
                                       g.Argument("where", shared)))
        seen = []

        def identity(node, ancestors):
            seen.append((node, tuple((edge.kind, edge.path) for edge in ancestors)))
            return node

        with patch.object(g, "reference", side_effect=AssertionError("adapter")):
            self.assertIs(s.rewrite_context(root, identity), root)
            paths = [path for node, path in seen if node is a]
            self.assertEqual(paths, [
                (("Select", ("expressions", 0)), ("And", ("this",))),
                (("Select", ("expressions", 1, 0)), ("And", ("this",))),
                (("Select", ("where",)), ("And", ("this",))),
            ])
            changed = s.rewrite_context(root, lambda node, ctx: atom("c")
                                        if node is a and ctx[0].path == ("where",) else node)
        self.assertIs(changed.args[0], root.args[0])
        self.assertIs(changed.args[1].value.right, b)
        self.assertIs(root.args[1].value, shared)
        self.assertIs(root.args[0].value, sequence)
        self.assertIs(shared.left, a)
        self.assertEqual(changed.args[1].value.left.kind, "c")

    def test_replacement_validation_and_unary_paths(self):
        root = g.Paren(g.Not(atom("a")))
        paths = []
        self.assertIs(s.rewrite_context(root, lambda node, ctx: paths.append(ctx) or node), root)
        self.assertEqual(tuple(edge.kind for edge in paths[-1]), ("Paren", "Not"))
        replacement = g.Not(root)
        calls = []
        self.assertIs(s.rewrite_context(root, lambda node, ctx: calls.append(node) or replacement), replacement)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(TypeError):
            s.rewrite_context(root, lambda node, ctx: None)
        with self.assertRaises((AttributeError, TypeError)):
            paths[-1][0].kind = "changed"

    def test_pure_boolean_rules_and_identity(self):
        a, b = atom("a"), atom("b")
        with patch.object(g, "reference", side_effect=AssertionError("adapter")):
            root = g.And(a, b)
            self.assertIs(s.boolean_pass(root, lambda node: node.kind), root)
            self.assertIs(s.simplify_connectors(g.And(s.TRUE, a)), a)
            self.assertIs(s.simplify_connectors(g.And(s.NULL, s.FALSE)), s.NULL)
            self.assertIs(s.remove_complements(g.Or(a, g.Not(a))), s.TRUE)
            self.assertIs(s.simplify_not(g.Not(g.Not(a))), a)
            self.assertFalse(s.sql_equal(g.And(a, b), g.Or(a, b)))
            self.assertFalse(s.sql_equal(g.Not(a), g.Paren(a)))
            self.assertFalse(s.sql_equal(g.Sequence(()), ()))


    def test_independent_two_valued_truth_tables(self):
        rng = random.Random(226)
        a, b, c = (atom(name) for name in "abc")

        def generate(depth):
            if depth == 0 or rng.random() < 0.25:
                return rng.choice((a, b, c, s.TRUE, s.FALSE))
            if rng.random() < 0.2:
                return g.Not(g.Paren(generate(depth - 1)))
            return rng.choice((g.And, g.Or))(generate(depth - 1), g.Paren(generate(depth - 1)))

        def evaluate(node, values):
            if type(node) is g.Expression:
                if node.kind == "Boolean":
                    return node.args[0].value
                return values[node.kind]
            if type(node) is g.And:
                return evaluate(node.left, values) and evaluate(node.right, values)
            if type(node) is g.Or:
                return evaluate(node.left, values) or evaluate(node.right, values)
            if type(node) is g.Not:
                return not evaluate(node.this, values)
            return evaluate(node.this, values)

        with patch.object(g, "reference", side_effect=AssertionError("fallback")):
            for _ in range(125):
                original = generate(4)
                actual = s.simplify(original, repr)
                for bits in itertools.product((False, True), repeat=3):
                    values = dict(zip("abc", bits))
                    self.assertEqual(evaluate(actual, values), evaluate(original, values))


class ReferenceRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.sqlglot = g.reference()
        except ModuleNotFoundError as exc:
            if exc.name != "sqlglot":
                raise
            raise unittest.SkipTest("install sqlglot==4.6.0 for differential tests")
        cls.upstream = importlib.import_module("sqlglot.optimizer.simplify")

    def test_equality_contract(self):
        exp = self.sqlglot.exp
        values = [self.sqlglot.parse_one(text) for text in (
            "a", "A", '"a"', "'a'", "'A'", "0", "FALSE", "TRUE", "NULL",
            "a AND b", "a OR b", "NOT a", "(a)", "x = 'a'", "X = 'A'",
        )]
        values += [
            exp.Identifier(this="a", quoted=True, extra="ignored"),
            exp.Literal(this="a", is_string=True, extra="ignored"),
            exp.Expression(this="A", extra=["B", "C"]),
            exp.Expression(extra=["b", "c"], this="a"),
            exp.Expression(this=("A",)), exp.Expression(this=("a",)),
            exp.Expression(this=exp.DataType.Type.DATE),
            exp.Expression(this=exp.DataType.Type.INT),
            exp.Expression(this=None), exp.Expression(),
        ]
        for left, right in itertools.product(values, repeat=2):
            with self.subTest(left=repr(left), right=repr(right)):
                a, b = g.from_sqlglot(left), g.from_sqlglot(right)
                self.assertEqual(s.sql_equal(a, b), left == right)
                self.assertEqual(s.equality_key(a) == s.equality_key(b), left == right)
                memo = {}
                self.assertEqual(s.equality_key(a, memo), s.equality_key(a))
                self.assertIs(memo[id(a)][0], a)

    def test_local_rules_exact_ast(self):
        exp = self.sqlglot.exp
        operands = [self.sqlglot.parse_one(text) for text in (
            "a", "A", "b", "NOT a", "NOT NOT a", "(NOT a)",
            "(a AND b)", "(a OR b)", "NULL", "TRUE", "FALSE", "0", "''", "'A'",
        )]
        cases = list(operands)
        cases += [exp.Not(this=value.copy()) for value in operands]
        cases += [kind(this=a.copy(), expression=b.copy())
                  for a, b in itertools.product(operands, repeat=2) for kind in (exp.And, exp.Or)]
        rng = random.Random(771)
        for _ in range(100):
            a, b, c = (rng.choice(operands).copy() for _ in range(3))
            cases.append(exp.And(this=exp.And(this=a, expression=b), expression=c))
        rules = [(s.simplify_not, self.upstream.simplify_not),
                 (s.simplify_connectors, self.upstream.simplify_connectors),
                 (s.remove_complements, self.upstream.remove_compliments)]
        for case in cases:
            for actual_rule, expected_rule in rules:
                with self.subTest(case=case.sql(), rule=actual_rule.__name__):
                    before = signature(case)
                    node = g.from_sqlglot(case)
                    expected = expected_rule(case.copy())
                    with patch.object(g, "reference", side_effect=AssertionError("fallback")):
                        actual = actual_rule(node)
                    self.assertEqual(signature(g.to_sqlglot(actual)), signature(expected))
                    self.assertEqual(signature(g.to_sqlglot(node)), before)
                    self.assertEqual(signature(case), before)

    def test_hierarchy_metadata(self):
        exp = self.sqlglot.exp
        for base, names in ((exp.Binary, s.BINARY_KINDS), (exp.Condition, s.CONDITION_KINDS)):
            expected = {name for name, cls in vars(exp).items()
                        if isinstance(cls, type) and issubclass(cls, base)}
            self.assertEqual(names, expected)
        for name, cls in vars(exp).items():
            if isinstance(cls, type) and issubclass(cls, exp.Expression):
                for base in (exp.Cast, exp.Interval, exp.Literal, exp.Select, exp.Where, exp.Join):
                    self.assertEqual(s._is(g.Expression(name, ()), base.__name__), issubclass(cls, base))

    def test_reordered_argument_hash_contract(self):
        exp = self.sqlglot.exp
        a = exp.Column(this=exp.Identifier(this="a"), table=None)
        b = exp.Column(table=None, this=exp.Identifier(this="a"))
        self.assertEqual(a, b)
        root = exp.And(this=a, expression=exp.Paren(this=exp.Or(this=b, expression=exp.column("b"))))
        # Upstream hashes generic expression args in insertion order despite
        # ignoring that order in equality. Our absorption sets use equality
        # keys instead. This is an explicit revised contract, not exact parity
        # for manually reordered argument dictionaries.
        self.assertEqual(self.upstream.simplify(root.copy()).sql(), "a AND (a OR b)")
        actual = s.simplify(g.from_sqlglot(root), g.canonical_sql)
        self.assertEqual(g.to_sqlglot(actual).sql(), "a")

    def test_literal_rules_and_exceptions(self):
        exp = self.sqlglot.exp
        values = [exp.Literal.number(number) for number in (0, 1, 2, -3, "0.5", "1e2")]
        values += [exp.Literal.string(text) for text in ("a", "A", "", "b")]
        values += [exp.NULL, exp.TRUE, exp.FALSE, exp.Not(this=exp.NULL),
                   self.sqlglot.parse_one("a")]
        operators = [exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv, exp.Mod,
                     exp.EQ, exp.NEQ, exp.Is, exp.GT, exp.GTE, exp.LT, exp.LTE]
        for operator, a, b in itertools.product(operators, values, values):
            case = operator(this=a.copy(), expression=b.copy())
            with self.subTest(case=case.sql()):
                node = g.from_sqlglot(case)
                try:
                    expected = self.upstream.simplify_literals(case.copy())
                except Exception as exc:
                    with self.assertRaises(type(exc)):
                        s.simplify_literals(node)
                else:
                    with patch.object(g, "reference", side_effect=AssertionError("fallback")):
                        actual = s.simplify_literals(node)
                    self.assertEqual(signature(g.to_sqlglot(actual)), signature(expected))
        for sql in ("a + 1 + 2 + b + 3", "8 / 3 / 2", "10 - 3 - 2",
                    "a + NULL + b", "1 + (2 + 3)", "a = A", "1.0 + 2"):
            original = self.sqlglot.parse_one(sql)
            expected = self.upstream.simplify_literals(original.copy())
            actual = s.simplify_literals(g.from_sqlglot(original))
            self.assertEqual(signature(g.to_sqlglot(actual)), signature(expected), sql)

    def test_dates_and_optional_dateutil(self):
        fixtures = [f"CAST('2020-01-31' AS DATE) {op} INTERVAL '1' {unit}"
                    for op in ("+", "-") for unit in ("YEAR", "MONTH", "WEEK", "DAY", "HOUR")]
        fixtures += ["TRY_CAST('2020-01-31' AS DATE) + INTERVAL '1' MONTH",
                     "INTERVAL '1' DAY + TRY_CAST('2020-01-31' AS DATE)",
                     "INTERVAL '1' MONTH + CAST('2020-01-31' AS DATE)",
                     "INTERVAL '1' DAY - CAST('2020-01-31' AS DATE)",
                     "CAST('invalid' AS DATE) + INTERVAL '1' DAY",
                     "CAST('2020-01-01' AS TIMESTAMP) + INTERVAL '1' DAY"]
        for missing in (False, True):
            with patch.dict(sys.modules, {"dateutil.relativedelta": None} if missing else {}):
                for sql in fixtures:
                    original = self.sqlglot.parse_one(sql)
                    with self.subTest(sql=sql, dateutil_missing=missing):
                        try:
                            expected = self.upstream.simplify(original.copy())
                        except Exception as exc:
                            with self.assertRaises(type(exc)):
                                s.simplify(g.from_sqlglot(original), g.canonical_sql)
                        else:
                            actual = s.simplify(g.from_sqlglot(original), g.canonical_sql)
                            self.assertEqual(signature(g.to_sqlglot(actual)), signature(expected))

    def test_absorption_context_and_fixed_point(self):
        formulas = ["a AND (a OR b)", "a OR (a AND b)", "a AND (NOT a OR b)",
                    "a OR (NOT a AND b)", "(a AND b) OR (a AND NOT b)",
                    "(a OR b) AND (a OR NOT b)", "(a OR b) AND (a OR b OR c)",
                    "(a AND b) OR (a AND b AND c)", "(a OR b) AND (b OR NOT a)",
                    "a AND ((NOT a) OR b)", "NOT ((NOT a OR b) AND (a OR c))"]
        queries = ["SELECT (1 + 2), (a = 1), (a IS NULL), (a LIKE 'x'), (SELECT 1) FROM t",
                   "SELECT * FROM t WHERE TRUE", "SELECT * FROM t WHERE 0",
                   "SELECT * FROM t JOIN u ON TRUE WHERE TRUE",
                   "SELECT * FROM t LEFT JOIN u ON 1 = 1",
                   "SELECT * FROM t WHERE (a = 1) AND (b = 2)",
                   "SELECT CASE WHEN TRUE AND a THEN (1 + 2) ELSE 0 END FROM t",
                   "SELECT COALESCE((a + b), 0), CAST((a + b) AS INT) FROM t",
                   "WITH q AS (SELECT * FROM t WHERE TRUE) SELECT * FROM q WHERE TRUE"]
        rng = random.Random(991)
        atoms = ("a", "b", "c", "d", "a = 1", "b > 2", "c IS NULL",
                 "TRUE", "FALSE", "NULL", "0", "''")

        def generate(depth):
            if depth == 0 or rng.random() < 0.3:
                return rng.choice(atoms)
            if rng.random() < 0.15:
                return f"NOT ({generate(depth - 1)})"
            return f"({generate(depth - 1)} {rng.choice(('AND', 'OR'))} {generate(depth - 1)})"

        formulas += [generate(4) for _ in range(350)]
        for sql in formulas + queries:
            with self.subTest(sql=sql):
                original = self.sqlglot.parse_one(sql)
                before = signature(original)
                node = g.from_sqlglot(original)
                expected = self.upstream.simplify(original.copy())
                with patch.object(self.upstream, "simplify", side_effect=AssertionError("fallback")):
                    actual = s.simplify(node, g.canonical_sql)
                self.assertEqual(value_signature(g.to_sqlglot(actual)), value_signature(expected))
                self.assertTrue(s.same_value(s.simplify(actual, g.canonical_sql), actual))
                self.assertEqual(signature(original), before)
                self.assertEqual(signature(g.to_sqlglot(node)), before)
        for sql in queries:
            original = self.sqlglot.parse_one(sql)
            self.assertEqual(g.to_sqlglot(s.simplify(g.from_sqlglot(original), g.canonical_sql)).sql(),
                             self.upstream.simplify(original.copy()).sql())

    def test_rendered_boolean_grouping(self):
        rng = random.Random(213)

        def generate(depth):
            if depth == 0 or rng.random() < 0.2:
                return rng.choice(("a", "b", "c", "NULL", "TRUE", "FALSE"))
            if rng.random() < 0.25:
                return f"NOT ({generate(depth - 1)})"
            return f"({generate(depth - 1)} {rng.choice(('AND', 'OR'))} {generate(depth - 1)})"

        def evaluate(node, values):
            if type(node) is g.Expression:
                if node.kind == "Column":
                    return values[s.text(node)]
                if node.kind == "Boolean":
                    return s.argument(node, "this")
                if node.kind == "Null":
                    return None
                raise AssertionError(node.kind)
            if type(node) in g.CONNECTORS:
                left, right = evaluate(node.left, values), evaluate(node.right, values)
                decisive = False if type(node) is g.And else True
                if left is decisive or right is decisive:
                    return decisive
                return None if left is None or right is None else not decisive
            if type(node) is g.Not:
                value = evaluate(node.this, values)
                return None if value is None else not value
            return evaluate(node.this, values)

        for _ in range(100):
            sql = generate(4)
            actual = s.simplify(g.from_sqlglot(self.sqlglot.parse_one(sql)), g.canonical_sql)
            rendered = g.to_sqlglot(actual).sql()
            reparsed = g.from_sqlglot(self.sqlglot.parse_one(rendered))
            # Compare the simplified value to its rendered SQL, not to the input:
            # upstream's nullable-complement/constant rules are not SQL-correct.
            for bits in itertools.product((False, True, None), repeat=3):
                values = dict(zip("abc", bits))
                self.assertEqual(evaluate(actual, values), evaluate(reparsed, values), (sql, rendered))

    def test_no_reference_simplification_in_normalize(self):
        normalizer = importlib.import_module("sqlglot.optimizer.normalize")
        sql = "SELECT * FROM t JOIN u ON TRUE WHERE ((a AND b) OR c) AND 1 = 1"
        original = self.sqlglot.parse_one(sql)
        expected = normalizer.normalize(original.copy())

        def forbidden(*args, **kwargs):
            raise AssertionError("reference simplifier rule fallback")

        rules = ("simplify", "uniq_sort", "absorb_and_eliminate", "simplify_not", "flatten",
                 "simplify_connectors", "remove_compliments", "simplify_literals",
                 "_simplify_binary", "simplify_parens", "remove_where_true")
        with patch.multiple(self.upstream, **dict.fromkeys(rules, forbidden)), \
             patch.object(normalizer, "simplify", side_effect=AssertionError("fallback")), \
             patch.object(normalizer, "distributive_law", side_effect=AssertionError("fallback")):
            actual = g.normalize(original)
        self.assertEqual(signature(actual), signature(expected))
        self.assertEqual(signature(g.normalize_reference_simplify(original)), signature(expected))

    def test_full_pass_sharing_and_pure_record_pipeline(self):
        a, b, c = atom("a"), atom("b"), atom("c")
        shared = g.Or(g.Not(a), b)
        root = g.Expression("Select", (
            g.Argument("expressions", g.Sequence((g.And(a, g.Paren(shared)), shared))),
            g.Argument("extra", c)))
        old_args = root.args
        with patch.object(g, "reference", side_effect=AssertionError("fallback")):
            changed = s.absorb_and_eliminate(root.args[0].value.items[0])
            self.assertIs(changed.left, a)
            self.assertIs(changed.right.this.right, b)
            self.assertTrue(s.sql_equal(changed.right.this.left, s.FALSE))
            self.assertIs(shared.left.this, a)
            self.assertIs(root.args, old_args)
            self.assertIs(s.simplify(g.And(s.TRUE, a), lambda node: s.kind(node)), a)
            self.assertIs(s.simplify(g.And(a, b), lambda node: s.kind(node)).left, a)
        # A plain no-op keeps the whole root and all argument/sequence identities.
        stable = g.Expression("Select", (g.Argument("expressions", g.Sequence((a, b))),))
        self.assertIs(s.simplify(stable, s.kind), stable)
        self.assertIs(g.normalize_records(stable, s.kind), stable)
        self.assertFalse(s.same_value(g.And(a, b), g.Or(a, b)))

    def test_boolean_pass_rule_order_without_reference_simplifier(self):
        # An independent mutable control with exactly the subset/order ported,
        # not upstream simplify (which also performs absorption and literals).
        def control(node):
            node = self.upstream.uniq_sort(node)
            self.sqlglot.exp.replace_children(node, control)
            node = self.upstream.simplify_not(node)
            node = self.upstream.flatten(node)
            node = self.upstream.simplify_connectors(node)
            return self.upstream.remove_compliments(node)

        for sql in ("NOT (a OR b)", "a AND TRUE", "NULL AND FALSE", "a OR NOT a",
                    "a AND (b AND a)", "NOT (NOT a AND b)",
                    "SELECT * FROM t WHERE a AND TRUE", "(a OR b) AND (a OR NOT b)"):
            original = self.sqlglot.parse_one(sql)
            expected = control(original.copy())
            with patch.object(self.upstream, "simplify", side_effect=AssertionError("fallback")):
                actual = s.boolean_pass(g.from_sqlglot(original), g.canonical_sql)
            self.assertEqual(signature(g.to_sqlglot(actual)), signature(expected), sql)


if __name__ == "__main__":
    unittest.main()
