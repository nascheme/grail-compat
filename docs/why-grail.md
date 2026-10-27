---
sources:
  - docs/design/00-project-grail-overview.md
  - docs/design/01-pep-draft-records.md
  - docs/design/02-pep-draft-generic-functions.md
  - docs/design/05-design-persistent-collections.md
  - docs/design/06-cpython-prototype.md
  - src/grail/__init__.py
  - src/grail/_atom.py
  - tests/test_grail.py
  - tests/test_grail_atom.py
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
sharing. This is not a promise that every idiomatic Grail version is faster: creating new
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

## Current benchmark status

[CPython prototype status](design/06-cpython-prototype.md) reports the latest
matched measurements, setup and remaining limits. They compare native Grail against
matched upstream builds, not grail-compat against stock Python.

Unchanged programs run near parity. Several coarse parallel rewrites scale
about 6-8x on eight cores. Record-heavy rewrites beat same-source stock Python
controls, but fully immutable nbody, Richards, DeltaBlue and Go remain slower
than their mutable originals. The GIL-enabled JIT improves numeric record
arithmetic; snapshot allocation remains a cost.

Distinguish three comparisons: unchanged code measures the VM effect;
a rewrite versus the original includes algorithm and representation changes;
the rewrite versus itself at more threads measures scaling. Copy avoidance
and a different search algorithm are not interpreter-only speedups. Results
are bounded benchmark observations, not full-application promises, and
small differences require more measurement.

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
