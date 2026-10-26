---
sources:
  - grail-compat/benchmarks/README.md
  - grail-compat/benchmarks/richards/idiomatic.py
  - bench/notes/richards.md
  - grail-compat/benchmarks/richards/upstream.py
  - grail-compat/benchmarks/richards/hybrid.py
---

# Richards: a pure transition kernel

Richards models a priority scheduler. The port separates a task's pure step
from the kernel that interprets its requested action. Small action records
make control flow explicit.

    >>> from grail import record
    >>> @record
    ... class Release:
    ...     target: int
    >>> def action_target(action):
    ...     match action:
    ...         case Release(target):
    ...             return target
    ...         case _:
    ...             return None
    >>> action_target(Release(3))
    3

## Separate behavior from bookkeeping

| Upstream | Idiomatic |
| --- | --- |
| Task subclasses with virtual `fn` | Generic `step(data, packet)` |
| Mutable linked packets | Packet records and short tuple queues |
| Updates to global task work area | `kernel_step(kernel)` returns a new Kernel |
| Scheduler methods perform effects directly | `Wait`, `Hold`, `Release`, `Send` records interpreted with match |

Read `benchmarks/richards/idiomatic.py`: the four `step` implementations return
`(new_data, action)`. `kernel_step` replaces affected Task records and rebuilds
the small seven-slot table. Old Kernel snapshots remain usable.
The source uses `copy.replace` to change packet fields without mutating
earlier packets. In new Grail code, use `grail.replace` for the same record
update; it also works on Python versions before 3.13.

`benchmarks/richards/hybrid.py` keeps the earlier scheduler with private mutable
lists. It uses the same task operations but does not publish an intermediate
kernel value on every transition. The original class hierarchy is in
`benchmarks/richards/upstream.py`.

## What not to parallelize

Each scheduler step depends on the previous step. Running independent complete
iterations concurrently measures throughput, not latency of one simulation.
Records do not remove that dependency.

The parent `bench/notes/richards.md` describes the earlier list-based kernel.
Use the current source and benchmark README for the snapshot design; do not
copy the old note's representation claims into new code.

See [porting](../guide/porting.md), [records](../guide/records.md),
[generic functions](../guide/generic-functions.md).
