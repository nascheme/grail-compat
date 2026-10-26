<!-- Copied from the Project Grail docs/design/ by tools/copy_design_docs.py; do not edit here. -->

# Appendix: Concurrency toolkit

Oct 25, 2026 · @Neil Schemenauer

*Status: an appendix, outside Grail's core scope. API and implementation proposals motivated by the benchmark workloads, not shipped toolkit features. The native prototype currently provides only a boxed `Atom` with lock-free `value`, `reset` and identity-based `compare_and_set`, a retrying `swap`, and integer `add`; it has no `Promise`, seed helper or executor.*

**Relation to the core design.** Grail itself needs only two small pieces of this: `Atom.add`, which belongs in the core Atom, and naming `queue.SimpleQueue` as safe to share. Everything else here (`Promise`, random streams, the executor) is library code that needs no language or VM support. It shows what can be built on the Grail foundation of values and atoms, and would ship in the `grail` package or a separate one, not in a PEP.

Grail's rules for shared state are simple: share immutable values freely, and put changing state in atoms. The benchmark workloads also need common scaffolding: a pre-started pool, an ordered map, a fold-and-merge, independent random streams, and a way to keep k tasks in flight. These are API motivations, not evidence that the proposed toolkit is fast or correct. Evaluate implementations in `cpython-grail`, separating allocation, dispatch, task overhead and shared-refcount costs; parallel modes that change results need separate treatment. Two programs needed primitives that Python lacks: an atomic counter and a write-once cell. When the safe pattern takes more code than the unsafe one, people write the unsafe one. In the PyCon 2026 free-threading contest, the winning entry patched a function's vectorcall pointer with hand-assembled `lock xadd` through ctypes, because Python has no atomic integer.

This tab proposes a small toolkit in the `grail` package that makes the Grail pattern the shortest and fastest way to write parallel Python. A proposed demo implementation would use a PyO3 extension; no such toolkit extension ships in the current prototype. The executor starts from Quansight's [spinningjenny](https://github.com/Quansight/spinningjenny).

## What the benchmarks asked for

| Need | Benchmarks | What they had to write |
| --- | --- | --- |
| A pre-started, low-overhead pool | buildgraph, nqueens, float, spectral\_norm | Module-level pools warmed at import; at N=8, nqueens' pool start-up cost more than the whole search |
| Ordered parallel map | mandelbrot, float, spectral\_norm, nqueens, raytrace | `pool.map(f, [xs] * n, chunks, [k] * n)` plus a `threads <= 1` branch |
| Fold and merge | aggregate, float, spectral\_norm | Split into ranges, fold locally, merge partial results in order |
| Dynamic task graphs | buildgraph | A `SimpleQueue`, stop sentinels, and hand-made shutdown and error propagation |
| Keep k tasks in flight | michi | Upstream polls with timed waits; the fix needs `wait(FIRST_COMPLETED)` and futures bookkeeping |
| Atomic counter | buildgraph | `Atom.swap(operator.sub, 1)`: a Python-level CAS loop that allocates an int above 256; the contest winner used ctypes and assembly |
| Write-once result cell | buildgraph | An `Atom` per slot, which states the intent poorly |
| Independent random streams | michi, chaos, go | michi's upstream rebinds one module-global `Random` from every thread, which makes runs unrepeatable and is 3.3x slower on 28 threads |
| A channel that may be shared | buildgraph | A `SimpleQueue` in a record field, which the "no shared mutable objects" rule would flag |

## Atoms: `add`

```python
pending = grail.Atom(3)
if pending.add(-1) == 0:      # returns the new value
    schedule(target)
```

