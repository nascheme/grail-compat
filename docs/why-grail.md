---
sources:
  - grail-compat/docs/design/00-project-grail-overview.md
  - grail-compat/docs/design/01-pep-draft-records.md
  - grail-compat/docs/design/02-pep-draft-generic-functions.md
  - grail-compat/docs/design/05-design-persistent-collections.md
  - grail-compat/docs/design/06-cpython-prototype.md
  - bench/results/20261026-111345-5c74a29e4ba/README.md
  - bench/results/20261026-111345-5c74a29e4ba/ft/results.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_atom.py
  - grail-compat/tests/test_grail.py
  - grail-compat/tests/test_grail_atom.py
---

# Why Grail?

Grail aims to make your Python code faster, scale better across cores, and
make concurrent programs easier to reason about. The common idea is simple:
keep data immutable and make shared changes explicit. You get fewer places
where races can happen, and a CPython runtime optimized for Grail gets stronger
guarantees it can use to avoid work.

    >>> from grail import record, replace, Atom
    >>> @record
    ... class Totals:
    ...     sales: int
    ...     revenue: int
    >>> totals = Atom(Totals(0, 0))
    >>> before = totals.value
    >>> def add_sale(old, amount):
    ...     return replace(old, sales=old.sales + 1, revenue=old.revenue + amount)
    >>> after = totals.swap(add_sale, 5)
    >>> before, after
    (Totals(sales=0, revenue=0), Totals(sales=1, revenue=5))

The old snapshot stays unchanged. The two totals change together through one
shared reference. A reader takes one snapshot and sees a consistent pair,
even while other threads record more sales.

## Faster code on a runtime built for it

Python's flexibility has a runtime cost. Attributes, classes, and behavior
can change, so optimized code often needs checks to establish that its
assumptions still hold. Grail makes some of those assumptions permanent:

- Records have fixed fields and immutable types. Fields cannot be rebound,
  and subclasses cannot change their layout or behavior.
- Generic functions give the runtime an explicit dispatch table. It can
  cache a selected implementation and invalidate that choice on registration.
- Conforming value records contain recognized immutable values. Deepcopy can
  reuse them instead of rebuilding an object graph.

With Grail-aware CPython, these guarantees create opportunities for faster
construction, dispatch, copying, and access to shared data. A future optimizer
could go further by avoiding temporary record allocations or keeping numeric
fields unboxed. You write the data model; the runtime exploits its guarantees.

The native prototype already shows gains in record-heavy code, copying, and
sharing. This is not a promise that every rewrite is faster: creating new
numeric records can still cost more than updating mutable objects in place.
Larger gains from unboxing and allocation elimination remain future work.
Installing grail-compat on stock Python lets you try the programming model;
it does not add those interpreter optimizations.

## Better scaling with free-threading

Removing the GIL lets threads execute Python code in parallel. It does not
remove contention on shared state. If every worker updates the same container
or takes the same lock, more workers may spend more time waiting than working.

Grail encourages a different shape:

1. Give workers an immutable input snapshot they can read without protecting
   every field access with an application lock.
2. Keep scratch lists, dictionaries, and builders private to each worker.
3. Return immutable results and combine them, rather than coordinating every
   small update through shared mutable state.

Persistent collections keep earlier snapshots usable while sharing unchanged
structure. Native generic dispatch uses registry snapshots without a shared
reader lock. Together, these make it easier to keep useful work independent
and reduce coordination as you add cores.

The native prototype demonstrates multi-threaded scaling on suitable workloads.
Scaling still depends on task size, serial dependencies, allocation, and
shared-reference costs. Grail does not make a sequential algorithm parallel,
and frequent writes to one Atom can still become a bottleneck.

## Early benchmark results

These are early benchmark results, based on translating existing benchmark
scripts into idiomatic Grail style. They show representative workloads,
including cases where the rewrite is slower. The comparisons include changes
to data representation, program structure, and parallel execution, not just
interpreter optimizations.

The measurements use the native CPython 3.15-based prototype, commit
`5c74a29e4ba`, on a Ryzen 5 2600X with **6 cores and 12 hardware threads**.
Both interpreters were built with clang-19, PGO and LTO. These are
**free-threaded builds, with the JIT disabled**, over three rounds.
Times are medians of fresh-process medians. Changes below about 5% should be
treated as noise, not reliable wins or losses.

### Single-threaded: rewrite versus original

Both programs run on **one thread**. The original runs on upstream CPython;
the Grail rewrite runs on cpython-grail. Speedup is the original's time divided
by the rewrite's time: **above 1x is faster; below 1x is slower**.

