# CPython prototype: status and findings

Oct 26, 2026 · @Neil Schemenauer

The prototype tests whether Grail's object model is practical in CPython's free-threaded interpreter. It is based on the CPython 3.15 branch. The core semantics work; performance is promising for sharing, copying and dispatch-heavy code, but immutable numeric updates remain expensive. It also builds with the GIL enabled and runs under CPython's experimental copy-and-patch JIT, so we can begin to measure what Grail's guarantees buy a JIT.

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
| Interpreter fast paths | Specialized record field loads and positional construction, and direct entry into registered operator and generic-function implementations without an extra dispatch call |
| JIT (GIL build) | Record construction, registered operators and generic calls are traced in tier 2 instead of ending traces. A constant record type needs no callable guard and gives an exactly typed result. Dispatch with known argument types is folded into a trace constant. Registration and override invalidate dependent executors. |
| Experimental | `deriving` element-wise `+ - * /` for all-float records as C kernels; not used by the benchmarks (see below) |

Exact semantics, runtime contracts and experiment switches are in `docs/implementation/cpython-runtime.md`; measurements are under `bench/notes/`. The matched results reported below are in `bench/results/20261026-190732-ea2e47369fa-klonk-c21-fast/`.

Records are shallowly immutable; an ordinary field may still contain a mutable object. **Conforming value records provide the stronger guarantee** used to skip deepcopy and reduce reference-count traffic when shared through closures. Persistent collections do not yet propagate typed value-conformance evidence.

## Rough performance

These figures come from a matched PGO/LTO run of cpython-grail `ea2e47369fa` against upstream, both built with clang 21 and the tail-call interpreter, on an **i7-14700K using 8 performance cores** (one thread per core). Each variant ran in a fresh process with fixed work, two rounds. They are wall-time indications, not precise speedup claims; changes under about 5% are noise, and the 8-thread figures varied by up to 40% between rounds for ray tracing and float.

The first two columns compare the **Grail rewrite against the upstream original program, which runs on one thread** (the originals are serial). They include representation, algorithm and threading changes, not just VM improvements. The scaling column compares the Grail rewrite with itself: one thread versus 8 threads. The parallel versions use `grail.Pool`.

| Workload | Rewrite, 1 thread, vs upstream original | Rewrite, 8 threads, vs upstream original (1 thread) | Scaling: rewrite, 8 threads vs 1 thread |
| --- | --- | --- | --- |
| Build graph, aggregation, Mandelbrot, fannkuch, float | 6% slower to 32% faster | About 6.3-8.6x faster | About 6.5-7.4x |
| Ray tracing | About 2.1x faster | About 5.9x faster | About 2.8x |
| Spectral norm | About 6% slower | About 4x faster | About 4.3x |
| Deepcopy workloads | 1.7x slower to 16x faster, depending on representation | 3.4x to 65x faster | About 4-5.8x |
| Chaos, hexiom, SQLGlot simplification and normalization | About 1.2-1.8x faster | Serial workloads | - |
| SQLGlot generation | About 2x slower | Serial workload | - |
| Richards, DeltaBlue, go | About 1.25-1.8x slower | DeltaBlue on 2 threads: about 1.1x slower | DeltaBlue: 1.6x (two tasks) |
| Nbody | About 2.1x slower | Serial workload | - |

Richards, DeltaBlue and go got slower than in earlier runs because their rewrites became fully immutable: a value kernel state, an immutable solver snapshot, and record positions and trees. They allocate where the originals mutate. The controls below run the same source, and Grail beats them on all three.

Unchanged Python programs run at **about parity** on the Grail VM: from 8% slower (aggregation, a code-layout effect) to 3% faster, with a median near 1% slower. These are bounded benchmark workloads, not claims about full applications.

### Compared with today's Python

To separate the Grail runtime from the rewrites' algorithm changes, each rewrite also runs unchanged on stock Python features: frozen or mutable slotted dataclasses, ordinary operator methods, and `functools.singledispatch` or a dictionary lookup. The stock versions run on upstream CPython, at one thread.

| Workload | Grail vs frozen dataclass | Grail vs mutable slotted class |
| --- | --- | --- |
| DeltaBlue | About 7.7x faster | About 6.1x faster |
| MDP | About 8.5x faster | About 8.5x faster |
| Richards | About 3.6x faster | About 2.2x faster |
| Nbody, float | 2.6-2.7x faster | 1.35-1.4x faster |
| Ray tracing | About 2.5x faster | About 1.3x faster |
| Go | About 1.7x faster | About 1.6x faster |
| Deepcopy workloads | About 23-26x faster | About 22-26x faster |
| Chaos, SQLGlot | 1.0-1.35x faster | 3% slower to 5% faster |
| Fannkuch, Mandelbrot, aggregation, build graph | About parity | About parity |

