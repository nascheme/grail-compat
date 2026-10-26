"""aggregate, Grail style: per-key sums over a stream of (key, value) pairs.

What it computes
----------------
The cereggii AtomicDict.reduce() example linked from
https://py-free-threading.github.io/examples/
(https://dpdani.github.io/cereggii/examples/AtomicDict/reduce/): sum the
values of 3,628,800 pairs (keys[i % 10], 5) per key, giving
{0..9: 1,814,400} and a total of 18,144,000.  The baseline is a plain dict
on one thread.  The example's threaded version has every thread fold its
share of the pairs straight into one shared AtomicDict, whose reduce()
retries an update when another thread changed the same key.

Grail has no shared mutable dict, and does not need one here: the
reduction is commutative and associative, so each thread folds its share
into a private dict and the partial results are merged.

Structure
---------
* Workload is a value record; pairs(w, lo, hi) yields the pairs with global
  indices lo..hi-1, so any split covers exactly the original stream.
* fold() reduces one chunk into a local dict and returns it frozen as a
  PMap.  The dict never leaves its thread.
* Two ways to combine, with the same result:
  - "merge" (default): the caller merges the chunk results in chunk order
    with PMap.update_with (merge-with).
  - "atom": each chunk swaps its PMap into one shared Atom[PMap].  This is
    the Grail counterpart of AtomicDict.reduce(), one CAS per chunk instead
    of one per pair.
* A per-pair swap on Atom[PMap] would also be correct, but it copies a
  HAMT path on every update and every thread contends on one cell; it is
  the anti-pattern, not provided.
"""

from __future__ import annotations

import operator
import os
from concurrent.futures import ThreadPoolExecutor

import grail

CHUNKS_PER_THREAD = 4


@grail.record
class Workload:
    size: int = 3_628_800       # 10!, divisible by 1..10 threads
    nkeys: int = 10
    value: int = 5


def pairs(w: Workload, lo: int, hi: int):
    """The (key, value) pairs with indices lo..hi-1 of the stream."""
    keys = list(range(w.nkeys))
    n = len(keys)
    value = w.value
    return ((keys[i % n], value) for i in range(lo, hi))


def fold(w: Workload, lo: int, hi: int) -> grail.PMap:
    sums: dict[int, int] = {}
    for k, v in pairs(w, lo, hi):
        sums[k] = sums.get(k, 0) + v
    return grail.PMap(sums)


def merge(a: grail.PMap, b: grail.PMap) -> grail.PMap:
    """Pointwise sum of two partial results (pure: swap may rerun it)."""
    return a.update_with(b, operator.add)


def split(size: int, parts: int) -> list[tuple[int, int]]:
    edges = [size * i // parts for i in range(parts + 1)]
    return [(lo, hi) for lo, hi in zip(edges, edges[1:]) if lo < hi]


def aggregate(w: Workload = Workload(), threads: int = os.cpu_count() or 8,
              combine: str = "merge") -> grail.PMap:
    if threads <= 1:
        return fold(w, 0, w.size)
    ranges = split(w.size, threads * CHUNKS_PER_THREAD)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        if combine == "merge":
            result = grail.PMap()
            for part in pool.map(lambda r: fold(w, *r), ranges):
                result = merge(result, part)
            return result
        if combine == "atom":
            totals = grail.Atom(grail.PMap())
            list(pool.map(lambda r: totals.swap(merge, fold(w, *r)), ranges))
            return totals.value
    raise ValueError(f"unknown combine {combine!r}")


def main(size: int = 3_628_800, threads: int = os.cpu_count() or 8,
         combine: str = "merge") -> grail.PMap:
    return aggregate(Workload(size=size), threads=threads, combine=combine)


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    sums = main()
    dt = time.perf_counter() - t0
    print(f"{dt:.3f}s  total={sum(sums.values())}  sums={dict(sorted(sums.items()))}")
