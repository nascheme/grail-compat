"""Fannkuch(n), Grail style.

Spec (what bm_fannkuch computes): for every permutation p of 0..n-1, count the
"pancake flips" needed until 0 is at the front, where one flip reverses the
first p[0]+1 elements.  The result is the maximum count over all n!
permutations (30 for n=9).  Like the original, permutations with p[0] == 0
(zero flips) or p[n-1] == n-1 (same count as a permutation of n-1 elements,
whose maximum is never larger) are skipped.

The original walks the permutations in the Benchmarks Game "counting
rotation" order.  We also return the Benchmarks Game checksum
(sum of flips, + for even and - for odd permutation index, over the
permutations that are evaluated), which depends on that order and so checks
that our index arithmetic reproduces it exactly.

Grail design
  * `Chunk(n, start, stop)` is a value record naming a range of permutation
    indices; `Result(max_flips, checksum)` is a value record; `combine` merges
    two results.  Both are safe to pass between threads.
  * Each chunk is a pure function of its record: it decodes permutation
    number `start` with the factorial number system (as the parallel
    Benchmarks Game programs do), then steps through the following ones with
    the same rotation rule as the original.  All working lists are local.
  * Parallelism: split 0..n! into `chunks` ranges (default 4 per thread for
    load balance), map them over a thread pool, and fold the results in index
    order.  The result is the same for any thread or chunk count.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from functools import reduce

import grail

DEFAULT_ARG = 9


@grail.record
class Chunk:
    n: int
    start: int      # first permutation index (inclusive)
    stop: int       # last permutation index (exclusive)


@grail.record
class Result:
    max_flips: int
    checksum: int


def combine(a: Result, b: Result) -> Result:
    return Result(max(a.max_flips, b.max_flips), a.checksum + b.checksum)


def count_flips(perm1: list[int]) -> int:
    """Pancake flips until 0 is in front, on a private copy of perm1."""
    perm = perm1[:]
    flips = 0
    k = perm[0]
    while k:
        perm[:k + 1] = perm[k::-1]
        flips += 1
        k = perm[0]
    return flips


def run_chunk(chunk: Chunk) -> Result:
    n, m = chunk.n, chunk.n - 1
    # Decode permutation number `start`.  count[i] is the number of left
    # rotations of perm1[0..i] already done: the mixed-radix digits of start.
    perm1 = list(range(n))
    count = [0] * n
    idx = chunk.start
    for i in range(n - 1, 0, -1):
        f = math.factorial(i)
        d, idx = divmod(idx, f)
        count[i] = d
        perm1[:i + 1] = perm1[d:i + 1] + perm1[:d]     # rotate left by d

    max_flips = checksum = 0
    for index in range(chunk.start, chunk.stop):
        if perm1[0] != 0 and perm1[m] != m:
            flips = count_flips(perm1)
            if flips > max_flips:
                max_flips = flips
            checksum += -flips if index & 1 else flips
        # Next permutation: rotate perm1[0..r] left by one, for the smallest
        # r whose rotation count has not wrapped (the original's inner loop).
        r = 1
        while r < n:
            perm1.insert(r, perm1.pop(0))
            count[r] += 1
            if count[r] <= r:
                break
            count[r] = 0
            r += 1
    return Result(max_flips, checksum)


def chunks_of(n: int, pieces: int) -> list[Chunk]:
    total = math.factorial(n)
    pieces = max(1, min(pieces, total))
    bounds = [total * i // pieces for i in range(pieces + 1)]
    return [Chunk(n, a, b) for a, b in zip(bounds, bounds[1:])]


def fannkuch(n: int, *, threads: int = 8, chunks: int | None = None) -> Result:
    work = chunks_of(n, chunks if chunks is not None else 4 * threads)
    if threads <= 1:
        results = [run_chunk(c) for c in work]
    else:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            results = list(pool.map(run_chunk, work))
    return reduce(combine, results, Result(0, 0))


def main(n: int = DEFAULT_ARG, *, threads: int = 8, chunks: int | None = None) -> int:
    """The benchmark's answer: the maximum flip count."""
    return fannkuch(n, threads=threads, chunks=chunks).max_flips


if __name__ == "__main__":
    res = fannkuch(DEFAULT_ARG)
    print(f"Pfannkuchen({DEFAULT_ARG}) = {res.max_flips}; checksum {res.checksum}")
