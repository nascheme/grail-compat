"""Float benchmark (originally from Factor), written Grail style.

What it computes
----------------
For i in 0..n-1 build the point p_i = (sin i, 3 cos i, sin(i)^2 / 2),
scale it to unit length, and return the component-wise maximum over all
the unit vectors (a single Point).

Design
------
* ``Point`` is a value record of three floats.  ``make_point``,
  ``normalize`` and ``maximize`` are plain functions that return new
  Points instead of mutating; no polymorphism, so no generic functions.
* The original first stores all n points in a list, normalizes them in
  place, then folds ``maximize`` over the list (mutating the first point).
  The list is an artifact of how it is written: the result only needs a
  running maximum.  Here each point is created, normalized and folded in
  immediately, so a Grail VM can scalar-replace every Point (no
  allocation at all in the hot loop).  This is a deliberate restructuring;
  it changes memory traffic, not the result.
* Component-wise maximum is associative and commutative, so the index
  range splits into ``threads`` contiguous blocks, each reduced on its own
  thread, and the block results are folded in block order.  Ties are
  resolved exactly as in the original (``a if a > b else b``), and for
  this input no component ties between -0.0 and 0.0 occur, so the result
  is bit-identical for any thread count.  ``threads=1`` is the
  deterministic, single-threaded mode.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from math import cos, sin, sqrt

import grail

POINTS = 100000


@grail.record
class Point:
    x: float
    y: float
    z: float


def make_point(i: int) -> Point:
    x = sin(i)
    return Point(x, cos(i) * 3, (x * x) / 2)


def normalize(p: Point) -> Point:
    norm = sqrt(p.x * p.x + p.y * p.y + p.z * p.z)
    return Point(p.x / norm, p.y / norm, p.z / norm)


def maximize(a: Point, b: Point) -> Point:
    """Component-wise maximum (same tie rule as the original)."""
    return Point(a.x if a.x > b.x else b.x,
                 a.y if a.y > b.y else b.y,
                 a.z if a.z > b.z else b.z)


def max_normalized(start: int, stop: int) -> Point:
    """Maximum of normalize(make_point(i)) for i in [start, stop)."""
    acc = normalize(make_point(start))
    for i in range(start + 1, stop):
        acc = maximize(acc, normalize(make_point(i)))
    return acc


def benchmark(n: int = POINTS, threads: int = 8) -> Point:
    if threads <= 1 or n < 2 * threads:
        return max_normalized(0, n)
    bounds = [n * k // threads for k in range(threads + 1)]
    with ThreadPoolExecutor(threads) as pool:
        parts = list(pool.map(max_normalized, bounds[:-1], bounds[1:]))
    acc = parts[0]
    for p in parts[1:]:
        acc = maximize(acc, p)
    return acc


def main(n: int = POINTS, threads: int = 8) -> Point:
    return benchmark(n, threads)


if __name__ == "__main__":
    print(main())
