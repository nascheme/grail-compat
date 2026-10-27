# Grail benchmarks

Idiomatic Grail versions of 15 pyperformance benchmarks, the PyCon US 2026
free-threading build-graph challenge, and three examples from the
free-threading guide (mandelbrot, michi, aggregate), each next to the
upstream program it was rewritten from.

The idiomatic versions are written for the native Grail prototype (a free-threaded
CPython fork) but use only the public `grail` API, so they also run on
stock CPython with this package (`grail-compat`).  Timings taken with
grail-compat say nothing about Grail performance: it adds Python-level
overhead the native runtime does not have.

A partial SQLGlot 4.6.0 port is also included; it is not a complete library
conversion. It requires `sqlglot==4.6.0` for adapters and differential tests
(`uv run --with sqlglot==4.6.0 ...`); `python-dateutil` enables optional date
folding. Pure record tests do not require SQLGlot.

## Layout

    <name>/idiomatic.py   the idiomatic Grail version; each has main() and a
                          __main__ entry
    <name>/upstream.py    the original, vendored byte-for-byte (where there is one)
    <name>/hybrid.py      an earlier, partly mutable conversion kept as a
                          comparison control (go, michi, deltablue, richards,
                          buildgraph)
    bpe_tokeniser/data/   the upstream training corpus
    buildgraph/challenge/, buildgraph/graphs/wide.json
                          vendored challenge code and one of its graphs
    michi/fetch_patterns.py
                          downloads michi's large pattern tables (not vendored)
    sqlglot_port/         idiomatic.py plus generate.py, simplify.py, scope.py,
                          optimizer.py and the vendored upstream.py fixture
    tests/                correctness tests (backend-agnostic)
    LICENSES/             licences of the vendored upstream code

No file or directory here may be named `grail.py` / `grail/`: running a
benchmark puts its directory on `sys.path`, which would shadow the `grail`
package.  Every directory has an `idiomatic.py`, so tools load these modules by
file path (`importlib.util.spec_from_file_location`) under distinct names
rather than by putting several benchmark directories on `sys.path`;
`tests/bench_support.py` does this for the tests. SQLGlot is the exception:
its sibling modules use the canonical `sqlglot_port` package so all imports
share the same record class identities, without shadowing upstream `sqlglot`.

## Upstream sources

All vendored code is MIT licensed; see `LICENSES/`. SQLGlot-derived algorithms
retain the SQLGlot 4.6.0 notice in `LICENSES/sqlglot-LICENSE`.

