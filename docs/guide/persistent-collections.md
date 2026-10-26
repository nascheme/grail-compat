---
sources:
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_pds.py
  - grail-compat/tests/test_grail_pds.py
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_vector.py
  - grail-compat/tests/test_grail_vector.py
---

# Keep collection snapshots

Use persistent collections when you need an updated collection and an old
snapshot at the same time. Assign the returned value; updates do not mutate.

    >>> from grail import PMap, PSet, PVector
    >>> stock = PMap({"tea": 2})
    >>> changed = stock.set("tea", 5)
    >>> stock["tea"], changed["tea"]
    (2, 5)

PMap behaves like an unordered mapping. PSet behaves like an unordered set.
PVector behaves like an indexed sequence. Map and set iteration is not
insertion-ordered; sort when output order matters.

    >>> sorted(PSet([1, 2]) | [2, 3])
    [1, 2, 3]
    >>> log = PVector(["opened"])
    >>> list(log.append("restocked"))
    ['opened', 'restocked']

Map unions use right-hand values on conflicts. Use `update_with(other, fn)`
to combine values instead. Vector `pop` returns the shortened vector, not
the last element. Vector equality requires another PVector; map and set
equality also work with corresponding builtin collections.

These containers do not deep-freeze their children. A list stored in PMap
remains a mutable list. Use immutable children for shared snapshots. Typed
collection annotations do not yet create value fields in records.

Use [transients](transients.md) for batches. Native map/set/vector updates
share structure; pure-Python compatibility updates copy storage and can cost
O(n). Do not infer native performance from compatibility timings.

References: [PMap](../reference/pmap.md), [PSet](../reference/pset.md),
[PVector](../reference/pvector.md).
