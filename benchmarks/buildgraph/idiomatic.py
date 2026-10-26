"""buildgraph, Grail style: a parallel build scheduler.

What it computes
----------------
The PyCon US 2026 free-threading challenge
(https://github.com/mpage/pycon26-ftp-challenge).  Given a DAG of build
targets, call target.build(dep_results) exactly once per target, never
before all of its dependencies have finished, and return
{target name: result bytes}.  build() is fixed CPU-bound work (randbytes,
a pure-Python FNV loop, sha256) of 0.2-2 ms per target.  The score is the
wall time against a single-threaded topological-order reference.

The two best submissions (mpage, PR 30; jbower-fb, PR 37) share one plan:
a persistent worker pool, a shared ready queue, a per-target count of
unfinished dependencies, dependents sorted heaviest first, and a worker
that keeps one newly ready dependent for itself instead of round-tripping
it through the queue.  They differ only in how the counts are decremented:
jbower-fb takes one global lock per finished target; mpage patches a
method's vectorcall with hand-assembled `lock xadd` through ctypes to get
a lock-free fetch-and-sub.

Structure
---------
* Plan is a value record built once per graph by one thread: a tuple of
  Job records (name, work, dependency and dependent indices, and the
  dependency names build() wants as dict keys) and the source jobs.  It
  holds nothing from the harness, so it conforms as a value; every worker
  reads it without synchronization.
* Run holds the state that changes, and all of it is in atoms: one
  Atom[int] per target counting unfinished dependencies, one write-once
  Atom[bytes | None] per target for its result, and an Atom[int] count of
  targets still to build.  `pending[i].add(-1) == 0` is the portable
  spelling of mpage's lock xadd: exactly one thread sees zero, so exactly
  one thread schedules each target, with no lock and no ctypes.  add() is
  native integer arithmetic on the cell, with no Python callback.
* The ready queue is a queue.SimpleQueue, the stdlib's channel; the
  record only holds a reference to it.
* Workers are long-running loops on a module-level ThreadPoolExecutor
  that is warmed at import, so a build pays no thread start-up.  The
  calling thread runs one of the loops itself.
* Unlike the submissions, a failing build() does not hang the pool: the
  worker posts stop sentinels and the exception reaches the caller.

The harness's Target objects are mutable dataclasses owned by the harness,
and the rules require calling Target.build (the harness instruments it).
They are the one adapter at the boundary: Run holds each target's bound
build method, in job order, and nothing else of them is read after
make_plan.  The submissions store scratch attributes on the targets; this
never mutates them.  The previous version, whose Plan held the Target
objects themselves, is kept as hybrid.py.
"""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue

import grail

# With the GIL, extra threads only add switching (4x slower than one on 28).
_GIL = getattr(sys, "_is_gil_enabled", lambda: True)()
DEFAULT_THREADS = 1 if _GIL else (os.cpu_count() or 8)
_STOP = -1          # queue sentinel: no more work for this run


# ------------------------------------------------------------------ data

@grail.record
class Job:
    """One target of the graph, in index form."""
    name: str
    work: int
    deps: tuple[int, ...]
    dep_names: tuple[str, ...]
    dependents: tuple[int, ...]           # heaviest first


@grail.record
class Plan:
    """The graph in index form: a value shared by every worker."""
    jobs: tuple[Job, ...]
    sources: tuple[int, ...]              # jobs with no deps


@grail.record
class Run:
    """Per-build state: the harness adapter, atoms and a channel."""
    plan: Plan
    build: tuple            # per job: its Target's bound build method
    pending: tuple          # tuple[Atom[int], ...]: unfinished deps
    results: tuple          # tuple[Atom[bytes | None], ...], write-once
    remaining: grail.Atom   # Atom[int]: targets not yet built
    ready: SimpleQueue      # target indices ready to build, or _STOP
    workers: int