| Source | Commit | Files |
|---|---|---|
| [pyperformance](https://github.com/python/pyperformance) | `ccc0aeb7ad46d65b6dcd4160e0fdda4d885852dd` (2026-09-01) | `pyperformance/data-files/benchmarks/bm_<name>/run_benchmark.py` -> `<name>/upstream.py`; `bm_bpe_tokeniser/data/` -> `bpe_tokeniser/data/` |
| [nascheme/michi](https://github.com/nascheme/michi) | `738853b941b1896712fea486614510f2460ff275` | `michi.py` -> `michi/upstream.py` |
| [nascheme/michi-patterns](https://github.com/nascheme/michi-patterns) (michi's `patterns` submodule) | `a5cb01c21af307bf3727d85ecefbf729db78cc60` | not vendored; `michi/fetch_patterns.py` downloads `prob.zst` and `spat.zst` to `michi/patterns/` (git-ignored) and checks their sha256 |
| [mpage/pycon26-ftp-challenge](https://github.com/mpage/pycon26-ftp-challenge) | `6c648f56122ee0bba6031c8b03e73ce071c47c56` | `challenge/{graph,harness,reference,generate}.py` -> `buildgraph/challenge/`; `graphs/wide.json` -> `buildgraph/graphs/` |

Not vendored:

- michi's `patterns/` submodule (53MB of large-pattern tables).  Run
  `python michi/fetch_patterns.py [--dest DIR]` to download them into
  `michi/patterns/` (files already present with the right hash are
  skipped).  Without them `michi` runs with its built-in 3x3 patterns only
  and the large-pattern test skips; `MICHI=<michi checkout>` points the
  test at a checkout's `patterns/` instead.
- The challenge's other four graphs (`chain`, `diamond`, `realistic`,
  `tree`; 4MB).  Get them from the challenge repository, or regenerate
  them with `buildgraph/challenge/generate.py`.
- mandelbrot and aggregate have no `upstream.py`. Mandelbrot's original is
  the [free-threading guide notebook](https://py-free-threading.github.io/examples/mandelbrot/).
  Aggregate's baseline is a serial dict loop accumulating `sums[key] += value`,
  from [cereggii's reduction example](https://dpdani.github.io/cereggii/examples/AtomicDict/reduce/).

## Common rules for the idiomatic versions

Data is `@grail.record`s (value records where the fields allow) and
behaviour is plain functions or `@grail.generic` where there is real
polymorphism; no classes with methods.  Shared state is immutable: records,
tuples and persistent collections (`PVector`, `PMap`, `PSet`, `FloatVector`);
anything that changes lives in an `Atom` or in thread-local scratch (plain
lists and dicts inside one function).  Where the work divides, the program
splits it over a `grail.Pool(threads)` (`threads=` parameter, default 8):
`grail.pmap` for independent items, `grail.parallel_reduce` for
reductions, and a pool scope (`spawn`) where tasks create more tasks.  A
deterministic mode reproduces upstream's result.  The algorithm is kept
unless the notes below say otherwise.  `tests/test_native.py` checks that
every class in an `idiomatic.py` is a record.

## Benchmarks

**aggregate** - Per-key sums of 3,628,800 `(key, value)` pairs, from
cereggii's [`AtomicDict.reduce()`](https://dpdani.github.io/cereggii/examples/AtomicDict/reduce/)
example linked from the free-threading guide.  Each chunk is folded into a
private dict and published as a `PMap`; the partial maps are merged in
chunk order (`PMap.update_with`) or, with `combine="atom"`,
scope tasks each swap theirs into one shared `Atom` with a pure merge
function.

**bpe_tokeniser** - pyperformance `bm_bpe_tokeniser` (Shantanu Jain, after
tiktoken): byte-pair-encoding training to 1,024 tokens on the Frankenstein
corpus, then encoding it.  The trained tokenizer is a record owning
read-only rank/decoder maps and the compiled pattern; training counters and
encoding parts stay private scratch.  Same merge order and output as
upstream; no parallelism.  Run: `idiomatic.py bpe_tokeniser/data/frankenstein_intro.txt`.

**buildgraph** - The PyCon US 2026 free-threading challenge: call each
target's `build()` exactly once, after all its dependencies, over a DAG, and
return all results.  `reference.py` (the challenge's single-threaded
topological-order reference) is the original.  The idiomatic version's `Plan` is a
value record of `Job` records by index; per-run state is atoms (one
fetch-and-add counter per target, write-once result cells), and a pool
scope runs each target as a task spawned by whichever task releases it.  `hybrid.py` keeps the
earlier Plan that held the harness's mutable `Target` objects.  Run:
`idiomatic.py buildgraph/graphs/wide.json [threads]` (the vendored challenge
modules are put on `sys.path`); `challenge/harness.py` times and validates
any submission against the reference.  `build()` uses unseeded random
bytes, so result digests differ from run to run.

**chaos** - pyperformance `bm_chaos`: a chaos-game fractal from B-spline
transforms.  Points, splines and bounds are records; control points and
splines are trie-backed `PVector`s so the legacy endpoint changes path-copy
only the edited branch.  One chain is serial (seeded RNG); `chains=` independent chains can run in
parallel.

**comprehensions** - pyperformance `bm_comprehensions`: filter, join, sort
and project a tray of 24 widgets.  Widgets and trays are immutable
records/tuples; dicts and sort lists are local scratch.  Upstream's
ascending Boolean sort, missing-reference counts and last-ID-wins lookup
are kept.

**deepcopy** - pyperformance `bm_deepcopy`.  The document, dataclass states
and reduce object become typed value records, which native `copy.deepcopy`
can return unchanged; the aliased document uses `PMap`/`PVector`, whose
deepcopy preserves aliases.  No global copy dispatch tables are patched.
Repetitions can run on separate threads.

**deltablue** - pyperformance `bm_deltablue`: the incremental constraint
solver.  Solver state is a value (a `PVector` of `Var` records plus
direction/constraint vectors by id); every operation returns a new snapshot,
editing through transients.  `hybrid.py` keeps the earlier
record-of-mutable-lists solver.

**fannkuch** - pyperformance `bm_fannkuch`: maximum pancake flips over all
permutations of 0..n-1.  Ranges of permutation indices (`Chunk` value
records) are mapped over threads and folded in order; also computes the
Benchmarks Game checksum.

**float** - pyperformance `bm_float`: normalize and take the component-wise
maximum of 100,000 points.  `Point` is a value record; contiguous blocks are
reduced on their own threads and folded in order, bit-identical to upstream.

**go** - pyperformance `bm_go`: 9x9 UCT Monte Carlo tree search.  Positions
and the search tree are values: moves return new positions (built in local
list scratch, superko hashes in a `PSet`) and the tree is path-copied.
`mode="exact"` reproduces upstream's move for `random.seed(1)`.  `hybrid.py`
keeps the earlier mutable-struct conversion.

**hexiom** - pyperformance `bm_hexiom` (Laurent Vaucher's solver, adapted by
Armin Rigo).  Topology and candidate-state snapshots are records;
propagation edits private scratch and publishes tuples at fixed points.
`storage="pvector"` runs the same search on `PVector` snapshots.  All
strategies, move orders and search traces match upstream.

**mandelbrot** - The free-threading guide's [Mandelbrot example](https://py-free-threading.github.io/examples/mandelbrot/):
escape-time counts over an 800x800 grid.  The view is a value record, rows
are computed in parallel batches, and the image is a value record of typed
nested tuples.  The original is numpy-based and is not vendored (see above).

**mdp** - pyperformance `bm_mdp`: probabilistic battle planning by value
iteration.  Battle states and graph nodes are value records with generic
successor expansion; the graph is published as read-only adjacency.  Value
iteration keeps local numeric tables and upstream's Gauss-Seidel order.

**michi** - Petr Baudis's Michi Go engine (nascheme's free-threading fork),
the guide's Monte Carlo example: 1,400 MCTS iterations on an empty 13x13
board.  Positions and the tree are values; pattern tables are persistent
maps.  With threads, searcher tasks in a pool scope share the root in an
`Atom`: descents are published with `compare_and_set` and playout results
backed up with `swap`, addressing nodes by child-index paths.  `hybrid.py` keeps the earlier mutable-struct
conversion.  Run: `idiomatic.py [PATTERNS_DIR [threads]]`; given
`michi/patterns` (after `fetch_patterns.py`) it also loads the large
patterns, which needs Python 3.14+ (`compression.zstd`) or the `zstd` tool.

**nbody** - pyperformance `bm_nbody`: symplectic-Euler integration of the
Jovian planets.  `Vec3` and `Body` are value records with vector
arithmetic through `grail.ops`.  One simulation is inherently serial and
runs on one thread; `ensemble` runs independent simulations in parallel.

**nqueens** - pyperformance `bm_nqueens`: all solutions for 8 queens, in
upstream's order.  A value record for the problem, tuples for solutions;
first-row subtrees are searched in parallel.

**raytrace** - pyperformance `bm_raytrace`: a 100x100 Whitted ray tracer.
Vectors, shapes, surfaces and the scene are records, with generic functions
for shape/surface polymorphism and `grail.ops` vector arithmetic; rows are
rendered in parallel.

**richards** - pyperformance `bm_richards`: Martin Richards' OS-kernel
simulation.  The scheduler state is an immutable snapshot (a record of a
persistent `PVector` table of `Task` records, the current task and counters)
advanced by the pure `scheduler_step` with `PVector.set` and `grail.replace`.
`hybrid.py` keeps the earlier scheduler over local mutable lists.

**sqlglot_port** - Partial SQLGlot 4.6.0 conversion: immutable expression trees,
record-native simplification, Boolean normalization, compact SQL generation,
acyclic scope graphs and five optimizer-tail stages. Parsing, qualification,
projection pushdown, unnesting and dialect output remain upstream. Local
mutable tables are scratch, not published state. The TPCH fixture is vendored
from pyperformance's `bm_sqlglot_v2`. Run:
`sqlglot_port/idiomatic.py '(a AND b) OR c'`.

**spectral_norm** - pyperformance `bm_spectral_norm`: the power method on
A^T A.  The vectors are shared as unboxed `FloatVector`s and rows are
computed in parallel; returns the final square root upstream omits.

Each `idiomatic.py` docstring has the full design notes.

## Running

On native Grail (the prototype interpreter, free-threaded):

    python -X gil=0 benchmarks/float/idiomatic.py
    python -X gil=0 -m unittest discover -s benchmarks/tests

With grail-compat, from this repository's root:

    uv run python benchmarks/float/idiomatic.py
    uv run python -m unittest discover -s benchmarks/tests

Check `grail.backend` (`"native-phase1"` or `"shim"`) if unsure which
implementation is loaded.  The default workloads are sized for native Grail
and are slow on grail-compat.

## Tests

`tests/` is backend-agnostic: checks that need the native runtime (the
backend name and similar) skip on grail-compat.

- `test_native.py`: imports every `idiomatic.py`/`hybrid.py` (except
  buildgraph's `hybrid.py`, which starts a thread pool at import), checks that idiomatic versions
  define only records and that nothing shadows `grail`, and exercises
  generic dispatch, record arithmetic, deepcopy/value conformance, `PMap`
  merges with `Atom.swap` and chaos's path-copying updates.
- `test_michi.py`: Monte Carlo playouts match `upstream.py` for fixed
  seeds, and, when the pattern files are present (and the `zstd` tool that
  upstream uses is installed), the loaded large-pattern tables are
  identical.
- `test_bpe_tokeniser.py`, `test_comprehensions.py`, `test_hexiom.py`,
  `test_mdp.py`: contract tests for the idiomatic version plus differential tests
  that load the vendored `upstream.py` (with a stub `pyperf` module) and
  compare results on the full upstream workload and on randomized inputs:
  merge sequences and token streams (bpe_tokeniser), every output field
  (comprehensions), parsed topology, propagation and complete search traces
  (hexiom; the level-36 solves run only with `HEXIOM_SLOW_TESTS=1`), and
  damage distributions, successors and value-iteration bounds (mdp).

- `test_sqlglot*.py`: pure record contracts plus SQLGlot 4.6.0 differential
  checks for adapters, simplification, generation, scope graphs and optimizer
  stages, including the vendored TPCH fixture. Differential tests skip when
  SQLGlot is absent; a version mismatch is an error.

The other directories retain their vendored originals for differential
comparison. Running an `idiomatic.py` is a workload smoke check, not proof of
equivalence or a native speedup. See the
[prototype status report](../docs/design/06-cpython-prototype.md) for the
measured native results and their limitations.
