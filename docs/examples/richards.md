---
sources:
  - benchmarks/README.md
  - benchmarks/richards/idiomatic.py
  - benchmarks/richards/upstream.py
  - benchmarks/richards/hybrid.py
  - benchmarks/richards/mutate.py
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

`benchmarks/richards/mutate.py` writes the same transition with an
experimental [draft](../reference/mutate.md). `scheduler_step` assigns to
the next snapshot as the mutable original assigns to its objects:

    with grail.mutate(state) as s:
        ...
        case Send(p):
            s.qpkt_count += 1
            dest = s.tasks[p.ident]
            dest.queue += (replace(p, ident=t),)
        ...
    return grail.finish(s)

It produces the same trace and counters. On the native backend it runs
about 1.3x slower than the `replace` version: finishing builds the same
records, and every edit also goes through a draft.

## What not to parallelize

Each scheduler step depends on the previous step. Running independent complete
iterations concurrently measures throughput, not latency of one simulation.
Records do not remove that dependency.

Use the current source and benchmark README for the snapshot design.

See [porting](../guide/porting.md), [records](../guide/records.md),
[generic functions](../guide/generic-functions.md).
