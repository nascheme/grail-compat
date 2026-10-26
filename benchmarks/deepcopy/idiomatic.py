"""copy.deepcopy benchmark using the native Grail copy protocols.

The fixed-key document, dataclass states, and reduce object become typed value
records. Native deepcopy can return conforming instances unchanged; ordinary
or nonconforming records still recursively copy their children. State changes
use copy.replace, exercising the native record replacement path.

The aliased document uses PMap/PVector. Their native deepcopy implementations
preserve aliases and mutable-child cycles, but do not infer value conformance
or promise O(1) substitution. No global copy dispatch tables are patched.

Independent repetitions may run on separate threads. The sources are immutable;
with value-record substitution there is little work left to parallelize.
"""

from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor

import grail
from grail import PMap, PVector


@grail.record
class A:
    string: str
    lst: tuple[int, ...]
    boolean: bool


@grail.record
class Sub:
    a: bool


@grail.record
class Doc:
    numbers: tuple[int, ...]
    t: tuple[int, int, int]
    text: str
    sub: Sub


@grail.record
class C:
    a: int = 1
    b: int = 2


def make_doc() -> Doc:
    return Doc((1, 2, 3, 43), (1, 2, 3), "hello", Sub(True))


def make_shared() -> PMap:
    row = (1,) * 100
    return PMap({"a": (row, row, row), "b": PVector([row] * 100)})


def _split(n: int, threads: int) -> list[int]:
    k = max(1, threads)
    return [n // k + (i < n % k) for i in range(k)]


def _run(task, n: int, threads: int) -> float:
    """Return summed copy time for each thread's share of n."""
    shares = _split(n, threads)
    if len(shares) == 1:
        return task(shares[0])
    with ThreadPoolExecutor(max_workers=len(shares)) as pool:
        return sum(pool.map(task, shares))


def a_states():
    """The original dataclass's 30 states per repetition."""
    dc = A("hello", (1, 2, 3), True)
    for s in ("red", "blue", "green"):
        dc = copy.replace(dc, string=s)
        for kk in range(5):
            dc = copy.replace(dc, lst=(kk, *dc.lst[1:]))
            for b in (True, False):
                dc = copy.replace(dc, boolean=b)
                yield dc


def benchmark(n: int, threads: int = 1, sink: list | None = None) -> float:
    """Doc copied 30 times, then A in 30 states; time inside deepcopy."""
    doc = make_doc()

    def task(reps: int) -> float:
        dt = 0.0
        for _ in range(reps):
            for _ in range(30):
                t0 = time.perf_counter()
                c = copy.deepcopy(doc)
                dt += time.perf_counter() - t0
                if sink is not None:
                    sink.append((doc, c))
            for dc in a_states():  # state construction is not timed
                t0 = time.perf_counter()
                c = copy.deepcopy(dc)
                dt += time.perf_counter() - t0
                if sink is not None:
                    sink.append((dc, c))
        return dt
    return _run(task, n, threads)


def benchmark_reduce(n: int, threads: int = 1) -> float:
    c = C()

    def task(reps: int) -> float:
        t0 = time.perf_counter()
        for _ in range(reps):
            copy.deepcopy(c)
        return time.perf_counter() - t0
    return _run(task, n, threads)


def benchmark_memo(n: int, threads: int = 1) -> float:
    data = make_shared()

    def task(reps: int) -> float:
        t0 = time.perf_counter()
        for _ in range(reps):
            copy.deepcopy(data)
        return time.perf_counter() - t0
    return _run(task, n, threads)


DEFAULT_N = {"deepcopy": 200, "deepcopy_reduce": 20000, "deepcopy_memo": 2000}


def main(*, threads: int = 8,
         n: dict[str, int] = DEFAULT_N) -> dict[str, float]:
    """Run all three sub-benchmarks using the native copy behavior."""
    return {"deepcopy": benchmark(n["deepcopy"], threads),
            "deepcopy_reduce": benchmark_reduce(n["deepcopy_reduce"], threads),
            "deepcopy_memo": benchmark_memo(n["deepcopy_memo"], threads)}


if __name__ == "__main__":
    sink: list = []
    benchmark(1, sink=sink)
    assert all(c == src for src, c in sink)
    print(f"{len(sink)} copies equal to their sources; "
          f"{sum(c is src for src, c in sink)} reused by native deepcopy")
    for k, v in main().items():
        print(f"{k:16s} {v * 1e3:8.2f} ms inside deepcopy")
