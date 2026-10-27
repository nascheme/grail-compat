---
sources:
  - grail-compat/benchmarks/README.md
  - grail-compat/benchmarks/richards/idiomatic.py
  - bench/notes/richards.md
  - grail-compat/benchmarks/richards/upstream.py
  - grail-compat/benchmarks/richards/hybrid.py
---

# Richards: a pure transition scheduler

Richards models a priority scheduler. The port separates a task's pure step
from the scheduler that interprets its requested action. Small action records
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
| Updates to global task work area | `scheduler_step(state)` returns a new SchedulerState |
| Scheduler methods perform effects directly | `Wait`, `Hold`, `Release`, `Send` records interpreted with match |

Read `benchmarks/richards/idiomatic.py`: the four `step` implementations return
`(new_data, action)`. `scheduler_step` replaces affected Task records with
`grail.replace` and stores them with `PVector.set`, so the task table is a
persistent vector and old snapshots remain usable. Packets are changed the
same way, without mutating earlier packets.

`benchmarks/richards/hybrid.py` keeps the earlier scheduler with private mutable
lists. It uses the same task operations but does not publish an intermediate
scheduler state on every transition. The original class hierarchy is in
`benchmarks/richards/upstream.py`.

## What not to parallelize

Each scheduler step depends on the previous step. Running independent complete
iterations concurrently measures throughput, not latency of one simulation.
Records do not remove that dependency.

The parent `bench/notes/richards.md` describes the earlier list-based scheduler.
Use the current source and benchmark README for the snapshot design; do not
copy the old note's representation claims into new code.

See [porting](../guide/porting.md), [records](../guide/records.md),
[generic functions](../guide/generic-functions.md).
