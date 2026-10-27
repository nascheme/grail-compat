---
sources:
  - benchmarks/README.md
  - benchmarks/deltablue/idiomatic.py
  - benchmarks/deltablue/upstream.py
  - benchmarks/deltablue/hybrid.py
  - benchmarks/deltablue/replace.py
---

# DeltaBlue: graph IDs and drafts

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

Read `benchmarks/deltablue/idiomatic.py` from `Solver` through
`add_constraint` and `open_draft`. Each operation edits a
[draft](../reference/mutate.md) of the Solver: propagation assigns fields of
the variables it visits, and `finish` publishes a new snapshot, rebuilding
each changed `Var` once. Work queues stay private deques. The graph can be
cyclic logically without needing a cycle of direct mutable object links.

    def recalculate(b, c):
        ...
        x.walk = max(c.strength, xi.walk)
        x.stay = stay

The constraint table is only read during an operation, so `open_draft`
assigns it back unchanged: an assigned field reads back as stored, so
lookups return the constraint records rather than drafts of them.

Solver's PVector annotations are ordinary fields today, despite the source
calling the snapshot a value. The state can still be immutable and shareable;
it does not qualify for value-record substitution by those annotations.
A draft is confined to its thread until it is finished.

## Compare the migration stages

`benchmarks/deltablue/upstream.py` is the class-based algorithm.
`benchmarks/deltablue/hybrid.py` keeps a record of mutable lists.
The idiomatic source and benchmark README describe the current snapshot port.
`benchmarks/deltablue/replace.py` is the same port with a Batch of PVector
transients, rebuilding a `Var` with `copy.replace` on every change.
Natively the draft version runs within about 3% of it free-threaded and 12%
with the JIT.

The chain and projection tests are independent and can run concurrently;
propagation within either solve remains dependent work. The port retains
upstream quirks for comparison rather than changing the solver's algorithm.

See [transients](../guide/transients.md), [value records](../guide/value-records.md),
[porting](../guide/porting.md).
