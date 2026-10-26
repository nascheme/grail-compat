"""chaos, Grail style: a "chaos game" fractal drawn from B-splines.

What it computes
----------------
Three cubic B-splines (de Boor evaluation) trace the outline of a letter-like
shape.  Each spline is split into num_trafos[i] segments, and each segment is
an affine-ish map ("trafo") of the unit square onto a thin band around that
piece of the curve.  Starting from the centre of the bounding box, the program
repeatedly picks a random trafo (seeded RNG), maps the current point through it
and marks the pixel it lands on in a w x h black/white image.  The marked
pixels approximate the attractor of this iterated function system.

Structure
---------
* Point2 and Bounds are value records. Spline and ChaosGame hold persistent
  vectors for branchable state; these are ordinary fields in today's native
  conformance model. Their contents are still immutable and safe to share.
* Behaviour is plain functions: there is no polymorphism in this program, so
  nothing is a generic function.
* The z coordinate of the original's GVector is always 0; it is dropped.
  (It only ever contributed "+ 0.0" terms, so results are bit-identical.)
* The random generator and the image are local working state of one chain.

Determinism and parallelism
---------------------------
The chaos game is a Markov chain: each point depends on the previous one, so
one chain cannot be split across threads without changing the result.

* chains=1 (the default): one chain, one random.Random(seed) stream.  This is
  the original program: same random stream, same points, same image.
  One caveat, a bug in the original: when a spline parameter lands exactly on
  a domain end, the original's spline returns its own control-point object
  and transform_point then moves it in place, silently changing the spline
  for the rest of the run.  The default seed (1234) never triggers this, but
  about 18% of other seeds do.  The Grail version keeps the splines fixed (the
  evident intent); legacy_aliasing=True reproduces the bug explicitly, by
  replacing the chain's local game value, and then matches for every seed.
* chains=N: N independent chains, chain k seeded from a stream derived from
  `seed`, each running iterations/N steps, images OR-ed together.  This is a
  DIFFERENT image (different samples of the same attractor).  It depends only
  on (seed, chains), never on `threads`, so it is deterministic too.
  `threads` only says how many chains run at once.
"""

from __future__ import annotations

import math
import random
from concurrent.futures import ThreadPoolExecutor
from copy import replace

import grail

DEFAULT_THICKNESS = 0.25
DEFAULT_WIDTH = 256
DEFAULT_HEIGHT = 256
DEFAULT_ITERATIONS = 5000
DEFAULT_RNG_SEED = 1234


# ------------------------------------------------------------------ records

@grail.record
class Point2:
    x: float
    y: float


@grail.record
class Spline:
    """A B-spline of the given degree; build with make_spline()."""
    points: grail.PVector[Point2]  # branchable control points
    degree: int
    knots: tuple[float, ...]


@grail.record
class Bounds:
    minx: float
    miny: float
    maxx: float
    maxy: float


@grail.record
class ChaosGame:
    """Everything the iteration reads.  Immutable, shared by all chains."""
    splines: grail.PVector[Spline]
    thickness: float
    bounds: Bounds
    num_trafos: tuple[int, ...]   # segments per spline
    num_total: int


# ------------------------------------------------------------------ splines

def make_spline(points, degree, knots) -> Spline:
    if len(points) != len(knots) - degree + 1:
        raise ValueError("wrong number of control points for knots/degree")
    if any(b < a for a, b in zip(knots, knots[1:])):
        raise ValueError("knots not increasing")
    # Keep numeric fields exact even though typed PVector fields are currently
    # ordinary fields (the prototype has no collection conformance plans).
    return Spline(grail.PVector(points), degree, tuple(float(k) for k in knots))


def domain(spl: Spline) -> tuple[float, float]:
    return spl.knots[spl.degree - 1], spl.knots[len(spl.knots) - spl.degree]


def lerp(a: Point2, b: Point2, l1: float, l2: float) -> Point2:
    return Point2(a.x * l1 + b.x * l2, a.y * l1 + b.y * l2)


