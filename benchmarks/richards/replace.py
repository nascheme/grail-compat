"""Richards (Martin Richards' task-scheduling simulation), Grail style:
scheduler state is an immutable snapshot with a persistent task table.

The previous idiomatic version, kept for comparison: the same data, task
behaviour and results as idiomatic.py, but ``scheduler_step`` builds the
next snapshot with explicit ``PVector.set`` and ``grail.replace`` calls
instead of editing a ``grail.mutate`` draft.  The difference in time is
the cost of drafts.

What it computes
----------------
A tiny priority-scheduled operating system with six tasks: an idle task, a
worker, two packet handlers and two "devices".  Two work packets and six
device packets circulate.  The worker fills a work packet with four letters
and sends it alternately to handler A or B; a handler copies the letters one
at a time into device packets and sends them to its device; a device holds a
packet until the idle task (driven by a 16-bit pseudo-random shift register)
releases it, then sends it back.  The idle task stops after 10000 ticks.  The
scheduler always runs the highest-priority runnable task.  The result is two
counters, the number of hold operations and of packets queued, which must be
9297 and 23246.  Nothing else is observable (the original's trace output is
off by default; ``schedule(trace=...)`` reproduces it for verification).

The original
------------
A Java/C++/Smalltalk translation: a Task class hierarchy with a virtual
``fn``, a mutable ``TaskState`` base class with boolean flags, mutable Packet
objects linked into intrusive lists, mutable per-task record objects and a
global ``taskWorkArea``.  The benchmark exists to measure virtual calls,
attribute access on mutable objects and pointer chasing.

Design
------
* Packets and the per-task private data are value records.  Each task kind's
  behaviour is a *pure* step function, ``step(data, packet) -> (data,
  action)``, dispatched by the generic function ``step`` on the exact type of
  the task data.  That generic call is the Grail counterpart of the
  benchmark's virtual ``Task.fn`` call.
* What a task asks the scheduler to do is a closed set of alternatives, so it is
  a union of records (``Action = Wait | Hold | Release | Send``) interpreted
  with ``match``.
* ``SchedulerState`` holds a persistent ``PVector`` task table (``Task``
  records by id: data, input queue, waiting and holding flags), the current
  task and the two counters.  ``scheduler_step`` is a pure transition
  between snapshots, moving past tasks that cannot run, then running one.
  Updates use ``PVector.set`` and ``grail.replace``; old snapshots remain
  usable.  The original's separate "packet pending" flag is the queue
  being non-empty.  Task-table updates need no slicing or concatenation
  (an earlier version kept the table as a tuple).  PVector fields
  are currently ordinary fields, so SchedulerState is not classified as
  a value record despite its immutable contents.
* Packet queues are tuples (never more than four packets), replacing the
  original's intrusive linked lists.  A packet that "changes" (a handler
  bumping a work packet's index, the scheduler stamping the sender id) is
  rebuilt with ``grail.replace``.

Parallelism
-----------
None within one run: every step depends on the state left by the previous
one.  pyperformance times ``Richards().run(1)`` repeatedly; those repetitions
are independent, and ``main(iterations, threads)`` will spread several
iterations over threads, but that is a throughput measure, not the
benchmark's latency, so the default workload (one iteration) runs on one
thread.
"""

import grail
from grail import PVector, replace

# Task ids.  Tasks are created in this order, and the scheduler's task list
# is in reverse creation order, so the list is DEVB, DEVA, ..., IDLE.
I_IDLE, I_WORK, I_HANDLERA, I_HANDLERB, I_DEVA, I_DEVB = 1, 2, 3, 4, 5, 6
NTASKS = 7                       # slot 0 unused (0 plays the role of None)
PRIORITY = (0, 0, 1000, 2000, 3000, 4000, 5000)   # the idle task runs at 0
NEXT = (0, 0, 1, 2, 3, 4, 5)     # next task in the scheduler's list; 0 = end

K_DEV, K_WORK = 1000, 1001       # packet kinds
BUFSIZE = 4
A = ord("A")

