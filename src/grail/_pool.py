"""Pool: pure-Python stand-in for the native _grail_pool module.

The API and several design choices come from Quansight's spinningjenny
(https://github.com/Quansight/spinningjenny), a Rust/PyO3
ThreadPoolExecutor by Itamar Turner-Trauring and Nathan Goldbaum.  This is
an independent implementation; no spinningjenny code is copied.

It follows the native pool's design, so the two behave alike: a map has no
task queue.  Each worker claims a batch of items by iterating the inputs
itself under the map's iterator lock, runs them, and stores each result
under its input position (ordered) or in completion order (unordered).
Batch size adapts to the measured task time.  A pool worker waiting for a
map of its own pool runs pool work meanwhile, so nested maps cannot
deadlock.  Workers are daemon threads.

Unlike the native pool, results are published under a lock, once per batch,
so the per-task overhead is that of Python-level lock operations, not of C
atomics.
"""

import collections
import contextvars
import os
import threading
import time

__all__ = ["Pool"]

# A batch should take about this long; the batch size follows from the
# measured time per task.
_BATCH_TARGET_NS = 100_000
_MAX_BATCH = 16
# A worker helping while it waits on a nested map polls this often.
_HELP_POLL = 0.001

_worker = threading.local()


class _Core:
    """The state shared by a Pool, its workers and its maps.

    Workers reference this, not the Pool, so dropping the last reference
    to a Pool shuts it down.
    """

    def __init__(self, n_threads):
        self.n_threads = n_threads
        self.cond = threading.Condition(threading.Lock())
        self.jobs = collections.deque()   # cond
        self.shutdown = False
        self.pid = os.getpid()
        self.threads = []
        self.idle = 0             # workers waiting on cond

    def find_job(self, block):
        """Return a map with work available, waiting for one if block."""
        with self.cond:
            while True:
                for _ in range(len(self.jobs)):
                    if not self.jobs:
                        break
                    job = self.jobs[0]
                    if job.done_pulling:
                        self.jobs.popleft()
                        continue
                    # Round robin: maps share the workers.
                    self.jobs.rotate(-1)
                    if job.window_open():
                        return job
                if not block or self.shutdown:
                    return None
                self.idle += 1
                try:
                    self.cond.wait()
                finally:
                    self.idle -= 1

    def notify(self, all):
        with self.cond:
            if not self.idle:
                return
            if all:
                self.cond.notify_all()
            else:
                self.cond.notify()

    def help(self):
        """Run one batch of any map, from a worker waiting on a nested map."""
        job = self.find_job(block=False)
        return job is not None and job.run_batch([0.0], help=True)


