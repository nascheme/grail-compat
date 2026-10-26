"""Compact record-native generator parity, context and no-fallback checks."""

import importlib
import importlib.util
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
from sqlglot_port import generate as r


# Base rendering is tested even for inputs parsed in other dialects; these are
# NOT claims of dialect-specific output support.
FIXTURES = (
    ("SELECT a, t.B, db.t.c, *, t.* FROM catalog.db.t AS t WHERE a = 1", None),
    ("SELECT 'it''s', 'a\\b', \"Mixed Case\", NULL, TRUE, FALSE, ?", None),
    ("SELECT a + b * 2, a / b, a % b, a || b, a & b, ~a, -a FROM t", None),
    ("SELECT * FROM t WHERE NOT (a = 1 AND b <> 2) OR c >= 3 AND d <= 4", None),
    ("SELECT * FROM t WHERE a BETWEEN 1 AND 4 AND b IN (1, 2) AND c IS NOT NULL", None),
    ("SELECT a LIKE 'a%' ESCAPE 'x', a ILIKE 'b%', a > b, a < b FROM t", None),
    ("SELECT CAST(a AS DECIMAL(10,2)), TRY_CAST(b AS INT), EXTRACT(YEAR FROM d)", None),
    ("SELECT CASE a WHEN 1 THEN 'x' ELSE 'y' END, IF(a, b, c), COALESCE(a, b, c)", None),
    ("SELECT COUNT(DISTINCT a), SUM(b), f(a, 1), CURRENT_DATE, CURRENT_DATE('UTC') FROM t", None),
    ("SELECT a, SUM(b) FROM t GROUP BY a HAVING COUNT(*) > 1 ORDER BY a DESC LIMIT 5 OFFSET 2", None),
    ("SELECT * FROM t LEFT JOIN u ON t.a = u.a JOIN v USING (b, c)", None),
    ("WITH RECURSIVE q AS (SELECT 1 AS a) SELECT a FROM q UNION ALL SELECT 2", None),
    ("SELECT 1 UNION SELECT 2 INTERSECT SELECT 3 EXCEPT SELECT 4", None),
    ("SELECT * FROM (SELECT a FROM t) AS q WHERE EXISTS(SELECT 1 FROM u)", None),
    ("SELECT * FROM t WHERE a IN (SELECT a FROM u) AND b > ALL(SELECT b FROM v)", None),
    ("SELECT SUM(a) OVER (PARTITION BY b ORDER BY c ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) FROM t", None),
    ("SELECT ARRAY(1, 2)[0], ARRAY(1)[1], ARRAY(1)[a]", None),
    ("SELECT * FROM UNNEST(ARRAY(1, 2)) AS t(x)", None),
    ("SELECT a FROM t GROUP BY a GROUPING SETS ((a, b), (a)) CUBE(a) ROLLUP(b)", None),
    ("SELECT * FROM t TABLESAMPLE BERNOULLI(10 PERCENT)", None),
    ("SELECT * FROM t TABLESAMPLE (BUCKET 1 OUT OF 2 ON a)", "hive"),
    ("SELECT * FROM t LATERAL VIEW OUTER EXPLODE(a) u AS x", "hive"),
    ("SELECT ARRAY_SORT(a, (x, y) -> x - y), FILTER(a, x -> x > 0)", "spark"),
    ("SELECT CAST(a AS ARRAY<STRUCT<x: INT, y: VARCHAR>>)", "spark"),
    ("SELECT COUNT(*) FILTER(WHERE a > 0), PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY b) FROM t", "postgres"),
    ("SELECT ts AT TIME ZONE 'UTC' FROM t", "postgres"),
    ("SELECT * EXCEPT(a) REPLACE(b AS c) FROM t", "bigquery"),
    ("SELECT DATE_ADD(d, INTERVAL 1 DAY), DATE_DIFF(a, b, DAY)", "bigquery"),
    ("SELECT `a`, 'it\\'s' FROM `t`", "mysql"),
    ("SELECT a FROM t QUALIFY ROW_NUMBER() OVER(PARTITION BY b ORDER BY c) = 1", "snowflake"),
    ("VALUES (1, 'a'), (2, 'b')", None),
    ("CREATE TABLE IF NOT EXISTS t (a INT NOT NULL DEFAULT 1, b VARCHAR(10), CONSTRAINT pk PRIMARY KEY (a))", None),
    ("CREATE TABLE t (a INT, b INT, FOREIGN KEY (a) REFERENCES u(a) ON DELETE CASCADE)", None),
    ("CREATE OR REPLACE TEMPORARY VIEW v AS SELECT a FROM t", None),
    ("CREATE TABLE t (a INT) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8 COLLATE=utf8_bin COMMENT='x'", "mysql"),
    ("CREATE TABLE t WITH (FORMAT = 'PARQUET', PARTITIONED_BY = ARRAY['a']) AS SELECT a FROM u", "presto"),
    ("DROP TABLE IF EXISTS t", None),
    ("INSERT INTO t SELECT a FROM u WHERE b > 0", None),
    ("INSERT OVERWRITE TABLE t PARTITION(a='x') SELECT b FROM u", "hive"),
    ("UPDATE t SET a = 1, b = 2 FROM u WHERE t.c = u.c", None),
    ("DELETE FROM t WHERE a = 1", None),
    ("CACHE LAZY TABLE t OPTIONS('storageLevel' = 'DISK_ONLY') AS SELECT * FROM u", "spark"),
    ("UNCACHE TABLE IF EXISTS t", "spark"),
    ("SHOW TABLES", "mysql"),
)


