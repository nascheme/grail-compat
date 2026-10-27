---
sources:
  - grail-compat/docs/design/00-project-grail-overview.md
  - grail-compat/docs/design/07-appendix-concurrency-toolkit.md
  - grail-compat/src/grail/_atom.py
  - grail-compat/tests/test_grail_atom.py
  - grail-compat/docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_vector.py
  - grail-compat/tests/test_grail_vector.py
---

# Share values across threads

Share immutable snapshots and make reference transfer explicit. An Atom or
queue transfers a fully constructed snapshot; ordinary mutable children still
need synchronization.

    >>> from grail import Atom, Pool
    >>> counter = Atom(0)
    >>> def work(_):
    ...     return counter.add(1)
    >>> with Pool(2) as pool:
    ...     results = list(pool.map(work, range(8)))
    >>> sorted(results), counter.value
    ([1, 2, 3, 4, 5, 6, 7, 8], 8)

Build the initial state before submitting work. For later communication use
Atom operations, `queue.SimpleQueue`, a pool scope, or a lock. Immutability does
not make unsynchronized C pointer transfer safe. A reader must both see
initialized data and own a valid reference.

Read `snapshot = state.value` once per logical operation. Reading separate
fields from separate snapshots can mix versions. Keep related state in one
record inside one Atom so a successful swap changes it together.

Record field bindings and published collection storage do not change.
Their children can: an ordinary list field is not protected by its parent,
and an Atom protects only its own reference. Use immutable children, private
scratch lists/dicts, and a transient owned by one worker until finish.

For reductions, have each worker fold privately and merge completed results
in a defined order. Floating-point sums depend on partition and merge order.
For random work, create a `random.Random` per task and pass it explicitly;
derive seeds from stable task indices when reproducibility matters. There
is no Grail random-stream helper today.

Generic registration is atomic, but implementation bodies need their own
thread-safety rules. Swap callbacks must be pure because they can retry.
The compatibility implementation uses locks for Atom operations; native reads
have different costs. Neither backend guarantees application speedup. CPU
parallelism needs a free-threaded interpreter and sufficient task size.

For parallel loops use `grail.pmap` (independent items) or
`grail.parallel_reduce` (reductions with a fixed partition); for tasks that
create more tasks, a pool scope's `spawn`.

References: [Atom](../reference/atom.md), [Pool](../reference/pool.md),
[generic functions](../reference/generic.md).
See [backend differences](../backends.md) and [the aggregate example](../examples/aggregate.md).
