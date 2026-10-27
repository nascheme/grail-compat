"""API regression checks on the idiomatic versions; no benchmark timings.

Backend-agnostic: native-only checks skip on grail-compat (backend "shim").
"""

import ast
import copy
from pathlib import Path
import sys
import sysconfig
import unittest

import grail
from bench_support import BENCHMARKS, load_benchmark

SHIM = grail.backend == "shim"
native_only = unittest.skipIf(SHIM, "native only")

aggregate, chaos, deepcopy, mandelbrot, raytrace, richards = (
    load_benchmark(name) for name in
    ("aggregate", "chaos", "deepcopy", "mandelbrot", "raytrace", "richards"))


class NativeTests(unittest.TestCase):
    @native_only
    def test_backend(self):
        self.assertEqual(grail.backend, "native-phase1")

    def test_imports(self):
        if sysconfig.get_config_var("Py_GIL_DISABLED"):
            self.assertFalse(sys._is_gil_enabled())
        self.assertFalse(Path(grail.__file__).resolve().is_relative_to(BENCHMARKS))
        # buildgraph's hybrid.py eagerly warms a machine-sized thread pool
        # at import; leave its execution to the challenge harness.
        for path in sorted(BENCHMARKS.glob("*/*.py")):
            if path.stem == "idiomatic" or (path.stem == "hybrid"
                                            and path.parent.name != "buildgraph"):
                with self.subTest(path=path.relative_to(BENCHMARKS)):
                    load_benchmark(path.parent.name, path.name)
        if sysconfig.get_config_var("Py_GIL_DISABLED"):
            self.assertFalse(sys._is_gil_enabled())

    def test_no_grail_shadowing(self):
        # A benchmark dir on sys.path must not shadow the grail package.
        self.assertEqual([p for p in BENCHMARKS.rglob("grail*")
                          if p.stem == "grail"], [])

    def test_rewrites_define_only_records(self):
        # hybrid.py files are earlier conversions kept as comparison controls.
        paths = sorted(BENCHMARKS.glob("*/idiomatic.py"))
        self.assertEqual(len(paths), 20)
        paths.extend(sorted((BENCHMARKS / "sqlglot_port").glob("*.py")))
        paths = [p for p in paths if p.name != "upstream.py"]
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ClassDef):
                    # SQLGlot's explicit failure boundaries use exception types,
                    # not application data or mutable AST classes.
                    if (path.parent.name == "sqlglot_port"
                            and node.name in ("UnsupportedError", "OptimizeError")
                            and [ast.unparse(b) for b in node.bases] == ["ValueError"]):
                        continue
                    names = [ast.unparse(d) for d in node.decorator_list]
                    self.assertIn("grail.record", names,
                                  f"{path.relative_to(BENCHMARKS)}: {node.name}")

    def test_generic_dispatch_and_keywords(self):
        data = richards.DeviceData(None)
        new, action = richards.step(data=data, packet=None)
        self.assertIs(new, data)
        self.assertIs(action, richards.WAIT)
        point = raytrace.Point(0.0, 0.0, 0.0)
        surface = raytrace.SimpleSurface(raytrace.BLACK)
        self.assertIs(raytrace.base_colour_at(surface=surface, p=point),
                      raytrace.BLACK)
        normal = raytrace.Vec(0.0, 1.0, 0.0)
        shape = raytrace.Halfspace(point, normal)
        self.assertIs(raytrace.normal_at(shape=shape, p=point), normal)
        ray = raytrace.Ray(point, normal)
        self.assertEqual(raytrace.intersection_time(shape=shape, ray=ray), -1.0)

    def test_record_arithmetic(self):
        a = raytrace.Vec(1.0, 2.0, 3.0)
        b = raytrace.Vec(4.0, 5.0, 6.0)
        self.assertEqual(a + b, raytrace.Vec(5.0, 7.0, 9.0))
        self.assertEqual(b - a, raytrace.Vec(3.0, 3.0, 3.0))
        self.assertEqual(a * 2.0, raytrace.Vec(2.0, 4.0, 6.0))

    def test_native_record_copy_and_conformance(self):
        for value in (deepcopy.make_doc(), deepcopy.A("x", (1, 2), True),
                      deepcopy.C()):
            self.assertTrue(grail.is_value_record(type(value)))
            self.assertIs(copy.deepcopy(value), value)
            self.assertNotIn(type(value), copy._deepcopy_dispatch)
        bad = deepcopy.A("x", [1, 2], True)
        cloned = copy.deepcopy(bad)
        self.assertEqual(cloned, bad)
        self.assertIsNot(cloned, bad)
        self.assertIsNot(cloned.lst, bad.lst)
        # bool is not an exact int: declaration conformance must not be
        # approximated using isinstance, as the old emulation did.
        bad = deepcopy.C(True, 2)
        self.assertIsNot(copy.deepcopy(bad), bad)
        changed = copy.replace(deepcopy.C(), b=3)
        self.assertEqual((changed.a, changed.b), (1, 3))

    def test_image_value_plan(self):
        self.assertTrue(grail.is_value_record(mandelbrot.Image))
        image = mandelbrot.Image(mandelbrot.View(), ((0, 1), (2, 3)))
        self.assertIs(copy.deepcopy(image), image)

    def test_collection_deepcopy_aliases(self):
        child = []
        source = grail.PMap({"a": child, "b": grail.PVector([child, child])})
        cloned = copy.deepcopy(source)
        self.assertEqual(cloned, source)
        self.assertIsNot(cloned["a"], child)
        self.assertIs(cloned["a"], cloned["b"][0])
        self.assertIs(cloned["b"][0], cloned["b"][1])
        cloned["a"].append(1)
        self.assertEqual(child, [])

    def test_map_merge_and_atom_publication(self):
        left = grail.PMap({"a": 1, "b": 2})
        right = grail.PMap({"b": 3, "c": 4})
        result = aggregate.merge(left, right)
        self.assertEqual(result, {"a": 1, "b": 5, "c": 4})
        self.assertEqual(left, {"a": 1, "b": 2})
        self.assertEqual(right, {"b": 3, "c": 4})
        cell = grail.Atom(left)
        self.assertEqual(cell.swap(aggregate.merge, right), result)
        self.assertEqual(cell.value, result)

    def test_spline_branch_update(self):
        a, b = chaos.Point2(0.0, 0.0), chaos.Point2(1.0, 1.0)
        spline = chaos.make_spline([a, b], 1, [0.0, 1.0])
        game = chaos.ChaosGame(grail.PVector([spline, spline]), 0.25,
                               chaos.Bounds(0.0, 0.0, 1.0, 1.0), (1, 1), 2)
        moved = chaos.Point2(0.25, 0.5)
        changed = chaos.with_endpoint_moved(game, 0, 0.0, moved)
        self.assertIsInstance(changed.splines, grail.PVector)
        self.assertIs(changed.splines[0].points[0], moved)
        self.assertIs(game.splines[0].points[0], a)
        self.assertIs(changed.splines[0].points[1], b)
        self.assertIs(changed.bounds, game.bounds)
        self.assertIs(changed.splines[1], game.splines[1])
        at_end = chaos.with_endpoint_moved(game, 0, 1.0, moved)
        self.assertIs(at_end.splines[0].points[-1], moved)
        self.assertIs(game.splines[0].points[-1], b)
        self.assertIs(chaos.with_endpoint_moved(game, 0, 0.5, moved), game)


if __name__ == "__main__":
    unittest.main()
