---
sources:
  - grail-compat/benchmarks/README.md
  - grail-compat/benchmarks/michi/idiomatic.py
  - bench/notes/michi.md
  - grail-compat/benchmarks/michi/upstream.py
  - grail-compat/benchmarks/michi/hybrid.py
---

# Michi: immutable trees and explicit randomness

Michi runs Monte Carlo tree search for Go. Positions and tree nodes are
records; the search thread updates its current root while workers run playouts
on immutable positions. This small tree illustrates path copying.

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
| Timed polling of playout jobs | `wait(FIRST_COMPLETED)` and bounded in-flight work |

Read `benchmarks/michi/idiomatic.py`: `tree_descend` returns a new root and a
path. `tree_update` applies a completed playout to the current root by rebuilding
that path. Workers do not mutate nodes from an earlier snapshot. Pattern
maps are read-only PMaps; mutable playout scratch stays local.

The small example's bare tuple annotation is ordinary, not a recursive value
plan. The full program also has annotations that do not qualify for value
plans. Immutable application state and recognized value-record types are not
the same claim.

## Reproducibility has two boundaries

Giving playout i its own seeded RNG makes that playout independent of which
worker executes it. It does not make the whole parallel search deterministic:
completion order changes what the search thread sees before later descents.
Use the single-thread exact mode for differential comparison with upstream.
Do not promise thread-count-independent moves from task-local seeding alone.

`benchmarks/michi/upstream.py` is the original program.
`benchmarks/michi/hybrid.py` keeps mutable tree nodes. The parent
`bench/notes/michi.md` describes that older representation; the current
idiomatic source describes path copying. Large pattern files are optional
external data, downloaded by `benchmarks/michi/fetch_patterns.py`, not by the
documentation tests.

See [concurrency](../guide/concurrency.md), [copying](../guide/copying.md),
[persistent collections](../guide/persistent-collections.md).
