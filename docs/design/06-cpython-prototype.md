# CPython prototype: status and findings

Oct 26, 2026 · @Neil Schemenauer

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
| Shared state | Native `Atom` with lock-free snapshots, reset and compare-and-set, retrying swap and integer add with backoff |
| Interpreter fast paths | Specialized record field loads, positional construction and keyword `grail.replace`/`copy.replace` calls, direct entry into registered operator and generic-function implementations without an extra dispatch call, and specialized `PVector` indexing and `set` calls |
| JIT (GIL build) | Record construction, registered operators and generic calls are traced in tier 2 instead of ending traces. Dispatch with known argument types is folded into a trace constant; registration and override invalidate dependent executors. The optimizer uses record guarantees: field loads are typed from value annotations and, since fields are never replaced, need no reference counting while the record is held; a record or tuple shown to conform stays conforming for the rest of the trace, so construction checks only the arguments it cannot prove. Element-wise arithmetic on all-float records and float expressions over their fields run as single operations on unboxed floats. |
| Experimental | `deriving` element-wise `+ - * /` for all-float records as C kernels; not used by the benchmarks (see below) |

The tables below summarize the current matched measurements and their limitations. See the [API reference](../reference/records.md) for record contracts and the prototype's [`GRAIL.md`](https://github.com/nascheme/cpython/blob/grail/GRAIL.md) for its source map, build and test commands.

Records are shallowly immutable; an ordinary field may still contain a mutable object. **Conforming value records provide the stronger guarantee** used to skip deepcopy and reduce reference-count traffic when shared through closures. Persistent collections do not yet propagate typed value-conformance evidence.

## Rough performance

These figures come from a matched PGO/LTO run of cpython-grail `8307f272393` against upstream, both built with clang 21 and the tail-call interpreter, on an **i7-14700K using 8 performance cores** (one thread per core). Each variant ran in a fresh process with fixed work, two rounds. They are wall-time indications, not precise speedup claims; changes under about 5% are noise, and in earlier runs the 8-thread figures varied by up to 40% between rounds for ray tracing and float.

These benchmarks are idiomatic Grail versions of existing upstream benchmarks. The first two columns compare the **idiomatic Grail version against the upstream original program, which runs on one thread** (the originals are serial). They include representation, algorithm and threading changes, not just VM improvements. The scaling column compares the Grail version with itself: one thread versus 8 threads. The parallel versions use `grail.Pool`.

| Workload | Idiomatic Grail, 1 thread, vs upstream original | Idiomatic Grail, 8 threads, vs upstream original (1 thread) | Scaling: idiomatic Grail, 8 threads vs 1 thread |
| --- | --- | --- | --- |
| Build graph, aggregation, Mandelbrot, fannkuch, float | 6% slower to 37% faster | About 6.2-8.8x faster | About 6.4-7.6x |
| Ray tracing | About 2.1x faster | About 6x faster | About 2.9x |
| Spectral norm | About 6% slower | About 4x faster | About 4.3x |
| Deepcopy workloads | 1.7x slower to 15x faster, depending on representation | 3.3x to 67x faster | About 4.3-5.7x |
| Chaos, hexiom, SQLGlot simplification and normalization | About 1.3-1.8x faster | Serial workloads | - |
| SQLGlot generation | About 2x slower | Serial workload | - |
| Richards, DeltaBlue, go | About 1.2-1.8x slower | DeltaBlue on 2 threads: about 1.1x slower | DeltaBlue: 1.6x (two tasks) |
| Nbody | About 2.1x slower | Serial workload | - |

Richards, DeltaBlue and go are slower than their originals because their idiomatic Grail versions are fully immutable: a scheduler state snapshot with a persistent task table, an immutable solver snapshot, and record positions and trees. They allocate where the originals mutate. The controls below run the same source, and Grail beats them on all three.

Unchanged Python programs run at **about parity** on the Grail VM: from 8% slower (aggregation, a code-layout effect) to 2% faster, with a median near 1% slower. These are bounded benchmark workloads, not claims about full applications.

### Compared with today's Python

To separate the Grail runtime from the idiomatic versions' algorithm changes, each Grail version also runs unchanged on stock Python features: frozen or mutable slotted dataclasses, ordinary operator methods, and `functools.singledispatch` or a dictionary lookup. The stock versions run on upstream CPython, at one thread.

| Workload | Grail vs frozen dataclass | Grail vs mutable slotted class |
| --- | --- | --- |
| DeltaBlue | About 7.7x faster | About 6.1x faster |
| MDP | About 8.5x faster | About 8.5x faster |
| Richards | About 7.4x faster | About 5.6x faster |
| Nbody, float | 2.6-2.75x faster | 1.4x faster |
| Ray tracing | About 2.5x faster | About 1.3x faster |
| Go | About 1.7x faster | About 1.6x faster |
| Deepcopy workloads | About 23-26x faster | About 22-26x faster |
| Chaos, SQLGlot | 1.0-1.35x faster | 3% slower to 6% faster |
| Fannkuch, Mandelbrot, aggregation, build graph | About parity | About parity |

Native records clearly beat the stock immutable equivalent, and on record-heavy code they also beat a mutable class that gives up immutability. Richards' controls now update their records with `copy.replace`, which on a dataclass goes through a Python `__replace__`; they run about 2.2x slower than the earlier controls, which rebuilt most records with constructors. Where the Grail version is slower than upstream but faster than both controls, the cost is in the immutable algorithm, not in Grail's records or dispatch.

