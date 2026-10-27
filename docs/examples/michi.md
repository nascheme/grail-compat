---
sources:
  - benchmarks/README.md
  - benchmarks/michi/idiomatic.py
  - benchmarks/michi/upstream.py
  - benchmarks/michi/hybrid.py
---

# Michi: immutable trees and explicit randomness

Michi runs Monte Carlo tree search for Go. Positions and tree nodes are
records. With threads, searcher tasks in a `grail.Pool` scope share the
current root in an `Atom` and run playouts on immutable positions. This small
tree illustrates path copying.

    >>> from grail import record, replace
    >>> @record
    ... class Node:
    ...     visits: int
    ...     children: tuple
    >>> child = Node(0, ())
    >>> root = Node(0, (child,))
    >>> changed_child = replace(child, visits=1)
    >>> changed_root = replace(root, children=(changed_child,))
    >>> root.children[0].visits, changed_root.children[0].visits
    (0, 1)

## Address nodes without object identity

| Upstream | Idiomatic |
| --- | --- |
| Frozen dataclass Position with methods | Position record and plain move/scoring functions |
| Mutable TreeNode graph | Node records rebuilt along a child-index path |
| Module-global RNG | Explicit `random.Random` passed to work |
| Search thread polling playout jobs | Searcher tasks sharing the root in an Atom |

Read `benchmarks/michi/idiomatic.py`: `tree_descend` returns a new root and a
path. A searcher publishes it with `compare_and_set`, and descends again from
the newer root if another searcher got there first. `tree_update`, run through
`swap`, applies a completed playout to whatever root is current by rebuilding
that path. No task mutates nodes of an earlier snapshot. Pattern maps are
read-only PMaps; mutable playout scratch stays local.

The small example's bare tuple annotation is ordinary, not a recursive value
plan. The full program also has annotations that do not qualify for value
plans. Immutable application state and recognized value-record types are not
the same claim.

## Reproducibility has two boundaries

Giving playout i its own seeded RNG makes that playout independent of which
worker executes it. It does not make the whole parallel search deterministic:
the order in which results reach the shared root changes what later descents
see.
Use the single-thread exact mode for differential comparison with upstream.
Do not promise thread-count-independent moves from task-local seeding alone.

`benchmarks/michi/upstream.py` is the original program.
`benchmarks/michi/hybrid.py` keeps mutable tree nodes; the current idiomatic
source describes path copying. Large pattern files are optional
external data, downloaded by `benchmarks/michi/fetch_patterns.py`, not by the
documentation tests.

See [concurrency](../guide/concurrency.md), [copying](../guide/copying.md),
[persistent collections](../guide/persistent-collections.md).
