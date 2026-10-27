---
sources:
  - docs/design/07-appendix-concurrency-toolkit.md
  - src/grail/_atom.py
  - tests/test_grail_atom.py
---

# Update shared state with atoms

Put a changing reference in an Atom and keep the referenced state immutable.
Read one snapshot, or use swap to compute and commit an updated value.

    >>> from grail import Atom, PMap
    >>> state = Atom(PMap({"tea": 2}))
    >>> def restock(stock, name, count):
    ...     return stock.set(name, stock.get(name, 0) + count)
    >>> state.swap(restock, "tea", 3)["tea"]
    5

`swap` may rerun the callback if another update wins. Keep it pure: return a
new value, with no logging, I/O, mutation of the old state, or other effects.
Performing an external action after swap is not atomic with the state update;
use an application lock or a queue when that coupling matters.

`reset(value)` unconditionally replaces the reference and returns the supplied
value. `compare_and_set(expected, desired)` uses identity, not equality. Read
`expected = state.value`; do not reconstruct an equal value for the test.
CAS does not detect a change away from and back to the same object.

    >>> pending = Atom(3)
    >>> pending.add(-1)
    2

Use `add` for exact builtin integer counters. It rejects bools, subclasses,
and floats; integers have no fixed-width overflow limit. For aggregation,
prefer local work followed by a merge rather than many contending atom writes.

An Atom holding a list does not synchronize mutations of that list. Avoid
`state.value.append(...)` and use immutable snapshots instead.

[Reference](../reference/atom.md), [concurrency](concurrency.md).
