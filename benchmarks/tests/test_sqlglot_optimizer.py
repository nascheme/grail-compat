"""Immutable scope graph and profiled optimizer-tail differential contracts."""

from contextlib import closing
import importlib
import importlib.util
import os
import random
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
from sqlglot_port import idiomatic as t
from sqlglot_port import scope as sc
from sqlglot_port import optimizer as o
from sqlglot_port import simplify as s
from test_sqlglot_rewrite import signature, value_signature


QUERIES = (
    "SELECT a FROM t", "SELECT * FROM t, u", "SELECT * FROM t,T",
    "SELECT x.a FROM t AS x JOIN u AS y ON x.a=y.a",
    "SELECT * FROM (SELECT a FROM t) AS q WHERE q.a=1",
    "SELECT * FROM (SELECT a FROM t) WHERE a=1",
    "WITH q AS (SELECT a FROM t) SELECT * FROM q",
    "WITH q AS (SELECT a FROM t), r AS (SELECT * FROM q) SELECT * FROM r JOIN q ON r.a=q.a",
    "SELECT a FROM t UNION ALL SELECT a FROM u",
    "WITH q AS (SELECT a FROM t) SELECT a FROM q UNION SELECT a FROM q",
    "SELECT a FROM t INTERSECT SELECT a FROM u EXCEPT SELECT a FROM v",
    "SELECT a FROM t WHERE EXISTS(SELECT 1 FROM u WHERE u.a=t.a)",
    "SELECT (SELECT MAX(u.a) FROM u WHERE u.b=t.b) AS x FROM t",
    "SELECT q.a FROM (SELECT a FROM t) AS q(x)",
    "WITH q(x) AS (SELECT a FROM t) SELECT * FROM q",
    "SELECT a AS x FROM t GROUP BY x ORDER BY x",
    "SELECT a AS x FROM t QUALIFY x > 0",
    "SELECT * FROM UNNEST(ARRAY(1,2)) AS x(a)",
    "SELECT * FROM t LATERAL VIEW EXPLODE(t.a) u AS x",
    "SELECT * FROM t AS x JOIN u AS y ON x.a=y.a WHERE y.b > 1",
    "SELECT * FROM t AS x LEFT JOIN u AS y ON x.a=y.a WHERE y.b > 1",
    "SELECT * FROM t AS x RIGHT JOIN u AS y ON x.a=y.a WHERE x.b > 1",
    "SELECT * FROM (SELECT a AS b FROM t) AS q WHERE q.b=1 AND q.b<4",
    "SELECT * FROM (SELECT a, SUM(b) AS s FROM t GROUP BY a) AS q WHERE q.a=1",
    "SELECT * FROM (SELECT a FROM t) AS q WHERE (q.a=1 AND q.a<3) OR (q.a=4 AND q.a<6)",
    "SELECT * FROM (SELECT a FROM t) AS q JOIN (SELECT a FROM u) AS r ON q.a=r.a WHERE (q.a=1 AND r.a=2) OR (q.a=3 AND r.a=4)",
    "SELECT * FROM (SELECT a FROM t) AS q JOIN (SELECT a FROM u) AS r ON q.a=r.a WHERE q.a=1 OR r.a=2",
    "SELECT * FROM (SELECT a FROM t) AS q JOIN (SELECT a FROM t) AS r ON q.a=r.a",
    "SELECT 1 AS x UNION ALL SELECT 1 AS x",
    "SELECT 1 AS x UNION ALL SELECT 1 AS x UNION ALL SELECT 1 AS x",
    "SELECT * FROM t AS x CROSS JOIN u AS y JOIN v AS z ON x.a=z.a AND y.a=z.a",
    "SELECT * FROM t AS x JOIN v AS z ON y.a=z.a JOIN u AS y ON x.a=y.a",
    "SELECT * FROM (SELECT a,b FROM t) AS q JOIN (SELECT a,b FROM u) AS r ON q.a=r.a WHERE q.a OR (q.b AND r.b)",
    "SELECT * FROM (SELECT a,b FROM t) AS q WHERE q.a=(SELECT MAX(q.a))",
)


