---
sources:
  - benchmarks/README.md
  - benchmarks/deltablue/idiomatic.py
  - benchmarks/deltablue/upstream.py
  - benchmarks/deltablue/hybrid.py
---

# DeltaBlue: graph IDs and batches

DeltaBlue maintains a constraint graph. The idiomatic port stores references
as integer IDs and updates a persistent snapshot through private batches.
This miniature graph shows how IDs avoid relying on record identity.

    >>> from grail import record, replace, PVector
    >>> @record
    ... class Variable:
    ...     value: int
    >>> before = PVector([Variable(1), Variable(2)])
    >>> with before.mutate() as batch:
    ...     batch[1] = replace(batch[1], value=9)
    >>> after = batch.finish()
    >>> before[1].value, after[1].value
    (2, 9)

## Definitions versus state

| Upstream | Idiomatic |
| --- | --- |
| Constraint inheritance hierarchy | `Stay`, `Edit`, `Equal`, `Scale` record alternatives |
| Object links and identity comparisons | Integer variable IDs and constraint `cid` |
| Mutable Variable objects | Var records in a PVector |
| Global mutable planner | Solver snapshots returned by operations |

Read `benchmarks/deltablue/idiomatic.py` from `Solver` through `Batch`,
`open_batch`, and `close_batch`. A batch owns transients over the vectors.
Propagation changes private builders, then finish publishes a new snapshot.
Work queues stay private deques. The graph can be cyclic logically without
needing a cycle of direct mutable object links.

Solver's PVector annotations are ordinary fields today, despite the source
calling the snapshot a value. The state can still be immutable and shareable;
it does not qualify for value-record substitution by those annotations.
Batch is deliberately not shareable while its builders are open.

## Compare the migration stages

`benchmarks/deltablue/upstream.py` is the class-based algorithm.
`benchmarks/deltablue/hybrid.py` keeps a record of mutable lists.
The idiomatic source and benchmark README describe the current snapshot port.

The chain and projection tests are independent and can run concurrently;
propagation within either solve remains dependent work. The port retains
upstream quirks for comparison rather than changing the solver's algorithm.

See [transients](../guide/transients.md), [value records](../guide/value-records.md),
[porting](../guide/porting.md).
