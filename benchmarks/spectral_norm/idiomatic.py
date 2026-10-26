"""Spectral norm (Computer Language Benchmarks Game), written Grail style.

What it computes
----------------
The spectral norm (largest singular value) of the infinite matrix
A[i, j] = 1 / ((i+j)(i+j+1)/2 + i + 1), truncated to n x n, by the power
method on A^T A: starting from u = (1, ..., 1), apply v = AtA u, u = AtA v
ten times, then the estimate is sqrt(<u, v> / <v, v>).  The original
computes <u, v> and <v, v> but never takes the square root; we return it.

Design
------
* Vectors are native ``FloatVector``s: immutable, unboxed float64, so
  every worker thread can share them with no copying, no locks, and no
  writes to shared element refcounts (a tuple of boxed floats incurs those
  on every read).  A record wrapping them would add nothing.
* One matrix-vector routine, ``times``, with a ``transposed`` flag; both
  cases call the single entry function ``a``.  No generic functions:
  there is no dispatch on data types.
* Parallel plan: each matrix-vector product is split into contiguous row
  blocks, one task per block; every row's sum is computed in the same
  j-order as the original, so the result is bit-identical for any thread
  count.  There is a join after every product (40 in total), because each
  product needs the whole previous vector.  ``threads=1`` runs inline.
* The original starts from integer 1s and an integer 0 accumulator; the
  first float product converts them, so starting from 1.0 / 0.0 gives the
  same bits and keeps the vectors homogeneous float vectors.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from math import sqrt

from grail import FloatVector

# Dense vectors are rebuilt in full each multiply, so a flat vector suits them
# better than a persistent trie. No Grail dispatch or operator registration
# is needed.

DEFAULT_N = 130
POWER_STEPS = 10

Vector = FloatVector


def a(i: int, j: int) -> float:
    """Entry A[i, j] of the matrix."""
    return 1.0 / ((i + j) * (i + j + 1) // 2 + i + 1)


def rows(u: Vector, lo: int, hi: int, transposed: bool) -> list[float]:
    """Rows lo..hi-1 of (A u), or of (A^T u) when ``transposed``.

    Both cases call the one function ``a`` so the call site stays
    monomorphic; the ``transposed`` test is loop-invariant, which a JIT
    can hoist (unswitch).
    """
    out = []
    for i in range(lo, hi):
        s = 0.0
        for j, u_j in enumerate(u):
            s += (a(j, i) if transposed else a(i, j)) * u_j
        out.append(s)
    return out


def times(u: Vector, transposed: bool, pool, blocks) -> Vector:
    """A u (or A^T u), split into row blocks when a pool is given."""
    if pool is None:
        return FloatVector(rows(u, 0, len(u), transposed))
    out: list[float] = []
    for part in pool.map(lambda b: rows(u, b[0], b[1], transposed), blocks):
        out += part
    return FloatVector(out)


def ata_times(u: Vector, pool, blocks) -> Vector:
    return times(times(u, False, pool, blocks), True, pool, blocks)


def spectral_norm(n: int = DEFAULT_N, threads: int = 8) -> float:
    u = FloatVector([1.0] * n)
    if threads <= 1:
        pool, blocks = None, None
    else:
        k = min(threads, n)
        blocks = [(n * b // k, n * (b + 1) // k) for b in range(k)]
        pool = ThreadPoolExecutor(threads)
    try:
        for _ in range(POWER_STEPS):
            v = ata_times(u, pool, blocks)
            u = ata_times(v, pool, blocks)
    finally:
        if pool is not None:
            pool.shutdown()
    vBv = vv = 0.0
    for ue, ve in zip(u, v):
        vBv += ue * ve
        vv += ve * ve
    return sqrt(vBv / vv)


def main(n: int = DEFAULT_N, threads: int = 8) -> float:
    return spectral_norm(n, threads)


if __name__ == "__main__":
    print(f"{main():.9f}")
