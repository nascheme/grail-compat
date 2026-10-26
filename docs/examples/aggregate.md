---
sources:
  - grail-compat/benchmarks/README.md
  - grail-compat/benchmarks/aggregate/idiomatic.py
  - bench/notes/aggregate.md
---

# Aggregate: fold privately, merge values

For per-key totals, give each worker a private fold and merge its result.
This avoids contending on shared state for every input pair. The merge can
also be used as a pure Atom.swap callback.

    >>> import operator
    >>> from grail import PMap, Atom
    >>> def fold(rows):
    ...     sums = {}
    ...     for key, value in rows:
    ...         sums[key] = sums.get(key, 0) + value
    ...     return PMap(sums)
    >>> def merge(a, b):
    ...     return a.update_with(b, operator.add)
    >>> a = fold([("tea", 2), ("tea", 3)])
    >>> b = fold([("tea", 4), ("coffee", 1)])
    >>> dict(merge(a, b)) == {"tea": 9, "coffee": 1}
    True
    >>> totals = Atom(a)
    >>> totals.swap(merge, b)["tea"]
    9

## Private work versus shared updates

| Baseline shape | Grail shape |
| --- | --- |
| Each input updates one shared map | Each worker updates its own local dict |
| Many shared-key conflicts | Freeze one PMap per chunk |
| Shared update for every pair | Merge partial results in order, or one swap per chunk |

Read `benchmarks/aggregate/idiomatic.py`: `pairs` uses global input indices,
`split` partitions ranges, `fold` owns local scratch, and `merge` is
`PMap.update_with` with addition. There is no `upstream.py` for aggregate;
the baseline snippet is in the parent `bench/harness/measure_aggregate.py`.

An Atom update per pair would be correct with a pure callback but adds shared
contention and allocation. It is not the recommended reduction shape.
Use shared coordination when workers need to observe intermediate updates,
not when only a final total is needed.

Integer addition here is associative. For floats, fixed merge order alone
does not guarantee the same answer if worker count changes the partition.
This pattern is not a replacement for a concurrent cache or deduplication map.

The parent `bench/notes/aggregate.md` explains the original motivation.
Its request for `update_with` is historical: the method exists now.

See [concurrency](../guide/concurrency.md), [PMap reference](../reference/pmap.md),
[atoms](../guide/atoms.md).