Native records clearly beat the stock immutable equivalent, and on record-heavy code they also beat a mutable class that gives up immutability. Where the rewrite is slower than upstream but faster than both controls, the cost is in the immutable algorithm, not in Grail's records or dispatch.

### Under the JIT

These runs use the **GIL-enabled build** with the JIT on, one thread, from the same matched PGO/LTO setup and commit as above. They are not comparable with the free-threaded figures. CPython's JIT is not yet supported on the free-threaded build.

| Workload | Grail rewrite vs upstream original | Grail rewrite vs mutable slotted class |
| --- | --- | --- |
| Ray tracing | About 2.3x faster | About 1.3x faster |
| Chaos, float | About 1.2-1.35x faster | 1.07-1.5x faster |
| Deepcopy | About 12-22x faster | About 27-35x faster |
| Go, DeltaBlue | 1.4-1.75x slower | 1.4x and 6.9x faster |
| Richards, nbody | About 2.7x slower | 2.7x and 1.6x faster |

Unchanged programs run within about 4% of upstream under the JIT. Against this tail-call interpreter the JIT gains little: 0.96-1.25x for most originals and 1.9x for Richards, and 1.0-1.4x for the rewrites, so Richards' rewrite gains 1.16x where its original gains 1.9x. Earlier measurements found that once Grail operations were traceable, 90-98% of executed work in the record- and dispatch-heavy workloads ran in tier 2, with no traces abandoned at Grail operations. Grail-specific optimization in the trace optimizer has barely started: folding dispatch into constants gained at most 2-3%.

## What we have learned

- **The semantics fit CPython.** Records need neither hidden instance types nor a custom metaclass. Immutable reconstruction handles copying and cyclic graphs without exposing half-built records.
- **Immutability helps most when it removes work.** Value proofs allow copy reuse and cheaper shared references; persistent structures avoid whole-container copies.
- **Dispatch must be cheap to share.** Immutable registry snapshots let threads dispatch without a shared reader lock.
- **The idioms the design teaches must be fast.** `match` on records spent most of its time in `isinstance` and `getattr`; reading fields by position cut DeltaBlue's time by 47% and Richards' by 8%. A native `copy.replace` cut Richards' time by another 29%.
- **Generality in the operator rules costs every program.** Allowing a registered implementation to decline forced an extra frame per operator; making its result final removed it and cut nbody's time by 11%. Allowing builtin pairs such as `(int, int)` forced a guard in front of CPython's hottest arithmetic, which slowed unchanged numeric code by up to 14%. Forbidding builtin pairs removed the guard.
- **Faster field loads alone are not a major win.** CPython already optimizes slotted access; the record-specific load gives a small improvement, not a clear application-wide gain.
- **Tracing Grail operations is necessary but not sufficient for the JIT.** Before tier-2 support, every record construction, registered operator and generic call ended a trace, and the Grail VM ran even unchanged Richards 40% slower under the JIT. Making these operations traceable fixed that, but the stock optimizer does not yet exploit closed record types or immutable fields, so the rewrites gain no more from the JIT than the originals do, and Richards' rewrite gains much less.
- **Boxed immutable arithmetic is the main remaining weakness.** Nbody builds about five temporary records per body pair where the original updates floats in place. Its time goes to the operator implementations and to allocating, building and freeing records; construction itself now takes about 23 ns. Generated C kernels for all-float records (`deriving`) cut nbody's time by 41%, but they are a special-purpose path the original and the controls do not have, so the benchmarks do not use them; a JIT that unboxes floats would get most of this generically.

## What comes next

Borrowed field loads (no reference-count traffic when a field is read from a record that outlives the use) are the main remaining general optimization without a JIT. Under the JIT, the next step is profiling why Richards gains less than the original, then using record guarantees in the trace optimizer to remove guards and reference counting; unboxing records is a later, larger question. Then come the semantics still needed to finish the design evaluation: comparison operators and an audit of where object identity matters. Unboxed fields and automatic value substitution are not implemented. The cost of delayed reclamation for shared values also needs measurement. Value records now cache their default hash (see `docs/implementation/cpython-runtime.md`).

The prototype has targeted debug-build and concurrency coverage, but is not production-ready. The current evidence supports Grail's sharing and value-reuse design and shows record-heavy code beating today's Python equivalents; it does **not** yet establish a general single-thread advantage over hand-written mutable code, nor that Grail's guarantees make JIT-compiled code faster.
