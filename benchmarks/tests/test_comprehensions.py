"""Widget pipeline contracts and full upstream differential checks."""

import copy
import random
import unittest

from bench_support import load_benchmark, load_upstream

g = load_benchmark("comprehensions")


def logical(widgets):
    return [(w.widget_id, w.creator_id, tuple(w.derived_widget_ids),
             w.kind.value, w.has_knob, w.has_spinner) for w in widgets]


def widget(i, creator=0, derived=(), knob=False, spinner=False, big=False):
    return g.Widget(i, creator, tuple(derived),
                    g.BIG if big else g.SMALL, knob, spinner)


class ComprehensionsTests(unittest.TestCase):
    def test_fixture(self):
        widgets = g.make_some_widgets()
        before = logical(widgets)
        tray = g.make_tray(1, widgets)
        self.assertEqual(len(widgets), 24)
        self.assertEqual(len(tray.sorted_widgets), 18)
        self.assertEqual(tray.owner_id, 1)
        self.assertEqual(logical(widgets), before)
        self.assertIsInstance(widgets, tuple)
        self.assertIsInstance(tray.sorted_widgets, tuple)
        self.assertTrue(all(type(w.derived_widget_ids) is tuple for w in widgets))
        self.assertTrue(all(any(w is source for source in widgets)
                            for w in tray.sorted_widgets))

    def test_empty_and_excluded(self):
        for widgets in ((), (widget(0, spinner=True, big=True),)):
            self.assertEqual(g.make_tray(1, widgets).sorted_widgets, ())

    def test_ascending_boolean_keys(self):
        plain = widget(30)
        knobby = widget(10, derived=(30,))
        target = widget(30, knob=True)
        mine = widget(0, creator=1)
        # not-mine before mine; no knobby derived before knobby derived.
        tray = g.make_tray(1, (mine, knobby, target))
        self.assertEqual(tray.sorted_widgets, (target, knobby, mine))
        self.assertEqual(g.make_tray(1, (widget(2), widget(1))).sorted_widgets,
                         (widget(1), widget(2)))
        self.assertEqual(g.make_tray(1, (plain,)).sorted_widgets, (plain,))

    def test_join_filters_before_lookup_and_counts_missing(self):
        excluded = widget(9, knob=True, spinner=True, big=True)
        dangling = widget(0, derived=(9, 99, 99))
        shorter = widget(1, derived=(99,))
        self.assertEqual(g.make_tray(1, (excluded, dangling, shorter)).sorted_widgets,
                         (shorter, dangling))
        # Repeated resolved references count too, and any() sees a real knob.
        target = widget(8, knob=True)
        once = widget(4, derived=(8,))
        twice = widget(3, derived=(8, 8))
        self.assertEqual(g.make_tray(1, (twice, target, once)).sorted_widgets,
                         (target, once, twice))

    def test_duplicate_ids_use_last_filtered_widget(self):
        first = widget(5, creator=1, derived=(99,))
        last = widget(5, creator=0)
        excluded = widget(5, spinner=True, big=True)
        tray = g.make_tray(1, (first, last, excluded))
        self.assertEqual(tray.sorted_widgets, (last, last))
        self.assertTrue(all(w is last for w in tray.sorted_widgets))

    def test_snapshot_and_replacement(self):
        widgets = g.make_some_widgets()
        old = g.make_tray(1, widgets)
        before = logical(old.sorted_widgets)
        changed = copy.replace(widgets[1], creator_id=99, derived_widget_ids=(99,))
        new_widgets = widgets[:1] + (changed,) + widgets[2:]
        new = g.make_tray(99, new_widgets)
        self.assertEqual(logical(old.sorted_widgets), before)
        self.assertIsNot(new, old)
        self.assertNotEqual(logical(new.sorted_widgets), before)
        self.assertIs(new_widgets[1], changed)
        self.assertIs(widgets[2], new_widgets[2])
        with self.assertRaises((AttributeError, TypeError)):
            widgets[1].creator_id = 99
        with self.assertRaises((AttributeError, TypeError)):
            old.sorted_widgets = ()

    def test_upstream_differential(self):
        original = load_upstream("comprehensions")
        self.assertEqual(logical(g.make_some_widgets()),
                         logical(original.make_some_widgets()))
        rng = random.Random(1287)
        cases = [g.make_some_widgets(), (), (widget(1, derived=(1,), knob=True),)]
        for _ in range(100):
            cases.append(tuple(
                widget(rng.randrange(12), rng.randrange(4),
                       [rng.randrange(15) for _ in range(rng.randrange(8))],
                       bool(rng.getrandbits(1)), bool(rng.getrandbits(1)),
                       bool(rng.getrandbits(1)))
                for _ in range(rng.randrange(40))))
        for case, widgets in enumerate(cases):
            for owner in (-1, 0, 1, 3):
                with self.subTest(case=case, owner=owner):
                    inputs = [original.Widget(
                        w.widget_id, w.creator_id, list(w.derived_widget_ids),
                        original.WidgetKind(w.kind.value), w.has_knob, w.has_spinner)
                        for w in widgets]
                    expected = original.WidgetTray(owner, inputs)
                    actual = g.make_tray(owner, widgets)
                    self.assertEqual(logical(actual.sorted_widgets),
                                     logical(expected.sorted_widgets))


if __name__ == "__main__":
    unittest.main()
