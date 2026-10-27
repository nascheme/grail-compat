"""mandelbrot, Grail style: escape-time image of a Mandelbrot set region.

What it computes
----------------
The py-free-threading.github.io example
(https://py-free-threading.github.io/examples/mandelbrot/): for each pixel
of an 800x800 grid centred on -0.4601222 + 0.570286i with half-width 0.002,
iterate z <- z**2 + c from z = 0 and record the iteration at which |z| >= 2
first holds, or 0 if it never does within 2000 iterations.  The grid is
numpy.linspace in each axis.

The original (a notebook) writes counts into a shared numpy array from
pool threads, one task per 4 rows.  Here each row is a pure function of
the view and its y coordinate and returns an immutable tuple of counts; the
image is the tuple of rows.  Nothing is shared but the view and the x
coordinates, both immutable.

Structure
---------
* View is a value record: centre, half-width, size, iteration limit.
* linspace() reproduces numpy.linspace bit for bit (i * step + start, last
  point set to stop), so the program needs no numpy.
* escape_count() is the original's loop with `z * z` for `z ** 2`; for
  complex numbers CPython computes both with the same multiply, so the
  counts are identical (checked by the harness).
* Parallelism: rows in chunks of 4 (grail.pmap's chunk), mapped in order, so the image is the
  same for any thread count.  Interior rows cost up to 2000 iterations a
  pixel and outer rows a few, so small chunks matter for load balance.
"""

from __future__ import annotations

import sys

import grail

CHUNK_ROWS = 4


@grail.record
class View:
    xcenter: float = -0.4601222
    ycenter: float = 0.570286
    bound: float = 0.002
    nrows: int = 800
    ncols: int = 800
    max_iterations: int = 2000


@grail.record
class Image:
    view: View
    rows: tuple[tuple[int, ...], ...]  # typed tuples keep Image a value record


def linspace(start: float, stop: float, num: int) -> tuple[float, ...]:
    """numpy.linspace(start, stop, num) for num >= 2, as a tuple."""
    step = (stop - start) / (num - 1)
    return tuple(i * step + start for i in range(num - 1)) + (stop,)


def x_domain(v: View) -> tuple[float, ...]:
    return linspace(v.xcenter - v.bound, v.xcenter + v.bound, v.ncols)


def y_domain(v: View) -> tuple[float, ...]:
    return linspace(v.ycenter - v.bound, v.ycenter + v.bound, v.nrows)


def escape_count(c: complex, max_iterations: int) -> int:
    """Iterations until |z| >= 2, or 0 if c looks bounded."""
    z = 0j
    for n in range(max_iterations):
        if abs(z) >= 2:
            return n
        z = z * z + c
    return 0


def row(xs: tuple[float, ...], y: float, max_iterations: int) -> tuple[int, ...]:
    return tuple(escape_count(complex(x, y), max_iterations) for x in xs)


def render(view: View = View(), threads: int = 8) -> Image:
    xs, ys = x_domain(view), y_domain(view)
    if threads <= 1:
        image = [row(xs, y, view.max_iterations) for y in ys]
    else:
        with grail.Pool(threads) as pool:
            image = grail.pmap(lambda y: row(xs, y, view.max_iterations), ys,
                               chunk=CHUNK_ROWS, pool=pool)
    return Image(view=view, rows=tuple(image))


def main(nrows: int = 800, ncols: int = 800, threads: int = 8):
    return render(View(nrows=nrows, ncols=ncols), threads=threads)


if __name__ == "__main__":
    import hashlib
    import time
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 800
    t0 = time.perf_counter()
    img = main(n, n)
    dt = time.perf_counter() - t0
    flat = [c for r in img.rows for c in r]
    h = hashlib.sha256(repr(img.rows).encode()).hexdigest()[:16]
    print(f"{n}x{n} in {dt:.2f}s  sum={sum(flat)}  inside={flat.count(0)}  sha256 {h}")
