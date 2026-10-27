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

Scopes (Pool.scope(), Scope.spawn()) run individual tasks with work
stealing: a task spawned on a worker goes onto that worker's deque, one
spawned from any other thread onto a shared injector deque.  A worker runs
its own newest task first, then the injector's oldest, then steals another
worker's oldest.  collections.deque's append, pop and popleft are atomic,
so the deques need no locks.

Unlike the native pool, results are published under a lock, once per batch,
so the per-task overhead is that of Python-level lock operations, not of C
atomics.
"""

import collections
import contextvars
import os
import sys as _sys
import threading
import time

try:
    _BaseExceptionGroup = BaseExceptionGroup        # 3.11+
except NameError:
    _BaseExceptionGroup = None

__all__ = ["Pool"]

# A batch should take about this long; the batch size follows from the
# measured time per task.
_BATCH_TARGET_NS = 100_000
_MAX_BATCH = 16
# A worker helping while it waits on a nested map polls this often.
_HELP_POLL = 0.001

_worker = threading.local()


def _make_defer_refcount():
    """Return _defer_refcount(func), which enables deferred reference
    counting on a Python function shared by pool workers.

    Called from many workers, a function's reference count would be written
    by each of them on every call.  With deferred reference counting it is
    not, but the function, and what it captures, is freed by the GC rather
    than when its last reference goes.  Module-level functions are deferred
    already.  Only free-threaded 3.14+ has the C API, reached here through
    ctypes; elsewhere this does nothing.
    """
    def nothing(func):
        return False
    if getattr(_sys, "_is_gil_enabled", lambda: True)():
        return nothing
    try:
        import ctypes
        enable = ctypes.pythonapi.PyUnstable_Object_EnableDeferredRefcount
    except (ImportError, AttributeError):
        return nothing
    enable.argtypes = [ctypes.py_object]
    enable.restype = ctypes.c_int
    import types
    import weakref
    seen = weakref.WeakSet()     # a ctypes call costs about 1 us

    def _defer_refcount(func):
        if type(func) is not types.FunctionType or func in seen:
            return False
        seen.add(func)
        return bool(enable(func))
    return _defer_refcount


_defer_refcount = _make_defer_refcount()


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
        self.deques = [collections.deque() for _ in range(n_threads)]
        self.injector = collections.deque()

    def tasks_visible(self):
        return bool(self.injector) or any(self.deques)

    def find_task(self, index):
        """Return a task for worker index: its own newest, the injector's
        oldest, or the oldest of another worker; or None."""
        # Test before popping: raising IndexError costs more than the test.
        own = self.deques[index]
        if own:
            try:
                return own.pop()
            except IndexError:
                pass
        if self.injector:
            try:
                return self.injector.popleft()
            except IndexError:
                pass
        deques = self.deques
        n = self.n_threads
        for k in range(1, n):
            victim = deques[(index + k) % n]
            if victim:
                try:
                    return victim.popleft()
                except IndexError:
                    pass
        return None

    def push_task(self, task):
        index = getattr(_worker, "index", None)
        if index is not None and _worker.core is self:
            self.deques[index].append(task)
        else:
            self.injector.append(task)
        # An idle worker increments idle before rechecking for tasks, under
        # cond, so reading idle after the push cannot miss it.
        if self.idle:
            self.notify(all=False)

    def find_job(self, block):
        """Return a map with work available.  If block, wait for work;
        return None when tasks are visible, or at shutdown once no map has
        work left."""
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
                    if self.tasks_visible():
                        return None
                    self.cond.wait()
                finally:
                    self.idle -= 1
                if self.tasks_visible():
                    return None

    def notify(self, all):
        with self.cond:
            if not self.idle:
                return
            if all:
                self.cond.notify_all()
            else:
                self.cond.notify()

    def help(self):
        """Run one task, or one batch of any map, from a worker waiting on a
        nested map or scope."""
        task = self.find_task(_worker.index)
        if task is not None:
            _run_task(task)
            return True
        job = self.find_job(block=False)
        return job is not None and job.run_batch([0.0], help=True)


class _Map:
    def __init__(self, core, func, iterables, buffersize, ordered):
        self.core = core
        _defer_refcount(func)
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


def _run_task(task):
    scope, func, args, kwargs, context = task
    try:
        if not scope._cancelled:
            context.run(func, *args, **kwargs)
    except BaseException as exc:
        scope._record(exc)
    finally:
        scope._task_done()


def _worker_main(core, index):
    _worker.core = core
    _worker.index = index
    job = None
    estimate = [0.0]
    while True:
        task = core.find_task(index)
        if task is not None:
            _run_task(task)
            task = None
            continue
        if job is not None and job.available():
            if not job.run_batch(estimate):
                job = None
            continue
        job = core.find_job(block=True)
        estimate = [0.0]
        if job is None and core.shutdown and not core.tasks_visible():
            break
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
        if job is None or _sys.is_finalizing():
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
        for index in range(n_threads):
            t = threading.Thread(target=_worker_main, args=(core, index),
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

    def scope(self):
        """Return a Scope for spawning tasks; use it as a context manager."""
        core = self._core
        if os.getpid() != core.pid:
            raise RuntimeError("the pool cannot be used after fork()")
        if core.shutdown:
            raise RuntimeError("cannot open a scope after the pool was shut down")
        return Scope(self)

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
        # During finalization a daemon worker may be frozen holding a lock
        # that shutdown() needs.
        if core is not None and not _sys.is_finalizing():
            self.shutdown(wait=False)

    def __reduce__(self):
        raise TypeError("cannot pickle 'grail.Pool' object")


Pool.__module__ = "grail"


class Scope:
    """A scope for spawning tasks into a Pool; made by Pool.scope().

    Leaving the with block waits until every spawned task, including tasks
    spawned by tasks, has finished.  If tasks raised, the first error cancels
    the tasks not yet started and leaving raises a BaseExceptionGroup of all
    of them (preceded by the with block's own exception, if any).  Before
    3.11, which has no exception groups, the first error is raised.
    """

    __slots__ = ("_pool", "_core", "_cond", "_pending", "_cancelled",
                 "_closed", "_errors")

    def __init__(self, pool):
        if not isinstance(pool, Pool):
            raise TypeError("cannot create 'grail.Scope' instances")
        self._pool = pool         # keeps the workers running
        self._core = pool._core
        self._cond = threading.Condition(threading.Lock())
        self._pending = 0         # _cond
        self._cancelled = False
        self._closed = False
        self._errors = []         # _cond

    def spawn(self, func, /, *args, **kwargs):
        """Run func(*args, **kwargs) on the pool, in a copy of the current
        contextvars context.  Returns None; tasks report results through
        shared state such as atoms.  Ignored once a task of the scope has
        failed."""
        if self._closed:
            raise RuntimeError("the scope is closed")
        if self._core.shutdown:
            raise RuntimeError("cannot spawn after the pool was shut down")
        if self._cancelled:
            return None
        _defer_refcount(func)
        with self._cond:
            self._pending += 1
        self._core.push_task((self, func, args, kwargs, contextvars.copy_context()))
        return None

    def _record(self, exc):
        self._cancelled = True
        with self._cond:
            self._errors.append(exc)

    def _task_done(self):
        with self._cond:
            self._pending -= 1
            if not self._pending:
                self._cond.notify_all()

    def _wait(self):
        """Wait for every task.  Returns an exception (KeyboardInterrupt)
        raised while waiting, after cancelling and still waiting for the
        running tasks."""
        core = self._core
        helper = getattr(_worker, "core", None) is core
        interrupt = None
        while True:
            try:
                while self._pending:
                    if helper:
                        if core.help():
                            continue
                        timeout = _HELP_POLL
                    elif (os.getpid() != core.pid
                          or not any(t.is_alive() for t in core.threads)):
                        # No workers to run the tasks: run them here.
                        try:
                            task = core.injector.popleft()
                        except IndexError:
                            pass
                        else:
                            _run_task(task)
                            continue
                        timeout = _HELP_POLL
                    else:
                        timeout = None
                    with self._cond:
                        if self._pending:
                            self._cond.wait(timeout)
                return interrupt
            except BaseException as exc:
                if interrupt is None:
                    interrupt = exc
                self._cancelled = True

    def __enter__(self):
        if self._closed:
            raise RuntimeError("the scope is closed")
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self._cancelled = True
        interrupt = self._wait()
        self._closed = True
        errors, self._errors = self._errors, []
        if not errors:
            if interrupt is not None:
                raise interrupt
            return False
        body = interrupt if interrupt is not None else exc
        excs = ([body] if body is not None else []) + errors
        if _BaseExceptionGroup is None:
            raise excs[0]
        raise _BaseExceptionGroup("unhandled errors in a Scope", excs)

    def __reduce__(self):
        raise TypeError("cannot pickle 'grail.Scope' object")


Scope.__module__ = "grail"