### Under the JIT

These runs use the **GIL-enabled build** with the JIT on, one thread, from the same matched PGO/LTO setup and commit as above. They are not comparable with the free-threaded figures. CPython's JIT is not yet supported on the free-threaded build.

| Workload | Idiomatic Grail vs upstream original | Idiomatic Grail vs mutable slotted class |
| --- | --- | --- |
| Ray tracing | About 3.3x faster | About 1.8x faster |
| Chaos, float | About 1.4x faster | 1.2x and 1.75x faster |
| Deepcopy | About 12-22x faster | About 27-35x faster |
| Nbody | About 1.25x slower | About 3.5x faster |
| Go, DeltaBlue | 1.3-1.7x slower | 1.5x and 7.1x faster |
| Richards | About 2.3x slower | About 7x faster |

Unchanged programs run within about 5% of upstream under the JIT. Against this tail-call interpreter the JIT gains little for the originals: 0.95-1.25x, and 1.9x for Richards. The idiomatic Grail versions now gain more than their originals where the trace optimizer uses record guarantees: nbody 2.4x, ray tracing 1.5x, float 1.3x (fused float arithmetic and typed field loads). The others gain 0.9-1.4x, and the Grail version of Richards 1.17x where its original gains 1.9x. Once Grail operations were traceable, 90-98% of executed work in the record- and dispatch-heavy workloads ran in tier 2, with no traces abandoned at Grail operations.

## What we have learned

- **The semantics fit CPython.** Records need neither hidden instance types nor a custom metaclass. Immutable reconstruction handles copying and cyclic graphs without exposing half-built records.
- **Immutability helps most when it removes work.** Value proofs allow copy reuse and cheaper shared references; persistent structures avoid whole-container copies.
- **Dispatch must be cheap to share.** Immutable registry snapshots let threads dispatch without a shared reader lock.
- **The idioms the design teaches must be fast.** `match` on records spent most of its time in `isinstance` and `getattr`; reading fields by position cut DeltaBlue's time by 47% and Richards' by 8%. A native `copy.replace` cut Richards' time by another 29%. When Richards moved to a persistent task table updated with `grail.replace`, it first ran twice as slow as before, because `grail.replace` was still a Python function taking `**changes`; making it native, and specializing its calls and `PVector` indexing and updates, made that version 2.2x faster under the JIT and slightly faster than the tuple table it replaced.
- **Generality in the operator rules costs every program.** Allowing a registered implementation to decline forced an extra frame per operator; making its result final removed it and cut nbody's time by 11%. Allowing builtin pairs such as `(int, int)` forced a guard in front of CPython's hottest arithmetic, which slowed unchanged numeric code by up to 14%. Forbidding builtin pairs removed the guard.
- **Faster field loads alone are not a major win.** CPython already optimizes slotted access; the record-specific load gives a small improvement, not a clear application-wide gain.
- **Tracing Grail operations is necessary but not sufficient for the JIT.** Before tier-2 support, every record construction, registered operator and generic call ended a trace, and the Grail VM ran even unchanged Richards 40% slower under the JIT. Making these operations traceable fixed that; the gains came when the optimizer started using what records guarantee. Typed field loads let operator dispatch on field values fold, which helps ray tracing most. Immutable fields make borrowed field loads safe. Closed all-float records let element-wise operators and float expressions run unboxed, which more than halved nbody's time under the JIT.
- **Immutable snapshots cost allocation, and traces lose what they know.** Richards' Grail version rebuilds one or two task records, the scheduler state and the task table's short tail on every step. Most of its remaining time is allocating and freeing them. Facts the optimizer proves (a record conforms, a field has a type) last only to the end of a trace, and Richards' traces split at its `match` on the requested action, so the side trace that rebuilds the state starts knowing nothing about it. In the earlier tuple-table version, skipping every conformance check would have gained about 12%; the in-trace facts recovered 2%.
- **Boxed immutable arithmetic is the main remaining weakness without the JIT.** Nbody builds about five temporary records per body pair where the original updates floats in place, so it runs about 2x slower than the original on the free-threaded build. Generated C kernels for all-float records (`deriving`) cut nbody's time by 41%, but they are a special-purpose path the original and the controls do not have, so the benchmarks do not use them. The JIT now gets most of this generically from the unchanged source: nbody's Grail version is about 1.25x slower than the original there, from 2.7x.

## What comes next

Borrowed field loads (no reference-count traffic when a field is read from a record that outlives the use) are the main remaining general optimization without a JIT; the JIT already does them. Under the JIT, the snapshot-per-step workloads (Richards, DeltaBlue, go) need records that are never materialized: scalar replacement of records built and consumed within a trace, then keeping state virtual across loop iterations, and carrying proven facts into side traces. Unboxing records in general is a later, larger question. Then come the semantics still needed to finish the design evaluation: comparison operators and an audit of where object identity matters. Unboxed fields and automatic value substitution are not implemented. The cost of delayed reclamation for shared values also needs measurement. Value records now cache their default hash.

The prototype has targeted debug-build and concurrency coverage, but is not production-ready. The current evidence supports Grail's sharing and value-reuse design and shows record-heavy code beating today's Python equivalents; it does **not** yet establish a general single-thread advantage over hand-written mutable code. The first JIT results show that Grail's guarantees can make JIT-compiled code faster (nbody, ray tracing, float), but not yet for code that rebuilds immutable state on every step.
