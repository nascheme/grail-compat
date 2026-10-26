"""Battle-planner contracts and complete upstream comparisons."""

from copy import deepcopy, replace
from fractions import Fraction
import random
import unittest

import grail
from bench_support import load_benchmark, load_upstream

g = load_benchmark("mdp")


def original_state(original, state):
    def half(value):
        fixed = value.fixed
        return original.halfstate_t(
            original.fixeddata_t(fixed.maxhp, original.stats_t(*g.stats_key(fixed.stats)),
                                 fixed.lvl, fixed.badges, fixed.basespeed),
            value.hp, value.status, original.stats_t(*g.stats_key(value.statmods)),
            original.stats_t(*g.stats_key(value.stats)))
    return half(state.me), half(state.them), state.extra


def normalized_edges(node, edges):
    if type(node) is g.Choice:
        return [g.node_key(child) for child in edges]
    return [(g.node_key(child), probability) for child, probability in edges]


class MDPTests(unittest.TestCase):
    def test_value_nodes_and_replacement(self):
        node = g.initial_node()
        old_hp = node.state.me.hp
        new_half = g.change_hp(node.state.me, -10)
        changed = replace(node, state=replace(node.state, me=new_half))
        self.assertEqual(node.state.me.hp, old_hp)
        self.assertEqual(changed.state.me.hp, old_hp - 10)
        self.assertIs(changed.state.them, node.state.them)
        self.assertIs(new_half.fixed, node.state.me.fixed)
        for value in (node, g.Opponent(node.state, "Dig"),
                      g.Pending(node.state, 1, "Water Gun"), g.WIN, g.LOSS):
            field = "won" if type(value) is g.Outcome else "state"
            with self.assertRaises((AttributeError, TypeError)):
                setattr(value, field, getattr(value, field))
            if grail.backend == "native-phase1":
                self.assertTrue(grail.is_value_record(type(value)))
                self.assertIs(deepcopy(value), value)

    def test_hp_clamps_and_potion(self):
        state = g.initial_node().state
        half = replace(state.me, hp=1)
        self.assertEqual(g.change_hp(half, -100).hp, 0)
        self.assertEqual(g.change_hp(half, 100).hp, half.fixed.maxhp)
        changed = replace(state, me=half)
        expected = replace(changed, me=replace(half, hp=51))
        self.assertEqual(g.apply_action(changed, 0, "Super Potion"),
                         {expected: Fraction(1)})
        self.assertEqual(g.apply_action(state, 1, "Super Potion"),
                         {state: Fraction(1)})
        self.assertEqual(half.hp, 1)

    def test_distribution_and_terminal_nodes(self):
        state = g.initial_node().state
        for side in (0, 1):
            for action in (*g.ATTACKS, "Super Potion"):
                dist = g.apply_action(state, side, action)
                self.assertEqual(sum(dist.values()), Fraction(1))
                self.assertTrue(all(p >= 0 for p in dist.values()))
        self.assertEqual(g.successors(g.WIN), ())
        self.assertEqual(g.successors(g.LOSS), ())
        both_dead = replace(state, me=replace(state.me, hp=0),
                            them=replace(state.them, hp=0))
        self.assertEqual(g.successors(g.Pending(both_dead, 0, "Dig")),
                         ((g.LOSS, 1.0),))
        with self.assertRaises(KeyError):
            g.apply_action(state, 0, "Not a move")
        with self.assertRaises(TypeError):
            g.successors(object())

    def test_graph_and_terminal_evaluation(self):
        # Tiny graph includes both node kinds without a full benchmark solve.
        state = g.initial_node().state
        graph = g.explore(g.Pending(replace(state, them=replace(state.them, hp=1)),
                                    0, "Dig"))
        self.assertIsInstance(graph.nodes, tuple)
        self.assertTrue(all(type(edges) is tuple for edges in graph.edges.values()))
        with self.assertRaises(TypeError):
            graph.edges[graph.initial] = ()
        low, high, frozen, iterations = g._iterate_bounds(graph, 0.192)
        self.assertEqual((low[graph.initial], high[graph.initial]), (1.0, 1.0))
        self.assertEqual(iterations, 1)
        for node, probability in ((g.WIN, 1.0), (g.LOSS, 0.0)):
            graph = g.explore(node)
            low, high, frozen, iterations = g._iterate_bounds(graph, 0.192)
            self.assertEqual((low[node], high[node], iterations),
                             (probability, probability, 0))

    def test_dfs_order_and_cycles(self):
        edges = {"a": ("b", "c"), "b": ("a",), "c": ()}
        self.assertEqual(g.topological_sort(["a"], edges.__getitem__),
                         ["c", "b", "a"])
        self.assertEqual(g.topological_sort(["a", "c"], edges.__getitem__),
                         ["c", "b", "a"])
        self.assertEqual(g.topological_sort([], edges.__getitem__), [])

    def test_fixture_result(self):
        result = g.evaluate()
        self.assertEqual(result.probability.hex(), (0.8987358988699916).hex())
        self.assertEqual(result.lower.hex(), (0.8027530727683209).hex())
        self.assertEqual(result.upper.hex(), (0.9947187249716625).hex())
        self.assertEqual((result.iterations, result.nodes), (111, 4823))
        self.assertLessEqual(result.upper - result.lower, 0.192)


class MDPUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = load_upstream("mdp")

    def test_damage_distributions(self):
        rng = random.Random(901)
        for _ in range(40):
            level, attack, base_attack, defense, base_defense, power = (
                rng.randrange(1, 150) for _ in range(6))
            probability = Fraction(rng.randrange(600), 512)
            stab = bool(rng.getrandbits(1))
            effectiveness = rng.choice((0, 0.5, 1, 2))
            args = (level, probability, attack, base_attack, defense, base_defense,
                    power, stab, effectiveness)
            self.assertEqual(g.critical_distribution(*args),
                             self.original.getCritDist(*args))

    def test_actions_and_successors(self):
        state = g.initial_node().state
        cases = [state,
                 replace(state, me=replace(state.me, hp=1),
                         them=replace(state.them, hp=1)),
                 replace(state, me=replace(state.me, stats=replace(state.me.stats, speed=56))),
                 replace(state, me=replace(state.me, stats=replace(state.me.stats, speed=90))),
                 replace(state, me=replace(state.me, fixed=replace(state.me.fixed, basespeed=999),
                                          stats=g.Stats(29, 22, 56, 40))),
                 replace(state, me=replace(state.me, hp=0), them=replace(state.them, hp=0))]
        battle = self.original.Battle()
        for state in cases:
            source = original_state(self.original, state)
            for side in (0, 1):
                for action in (*g.ATTACKS, "Super Potion"):
                    with self.subTest(hp=state.me.hp, speed=state.me.stats.speed,
                                      side=side, action=action):
                        expected = self.original._applyAction(source, side, action)
                        actual = g.apply_action(state, side, action)
                        self.assertEqual({g.state_key(k): v for k, v in actual.items()}, expected)
                        pending = g.Pending(state, side, action)
                        self.assertEqual(normalized_edges(pending, g.successors(pending)),
                                         battle.getSuccessors((2, source, side, action)))
            for action in ("Dig", "Super Potion"):
                node = g.Opponent(state, action)
                self.assertEqual(normalized_edges(node, g.successors(node)),
                                 battle.getSuccessors((1, source, action)))

    def test_complete_graph_and_final_bounds(self):
        original = self.original
        battle = original.Battle()
        expected = battle.evaluate(0.192)
        graph = g.explore(g.initial_node())
        source = (0, original_state(original, graph.initial.state))
        order = original.topoSort([source], battle.getSuccessorsList)
        self.assertEqual([g.node_key(node) for node in graph.nodes], order)
        self.assertEqual(len(graph.edges), len(battle.successors))
        for node, edges in graph.edges.items():
            self.assertEqual(normalized_edges(node, edges), battle.successors[g.node_key(node)])
        lower, upper, frozen, iterations = g._iterate_bounds(graph, 0.192)
        self.assertEqual({g.node_key(k): v for k, v in lower.items()}, dict(battle.min))
        self.assertEqual({g.node_key(k): v for k, v in upper.items()}, dict(battle.max))
        self.assertEqual({g.node_key(k) for k in frozen}, battle.frozen)
        self.assertEqual((upper[graph.initial] + lower[graph.initial]) / 2, expected)
        self.assertEqual(iterations, 111)
        # A looser tolerance must retain exactly the same update semantics.
        battle = original.Battle()
        expected = battle.evaluate(0.5)
        lower, upper, frozen, iterations = g._iterate_bounds(graph, 0.5)
        self.assertEqual({g.node_key(k): v for k, v in lower.items()}, dict(battle.min))
        self.assertEqual({g.node_key(k): v for k, v in upper.items()}, dict(battle.max))
        self.assertEqual({g.node_key(k) for k in frozen}, battle.frozen)
        self.assertEqual((upper[graph.initial] + lower[graph.initial]) / 2, expected)


if __name__ == "__main__":
    unittest.main()