class PureGenerationTests(unittest.TestCase):
    def test_generic_records_and_immutable_options(self):
        a = g.Expression("Identifier", (g.Argument("this", "A"),))
        shared = g.Paren(g.Or(a, g.Not(a)))
        root = g.And(shared, shared)
        with patch.object(g, "reference", side_effect=AssertionError("reference dependency")), \
             patch.object(g, "to_sqlglot", side_effect=AssertionError("materialization")):
            self.assertEqual(r.canonical_sql(root), '("a" OR NOT "a") AND ("a" OR NOT "a")')
            self.assertEqual(r.generate(root), '(A OR NOT A) AND (A OR NOT A)')
        self.assertIs(root.left, root.right)
        self.assertIs(shared.this.left, a)
        # One immutable Window occurs both as an OVER expression and a named
        # declaration. Context belongs to occurrences, not the shared value.
        window = g.Expression("Window", (g.Argument("this", a),
                                         g.Argument("partition_by", g.Sequence((a,)))))
        query = g.Expression("Select", (g.Argument("expressions", g.Sequence((window,))),
                                         g.Argument("window", window)))
        self.assertEqual(r.canonical_sql(query),
                         'SELECT "a" OVER (PARTITION BY "a") WINDOW "a" AS (PARTITION BY "a")')
        self.assertTrue(r.CANONICAL.identify)
        with self.assertRaises((AttributeError, TypeError)):
            r.CANONICAL.identify = False
        with self.assertRaises(NotImplementedError):
            r.generate(root, pretty=True)
        with self.assertRaises(ValueError):
            r.generate(g.Expression("UnknownExtension", ()))
        with self.assertRaises(ValueError):
            r.generate(root, unsupported="unknown")
        with self.assertRaises(TypeError):
            r.generate("not a tree")

    def test_normalization_without_reference_services(self):
        a, b, c = (g.Expression("Identifier", (g.Argument("this", name),)) for name in "ABC")
        root = g.Or(g.Paren(g.And(a, b)), c)
        with patch.object(g, "reference", side_effect=AssertionError("reference dependency")), \
             patch.object(g, "canonical_sql_reference", side_effect=AssertionError("reference renderer")), \
             patch.object(g, "to_sqlglot", side_effect=AssertionError("materialization")):
            actual = g.normalize_records(root)
            self.assertEqual(r.canonical_sql(actual), '("a" OR "c") AND ("b" OR "c")')
        self.assertIs(root.left.this.left, a)
        self.assertIs(root.left.this.right, b)
        self.assertIs(root.right, c)

    def test_diagnostics_are_local(self):
        root = g.Expression("Hint", (g.Argument("this", "hint"),))
        self.assertEqual(r.render(root).unsupported, ("Hints are not supported",))
        self.assertEqual(r.render(g.Expression("Null", ())).unsupported, ())
        with self.assertLogs("sqlglot", level="WARNING") as logs:
            self.assertEqual(r.generate(root), "")
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(r.generate(root, unsupported="ignore"), "")
        with self.assertRaises(r.UnsupportedError):
            r.generate(root, unsupported="raise")


class ReferenceGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.sqlglot = g.reference()
        except ModuleNotFoundError as exc:
            if exc.name != "sqlglot":
                raise
            raise unittest.SkipTest("install sqlglot==4.6.0 for generator comparisons")
        cls.upstream = importlib.import_module("sqlglot.generator")
        cls.exp = cls.sqlglot.exp

    def compare(self, value, options=r.CANONICAL):
        node = g.from_sqlglot(value)
        # Fresh root occurrences match the old canonical-key adapter contract,
        # not stale arg_key metadata on detached reference subtrees.
        occurrence = g.to_sqlglot(node)
        kwargs = {key: getattr(options, key) for key in (
            "identify", "normalize", "identifier", "quote", "escape", "normalize_functions",
            "index_offset", "unnest_column_only", "alias_post_tablesample")}
        generator = self.upstream.Generator(pretty=False, **kwargs)
        try:
            expected = generator.generate(occurrence)
        except Exception as exc:
            with self.assertRaises(type(exc)):
                r.generate(node, options)
        else:
            with patch.object(g, "to_sqlglot", side_effect=AssertionError("materialization")), \
                 patch.object(self.upstream.Generator, "generate", side_effect=AssertionError("fallback")), \
                 patch.object(self.upstream.Generator, "sql", side_effect=AssertionError("fallback")):
                actual = r.generate(node, options)
            self.assertEqual(actual, expected, repr(value))

    def test_function_metadata_and_every_function(self):
        expected = {name: (cls.sql_name(), tuple(cls.arg_types))
                    for name, cls in vars(self.exp).items()
                    if isinstance(cls, type) and issubclass(cls, self.exp.Func) and cls is not self.exp.Func}
        self.assertEqual(dict(r.FUNCTIONS), expected)
        from sqlglot.tokens import Tokenizer
        self.assertEqual(r.ESCAPE_CODE, Tokenizer.ESCAPE_CODE)
        for name, (_, keys) in expected.items():
            cls = getattr(self.exp, name)
            args = {key: self.exp.column(f"X{i}") for i, key in enumerate(keys)}
            if "expressions" in args:
                args["expressions"] = [self.exp.column("Y"), self.exp.column("Z")]
            node = cls(**args)
            for options in (r.DEFAULT, r.CANONICAL, r.Options(normalize_functions="lower"),
                            r.Options(normalize_functions=None)):
                with self.subTest(name=name, options=repr(options)):
                    self.compare(node, options)

    def test_queries_and_all_subtrees(self):
        configurations = (r.DEFAULT, r.CANONICAL,
                          r.Options(identify=True, normalize=True, identifier="`", quote='"', escape="\\",
                                    index_offset=1, unnest_column_only=True, alias_post_tablesample=True))
        for query, dialect in FIXTURES:
            with self.subTest(query=query, dialect=dialect):
                original = self.sqlglot.parse_one(query, read=dialect)
                before = repr(original)
                for options in configurations:
                    self.compare(original, options)
                for child, _, _ in original.walk():
                    with self.subTest(kind=type(child).__name__):
                        self.compare(child)
                self.assertEqual(repr(original), before)

    def test_data_types_and_programmatic_shapes(self):
        e = self.exp
        values = [e.DataType.build(dtype) for dtype in e.DataType.Type]
        values += [e.Literal.string("a'b"), e.Literal.string("a\\b"),
                   e.Literal.string("__sqlglot_escape__'"),
                   e.Identifier(this='a"b', quoted=True),
                   e.Fetch(direction="FIRST", count=e.Literal.number(3)),
                   e.Hint(this="hint"), e.Annotation(this="annotation", expression=e.column("A")),
                   e.Aliases(this=e.column("a"), expressions=[e.column("b"), e.column("c")]),
                   e.CharacterSet(this="utf8", default=True),
                   e.ColumnDef(this=e.to_identifier("a"), kind=e.DataType.build("INT"),
                               unique=True, primary=True, auto_increment=True,
                               default=e.Literal.number(1), collate=e.to_identifier("B"),
                               comment=e.Literal.string("x")),
                   e.Property(this=e.Literal.string("key"), value=e.Literal.string("value")),
                   e.Properties(expressions=[e.AnonymousProperty(this=e.Literal.string("key"), value=e.Literal.number(1)),
                                             e.LocationProperty(value=e.Literal.string("here"))]),
                   e.IntDiv(this=e.column("A"), expression=e.Literal.number(2)),
                   e.Window(this=e.Sum(this=e.column("a")), alias=e.to_identifier("w")),
                   e.Select(expressions=[e.Window(this=e.Sum(this=e.column("a")), alias=e.to_identifier("w"))],
                            window=e.Window(this=e.to_identifier("w"), partition_by=[e.column("b")],
                                            order=e.Order(expressions=[e.Ordered(this=e.column("c"))]))),
                   e.Array(expressions=[]), e.Coalesce(this=e.column("a"), expressions=[])]
        for cls in (e.AutoIncrementProperty, e.CollateProperty, e.EngineProperty,
                    e.FileFormatProperty, e.LocationProperty, e.PartitionedByProperty,
                    e.SchemaCommentProperty, e.TableFormatProperty):
            values.append(cls(value=e.Literal.string("x")))
        values.append(e.CharacterSetProperty(value=e.Literal.string("utf8"), default=True))
        for value in values:
            with self.subTest(value=repr(value)):
                self.compare(value)

    def test_randomized_expression_generation(self):
        rng = random.Random(514)
        atoms = ('a', 'B', '"Quoted"', "'it''s'", '0', '1.5', 'NULL', 'TRUE', 't.x')

        def expression(depth):
            if depth == 0 or rng.random() < 0.25:
                return rng.choice(atoms)
            rule = rng.randrange(5)
            if rule == 0:
                return f"NOT ({expression(depth - 1)})"
            if rule == 1:
                return f"COALESCE({expression(depth - 1)}, {expression(depth - 1)})"
            return f"({expression(depth - 1)} {rng.choice(('AND', 'OR', '+', '*', '=', '<>'))} {expression(depth - 1)})"

        for _ in range(200):
            query = expression(4)
            self.compare(self.sqlglot.parse_one(query))

    def test_integrated_controls_and_default_cli_output(self):
        for sql in ("(a AND b) OR c", "SELECT * FROM t WHERE a AND (a OR b)",
                    "SELECT CASE WHEN a THEN 'it''s' ELSE 'x' END FROM t WHERE TRUE"):
            original = self.sqlglot.parse_one(sql)
            expected = g.normalize_reference_keys(original).sql()
            self.assertEqual(g.normalize(original).sql(), expected)
            self.assertEqual(g.normalize_reference_simplify(original).sql(), expected)
            with patch.object(g, "to_sqlglot", side_effect=AssertionError("materialization")), \
                 patch.object(self.upstream.Generator, "generate", side_effect=AssertionError("fallback")):
                self.assertEqual(g.main(sql), expected)

    def test_tpch_default_and_canonical(self):
        source = BENCH / "sqlglot_port/upstream.py"
        spec = importlib.util.spec_from_file_location("_generator_fixture", source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: module, "pyperf": types.ModuleType("pyperf")}):
            spec.loader.exec_module(module)
        original = self.sqlglot.parse_one(module.SQL)
        optimized = module.optimize(original.copy(), module.TPCH_SCHEMA)
        for query in (original, optimized):
            self.compare(query, r.DEFAULT)
            self.compare(query, r.CANONICAL)
            for child, _, _ in query.walk():
                self.compare(child)


if __name__ == "__main__":
    unittest.main()