- Proposed `Atom.add(delta)` atomically adds an exact builtin `int` delta to an atom holding an exact builtin `int`, and returns the new value. Other values, including bools and int subclasses, raise `TypeError`; no user arithmetic or coercion executes. It is linearizable and has ordinary arbitrary-precision integer results, without consulting open `grail.ops` registrations. Allocation failure must leave that operation's update uncommitted. This is not generally equivalent to `swap(operator.add, delta)` when arithmetic registrations exist.
- **Unboxing stays invisible.** Values remain Python ints, of any size. An implementation may store a small int unboxed in a machine word and update it with one atomic add or CAS. It falls back to a boxed int on overflow, and reading `value` boxes the word. This follows the Records PEP's rule that an implementation may unbox `int` fields invisibly. It avoids a separate `AtomicInt64` type, and with it any int64 overflow semantics or second atom type.
- No `fetch_add` is needed: it is `add(delta) - delta`.
- **What to measure.** Compare native `Atom.add` with `Atom.swap` in buildgraph, separating scheduler-only overhead from build work and varying task granularity. A direct int operation may help fine-grained scheduling by avoiding callback/retry overhead; its benefit is not yet measured in `cpython-grail`. The API also removes the need for application-level ctypes atomics.
- **What it does not buy.** Atomics speed up coordination, not accumulation. Many threads adding to a few shared counters fight over a few cache lines. Use native aggregate to compare a private fold per thread plus a merge with shared-counter updates. Docs should teach fold-and-merge for accumulation and atomics for coordination, without assuming an unmeasured speedup.

## Promise: a write-once cell

```python
r = grail.Promise()
r.set(digest)          # once; a second set raises
r.get()                # blocks until set, then returns the value
r.done()               # non-blocking test
```

`set` happens-before every `get` that returns its value. A `Promise` holding a value record is itself safe to share. It replaces an atom per result slot (buildgraph), and futures whose only job is to carry a value. The name follows Clojure's `promise`; `IVar` and `OnceCell` are the alternatives.

## Channels

`queue.SimpleQueue` is a correct, fast channel today. Grail should name it as safe to share: a record field typed `SimpleQueue` should not trip the shared-mutable-object warning. A grail channel can come later if bounded or select-able channels are needed. The concurrency table in the overview gains a row for channels.

## Random streams

```python
seeds = grail.spawn_seeds(seed, n)      # n independent 64-bit seeds
rngs = [random.Random(s) for s in seeds]
```

`spawn_seeds` would derive reproducible, well-separated seed material from `(seed, task_index)`, following the motivation of NumPy's `SeedSequence.spawn`. Hashing alone does not prove statistical independence. Stable task indices give the same seed per task regardless of worker count; changing the task partition or number of streams can still change program results. The docs should show the rule: pass a generator down explicitly, one per task, and never share a module-global `Random` between threads. Executor tasks can take a seed by index. Parallel modes that change results (chaos, go) then stay repeatable.

## The executor

### Starting point: spinningjenny

spinningjenny is an experimental `ThreadPoolExecutor` replacement written in Rust with PyO3. It runs tasks on a rayon work-stealing pool and passes results through crossbeam channels. What it already does well:

- **Much lower overhead per task** than `concurrent.futures`, so tasks of tens of microseconds become worth parallelizing.
- `map(func, *iterables, buffersize=None, return_in_order=True)`. Returning results unordered is the "keep k tasks in flight" helper michi needed. `buffersize` bounds how far the input is iterated ahead of the results.
- The `contextvars` context is copied when a task is scheduled, not when the pool is created. This matches Grail's `bind` for `contextvars`.
- Workers stay detached from the interpreter while idle, so parked workers do not block the GC.

The historical study found no license file in that checkout. Recheck the current repository and resolve licensing with Quansight before copying code; this document is not a current licensing determination.

### Changes for Grail

