"""Richards (Martin Richards' OS-kernel simulation), Grail style: hybrid control.

The earlier conversion, kept as a comparison control for idiomatic.py: value
records for packets, task data and actions, but the kernel keeps its state
in local mutable lists.  Same algorithm, trace and counters.

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
* What a task asks the kernel to do is a closed set of alternatives, so it is
  a union of records (``Action = Wait | Hold | Release | Send``) interpreted
  with ``match``.
* The kernel itself (``schedule``) owns all changing state: per-task run
  flags, input queues and current data live in plain lists that are local to
  that one function, and the two counters are local ints.  This is the
  honest choice: the scheduler is an inherently sequential state machine
  that updates a handful of slots per step; making the whole kernel state a
  persistent value would allocate a new state per step for no benefit, since
  nothing else ever looks at intermediate states.
* Packet queues are tuples (never more than four packets), replacing the
  original's intrusive linked lists.  A packet that "changes" (a handler
  bumping a work packet's index, the kernel stamping the sender id) is
  rebuilt with ``copy.replace``.

Parallelism
-----------
None within one run: every step depends on the state left by the previous
one.  pyperformance times ``Richards().run(1)`` repeatedly; those repetitions
are independent, and ``main(iterations, threads)`` will spread several
iterations over threads, but that is a throughput measure, not the
benchmark's latency, so the default workload (one iteration) runs on one
thread.
"""

import copy
from concurrent.futures import ThreadPoolExecutor

import grail

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


# What a step asks the kernel to do: a closed union of records.
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
    work_in = (copy.replace(work, datum=i + 1),) + work_in[1:]
    return (HandlerData(work_in, device_in[1:]),
            Send(copy.replace(dev, datum=work.data[i])))


@step.impl
def _(data: DeviceData, packet):
    d = data
    if packet is None:
        if d.pending is None:
            return d, WAIT
        return DeviceData(None), Send(d.pending)
    return DeviceData(packet), HOLD


# ---------------------------------------------------------------- kernel

def initial_state():
    """Task data and input queues as the original's Richards.run sets them up."""
    work = Packet(0, K_WORK, 0, EMPTY_DATA)
    dev_a = Packet(I_DEVA, K_DEV, 0, EMPTY_DATA)
    dev_b = Packet(I_DEVB, K_DEV, 0, EMPTY_DATA)
    data = (None, IdleData(1, 10000), WorkerData(I_HANDLERA, 0),
            HandlerData((), ()), HandlerData((), ()),
            DeviceData(None), DeviceData(None))
    queues = ((), (), (work,) * 2, (dev_a,) * 3, (dev_b,) * 3, (), ())
    return data, queues


def schedule(trace=None):
    """Run the kernel to completion; return the hold and queue counters."""
    data_t, queues_t = initial_state()
    # Local working state of this one function, indexed by task id.
    data = list(data_t)
    queue = list(queues_t)
    pending = [bool(q) for q in queue]       # packet waiting in the input queue
    waiting = [False, False, True, True, True, True, True]  # idle starts running
    holding = [False] * NTASKS
    hold_count = qpkt_count = 0

    t = I_DEVB                               # head of the task list
    while t:
        if holding[t] or (waiting[t] and not pending[t]):
            t = NEXT[t]                      # not runnable: try the next one
            continue
        if trace is not None:
            trace(chr(ord("0") + t))
        packet = None
        if waiting[t] and pending[t]:        # waiting with a packet: take it
            q = queue[t]
            packet, queue[t] = q[0], q[1:]
            waiting[t] = False
            pending[t] = len(q) > 1
            if trace is not None and t >= I_DEVA:
                trace(packet.datum)
        data[t], action = step(data[t], packet)

        match action:
            case Wait():
                waiting[t] = True
            case Hold():
                hold_count += 1
                holding[t] = True
                t = NEXT[t]
            case Release(target):
                holding[target] = False
                if PRIORITY[target] > PRIORITY[t]:
                    t = target
            case Send(p):
                dest = p.ident
                qpkt_count += 1
                p = copy.replace(p, ident=t)  # the receiver sees the sender's id
                if queue[dest]:
                    queue[dest] += (p,)
                else:
                    queue[dest] = (p,)
                    pending[dest] = True
                    if PRIORITY[dest] > PRIORITY[t]:
                        t = dest
    return Result(hold_count, qpkt_count)


def check(r: Result) -> bool:
    return r.hold_count == EXPECTED_HOLD and r.qpkt_count == EXPECTED_QPKT


def main(iterations=1, threads=8):
    """Same workload as pyperformance's Richards().run(iterations).

    Iterations are independent; with more than one iteration and threads > 1
    they run concurrently (a throughput variant; results are identical).
    """
    if iterations > 1 and threads > 1:
        with ThreadPoolExecutor(min(threads, iterations)) as ex:
            results = list(ex.map(lambda _: schedule(), range(iterations)))
    else:
        results = [schedule() for _ in range(iterations)]
    return all(check(r) for r in results), results


if __name__ == "__main__":
    ok, results = main()
    r = results[0]
    print(f"richards: hold_count={r.hold_count} qpkt_count={r.qpkt_count} "
          f"{'OK' if ok else 'FAILED'}")