EXPECTED_HOLD = 9297
EXPECTED_QPKT = 23246


# ------------------------------------------------------------------ data

@grail.record
class Packet:
    ident: int                   # destination while queued for sending,
                                 # sender ("reply-to") once delivered
    kind: int                    # K_DEV or K_WORK
    datum: int                   # work packet: next index; device packet: a letter
    data: tuple[int, ...]        # work packet payload (four letters)


EMPTY_DATA = (0,) * BUFSIZE


@grail.record
class IdleData:
    control: int                 # 16-bit shift register choosing DEVA / DEVB
    count: int                   # ticks left


@grail.record
class WorkerData:
    destination: int             # handler the next work packet goes to
    count: int                   # last letter used, 1..26


@grail.record
class HandlerData:
    work_in: tuple[Packet, ...]
    device_in: tuple[Packet, ...]


@grail.record
class DeviceData:
    pending: Packet | None


# What a step asks the scheduler to do: a closed union of records.
@grail.record
class Wait:                      # nothing to do until a packet arrives
    pass


@grail.record
class Hold:                      # suspend me until released
    pass


@grail.record
class Release:                   # make task `target` runnable again
    target: int


@grail.record
class Send:                      # queue `packet` for task packet.ident
    packet: Packet


Action = Wait | Hold | Release | Send
WAIT, HOLD = Wait(), Hold()


@grail.record
class Result:
    hold_count: int
    qpkt_count: int


# ------------------------------------------------------- task behaviour

@grail.generic
def step(data, packet):
    """One activation of a task: (task data, packet or None) -> (data, action)."""
    raise TypeError(f"no task behaviour for {type(data).__name__}")