def spline_at(spl: Spline, u: float) -> Point2:
    """Point of the spline at parameter u (de Boor's algorithm)."""
    start, end = domain(spl)
    if u < start or u > end:
        raise ValueError("parameter not in domain")
    if u == start:
        return spl.points[0]
    if u == end:
        return spl.points[-1]
    k, p, U = spl.degree, spl.points, spl.knots
    # knot span: U[i] <= u < U[i+1]; always found because start <= u < end
    i = next(ii for ii in range(k - 1, len(U) - k) if U[ii] <= u < U[ii + 1])
    d = [p[i - k + 1 + j] for j in range(k + 1)]      # local working list
    for r in range(1, k + 1):
        for ii in range(i - k + r + 1, i + 2):
            ua, ub = U[ii + k - r], U[ii - 1]
            j = ii - i + k - r - 1
            d[j] = lerp(d[j], d[j + 1], (ua - u) / (ua - ub), (u - ub) / (ua - ub))
    return d[0]


def dist(a: Point2, b: Point2) -> float:
    # ** 2, not x*x: pow() and x*x round differently in the last bit, and the
    # deterministic mode must reproduce the original bit for bit.
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def spline_length(spl: Spline, samples: int = 1000) -> float:
    """Polyline length through spl(i/999), i = 0..999 (as the original)."""
    total = 0
    curr = spline_at(spl, 0)
    for i in range(1, samples):
        last, curr = curr, spline_at(spl, 1 / (samples - 1) * i)
        total += dist(curr, last)
    return total


# --------------------------------------------------------------- the game

def make_chaosgame(splines, thickness: float = 0.1) -> ChaosGame:
    pts = [p for s in splines for p in s.points]
    b = Bounds(min(p.x for p in pts), min(p.y for p in pts),
               max(p.x for p in pts), max(p.y for p in pts))
    maxlength = thickness * (b.maxx - b.minx) / (b.maxy - b.miny)
    num = tuple(max(1, int(spline_length(s) / maxlength * 1.5)) for s in splines)
    return ChaosGame(grail.PVector(splines), thickness, b, num, sum(num))


def random_trafo(g: ChaosGame, rng: random.Random) -> tuple[int, int]:
    """Pick (spline index, segment index).  Reproduces the original's draw,
    including its quirk: r == num_total falls through to the last spline."""
    r = rng.randrange(g.num_total + 1)
    lo = 0
    for i, n in enumerate(g.num_trafos):
        if lo <= r < lo + n:
            return i, rng.randrange(n)
        lo += n
    return len(g.num_trafos) - 1, rng.randrange(g.num_trafos[-1])


def trafo_param(g: ChaosGame, p: Point2, trafo: tuple[int, int]) -> float:
    """Spline parameter that point p's x maps to under trafo."""
    b = g.bounds
    x = (p.x - b.minx) / (b.maxx - b.minx)
    which, seg = trafo
    start, end = domain(g.splines[which])
    seg_length = (end - start) / g.num_trafos[which]
    return start + seg_length * seg + seg_length * x


def transform_point(g: ChaosGame, p: Point2, trafo: tuple[int, int]) -> Point2:
    b = g.bounds
    y = (p.y - b.miny) / (b.maxy - b.miny)
    spl = g.splines[trafo[0]]
    start, end = domain(spl)
    t = trafo_param(g, p, trafo)
    base = spline_at(spl, t)
    # Tangent by a finite difference, stepping backwards near the end.
    h = 1 / 50000
    if t + h > end:
        n = spline_at(spl, t - h)
        dx, dy = n.x - base.x, n.y - base.y
    else:
        n = spline_at(spl, t + h)
        dx, dy = base.x - n.x, base.y - n.y
    mag = math.sqrt(dx ** 2 + dy ** 2)
    if mag != 0:
        # offset along the normal, by (y - 0.5) * thickness
        base = Point2(base.x + dy / mag * (y - 0.5) * g.thickness,
                      base.y + -dx / mag * (y - 0.5) * g.thickness)
    # clamp into the bounding box (same order of tests as the original)
    return Point2(b.minx if base.x < b.minx else min(base.x, b.maxx),
                  b.miny if base.y < b.miny else min(base.y, b.maxy))


def with_endpoint_moved(g: ChaosGame, which: int, t: float,
                        p: Point2) -> ChaosGame:
    """legacy_aliasing support.  When t is exactly a domain end, the
    original's spline returns its own first/last control point object, and
    transform_point then moves it in place: the spline is permanently
    changed.  Here that hidden mutation becomes an explicit new game value
    (local to the chain; the shared game is untouched)."""
    spl = g.splines[which]
    start, end = domain(spl)
    if t != start and t != end:
        return g
    # Path-copy just the edited control-point and spline paths. All other
    # branches remain shared; the original game stays safe for other chains.
    points = spl.points.set(0 if t == start else -1, p)
    return replace(g, splines=g.splines.set(which, replace(spl, points=points)))