def make_plan(graph) -> Plan:
    targets = tuple(graph.targets.values())
    index = {t.name: i for i, t in enumerate(targets)}
    deps = tuple(tuple(index[d.name] for d in t.deps) for t in targets)
    dependents = [[] for _ in targets]
    for i, ds in enumerate(deps):
        for j in ds:
            dependents[j].append(i)
    # Heaviest first: the first one to become ready is the one this worker
    # keeps, and long jobs started early balance the tail.
    for ds in dependents:
        ds.sort(key=lambda i: targets[i].work, reverse=True)
    jobs = tuple(Job(t.name, t.work, ds, tuple(d.name for d in t.deps), tuple(dt))
                 for t, ds, dt in zip(targets, deps, dependents))
    return Plan(jobs, tuple(i for i, ds in enumerate(deps) if not ds))


def start_run(plan: Plan, targets, workers: int) -> Run:
    """targets: the harness's Target objects, in plan job order."""
    run = Run(
        plan=plan,
        build=tuple(t.build for t in targets),
        pending=tuple(grail.Atom(len(job.deps)) for job in plan.jobs),
        results=tuple(grail.Atom(None) for _ in plan.jobs),
        remaining=grail.Atom(len(plan.jobs)),
        ready=SimpleQueue(),
        workers=workers,
    )
    for i in plan.sources:
        run.ready.put(i)
    return run


# --------------------------------------------------------------- workers

def stop_all(run: Run) -> None:
    for _ in range(run.workers):
        run.ready.put(_STOP)


def build_one(run: Run, i: int) -> int:
    """Build target i; return a dependent it made ready, or -1."""
    job = run.plan.jobs[i]
    results = run.results
    dep_results = {name: results[j].value
                   for name, j in zip(job.dep_names, job.deps)}
    results[i].reset(run.build[i](dep_results))

    # Release dependents.  The atom update that reaches zero happens after
    # every dependency's result was stored, so the thread that sees zero
    # also sees all those results.
    keep = -1
    for k in job.dependents:
        if run.pending[k].add(-1) == 0:
            if keep < 0:
                keep = k
            else:
                run.ready.put(k)
    if run.remaining.add(-1) == 0:
        stop_all(run)
    return keep


def worker(run: Run) -> None:
    ready = run.ready
    try:
        i = ready.get()
        while i != _STOP:
            i = build_one(run, i)
            if i < 0:
                i = ready.get()
    except BaseException:
        stop_all(run)       # wake the others so the caller sees the error
        raise


# ------------------------------------------------------------------ pool

_POOL_SIZE = max(DEFAULT_THREADS - 1, 1)   # the caller is the last worker
_POOL = ThreadPoolExecutor(max_workers=_POOL_SIZE,
                           thread_name_prefix="grail-build")


def _warm_pool(n: int) -> None:
    """Start all pool threads now rather than on the first build."""
    barrier = threading.Barrier(n)
    for f in [_POOL.submit(barrier.wait) for _ in range(n)]:
        f.result()


_warm_pool(_POOL_SIZE)
_pool_lock = threading.Lock()   # one build at a time owns the pool


def build_all(graph, threads: int = DEFAULT_THREADS) -> dict[str, bytes]:
    plan = make_plan(graph)
    if not plan.jobs:
        return {}
    threads = max(1, min(threads, _POOL_SIZE + 1))
    run = start_run(plan, graph.targets.values(), threads)
    with _pool_lock:
        futures = [_POOL.submit(worker, run) for _ in range(threads - 1)]
        worker(run)
        for f in futures:
            f.result()
    return {job.name: r.value for job, r in zip(plan.jobs, run.results)}


def main(graph_path: str, threads: int = DEFAULT_THREADS) -> dict[str, bytes]:
    from graph import BuildGraph   # the vendored challenge/ on sys.path
    return build_all(BuildGraph.load(graph_path), threads=threads)


if __name__ == "__main__":
    import hashlib
    import time
    if len(sys.argv) < 2:
        sys.exit(f"usage: {sys.argv[0]} GRAPH.json [threads]")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "challenge"))
    t0 = time.perf_counter()
    res = main(sys.argv[1], *(int(a) for a in sys.argv[2:3]))
    dt = time.perf_counter() - t0
    h = hashlib.sha256(b"".join(res[k] for k in sorted(res))).hexdigest()
    print(f"{len(res)} targets  {dt:.3f}s  sha256 {h[:16]}")