class ScopeValueTests(unittest.TestCase):
    def test_path_copying_and_occurrences(self):
        atom = o.expression("Identifier", this="a")
        shared = o.expression("Table", this=atom)
        root = o.expression("Select", **{"from": o.expression("From", expressions=t.Sequence((shared, shared)))})
        with patch.object(t, "reference", side_effect=AssertionError("reference dependency")):
            graph = sc.build_scopes(root)
            self.assertEqual(len(graph.scopes[0].tables), 2)
            self.assertEqual(graph.scopes[0].tables[0], ("from", "expressions", 0))
            changed = sc.put(root, ("from", "expressions", 0, "this"), o.identifier("b"))
        self.assertIs(sc.get(changed, ("from", "expressions", 1)), shared)
        self.assertIs(sc.get(root, ("from", "expressions", 0)), shared)
        self.assertIs(graph.root, root)
        self.assertIs(sc.put(root, ("from", "expressions", 0), shared), root)
        with self.assertRaises((AttributeError, TypeError)):
            graph.scopes[0].sources = ()
        shared_query = o.expression("Select", expressions=t.Sequence((o.expression("Literal", this="1", is_string=False),)))
        derived = tuple(o.expression("Subquery", this=shared_query,
                                     alias=o.expression("TableAlias", this=o.identifier(name))) for name in ("x", "y"))
        dag = o.expression("Select", **{"from": o.expression("From", expressions=t.Sequence(derived))})
        graph = sc.build_scopes(dag)
        self.assertEqual(graph.order, (1, 2, 0))
        self.assertEqual(tuple(source.scope_id for source in graph.scopes[0].sources), (1, 2))
        self.assertIs(sc.get(graph.root, graph.scopes[1].path), sc.get(graph.root, graph.scopes[2].path))

    def test_pure_tail_and_graph_ids(self):
        table = o.expression("Alias", this=o.expression("Table", this=o.identifier("t")),
                             alias=o.expression("TableAlias", this=o.identifier("x")))
        query = o.expression("Select", expressions=t.Sequence((o.expression("Star"),)),
                             **{"from": o.expression("From", expressions=t.Sequence((table,)))})
        with patch.object(t, "reference", side_effect=AssertionError("reference dependency")), \
             patch.object(t, "to_sqlglot", side_effect=AssertionError("materialization")):
            result = o.optimize_tail(query)
        self.assertEqual(sc.build_scopes(result).order, (0,))
        self.assertTrue(sc.get(result, ("from", "expressions", 0, "this", "this")).args[-1].value)
        self.assertIs(query.args[0], result.args[0])


class ReferenceOptimizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.sqlglot = t.reference()
        except ModuleNotFoundError as exc:
            if exc.name != "sqlglot":
                raise
            raise unittest.SkipTest("install sqlglot==4.6.0 for differential tests")
        cls.scope = importlib.import_module("sqlglot.optimizer.scope")
        cls.upstream = importlib.import_module("sqlglot.optimizer.optimizer")

    def compare_scopes(self, original):
        # Reference normalization can share/reparent operands. Scope comparisons
        # use independent occurrences, not ambiguous mutable DAG backlinks.
        original = t.to_sqlglot(t.from_sqlglot(original))
        graph = sc.build_scopes(t.from_sqlglot(original))
        paths = {}

        def collect(node, path=()):
            paths[id(node)] = path
            for key, value in node.args.items():
                if isinstance(value, self.sqlglot.exp.Expression):
                    collect(value, path + (key,))
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, self.sqlglot.exp.Expression):
                            collect(item, path + (key, i))
        collect(original)
        expected = self.scope.traverse_scope(original)
        self.assertEqual([graph.scopes[sid].path for sid in graph.order], [paths[id(scope.expression)] for scope in expected])
        for sid, scope in zip(graph.order, expected):
            actual = graph.scopes[sid]
            with self.subTest(query=original.sql(), scope=scope.expression.sql()):
                self.assertEqual(actual.scope_type, scope.scope_type.name)
                self.assertEqual(actual.parent is None, scope.parent is None)
                if scope.parent:
                    self.assertEqual(graph.scopes[actual.parent].path, paths[id(scope.parent.expression)])
                self.assertEqual(actual.outer_columns, tuple(scope.outer_column_list))
                for key in ("tables", "ctes", "derived_tables", "subqueries"):
                    self.assertEqual(getattr(actual, key), tuple(paths[id(value)] for value in getattr(scope, key)))
                self.assertEqual(sc.columns(graph, sid), tuple(paths[id(value)] for value in scope.columns))
                self.assertEqual(sc.columns(graph, sid, external=True), tuple(paths[id(value)] for value in scope.external_columns))
                sources = {source.name: (source.path, source.scope_id is not None) for source in actual.sources}
                expected_sources = {name: (paths[id(source.expression)] if isinstance(source, self.scope.Scope) else paths[id(source)],
                                           isinstance(source, self.scope.Scope)) for name, source in scope.sources.items()}
                self.assertEqual(sources, expected_sources)
                self.assertEqual([value.name for value in actual.selected], list(scope.selected_sources))
                for value in actual.selected:
                    self.assertEqual(value.path, paths[id(scope.selected_sources[value.name][0])])
                if scope.union:
                    self.assertEqual(tuple(graph.scopes[i].path for i in actual.union), tuple(paths[id(value.expression)] for value in scope.union))

    def test_scope_graph_and_correlations(self):
        for sql in QUERIES:
            with self.subTest(sql=sql):
                self.compare_scopes(self.sqlglot.parse_one(sql, read="snowflake" if "QUALIFY" in sql else None))

    def test_individual_tail_stages(self):
        for sql in QUERIES:
            for stage in o.STAGES:
                with self.subTest(sql=sql, stage=stage):
                    original = self.sqlglot.parse_one(sql, read="snowflake" if "QUALIFY" in sql else None)
                    before = signature(original)
                    # Qualification is outside this stage's scope. The fixtures
                    # intentionally also exercise permitted unqualified inputs.
                    try:
                        expected = getattr(self.upstream, stage)(original.copy())
                    except Exception as exc:
                        with self.assertRaises(type(exc)):
                            getattr(o, stage)(t.from_sqlglot(original))
                    else:
                        node = t.from_sqlglot(original)
                        with patch.object(t, "reference", side_effect=AssertionError("reference dependency")), \
                             patch.object(t, "to_sqlglot", side_effect=AssertionError("materialization")):
                            actual = getattr(o, stage)(node)
                        self.assertEqual(value_signature(t.to_sqlglot(actual)), value_signature(expected))
                        self.assertEqual(signature(t.to_sqlglot(node)), before)
                    self.assertEqual(signature(original), before)

    def test_randomized_optimizer_pipeline(self):
        rng = random.Random(217)
        schema = {name: {column: "int" for column in ("a", "b")} for name in ("t", "u", "v")}
        atoms = ("q.a=1", "q.b>2", "r.a=3", "r.b<4", "q.a=r.a")

        def predicate(depth):
            if depth == 0 or rng.random() < 0.3:
                return rng.choice(atoms)
            return f"({predicate(depth - 1)} {rng.choice(('AND', 'OR'))} {predicate(depth - 1)})"

        for _ in range(100):
            query = ("SELECT q.a, r.b FROM (SELECT a,b FROM t) AS q "
                     f"{rng.choice(('JOIN', 'LEFT JOIN', 'RIGHT JOIN'))} "
                     f"(SELECT a,b FROM u) AS r ON q.a=r.a WHERE {predicate(3)}")
            original = self.sqlglot.parse_one(query)
            before = signature(original)
            expected = self.upstream.optimize(original, schema)
            actual = t.optimize(original, schema)
            with self.subTest(query=query):
                self.assertEqual(value_signature(actual), value_signature(expected))
                self.assertEqual(actual.sql(), expected.sql())
                self.assertEqual(signature(original), before)
        for query in (
            "SELECT q.a FROM (SELECT a,b FROM t) q WHERE q.a IN (SELECT u.a FROM u WHERE u.b=q.b)",
            "SELECT q.a FROM (SELECT a,b FROM t) q WHERE EXISTS (SELECT 1 FROM u WHERE u.a=q.a)",
            "WITH q AS (SELECT a,b FROM t) SELECT q.a FROM q JOIN q AS r ON q.a=r.a",
            "SELECT a FROM t UNION ALL SELECT a FROM t UNION ALL SELECT a FROM u",
        ):
            with self.subTest(query=query):
                original = self.sqlglot.parse_one(query)
                self.assertEqual(signature(t.optimize(original, schema)), signature(self.upstream.optimize(original, schema)))

    def test_execution_oracle(self):
        try:
            import sqlite3
        except ImportError:
            self.skipTest("sqlite3 unavailable")
        queries = (
            "SELECT q.a FROM (SELECT a FROM t) q WHERE q.a > 1",
            "SELECT q.a, r.b FROM (SELECT a,b FROM t) q JOIN (SELECT a,b FROM u) r ON q.a=r.a WHERE r.b<4",
            "SELECT q.a, r.b FROM (SELECT a,b FROM t) q LEFT JOIN (SELECT a,b FROM u) r ON q.a=r.a WHERE r.b<4",
            "SELECT q.a FROM (SELECT a FROM t) q WHERE q.a=1 OR q.a=3",
            "SELECT q.a FROM (SELECT a FROM t) q JOIN (SELECT a FROM t) r ON q.a=r.a",
            "SELECT a FROM t UNION ALL SELECT a FROM t",
        )
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript("CREATE TABLE t(a,b); CREATE TABLE u(a,b); "
                                     "INSERT INTO t VALUES(1,2),(2,3),(3,4),(NULL,5); "
                                     "INSERT INTO u VALUES(1,1),(2,2),(3,6),(NULL,7);")
            for query in queries:
                node = t.from_sqlglot(self.sqlglot.parse_one(query))
                result = o.optimize_tail(node)
                actual_sql = t.to_sqlglot(result).sql()
                with self.subTest(query=query, actual=actual_sql):
                    self.assertEqual(sorted(connection.execute(query).fetchall(), key=repr),
                                     sorted(connection.execute(actual_sql).fetchall(), key=repr))

    def test_error_and_cycle_contracts(self):
        for query in ("WITH q AS (SELECT 1) SELECT * FROM t AS q",):
            original = self.sqlglot.parse_one(query)
            with self.assertRaises(sc.OptimizeError):
                sc.build_scopes(t.from_sqlglot(original))
        for root in (None, o.expression("Literal", this="1", is_string=False)):
            with self.assertRaisesRegex(sc.OptimizeError, "Unexpected expression type"):
                sc.build_scopes(root)
        with self.assertRaisesRegex(ValueError, "Cycle error"):
            o._tsort({"a": ("b",), "b": ("a",)})
        query = self.sqlglot.parse_one("SELECT * FROM t JOIN u ON u.a=v.a JOIN v ON v.a=u.a")
        with self.assertRaisesRegex(ValueError, "Cycle error"):
            o.optimize_joins(t.from_sqlglot(query))

    def test_tpch_stage_boundaries(self):
        source = BENCH / "sqlglot_port/upstream.py"
        spec = importlib.util.spec_from_file_location("_tail_fixture", source)
        fixture = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: fixture, "pyperf": types.ModuleType("pyperf")}):
            spec.loader.exec_module(fixture)
        original = self.sqlglot.parse_one(fixture.SQL)
        for stage in ("qualify_tables", "isolate_table_selects", "qualify_columns", "pushdown_projections", "normalize", "unnest_subqueries"):
            original = (getattr(self.upstream, stage)(original, fixture.TPCH_SCHEMA) if stage == "qualify_columns"
                        else getattr(self.upstream, stage)(original))
        self.compare_scopes(original)
        node = t.from_sqlglot(original)
        for stage in o.STAGES:
            with self.subTest(stage=stage):
                original = getattr(self.upstream, stage)(original)
                simplifier = importlib.import_module("sqlglot.optimizer.simplify")
                with patch.object(t, "reference", side_effect=AssertionError("reference dependency")), \
                     patch.object(t, "to_sqlglot", side_effect=AssertionError("materialization")), \
                     patch.object(self.scope, "traverse_scope", side_effect=AssertionError("scope fallback")), \
                     patch.object(simplifier, "simplify", side_effect=AssertionError("simplifier fallback")):
                    node = getattr(o, stage)(node)
                actual = t.to_sqlglot(node)
                self.assertEqual(signature(actual), signature(original))
                self.assertEqual(actual.sql(), original.sql())


if __name__ == "__main__":
    unittest.main()