@step.impl
def _(data: IdleData, packet):
    d = data
    count = d.count - 1
    if count == 0:
        return IdleData(d.control, count), HOLD
    if d.control & 1 == 0:
        return IdleData(d.control // 2, count), Release(I_DEVA)
    return IdleData((d.control // 2) ^ 0xD008, count), Release(I_DEVB)


@step.impl
def _(data: WorkerData, packet):
    w = data
    if packet is None:
        return w, WAIT
    dest = I_HANDLERB if w.destination == I_HANDLERA else I_HANDLERA
    count = w.count
    letters = []
    for _ in range(BUFSIZE):
        count = count + 1 if count < 26 else 1
        letters.append(A + count - 1)
    out = Packet(dest, packet.kind, 0, tuple(letters))
    return WorkerData(dest, count), Send(out)


@step.impl
def _(data: HandlerData, packet):
    h = data
    work_in, device_in = h.work_in, h.device_in
    if packet is not None:
        if packet.kind == K_WORK:
            work_in += (packet,)
        else:
            device_in += (packet,)
    if not work_in:
        return HandlerData(work_in, device_in), WAIT
    work = work_in[0]
    i = work.datum
    if i >= BUFSIZE:                         # work packet used up: return it
        return HandlerData(work_in[1:], device_in), Send(work)
    if not device_in:
        return HandlerData(work_in, device_in), WAIT
    dev = device_in[0]
    work_in = (replace(work, datum=i + 1),) + work_in[1:]
    return (HandlerData(work_in, device_in[1:]),
            Send(replace(dev, datum=work.data[i])))


@step.impl
def _(data: DeviceData, packet):
    d = data
    if packet is None:
        if d.pending is None:
            return d, WAIT
        return DeviceData(None), Send(d.pending)
    return DeviceData(packet), HOLD


# ------------------------------------------------------------- scheduler

@grail.record
class Task:
    data: IdleData | WorkerData | HandlerData | DeviceData
    queue: tuple[Packet, ...]    # input packets, oldest first
    waiting: bool                # waiting for a packet
    holding: bool                # suspended until released


@grail.record
class SchedulerState:
    tasks: PVector[Task | None]   # by task id; slot 0 unused
    current: int                     # task to consider next; 0 = finished
    hold_count: int
    qpkt_count: int


def initial_state() -> SchedulerState:
    """The tasks and packets as the original's Richards.run sets them up."""
    work = Packet(0, K_WORK, 0, EMPTY_DATA)
    dev_a = Packet(I_DEVA, K_DEV, 0, EMPTY_DATA)
    dev_b = Packet(I_DEVB, K_DEV, 0, EMPTY_DATA)
    tasks = PVector((None,
             Task(IdleData(1, 10000), (), False, False),   # idle starts running
             Task(WorkerData(I_HANDLERA, 0), (work,) * 2, True, False),
             Task(HandlerData((), ()), (dev_a,) * 3, True, False),
             Task(HandlerData((), ()), (dev_b,) * 3, True, False),
             Task(DeviceData(None), (), True, False),
             Task(DeviceData(None), (), True, False)))
    return SchedulerState(tasks, I_DEVB, 0, 0)  # DEVB heads the task list


def scheduler_step(state: SchedulerState, trace=None) -> SchedulerState:
    """The scheduler after its next task activation (or finished)."""
    tasks = state.tasks
    t = state.current
    while True:                              # skip tasks that cannot run
        task = tasks[t]
        if not (task.holding or (task.waiting and not task.queue)):
            break
        t = NEXT[t]
        if not t:
            return replace(state, current=0)
    if trace is not None:
        trace(chr(ord("0") + t))
    packet = None
    queue = task.queue
    waiting = task.waiting
    if waiting and queue:                    # waiting with a packet: take it
        packet, queue = queue[0], queue[1:]
        waiting = False
        if trace is not None and t >= I_DEVA:
            trace(packet.datum)
    data, action = step(task.data, packet)

    hold_count, qpkt_count = state.hold_count, state.qpkt_count
    match action:
        case Wait():
            tasks = tasks.set(t, replace(task, data=data, queue=queue,
                                         waiting=True))
        case Hold():
            hold_count += 1
            tasks = tasks.set(t, replace(task, data=data, queue=queue,
                                         waiting=waiting, holding=True))
            t = NEXT[t]
        case Release(target):
            tasks = tasks.set(t, replace(task, data=data, queue=queue,
                                         waiting=waiting))
            tasks = tasks.set(target, replace(tasks[target], holding=False))
            if PRIORITY[target] > PRIORITY[t]:
                t = target
        case Send(p):
            dest = p.ident
            qpkt_count += 1
            p = replace(p, ident=t)          # the receiver sees the sender's id
            tasks = tasks.set(t, replace(task, data=data, queue=queue,
                                         waiting=waiting))
            d = tasks[dest]
            tasks = tasks.set(dest, replace(d, queue=d.queue + (p,)))
            if not d.queue and PRIORITY[dest] > PRIORITY[t]:
                t = dest
    return replace(state, tasks=tasks, current=t,
                   hold_count=hold_count, qpkt_count=qpkt_count)


def schedule(trace=None):
    """Run the scheduler to completion; return the hold and queue counters."""
    state = initial_state()
    while state.current:
        state = scheduler_step(state, trace)
    return Result(state.hold_count, state.qpkt_count)


def check(r: Result) -> bool:
    return r.hold_count == EXPECTED_HOLD and r.qpkt_count == EXPECTED_QPKT


def main(iterations=1, threads=8):
    """Same workload as pyperformance's Richards().run(iterations).

    Iterations are independent; with more than one iteration and threads > 1
    they run concurrently (a throughput variant; results are identical).
    """
    if iterations > 1 and threads > 1:
        with grail.Pool(min(threads, iterations)) as pool:
            results = grail.pmap(lambda _: schedule(), range(iterations),
                                 chunk=1, pool=pool)
    else:
        results = [schedule() for _ in range(iterations)]
    return all(check(r) for r in results), results


if __name__ == "__main__":
    ok, results = main()
    r = results[0]
    print(f"richards: hold_count={r.hold_count} qpkt_count={r.qpkt_count} "
          f"{'OK' if ok else 'FAILED'}")
