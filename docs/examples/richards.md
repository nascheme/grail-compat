---
sources:
  - benchmarks/README.md
  - benchmarks/richards/idiomatic.py
  - benchmarks/richards/upstream.py
  - benchmarks/richards/hybrid.py
  - benchmarks/richards/replace.py
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
`(new_data, action)`. `scheduler_step` edits a [draft](../reference/mutate.md)
of the state, assigning to the next snapshot as the mutable original assigns
to its objects:

    with mutate(state) as s:
        ...
        case Send(p):
            s.qpkt_count += 1
            dest = s.tasks[p.ident]
            dest.queue += (replace(p, ident=t),)
        ...
    return finish(s)

`finish` builds the changed Task records and the task table, a persistent
vector, so old snapshots remain usable. A packet placed in a queue is a new
value built with `replace`, not an edit of the old one.

`benchmarks/richards/hybrid.py` keeps the earlier scheduler with private mutable
lists. It uses the same task operations but does not publish an intermediate
scheduler state on every transition. The original class hierarchy is in
`benchmarks/richards/upstream.py`.

`benchmarks/richards/replace.py` builds each snapshot with explicit
`grail.replace` and `PVector.set` calls instead. It produces the same trace
and counters. On the native backend the draft version runs about 1.2x
slower: finishing builds the same records, and every edit also goes through
a draft.

## What not to parallelize

Each scheduler step depends on the previous step. Running independent complete
iterations concurrently measures throughput, not latency of one simulation.
Records do not remove that dependency.

Use the current source and benchmark README for the snapshot design.

See [porting](../guide/porting.md), [records](../guide/records.md),
[generic functions](../guide/generic-functions.md).