1. **One shared pool per interpreter.** `grail.pool()` would return a lazily started pool sized to available CPU resources, so no program builds or warms its own. It must not share Python object references across interpreters implicitly. With the GIL enabled it runs tasks on the calling thread by default. Under the GIL, 28 threads ran 4x slower than serial in buildgraph and michi.
2. **Scoped spawning for dynamic task graphs.** `with grail.pool().scope() as s: s.spawn(f, *args)` is rayon's `scope`. A task may spawn more tasks into the same scope, and the `with` block exits when all of them have finished. buildgraph's scheduler becomes "build, decrement the dependents' atoms, spawn each one that reached zero", with no queue, no sentinels and no shutdown protocol.
3. **Nested parallelism must not deadlock or idle.** A `map` or `scope` called from inside a task must run pending tasks while it waits, as rayon's own join does, instead of blocking a worker on a channel. This needs testing in spinningjenny, whose result iterator blocks today.
4. **Errors.** The first exception in a scope cancels the tasks not yet started and is raised at scope exit, with the others attached as an `ExceptionGroup`. `map` keeps spinningjenny's per-item delivery, so the error appears when its result is reached.
5. **Helpers on top:**
   - `grail.pmap(f, items, chunk=None)`: ordered, chunked, and serial below a size threshold, so small inputs skip the pool.
   - `grail.parallel_reduce(fold, combine, items, init)`: folds chunks locally and combines in chunk order, so scheduling does not change results for a fixed partition and merge order. Worker-count independence needs a partition independent of worker count, or a genuinely associative `combine`; floating-point addition is not associative.
   - Both pass a per-task seed when asked, via `spawn_seeds`.
6. **Values in, values out.** The docs should state the contract: task arguments and results should be values (records, tuples, persistent collections), and changing state should live in atoms or promises. The executor does not enforce this, but linters can flag a mutable argument shared by many tasks.

### API sketch

```python
pool = grail.pool()

rows = grail.pmap(render_row, ys)                        # mandelbrot
totals = grail.parallel_reduce(fold, merge, ranges,      # aggregate
                               init=grail.PMap())
for result in pool.map(playout, positions,               # michi
                       return_in_order=False, buffersize=2 * pool.n_threads):
    update_tree(result)

with pool.scope() as s:                                  # buildgraph
    def build(i):
        results[i].set(targets[i].build(inputs(i)))
        for k in dependents[i]:
            if pending[k].add(-1) == 0:
                s.spawn(build, k)
    for i in sources:
        s.spawn(build, i)
```

## Proposed demo implementation

A possible Rust extension, built with PyO3 and maturin, would provide:

- `Atom`: possibly an unboxed integer representation with a boxed fallback. Any representation must preserve the existing identity-based `compare_and_set` contract: a snapshot returned by `.value` must remain a valid expected object while the state is unchanged. Boxing a fresh object on every read would break that contract. Define boxing, transitions, overflow, reference ownership and reclamation before choosing CAS storage.
- `Promise`: a once-cell with parking.
- `spawn_seeds`: SplitMix64- or SeedSequence-style hashing.
- The executor: spinningjenny with the changes above, once licensing allows.

Native boxed `Atom` already exists in `cpython-grail`, which is the current testing target. An unboxed Atom or Promise would be a separate experiment, not a reason to weaken identity-CAS or publication semantics. A future compatibility implementation would need its own tests and documented limits; it is not a performance baseline for this work.

## Out of scope

- **A shared concurrent map.** This covers caches, deduplication tables and anything else that threads must read while others update it. Proposed decision: such code is outside the Grail subset for now. Use a library such as cereggii's `AtomicDict`, or an `Atom[PMap]` when writes are rare. The native `PMap` is now HAMT-backed, so that alternative can be measured; it does not automatically make contended updates cheap. Local fold-and-merge covers aggregation.
- **A lock-owning cell** (`Locked[T]`) remains an open question in the overview. No benchmark needed one.
- **async.** The toolkit is for CPU parallelism on threads. Running one asyncio loop per thread, as the free-threading guide's scraper does, needs nothing from Grail.

## Open issues

- **Licensing and collaboration** with Quansight on spinningjenny: depend on it, fork it, or upstream the changes.
- **Names:** `Promise`, `pmap`, `parallel_reduce`, `spawn_seeds` and `scope`.
- **Pool size and the hybrid-core mix.** On an 8P+12E machine, scaling flattened past 16 threads in buildgraph and michi.
- **Whether `Atom.add` should also accept `float`** (no benchmark needed it). The current proposal places int-only `add` in the core Atom API; exact accepted types, allocation failures and representation transitions need validation.
- **Interaction with subinterpreters**, if a pool should ever span them.