| Workload | Original, 1 thread (ms) | Grail rewrite, 1 thread (ms) | Speedup versus original |
| --- | ---: | ---: | ---: |
| Ray tracing | 483.8 | 250.1 | 1.93x |
| Richards | 84.8 | 73.2 | 1.16x |
| DeltaBlue | 5.85 | 5.88 | 1.00x |
| Float | 117.0 | 102.6 | 1.14x |
| Fannkuch | 642.8 | 654.9 | 0.98x |
| Aggregation | 679.5 | 620.4 | 1.10x |
| Mandelbrot | 432.6 | 346.3 | 1.25x |
| Spectral norm | 130.4 | 143.0 | 0.91x |
| Deepcopy | 117.8 | 7.78 | 15.2x |
| Nbody | 159.0 | 366.7 | 0.43x |

The deepcopy rewrite can reuse conforming immutable values instead of copying
them. Nbody shows the opposite trade-off: its immutable numeric updates create
many temporary records, while the original updates numbers in place.
A faster Grail runtime does not make every representation change a win.

### Multi-threaded: application speedup versus scaling

Here the **same Grail rewrite uses 12 worker threads**. The two ratio columns
answer different questions:

- **Versus original:** original program's one-thread time divided by the
  Grail rewrite's 12-thread time. This combines the rewrite's single-thread
  advantage or disadvantage with its parallel speedup.
- **Scaling:** Grail rewrite's one-thread time divided by its own 12-thread
  time. This isolates how much faster that rewrite runs when given more threads.
  It is not a comparison against a parallel version of the original.

| Workload | Grail rewrite, 12 threads (ms) | Versus original, 1 thread | Scaling: Grail 1 to 12 threads |
| --- | ---: | ---: | ---: |
| Ray tracing | 51.0 | 9.49x | 4.91x |
| Float | 13.9 | 8.41x | 7.38x |
| Fannkuch | 96.4 | 6.67x | 6.79x |
| Aggregation | 96.8 | 7.02x | 6.41x |
| Mandelbrot | 56.9 | 7.61x | 6.09x |
| Spectral norm | 37.5 | 3.48x | 3.81x |
| Deepcopy | 3.43 | 34.3x | 2.26x |
| Build graph | 442.3 | 7.42x | 7.38x |

For example, ray tracing is about **9.5x faster than the original serial
program**, but its **parallel scaling is about 4.9x**, not 9.5x. Deepcopy's
large advantage over the original mostly comes from avoiding copying, not
from adding threads. Twelve workers share six physical cores with simultaneous
multithreading; the scaling column is a wall-time ratio, not per-core efficiency.

Not every workload has a comparable 12-thread version. Richards, chaos and
Nbody are serial here; independent runs would measure throughput rather than
speeding up one run. Changed search or random algorithms are not substituted
for missing parallel results.

These are small benchmark programs, not full-application performance promises.
They are native-prototype measurements, not grail-compat timings. The source
snapshot and full results are recorded in the parent workspace under
`bench/results/20261026-111345-5c74a29e4ba/`; the prototype status report is
[CPython prototype status](design/06-cpython-prototype.md).

## Fewer opportunities for races

With mutable shared objects, every read raises a question: can another thread
change this while I use it? A lock around one container operation is not enough
if your invariant spans several fields or operations.

With an immutable snapshot, you can inspect related fields without the data
changing underneath you. Put related state in one record, then use an Atom
to replace that snapshot atomically. In the example, a successful swap updates
both the sale count and revenue. Competing swaps retry instead of silently
overwriting each other's changes.

This does not make races impossible. A record can hold an ordinary mutable
child; the record does not protect that child. Transfer snapshots through
supported synchronization, such as an Atom, a queue, or task submission.
Keep swap callbacks pure because they may run more than once. External effects
such as charging a card still need their own coordination.

The benefit is a smaller area to review: immutable data can be shared, private
scratch stays private, and shared updates have an explicit entry point.

## When to try it

Grail is a good experiment when you have data shared by many readers,
copy-heavy object graphs, type-dependent operations, or independent work you
want to spread across threads. Start with one part of your program, keep its
behavior tests, and measure on the native runtime before drawing performance
conclusions. Ordinary classes and local mutation remain useful tools.

[Get started](getting-started.md), follow the [shop tutorial](tutorial/index.md),
or read the [concurrency guide](guide/concurrency.md). The
[ray tracer](examples/raytrace.md) shows shared immutable input; the
[aggregate example](examples/aggregate.md) shows private work followed by a merge.
See [backends](backends.md) for what the compatibility package and native
prototype provide today.
