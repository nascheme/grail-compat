"""Immutable Hexiom snapshots, propagation and exact upstream search traces."""

from copy import deepcopy, replace
import io
import os
import random
import unittest
from unittest.mock import patch

import grail
from bench_support import load_benchmark, load_upstream

g = load_benchmark("hexiom")

STORAGES = ("tuple", "pvector") if grail.backend == "native-phase1" else ("tuple",)


def snapshot(pos):
    return tuple(tuple(cell) for cell in pos.cells)


def convert(original, pos):
    hex = original.Hex(pos.hex.size)
    hex.link_nodes()
    result = original.Pos(hex, list(pos.tiles))
    result.done.cells = [list(cell) for cell in pos.cells]
    return result


def original_snapshot(pos):
    return tuple(tuple(cell) for cell in pos.done.cells)


class HexiomTests(unittest.TestCase):
    def test_topology(self):
        for size in range(1, 6):
            hex = g.make_hex(size)
            self.assertEqual(len(hex.nodes), 3 * size * (size - 1) + 1)
            self.assertEqual(tuple(i for row in hex.rows for i in row),
                             tuple(range(len(hex.nodes))))
            for i, node in enumerate(hex.nodes):
                self.assertLessEqual(len(node.links), 6)
                for other in node.links:
                    self.assertIn(i, hex.nodes[other].links)
                    self.assertIn((hex.nodes[other].pos[0] - node.pos[0],
                                   hex.nodes[other].pos[1] - node.pos[1]), g.DIRS)
        with self.assertRaises(ValueError):
            g.make_hex(0)

    def test_snapshots_and_branch_isolation(self):
        for storage in STORAGES:
            root = g.read_file(g.LEVELS[30][0].strip(), storage)
            before = snapshot(root)
            # Cross the PVector's first leaf boundary, as well as its tail.
            for i in (0, 31, 32, 36):
                changed = g.play_move(root, (i, g.EMPTY))
                sibling = g.play_move(root, (i, 2))
                propagated = g.propagate(changed, i)
                self.assertEqual(snapshot(root), before)
                self.assertEqual(changed.cells[i], (g.EMPTY,))
                self.assertEqual(sibling.cells[i], (2,))
                self.assertIs(changed.hex, root.hex)
                self.assertIs(changed.tiles, root.tiles)
                self.assertIs(propagated.hex, root.hex)
                for j in range(len(root.cells)):
                    if j != i:
                        self.assertIs(changed.cells[j], root.cells[j])
                with self.assertRaises((AttributeError, TypeError)):
                    changed.cells = root.cells
                with self.assertRaises(TypeError):
                    changed.cells[i] = (0,)
            self.assertEqual(g.play_move(root, (-1, 0)).cells[-1], (0,))
            for i in (-len(root.cells) - 1, len(root.cells)):
                with self.assertRaises(IndexError):
                    g.play_move(root, (i, 0))
        if grail.backend == "native-phase1":
            root = g.read_file(g.LEVELS[25][0].strip())
            self.assertTrue(grail.is_value_record(g.Position))
            self.assertIs(deepcopy(root), root)
            self.assertFalse(grail.is_value_record(g.PersistentPosition))

    def test_published_fixed_point(self):
        for storage in STORAGES:
            root = g.read_file(g.LEVELS[25][0].strip(), storage)
            before = snapshot(root)
            result = g.propagate(root)
            next_pos, changed = g.constraint_pass(result)
            self.assertFalse(changed)
            self.assertIs(next_pos, result)
            self.assertEqual(snapshot(root), before)

    def test_moves_and_ties(self):
        pos = g.read_file(g.LEVELS[2][0].strip())
        self.assertEqual(g.find_moves(pos, g.FIRST_STRATEGY, g.ASCENDING),
                         ((0, 1), (0, g.EMPTY)))
        self.assertEqual(g.find_moves(pos, g.FIRST_STRATEGY, g.DESCENDING),
                         ((0, 1), (0, g.EMPTY)))
        pos = replace(pos, cells=((1, 3, 5, g.EMPTY),) * len(pos.cells))
        self.assertEqual(g.find_moves(pos, g.FIRST_STRATEGY, g.DESCENDING),
                         ((0, 5), (0, 3), (0, 1), (0, g.EMPTY)))
        for strategy in range(6):
            self.assertEqual(g.next_cell(pos, strategy), 0)
        with self.assertRaisesRegex(Exception, "Wrong strategy: 99"):
            g.next_cell(pos, 99)

    def test_validation_and_status(self):
        hex = g.make_hex(1)
        invalid = g.Position(hex, (2,) + (0,) * 7, ((0,),))
        with self.assertRaisesRegex(Exception, "Expected 1 tiles, got 2"):
            g.check_valid(invalid)
        pos = replace(invalid, tiles=(1, -2) + (0,) * 6)
        checked = g.check_valid(pos)
        self.assertEqual(checked.tiles, (1,) + (0,) * 7)
        self.assertEqual(pos.tiles[1], -2)
        for cells, tiles, status, text in (
                (((0,),), (1,) + (0,) * 7, g.SOLVED, "0 \n"),
                (((g.EMPTY,),), (0,) * 7 + (1,), g.SOLVED, ". \n"),
                (((1,),), (0, 1) + (0,) * 6, g.IMPOSSIBLE, ""),
                (((),), (1,) + (0,) * 7, g.IMPOSSIBLE, ""),
                (((0, g.EMPTY),), (1,) + (0,) * 7, g.OPEN, "")):
            output = io.StringIO()
            self.assertEqual(g.solved(g.Position(hex, tiles, cells), output), status)
            self.assertEqual(output.getvalue(), text)
        # Valid inventory, but an impossible locked number on a one-cell board.
        output = io.StringIO()
        self.assertEqual(g.solve_file("1\n+1", g.FIRST_STRATEGY, g.DESCENDING, output),
                         g.IMPOSSIBLE)
        self.assertEqual(output.getvalue(), "")
        with self.assertRaises(ValueError):
            g.read_file("1\n 0", "unknown")

    def test_fixture_solutions(self):
        for storage in STORAGES:
            for level in (2, 10, 20, 25, 30):
                with self.subTest(storage=storage, level=level):
                    self.assertEqual(g.main(level, storage), g.LEVELS[level][1].rstrip())

    @unittest.skipUnless(os.environ.get("HEXIOM_SLOW_TESTS") == "1",
                         "set HEXIOM_SLOW_TESTS=1 for the level-36 search")
    def test_level_36(self):
        for storage in STORAGES:
            self.assertEqual(g.main(36, storage), g.LEVELS[36][1].rstrip())


class HexiomUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = load_upstream("hexiom")

    def test_parser_and_topology(self):
        self.assertEqual(dict(g.LEVELS), self.original.LEVELS)
        for level, (board, expected) in g.LEVELS.items():
            old = self.original.read_file(board.strip())
            for storage in STORAGES:
                pos = g.read_file(board.strip(), storage)
                self.assertEqual(pos.hex.size, old.hex.size)
                self.assertEqual(len(pos.hex.nodes), old.hex.count)
                self.assertEqual(pos.tiles, tuple(old.tiles))
                self.assertEqual(snapshot(pos), original_snapshot(old))
                for i, node in enumerate(pos.hex.nodes):
                    self.assertEqual(node.pos, old.hex.nodes_by_id[i].pos)
                    self.assertEqual(node.links, tuple(old.hex.nodes_by_id[i].links))
                left, right = io.StringIO(), io.StringIO()
                g.print_pos(pos, left)
                self.original.print_pos(old, right)
                self.assertEqual(left.getvalue(), right.getvalue())

    def test_passes_and_fixed_points(self):
        rng = random.Random(661)
        cases = [g.read_file(board.strip()) for board, expected in g.LEVELS.values()]
        base = g.read_file(g.LEVELS[25][0].strip())
        for _ in range(40):
            cases.append(replace(base, cells=tuple(
                tuple(sorted(rng.sample(range(8), rng.randrange(9)))) for cell in base.cells)))
        for case, root in enumerate(cases):
            for last_move in (None, 0, len(root.cells) - 1):
                expected = convert(self.original, root)
                pos = root
                before = snapshot(root)
                for _ in range(8 * len(root.cells) + 1):
                    changed = self.original.constraint_pass(expected, last_move)
                    pos, actual_changed = g.constraint_pass(pos, last_move)
                    self.assertEqual(actual_changed, changed, (case, last_move))
                    self.assertEqual(snapshot(pos), original_snapshot(expected), (case, last_move))
                    if not changed:
                        break
                else:
                    self.fail("propagation did not converge")
                self.assertEqual(snapshot(g.propagate(root, last_move)), snapshot(pos))
                self.assertEqual(snapshot(root), before)
                for storage in STORAGES:
                    if storage == "pvector":
                        persistent = g.PersistentPosition(root.hex, root.tiles, grail.PVector(root.cells))
                        self.assertEqual(snapshot(g.propagate(persistent, last_move)), snapshot(pos))
                out1, out2 = io.StringIO(), io.StringIO()
                self.assertEqual(g.solved(pos, out1), self.original.solved(expected, out2))
                self.assertEqual(out1.getvalue(), out2.getvalue())

    def test_strategy_selection(self):
        rng = random.Random(552)
        root = g.read_file(g.LEVELS[25][0].strip())
        cases = [root, g.propagate(root)]
        for _ in range(60):
            cases.append(replace(root, cells=tuple(
                tuple(sorted(rng.sample(range(8), rng.randrange(9)))) for cell in root.cells)))
        for pos in cases:
            old = convert(self.original, pos)
            for strategy in range(6):
                for order in (g.ASCENDING, g.DESCENDING):
                    try:
                        expected = self.original.find_moves(old, strategy, order)
                    except ValueError:
                        # Preserve highest-value strategy's behavior on empty candidates.
                        with self.assertRaises(ValueError):
                            g.find_moves(pos, strategy, order)
                    else:
                        self.assertEqual(g.find_moves(pos, strategy, order), tuple(expected))

    def compare_search(self, level, strategy, order):
        original = self.original
        source = g.LEVELS[level][0].strip()
        expected_trace = []
        output = io.StringIO()
        old_solved = original.solved

        def visit(pos, output, verbose=False):
            expected_trace.append(original_snapshot(pos))
            return old_solved(pos, output, verbose)

        with patch.object(original, "solved", visit):
            expected_status = original.solve(original.read_file(source), strategy, order, output)
        for storage in STORAGES:
            pos = g.read_file(source, storage)
            before = snapshot(pos)
            trace = []
            actual_output = io.StringIO()
            status = g.solve(pos, strategy, order, actual_output,
                             on_visit=lambda pos: trace.append(snapshot(pos)))
            self.assertEqual(status, expected_status)
            self.assertEqual(actual_output.getvalue(), output.getvalue())
            self.assertEqual(trace, expected_trace)
            self.assertEqual(snapshot(pos), before)

    def test_exact_search_traces(self):
        for level in (10, 20, 25, 30):
            self.compare_search(level, g.FIRST_STRATEGY, g.DESCENDING)
        for strategy in range(6):
            for order in (g.ASCENDING, g.DESCENDING):
                self.compare_search(2, strategy, order)

    @unittest.skipUnless(os.environ.get("HEXIOM_SLOW_TESTS") == "1",
                         "set HEXIOM_SLOW_TESTS=1 for the upstream level-36 search")
    def test_upstream_level_36(self):
        output = io.StringIO()
        status = self.original.solve(self.original.read_file(g.LEVELS[36][0].strip()),
                                     g.FIRST_STRATEGY, g.DESCENDING, output)
        self.assertEqual(status, g.SOLVED)
        result = "\n".join(line.rstrip() for line in output.getvalue().splitlines())
        self.assertEqual(result, g.LEVELS[36][1].rstrip())


if __name__ == "__main__":
    unittest.main()