class _Map:
    def __init__(self, core, func, iterables, buffersize, ordered):
        self.core = core
        self.func = func
        self.iters = [iter(it) for it in iterables]
        self.buffersize = buffersize
        self.ordered = ordered
        self.context = contextvars.copy_context()
        self.iter_context = self.context.copy()

        self.iter_lock = threading.Lock()
        self.next_index = 0       # items claimed; written under iter_lock
        self.done_pulling = False # written under iter_lock
        self.pull_error = None
        self.detached = False     # the result iterator is gone

        self.cond = threading.Condition(threading.Lock())
        self.results = {} if ordered else collections.deque()   # cond
        self.consumed = 0         # results yielded; cond
        self.waiters = 0          # cond

    def window_open(self):
        if not self.buffersize or self.detached:
            return True
        return self.next_index - self.consumed < self.buffersize

    def available(self):
        return not self.done_pulling and self.window_open()

    def _finish(self, error):
        """Mark the inputs finished.  Called with iter_lock held."""
        if error is not None and self.pull_error is None and not self.detached:
            self.pull_error = error
        self.done_pulling = True
        with self.cond:
            self.cond.notify_all()

    def _pull(self, items, limit):
        """Append up to limit [index, args] pairs; return True at the end."""
        iters = self.iters
        index = self.next_index
        try:
            while len(items) < limit:
                args = tuple([next(it) for it in iters])
                items.append([index, args])
                index += 1
                self.next_index = index
        except StopIteration:
            return True
        return False

    def claim(self, limit):
        with self.iter_lock:
            if self.done_pulling:
                return []
            if self.core.shutdown and not self.detached:
                self._finish(RuntimeError("the pool was shut down"))
                return []
            if self.buffersize and not self.detached:
                room = self.buffersize - (self.next_index - self.consumed)
                if room <= 0:
                    return []
                limit = min(limit, room)
            items = []
            try:
                finished = self.iter_context.run(self._pull, items, limit)
            except BaseException as exc:
                self._finish(exc)
            else:
                if finished:
                    self._finish(None)
            return items

    def run_batch(self, estimate, help=False):
        """Claim and run one batch.  estimate is a one-item list holding the
        worker's running estimate of the task time in ns."""
        if estimate[0] > 0:
            limit = int(min(_MAX_BATCH, max(1, _BATCH_TARGET_NS / estimate[0])))
        else:
            limit = 1
        if self.buffersize:
            limit = min(limit, max(1, self.buffersize // self.core.n_threads))
        items = self.claim(limit)
        if not items:
            return False
        # Wake idle workers one at a time while work is left, rather than
        # all at once, so they do not all contend for the locks.  The
        # unlocked read of idle is only a hint: a missed wakeup here costs
        # parallelism, not progress, since this worker continues.
        if self.core.idle and self.available():
            self.core.notify(all=False)
        if help:
            # The cached copy may be the context of the task we are inside.
            ctx = self.context.copy()
        else:
            if getattr(_worker, "ctx_job", None) is not self:
                _worker.ctx = self.context.copy()
                _worker.ctx_job = self
            ctx = _worker.ctx
        start = time.perf_counter_ns()
        ctx.run(self._run_items, items)
        per = (time.perf_counter_ns() - start) / len(items)
        estimate[0] = 0.75 * estimate[0] + 0.25 * per if estimate[0] else per
        return True

    def _run_items(self, items):
        """Run a batch and publish its results under one lock acquisition.
        A batch is about 100 us of work, so results wait at most that long."""
        func = self.func
        for item in items:
            args = item[1]
            item[1] = None        # free the arguments as each task finishes
            try:
                item[1] = (True, func(*args))
            except BaseException as exc:
                item[1] = (False, exc)
            args = None
        with self.cond:
            if self.detached:
                return
            if self.ordered:
                for index, result in items:
                    self.results[index] = result
            else:
                self.results.extend([result for _, result in items])
            if self.waiters:
                self.cond.notify_all()


def _worker_main(core):
    _worker.core = core
    job = None
    estimate = [0.0]
    while True:
        if job is None or not job.available():
            job = core.find_job(block=True)
            estimate = [0.0]
            if job is None:
                break
        if not job.run_batch(estimate):
            job = None
    _worker.ctx = _worker.ctx_job = None


class PoolMapResults:
    """Iterator over the results of Pool.map."""

    __slots__ = ("_pool", "_job", "_next")

    def __init__(self, pool, job):
        self._pool = pool         # keeps the workers running
        self._job = job
        self._next = 0

    def __iter__(self):
        return self

    def _ready(self):
        job = self._job
        if job.ordered:
            if self._next in job.results:
                return True
            return job.done_pulling and self._next >= job.next_index
        if job.results:
            return True
        return job.done_pulling and job.consumed >= job.next_index

    def _wait(self):
        """Wait for a result or the end.  Called with job.cond held."""
        job = self._job
        if getattr(_worker, "core", None) is job.core:
            # A worker waiting on a map of its own pool runs pool work.
            job.cond.release()
            try:
                helped = job.core.help()
            finally:
                job.cond.acquire()
            if not helped and not self._ready():
                job.waiters += 1
                try:
                    job.cond.wait(_HELP_POLL)
                finally:
                    job.waiters -= 1
            return
        job.waiters += 1
        try:
            job.cond.wait()
        finally:
            job.waiters -= 1

    def __next__(self):
        job = self._job
        with job.cond:
            while not self._ready():
                self._wait()
            if job.ordered:
                if self._next not in job.results:
                    return self._end()
                ok, value = job.results.pop(self._next)
                self._next += 1
            else:
                if not job.results:
                    return self._end()
                ok, value = job.results.popleft()
            full = bool(job.buffersize) and not job.window_open()
            job.consumed += 1
        if full:
            job.core.notify(all=False)
        if not ok:
            raise value
        return value

    def _end(self):
        job = self._job
        error, job.pull_error = job.pull_error, None
        if error is not None:
            raise error
        raise StopIteration

    def _is_full(self):
        """For tests: is the buffersize window full?"""
        return not self._job.window_open()

    def __del__(self):
        job = getattr(self, "_job", None)
        if job is None:
            return
        # Unconsumed items still run, as with concurrent.futures, but their
        # results are dropped and buffersize no longer limits them.
        with job.cond:
            job.detached = True
            job.results.clear()
        if not job.done_pulling:
            job.core.notify(all=True)


PoolMapResults.__module__ = "grail"


class Pool:
    """Pool(n_threads)

    A pool of worker threads with low per-task overhead.

    Based on the design of Quansight's spinningjenny.
    """

    __slots__ = ("_core", "__weakref__")

    def __init__(self, n_threads):
        if not isinstance(n_threads, int):
            raise TypeError(f"n_threads must be an int, not {type(n_threads).__name__}")
        if n_threads < 1:
            raise ValueError("n_threads must be greater than 0")
        core = self._core = _Core(n_threads)
        for _ in range(n_threads):
            t = threading.Thread(target=_worker_main, args=(core,),
                                 name="grail.Pool worker", daemon=True)
            t.start()
            core.threads.append(t)

    @property
    def n_threads(self):
        """The number of worker threads."""
        return self._core.n_threads

    def map(self, func, /, *iterables, buffersize=None, return_in_order=True):
        """Return an iterator of func applied to the items of the iterables.

        Tasks run in a copy of the contextvars context current at the call.
        buffersize bounds the number of claimed but unyielded items.  With
        return_in_order=False, results are yielded as they complete.  An
        exception raised by func is raised when its result is reached.
        """
        if not iterables:
            raise TypeError("map() requires a function and at least one iterable")
        if buffersize is not None:
            buffersize = buffersize.__index__()
            if buffersize < 0:
                raise OverflowError("buffersize must not be negative")
            if buffersize == 0:
                raise ValueError("buffersize must be >= 1")
        ordered = bool(return_in_order)
        core = self._core
        if os.getpid() != core.pid:
            raise RuntimeError("the pool cannot be used after fork()")
        job = _Map(core, func, iterables, buffersize, ordered)
        with core.cond:
            if core.shutdown:
                raise RuntimeError("cannot map after the pool was shut down")
            core.jobs.append(job)
            core.cond.notify()
        return PoolMapResults(self, job)

    def shutdown(self, wait=True):
        """Stop the workers.  Maps with inputs left raise RuntimeError, except
        maps whose result iterator was dropped, which run to completion."""
        core = self._core
        failed = []
        with core.cond:
            if not core.shutdown:
                core.shutdown = True
                kept = [job for job in core.jobs if job.detached]
                failed = [job for job in core.jobs if not job.detached]
                core.jobs = collections.deque(kept)
                core.cond.notify_all()
        for job in failed:
            with job.iter_lock:
                if not job.done_pulling:
                    job._finish(RuntimeError("the pool was shut down"))
        if (wait and getattr(_worker, "core", None) is not core
                and os.getpid() == core.pid):
            for t in core.threads:
                t.join()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.shutdown()
        return False

    def __del__(self):
        core = getattr(self, "_core", None)
        if core is not None:
            self.shutdown(wait=False)

    def __reduce__(self):
        raise TypeError("cannot pickle 'grail.Pool' object")


Pool.__module__ = "grail"
