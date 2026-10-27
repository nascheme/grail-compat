---
sources:
  - docs/design/07-appendix-concurrency-toolkit.md
  - src/grail/_pool.py
  - src/grail/_parallel.py
  - tests/test_grail_pool.py
---

# Pool reference

`Pool` is a pool of worker threads with a parallel `map` and scoped task
spawning. `pmap` and `parallel_reduce` are helpers built on `Pool.map`.
Threads only run Python code in parallel on a free-threaded interpreter; with
the GIL they help only tasks that release it.

    >>> import grail
    >>> with grail.Pool(2) as pool:
    ...     list(pool.map(abs, [-1, -2, 3]))
    [1, 2, 3]
    >>> grail.pmap(lambda x: x * x, range(5))
    [0, 1, 4, 9, 16]

## Pool

`Pool(n_threads)` starts `n_threads` workers at once; `n_threads` must be an
int of at least 1. `grail.pool()` returns a shared pool, started on first
use with one worker per CPU, shut down at exit, and replaced in a child made
by `fork()`.

| Member | Contract |
| --- | --- |
| `n_threads` | Number of workers |
| `map(func, *iterables, buffersize=None, return_in_order=True)` | Iterator of `func(*args)` per tuple of items, stopping at the shortest iterable |
| `scope()` | A new `Scope`, used as a context manager |
| `shutdown(wait=True)` | Stop the workers; also done on leaving a `with` block |

`map` returns at once. Workers iterate the inputs lazily and run each call in
a copy of the `contextvars` context current when `map` was called. Results
come in input order, or as they finish with `return_in_order=False`. An
exception raised by `func` is raised when its result is reached; later
results can still be read. `buffersize` bounds the items taken from the
inputs but not yet yielded, as in `concurrent.futures`. Dropping the result
iterator does not cancel the remaining items. Several threads may read one
result iterator; each value is yielded once.

After `shutdown`, `map`, `scope()` and `spawn` raise `RuntimeError`. A map
whose result iterator is still live and has inputs left raises
`RuntimeError` after the results already taken. A pool cannot be used in a
child made by `fork()`.

    >>> with grail.Pool(2) as pool:
    ...     sorted(pool.map(len, ["a", "bb", "ccc"], return_in_order=False))
    [1, 2, 3]

## Scope

`Scope.spawn(func, /, *args, **kwargs)` runs `func(*args, **kwargs)` on a
pool worker in a copy of the context current at the spawn, and returns
`None`; tasks report results through shared state such as an `Atom`. Any
thread may spawn into an open scope, including the scope's own tasks.

Leaving the `with` block waits for every task, including tasks spawned by
tasks. A worker waiting on a nested scope or map runs pool work meanwhile,
so nesting cannot deadlock the pool, even with one worker. The first task
error cancels the scope: tasks not yet started are skipped and later spawns
are ignored. Leaving then raises a `BaseExceptionGroup` (an `ExceptionGroup`
when all are `Exception`s) of every task error, preceded by the `with`
block's own exception if it raised one. A scope cannot be reopened.

    >>> total = grail.Atom(0)
    >>> def count(n):
    ...     total.add(n)
    ...     if n > 1:
    ...         s.spawn(count, n - 1)
    >>> with grail.Pool(2) as pool, pool.scope() as s:
    ...     s.spawn(count, 4)
    >>> total.value
    10

## pmap and parallel_reduce

`pmap(func, iterable, /, *, chunk=None, pool=None)` returns
`[func(x) for x in iterable]`, run in chunks of `chunk` items, in order.
Without `chunk` it times a prefix of the items in the calling thread and,
if the rest would take under about 0.1 ms, runs them there too; otherwise
chunks get about 50 us of work and number at least four per worker. The
first exception in input order propagates. Pass `chunk=1` for a few large
items.

`parallel_reduce(fold, combine, iterable, init, *, chunk=None, pool=None)`
folds each chunk from `init` (`acc = fold(acc, item)`), then combines the
chunk results as a balanced tree of adjacent pairs, earlier result first.
The default partition is 64 chunks, so for a given input and `chunk` the
result does not depend on the pool size or on scheduling, even for float
addition. `init` is shared by every chunk and must not be mutated. An empty
input returns `init`. Each phase runs its first call in the calling thread
to time it, so very large chunks start one after another.

    >>> import operator
    >>> grail.parallel_reduce(lambda acc, x: acc + x, operator.add, range(101), 0)
    5050

Both default to `grail.pool()`. They, `map` and `spawn` enable deferred
reference counting on the functions they are given where the interpreter
supports it, so a function called from many workers is freed by the garbage
collector rather than when its last reference goes.

Guides: [concurrency](../guide/concurrency.md).