def run_chain(g: ChaosGame, w: int, h: int, iterations: int,
              seed, legacy_aliasing: bool = False) -> bytes:
    """One chain of the chaos game; returns a w*h bitmap, column-major
    (index x*h + row, row 0 at the top), 1 = white, 0 = marked."""
    rng = random.Random(seed)              # local, never shared
    im = bytearray(b"\x01" * (w * h))      # local working image
    b = g.bounds
    width, height = b.maxx - b.minx, b.maxy - b.miny
    p = Point2((b.maxx + b.minx) / 2, (b.maxy + b.miny) / 2)
    for _ in range(iterations):
        trafo = random_trafo(g, rng)
        q = transform_point(g, p, trafo)
        if legacy_aliasing:
            g = with_endpoint_moved(g, trafo[0], trafo_param(g, p, trafo), q)
        p = q
        x = min(int((p.x - b.minx) / width * w), w - 1)
        y = min(int((p.y - b.miny) / height * h), h - 1)
        im[x * h + (h - y - 1)] = 0
    return bytes(im)


def chain_seeds(seed: int, chains: int) -> list:
    if chains == 1:
        return [seed]                      # exactly the original stream
    parent = random.Random(seed)
    return [parent.getrandbits(64) for _ in range(chains)]


def create_image(g: ChaosGame, w: int, h: int, iterations: int, seed: int,
                 chains: int = 1, threads: int = 8,
                 legacy_aliasing: bool = False) -> bytes:
    seeds = chain_seeds(seed, chains)
    counts = [iterations // chains + (k < iterations % chains)
              for k in range(chains)]
    if chains == 1 or threads == 1:
        parts = [run_chain(g, w, h, n, s, legacy_aliasing)
                 for n, s in zip(counts, seeds)]
    else:
        with ThreadPoolExecutor(max_workers=min(threads, chains)) as ex:
            parts = list(ex.map(lambda ns: run_chain(g, w, h, *ns,
                                                     legacy_aliasing),
                                zip(counts, seeds)))
    # A pixel is marked (0) if any chain marked it: AND the 0/1 bitmaps,
    # done as one big-int AND so the serial merge stays cheap.
    merged = int.from_bytes(parts[0])
    for part in parts[1:]:
        merged &= int.from_bytes(part)
    return merged.to_bytes(w * h)


def default_game(thickness: float = DEFAULT_THICKNESS) -> ChaosGame:
    P = Point2
    splines = [
        make_spline([P(1.597350, 3.304460), P(1.575810, 4.123260),
                     P(1.313210, 5.288350), P(1.618900, 5.329910),
                     P(2.889940, 5.502700), P(2.373060, 4.381830),
                     P(1.662000, 4.360280)],
                    3, [0, 0, 0, 1, 1, 1, 2, 2, 2]),
        make_spline([P(2.804500, 4.017350), P(2.550500, 3.525230),
                     P(1.979010, 2.620360), P(1.979010, 2.620360)],
                    3, [0, 0, 0, 1, 1, 1]),
        make_spline([P(2.001670, 4.011320), P(2.335040, 3.312830),
                     P(2.366800, 3.233460), P(2.366800, 3.233460)],
                    3, [0, 0, 0, 1, 1, 1]),
    ]
    return make_chaosgame(splines, thickness)


def main(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
         iterations=DEFAULT_ITERATIONS, seed=DEFAULT_RNG_SEED,
         thickness=DEFAULT_THICKNESS, chains=1, threads=8,
         game: ChaosGame | None = None, legacy_aliasing=False) -> bytes:
    """Same workload as one timed call of the original (the game is built
    outside the timed region there, so it can be passed in)."""
    g = game if game is not None else default_game(thickness)
    return create_image(g, width, height, iterations, seed, chains, threads,
                        legacy_aliasing)


if __name__ == "__main__":
    import hashlib
    for chains in (1, 8):
        im = main(chains=chains)
        print(f"chains={chains}: marked={im.count(0)} "
              f"sha256={hashlib.sha256(im).hexdigest()[:16]}")
