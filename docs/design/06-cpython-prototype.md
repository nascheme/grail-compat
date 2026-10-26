<!-- Copied from the Project Grail docs/design/ by tools/copy_design_docs.py; do not edit here. -->

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

Exact semantics, runtime contracts and experiment switches are in `docs/implementation/cpython-runtime.md`; measurements are under `bench/notes/`. The matched results reported below are in `bench/results/20261026-111345-5c74a29e4ba/`.

Records are shallowly immutable; an ordinary field may still contain a mutable object. **Conforming value records provide the stronger guarantee** used to skip deepcopy and reduce reference-count traffic when shared through closures. Persistent collections do not yet propagate typed value-conformance evidence.

## Rough performance

These figures come from a matched PGO/LTO run (clang-19, three rounds) of prototype commit `5c74a29e4ba` on a **6-core, 12-thread Ryzen 5 2600X**, with both interpreters built the same way. They are wall-time indications, not precise speedup claims; changes under about 5% are noise.

The first two columns compare the **Grail rewrite against the upstream original program, which runs on one thread** (the originals are serial). They include representation, algorithm and threading changes, not just VM improvements. The scaling column compares the Grail rewrite with itself: one thread versus 12 threads.

| Workload | Rewrite, 1 thread, vs upstream original | Rewrite, 12 threads, vs upstream original (1 thread) | Scaling: rewrite, 12 threads vs 1 thread |
| --- | --- | --- | --- |
| Ray tracing | About 1.9x faster | About 9.5x faster | About 4.9x |
| Build graph, aggregation, Mandelbrot, fannkuch, float | 2% slower to 25% faster | About 6.7-8.4x faster | About 6-7.4x |
| Spectral norm | About 9% slower | About 3.5x faster | About 3.8x |
| Deepcopy workloads | 1.1x to 16x faster, depending on representation | 3.6x to 34x faster | About 2-3x |
| Richards, chaos | About 1.15-1.25x faster | Serial workloads | - |
| DeltaBlue, go | Parity to 1.1x faster | No reliable parallel result | - |
| Nbody | About 2.3x slower | Serial workload | - |

Unchanged Python programs run at **about parity** on the Grail VM: from 9% slower (spectral norm) to 7% faster, with a median near zero. These are bounded benchmark workloads, not claims about full applications.

### Compared with today's Python

To separate the Grail runtime from the rewrites' algorithm changes, each rewrite also runs unchanged on stock Python features: frozen or mutable slotted dataclasses, ordinary operator methods, and `functools.singledispatch` or a dictionary lookup. The stock versions run on upstream CPython, at one thread.

| Workload | Grail vs frozen dataclass | Grail vs mutable slotted class |
| --- | --- | --- |
| Richards | About 3.3x faster | About 2.3x faster |
| Nbody, float | 2.1-2.5x faster | 1.2-1.3x faster |
| Ray tracing | About 2.2x faster | About 1.25x faster |
| DeltaBlue | About 1.45x faster | About 1.4x faster |
| Deepcopy workloads | About 27-30x faster | About 24-27x faster |
| Fannkuch, Mandelbrot, go | About parity | Parity to 3% slower |

Native records clearly beat the stock immutable equivalent, and on record-heavy code they now also beat a mutable class that gives up immutability. Where the rewrite is slower than both controls, little of the time is in records or dispatch.

### Under the JIT

These runs use the **GIL-enabled build** with the JIT on, one thread, from the same matched PGO/LTO setup and commit as above. They are not comparable with the free-threaded figures. CPython's JIT is not yet supported on the free-threaded build.

| Workload | Grail rewrite vs upstream original | Grail rewrite vs mutable slotted class |
| --- | --- | --- |
| Ray tracing | About 2x faster | About 1.15x faster |
| Chaos, go, DeltaBlue | About 1.15-1.3x faster | Go at parity; DeltaBlue about 1.7x faster |
| Deepcopy | About 13-22x faster | About 33x faster |
| Richards | About 1.45x slower | About 3.6x faster |
| Nbody | About 3.1x slower | About 1.5x faster |

Unchanged programs run within about 6% of upstream under the JIT. The JIT speeds most rewrites up about as much as the originals (1.5-2.3x), but Richards' rewrite gains 2.4x against 4x for its original. Once Grail operations were traceable, 90-98% of executed work in the record- and dispatch-heavy workloads ran in tier 2, with no traces abandoned at Grail operations. Grail-specific optimization in the trace optimizer has barely started: folding dispatch into constants gained at most 2-3%.

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

Borrowed field loads (no reference-count traffic when a field is read from a record that outlives the use) are the main remaining general optimization without a JIT. Under the JIT, the next step is profiling why Richards gains less than the original, then using record guarantees in the trace optimizer to remove guards and reference counting; unboxing records is a later, larger question. Then come the semantics still needed to finish the design evaluation: comparison operators and an audit of where object identity matters. Unboxed fields and automatic value substitution are not implemented. The cost of delayed reclamation for shared values also needs measurement. Value records now cache their default hash (see `docs/implementation/cpython-runtime.md`); it is not yet in the matched results above.

The prototype has targeted debug-build and concurrency coverage, but is not production-ready. The current evidence supports Grail's sharing and value-reuse design and shows record-heavy code beating today's Python equivalents; it does **not** yet establish a general single-thread advantage over hand-written mutable code, nor that Grail's guarantees make JIT-compiled code faster.
