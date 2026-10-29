# CPython prototype: status and findings

Oct 27, 2026 · @Neil Schemenauer

The prototype tests whether Grail's object model is practical in CPython's free-threaded interpreter. It is based on the CPython 3.15 branch and is published as the `grail` branch of [github.com/nascheme/cpython](https://github.com/nascheme/cpython/tree/grail). The core semantics work; performance is promising for sharing, copying and dispatch-heavy code, but immutable numeric updates remain expensive. It also builds with the GIL enabled and runs under CPython's experimental copy-and-patch JIT, so we can begin to measure what Grail's guarantees buy a JIT.

## What is implemented

| Feature | Current support |
| --- | --- |
| Native records | Closed immutable types, fixed boxed fields, native construction, `copy.replace` and class patterns that read fields by position, and generated equality/hash/repr computed without allocation |
| Value conformance | Construction records whether the actual contents satisfy supported value annotations: scalars, nested records, unions and typed tuples. Mismatches are allowed but do not qualify for value optimizations. |
| Generic functions | Native exact dispatch and cached MRO/ABC dispatch, with registration and overrides. Registry reads are lock-free. |
| Operators | Exact ordered-pair registration for `+`, `-`, `*`, `/` and augmented forms, consistent across expressions, `operator` and C arithmetic APIs. A registered result is final; unregistered pairs keep normal Python behavior. A pair of two builtin types cannot be registered. |
| Copy and serialization | Copy, deepcopy and pickle, including ordinary aliases and cycles. Proven values can be reused rather than copied. |
| Persistent collections | Native `PMap`, `PSet` and `PVector`, sharing unchanged structure between versions; flat unboxed `FloatVector` and `IntVector` with a read-only buffer; thread-confined builders for all three that edit exclusively owned nodes in place; bottom-up map/set construction; `PMap.update_with` |
| Thread pool | Native `grail.Pool` and shared `grail.pool()` with low per-task overhead, scoped spawning with work stealing, and the `pmap` and `parallel_reduce` helpers |
| Drafts (experimental) | `grail.mutate` drafts of records, `PVector` and `PMap`: thread-confined, with nested child drafts and `finish` building each changed record once. Specialized field loads and stores, `with` entry and exit and integer subscripts. |
| Shared state | Native `Atom` with lock-free snapshots, reset and compare-and-set, retrying swap and integer add with backoff |
| Interpreter fast paths | Specialized record field loads, positional construction and keyword `grail.replace`/`copy.replace` calls, direct entry into registered operator and generic-function implementations without an extra dispatch call, and specialized `PVector` indexing and `set` calls |
| JIT (GIL build) | Record construction, registered operators and generic calls are traced in tier 2 instead of ending traces. Dispatch with known argument types is folded into a trace constant; registration and override invalidate dependent executors. The optimizer uses record guarantees: field loads are typed from value annotations and, since fields are never replaced, need no reference counting while the record is held; a record or tuple shown to conform stays conforming for the rest of the trace, so construction checks only the arguments it cannot prove. Element-wise arithmetic on all-float records and float expressions over their fields run as single operations on unboxed floats. |
| Experimental | `deriving` element-wise `+ - * /` for all-float records as C kernels; not used by the benchmarks (see below) |

The tables below summarize the current matched measurements and their limitations. See the [API reference](../reference/records.md) for record contracts and the prototype's [`GRAIL.md`](https://github.com/nascheme/cpython/blob/grail/GRAIL.md) for its source map, build and test commands.

Records are shallowly immutable; an ordinary field may still contain a mutable object. **Conforming value records provide the stronger guarantee** used to skip deepcopy and reduce reference-count traffic when shared through closures. Persistent collections do not yet propagate typed value-conformance evidence.

## Rough performance

These figures come from a matched PGO/LTO run on a **Ryzen 5 2600X with 6 cores** (boost off; parallel runs use one thread per core). The reference is the `grail-base` branch: the same CPython 3.15 base and the same Grail-independent interpreter and JIT work, without Grail. It is compared with the `grail` branch at `13f53d68531`. Both are built with clang 21 and the tail-call interpreter. Each variant ran in a fresh process with fixed work, two rounds. They are wall-time indications, not precise speedup claims; changes under about 5% are noise.

These benchmarks are idiomatic Grail versions of existing upstream benchmarks. The first two columns compare the **idiomatic Grail version against the original program on `grail-base`, which runs on one thread** (the originals are serial). They include representation, algorithm and threading changes, not just VM improvements. The scaling column compares the Grail version with itself: one thread versus 6 threads. The parallel versions use `grail.Pool`.

| Workload | Idiomatic Grail, 1 thread, vs original | Idiomatic Grail, 6 threads, vs original (1 thread) | Scaling: idiomatic Grail, 6 threads vs 1 thread |
| --- | --- | --- | --- |
| Build graph, aggregation, Mandelbrot, fannkuch | 9% slower to 23% faster | About 4.3-6.4x faster | About 4.7-5.3x |
| Float | About 1.4x faster | About 4.3x faster | About 3.2x |
| Ray tracing | About 2.2x faster | About 6.2x faster | About 2.8x |
| Spectral norm | About 1.2x slower | About 3.5x faster | About 4.2x |
| Deepcopy workloads | 7x to 20x faster, depending on representation | 9x to 51x faster | About 2.5-2.8x; the memo variant 1.3x |
| Chaos, hexiom, SQLGlot simplification and normalization | About 1.3-1.7x faster | Serial workloads | - |
| SQLGlot generation | About 2.1x slower | Serial workload | - |
| Richards, DeltaBlue, go | About 1.15-1.5x slower | DeltaBlue on 2 threads: about 1.06x slower | DeltaBlue: 1.4x (two tasks) |
| Nbody | About 2x slower | Serial workload | - |

Across the 12 workloads with parallel versions, 6 threads run 2.9x faster than one (geometric mean). Workloads with coarse independent tasks scale best; ray tracing and the deepcopy variants scale less.

Richards, DeltaBlue and go are slower than their originals because their idiomatic Grail versions are fully immutable: a scheduler state snapshot with a persistent task table, an immutable solver snapshot, and record positions and trees. They allocate where the originals mutate. Richards and DeltaBlue update that state through `grail.mutate` drafts. The controls below run the same source, and Grail beats them on all three.

Unchanged Python programs run at **about parity** on the Grail VM: 0.996x of `grail-base` (geometric mean over 31 workloads), from 8% slower (spectral norm and nqueens, consistent across rounds on the free-threaded build, at parity on the JIT build) to 5% faster. These are bounded benchmark workloads, not claims about full applications.

### Compared with today's Python

To separate the Grail runtime from the idiomatic versions' algorithm changes, each Grail version also runs unchanged on stock Python features: frozen or mutable slotted dataclasses, ordinary operator methods, and `functools.singledispatch` or a dictionary lookup. The stock versions run on `grail-base`, at one thread.

| Workload | Grail vs frozen dataclass | Grail vs mutable slotted class |
| --- | --- | --- |
| DeltaBlue | About 14.5x faster | About 13x faster |
| MDP | About 10.7x faster | About 10.4x faster |
| Richards | About 12.6x faster | About 11x faster |
| Nbody, float | 2.6-2.9x faster | 1.45-1.55x faster |
| Ray tracing | About 2.6x faster | About 1.35x faster |
| Go | About 1.6x faster | About 1.5x faster |
| Deepcopy workloads | About 30-34x faster | About 27-31x faster |
| Chaos, hexiom, comprehensions, SQLGlot | 1.04-1.33x faster | 1.02-1.09x faster |
| Fannkuch, Mandelbrot, aggregation, build graph | About parity | About parity |
| Spectral norm | About 1.1x slower | About 1.1x slower |

Native records clearly beat the stock immutable equivalent, and on record-heavy code they also beat a mutable class that gives up immutability. Richards' controls update their records with `copy.replace`, which on a dataclass goes through a Python `__replace__`. Where the Grail version is slower than the original but faster than both controls, the cost is in the immutable algorithm, not in Grail's records or dispatch.

### Under the JIT

These runs use the **GIL-enabled build** with the JIT on, one thread, from the same matched PGO/LTO setup and commits as above; the reference is the original program on `grail-base` with its JIT on. They are not comparable with the free-threaded figures. CPython's JIT is not yet supported on the free-threaded build.

| Workload | Idiomatic Grail vs original | Idiomatic Grail vs mutable slotted class |
| --- | --- | --- |
| Ray tracing | About 3.7x faster | About 2.1x faster |
| Float, chaos | About 1.8x and 1.5x faster | 1.9x and 1.2x faster |
| Deepcopy workloads | About 6.6-19.5x faster | About 28-32x faster |
| Spectral norm, fannkuch | 3-5% slower | About parity |
| Nbody | About 1.35x slower | About 3x faster |
| Go, DeltaBlue | 1.2x and 1.7x slower | 1.6x and 14.8x faster |
| Richards | About 2.75x slower | About 15.7x faster |

Unchanged programs run within about 3% of `grail-base` under the JIT. Against this tail-call interpreter the JIT gains 1.1-1.5x for the originals, and 3x for Richards. The idiomatic Grail versions gain more than their originals where the trace optimizer uses record guarantees: nbody 2.6x, ray tracing 1.9x, float 1.8x (fused float arithmetic and typed field loads). The others gain 1.05-1.6x, and the Grail version of Richards 1.6x where its original gains 3x. Once Grail operations were traceable, 90-98% of executed work in the record- and dispatch-heavy workloads ran in tier 2, with no traces abandoned at Grail operations.

## What we have learned

- **The semantics fit CPython.** Records need neither hidden instance types nor a custom metaclass. Immutable reconstruction handles copying and cyclic graphs without exposing half-built records.
- **Immutability helps most when it removes work.** Value proofs allow copy reuse and cheaper shared references; persistent structures avoid whole-container copies.
- **Dispatch must be cheap to share.** Immutable registry snapshots let threads dispatch without a shared reader lock.
- **The idioms the design teaches must be fast.** `match` on records spent most of its time in `isinstance` and `getattr`; reading fields by position cut DeltaBlue's time by 47% and Richards' by 8%. A native `copy.replace` cut Richards' time by another 29%. When Richards moved to a persistent task table updated with `grail.replace`, it first ran twice as slow as before, because `grail.replace` was still a Python function taking `**changes`; making it native, and specializing its calls and `PVector` indexing and updates, made that version 2.2x faster under the JIT and slightly faster than the tuple table it replaced.
- **Updates written as assignments can come close to explicit replacement.** `grail.mutate` drafts let state transitions read like the mutable original (`s.tasks[t].waiting = True`) and build each changed record once. As a Python library they cost 10x more than native drafts. Natively, after specializing draft field access, `with` entry and exit and draft subscripts, they cost about 3% where an operation makes many edits (DeltaBlue) and about 20% where it makes one small edit (Richards): the remainder is creating and freeing a draft per edited level.
- **Generality in the operator rules costs every program.** Allowing a registered implementation to decline forced an extra frame per operator; making its result final removed it and cut nbody's time by 11%. Allowing builtin pairs such as `(int, int)` forced a guard in front of CPython's hottest arithmetic, which slowed unchanged numeric code by up to 14%. Forbidding builtin pairs removed the guard.
- **Faster field loads alone are not a major win.** CPython already optimizes slotted access; the record-specific load gives a small improvement, not a clear application-wide gain.
- **Tracing Grail operations is necessary but not sufficient for the JIT.** Before tier-2 support, every record construction, registered operator and generic call ended a trace, and the Grail VM ran even unchanged Richards 40% slower under the JIT. Making these operations traceable fixed that; the gains came when the optimizer started using what records guarantee. Typed field loads let operator dispatch on field values fold, which helps ray tracing most. Immutable fields make borrowed field loads safe. Closed all-float records let element-wise operators and float expressions run unboxed, which more than halved nbody's time under the JIT.
- **Immutable snapshots cost allocation, and traces lose what they know.** Richards' Grail version rebuilds one or two task records, the scheduler state and the task table's short tail on every step. Most of its remaining time is allocating and freeing them. Facts the optimizer proves (a record conforms, a field has a type) last only to the end of a trace, and Richards' traces split at its `match` on the requested action, so the side trace that rebuilds the state starts knowing nothing about it. In the earlier tuple-table version, skipping every conformance check would have gained about 12%; the in-trace facts recovered 2%.
- **Boxed immutable arithmetic is the main remaining weakness without the JIT.** Nbody builds about five temporary records per body pair where the original updates floats in place, so it runs about 2x slower than the original on the free-threaded build. Generated C kernels for all-float records (`deriving`) cut nbody's time by 41%, but they are a special-purpose path the original and the controls do not have, so the benchmarks do not use them. The JIT now gets most of this generically from the unchanged source: nbody's Grail version is about 1.35x slower than the original there, from 2.7x.

## What comes next

Borrowed field loads (no reference-count traffic when a field is read from a record that outlives the use) are the main remaining general optimization without a JIT; the JIT already does them. Under the JIT, the snapshot-per-step workloads (Richards, DeltaBlue, go) need records that are never materialized: scalar replacement of records built and consumed within a trace, then keeping state virtual across loop iterations, and carrying proven facts into side traces. Unboxing records in general is a later, larger question. Then come the semantics still needed to finish the design evaluation: comparison operators and an audit of where object identity matters. Unboxed fields and automatic value substitution are not implemented. The cost of delayed reclamation for shared values also needs measurement. Value records now cache their default hash.

The prototype has targeted debug-build and concurrency coverage, but is not production-ready. The current evidence supports Grail's sharing and value-reuse design and shows record-heavy code beating today's Python equivalents; it does **not** yet establish a general single-thread advantage over hand-written mutable code. The first JIT results show that Grail's guarantees can make JIT-compiled code faster (nbody, ray tracing, float), but not yet for code that rebuilds immutable state on every step.
