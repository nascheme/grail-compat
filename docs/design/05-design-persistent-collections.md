<!-- Copied from the Project Grail docs/design/ by tools/copy_design_docs.py; do not edit here. -->

# Design: Persistent collections

Oct 25, 2026 · @Neil Schemenauer

Project Grail includes immutable, persistent collections in the style of Clojure: a map, a vector and a set that never change, where every update returns a new collection sharing most of its structure with the old one. With a suitable conformance rule, they can make records with collection fields value records, so program state can live in values updated through atoms instead of mutable `dict`s and `list`s. The native prototype already includes HAMT-backed `PMap`/`PSet`, trie-backed `PVector`, flat unboxed `FloatVector`/`IntVector`, and boxed `Atom`; typed-collection conformance is not implemented. The API and complexity targets below are proposals unless explicitly identified as current behavior.

## Why

A record that needs a collection today has two options, and both are poor:

- **A mutable `dict` or `list` in an ordinary field.** The record is no longer a value record, the VM loses its guarantees, and under free threading the container is shared mutable state with nothing to coordinate it.
- **A `tuple`, `frozenset` or `frozendict`.** These are immutable, but every update copies the whole collection: O(n) per change. That is fine for small, rarely changed data and unusable for the large maps that real state is made of.

Persistent collections close the gap. An update returns a new collection in O(log n), sharing all unchanged structure with the old one, and the old version stays valid. Because nothing ever mutates, any thread can read any version without synchronization. Combined with records and atoms, this gives the Clojure pattern: all related state in one value, replaced atomically.

```python
@grail.record
class Bank:
    accounts: grail.PMap[str, Money]
    audit: grail.PVector[str]

state = Atom(Bank(grail.PMap(), grail.PVector()))

def transfer(b: Bank, src: str, dst: str, amt: Money) -> Bank:
    accounts = b.accounts.set(src, b.accounts[src] - amt).set(dst, b.accounts[dst] + amt)
    return grail.replace(b, accounts=accounts, audit=b.audit.append(f"{src}->{dst} {amt}"))

state.swap(transfer, "ann", "bob", Money(5))   # atomic across both accounts and the log
```

Under the proposed typed-conformance rule, `Bank` would be a value record when its contents conform. In the current prototype these collection annotations are ordinary fields: the bindings remain immutable and readers using `Atom.value` see a consistent state reference, but value-record substitution permissions do not follow. An update path-copies trie nodes rather than copying the whole map.

## The collections

Names are working names.

| Type | Structure | Clojure analogue | Lookup | Update | Notes |
| --- | --- | --- | --- | --- | --- |
| `PMap[K, V]` | HAMT; CHAMP is a possible later layout | `hash-map` | Expected O(log₃₂ n) | Expected O(log₃₂ n) | Prototype reuses CPython's contextvars HAMT; collision behavior and equality costs must be measured |
| `PSet[T]` | Same trie, keys only | `hash-set` | Expected O(log₃₂ n) | Expected O(log₃₂ n) | Trie-sharing set algebra is a target; prototype algebra between two `PSet`s probes per element, and mixed operands go through temporary frozensets |
| `PVector[T]` | 32-way bit-partitioned trie with a tail buffer | `vector` | O(log₃₂ n) by index | O(log₃₂ n); amortized O(1) append | Append and pop at the end are the fast path |
| Sorted map and set (later) | Persistent balanced tree | `sorted-map` | O(log n) | O(log n) | Ordered iteration and range queries |
| `FloatVector`, `IntVector` | Flat unboxed buffer (`float64`, `int64`; perhaps `complex128` later); a trie of buffers if updates matter | Clojure `vector-of` | O(1) by index | O(n) copy (prototype), or O(log n) as a trie | No per-element objects or refcounts |

A branching factor of 32 makes typical tries shallow, but it is not a four-level bound for a million entries: vector index capacity, hash distribution, and HAMT collision nodes differ. Adversarial hash collisions can make map/set operations much more expensive than the expected bounds.

**The unboxed numeric vectors.** The benchmark studies asked for this more often than for any other collection: spectral\_norm, fannkuch, go and mandelbrot. A `tuple` of floats costs a boxed object and a refcount per element. When threads share it, acquiring references to non-immortal elements can write shared refcounts. `FloatVector` and `IntVector` hold unboxed `float64` or `int64` elements in one immutable buffer, expose the buffer protocol read-only, and build through the same transient API (`mutate()`/`finish()`). Elements read as Python floats and ints; out-of-range values raise rather than widen. The prototype implements the flat form: updates copy the buffer, which suits the built-once, read-many uses the studies found. Without a JIT each read boxes a new object, so single-thread reads are about 1.7x slower than a tuple's, while 8 threads iterating one shared vector run about 2x faster than over a tuple, at one sixth of the memory. A JIT's unboxed loads, a trie of buffers for update-heavy programs, and value-field conformance remain to be done. See `docs/implementation/numeric-vectors.md` (Project Grail workspace).

## API

The collections should feel like Python's own, minus mutation.

- **Reading.** `PMap` implements `collections.abc.Mapping`, `PSet` implements `Set`, `PVector` implements `Sequence`, so `m[k]`, `k in m`, `len`, iteration, slicing and `match` mapping and sequence patterns all work. Mutating methods (`__setitem__`, `append` on a `list`, `update`) do not exist.
- **Updating.** Every update returns a new collection; the old one is unchanged. Names avoid the mutating names of `dict` and `list` where the meaning would differ:

  | Operation | `PMap` | `PSet` | `PVector` |
  | --- | --- | --- | --- |
  | Add or replace | `m.set(k, v)` | `s.add(x)` | `v.set(i, x)`, `v.append(x)` |
  | Remove | `m.delete(k)` | `s.discard(x)` | `v.pop()` (last element) |
  | Merge | `m \| other` | `s \| t`, `s & t`, `s - t` | `v + w` |
  | Merge with a function | `m.update_with(other, fn)` | | |
- **Batch updates.** An optimized transient, Clojure's name for a temporary mutable builder, can avoid copying a path on every edit and then finish in O(1). The current map/set transient is thread-confined but still path-copies each edit; its `finish()` simply keeps the current root. The API:

  ```python
  with m.mutate() as t:        # t is a private, mutable builder
      for k, v in rows:
          t[k] = v
  m2 = t.finish()               # a new PMap; t can no longer be used
  ```

  The `immutables` package uses this spelling (`mutate()`, `finish()`); pyrsistent calls the same thing an evolver.
- **Merging with a function.** `m.update_with(other, fn)` returns a map with every key from both. Where a key appears in both, its value is `fn(m[k], other[k])`. This is Clojure's `merge-with`, and it is the combine step of a parallel fold (aggregate). It should be pure so that `Atom.swap` may rerun it.
- **Construction target.** Bulk construction should be O(n). The prototype builds a map/set trie bottom-up after one radix sort of the hash chunks; vectors build bottom-up from a tuple or by appending through a transient. Transients for all three types edit nodes in place while the builder exclusively owns them, so a transient copies each shared path once. Empty instances may be shared.
- **Typing.** All three are generic classes (`PMap[str, int]`), so type checkers need nothing new.

## Value semantics and records

A persistent collection is a value type when its element types are value types: `PMap[str, int]` and `PVector[Point]` are value types, `PMap[str, list]` is not. A record field annotated with a value-typed collection is a value field. As with other value fields, construction would not enforce annotations or reject mismatches. Value-only optimizations require proof that elements conform to the particular declared types. The prototype does not yet recognize these annotations as value-field plans.

A generic "all elements are values" bit is not enough: both strings and ints are value types, but a map of string values does not conform to `PMap[str, int]`. A boolean also cannot recover after removal of the last nonconforming element without richer metadata or a scan. Investigate parameter-specific plans, counts, or composable node summaries. Specify how nested collections, replacements, deletion, and transient edits maintain evidence before promising constant-time field checks.

| Property | Rule |
| --- | --- |
| Equality | Structural: same keys and values, or same elements in the same order |
| Hash | Structural when elements are hashable; caching requires stable element hashes and is not implemented in the prototype |
| Substitutability | Element-wise, by the declared element types, as for tuples |
| Identity | Implementation-defined, as for records; an implementation may intern small collections |
| Copy and pickle | May substitute, under the same rule as records |

## Concurrency

- **Published collection storage never changes.** Transfer through supported synchronizing operations gives readers initialized nodes and valid references. Element reference acquisition still has a lifetime/refcount cost; mutable elements retain their own synchronization requirements. A shared iterator's advancement state is mutable and may lock even though the collection does not.
- **Transients are confined to the thread that created them.** Using a transient from another thread, or after `finish()`, raises an error. The API rejects cross-thread transient use; `finish()` returns an immutable result for transfer through supported synchronization. It does not make arbitrary racy pointer transfer safe.
- **Shared updates go through atoms.** A collection in an atom is updated with `swap`; the update function returns a new collection, so it is pure as `swap` requires.

## Implementation and phasing

1. **CPython prototype, now.** Native implementations are imported into `cpython-grail`, the current testing target. Map/set reuse CPython's HAMT, used since 3.7 for `contextvars` (PEP 567). Measure lookup, update, iteration and memory against `dict`, `frozendict` and `tuple`, and the effect of atoms holding persistent state. See `docs/implementation/persistent-collections.md` (Project Grail workspace), `docs/implementation/pvector.md` (Project Grail workspace), `docs/implementation/numeric-vectors.md` (Project Grail workspace) and `docs/implementation/atom.md` (Project Grail workspace) for actual APIs and limits.
2. **Compatibility package, later.** A new implementation could expose the API on older interpreters. Its representation, supported subset and performance need separate validation; no fallback implementation is assumed here. The `immutables` package and PEP 603's proposed `frozenmap` provide relevant prior art.
3. **A PEP, later.** Propose the collections for the standard library or as builtins once the API and performance are proven. Records do not depend on this; until then, value records list the `grail` collections among their value types.

`frozendict` (PEP 814, Python 3.15) and these collections are complementary: `frozendict` is the right choice for small maps that are built once, and a persistent map for large or frequently updated ones.

## Prior art

| Source | What to take from it |
| --- | --- |
| Clojure persistent collections | The overall model: values plus atoms, `assoc`-style updates, transients |
| Phil Bagwell, "Ideal Hash Trees" (2001) | The hash array mapped trie |
| Steindorfer and Vinju, CHAMP (OOPSLA 2015) | Canonical, cache-friendly HAMT layout with faster iteration and equality |
| CPython `Python/hamt.c` and the `immutables` package | A HAMT already proven inside CPython; the `mutate()` / `finish()` API |
| pyrsistent | A Python API for persistent map, vector and set, and evolvers |
| Scala immutable collections, Immutable.js | Persistent collections in mainstream, non-Lisp ecosystems |

## Open issues

- **Map iteration order.** Python users expect `dict` insertion order. A HAMT iterates in hash order: deterministic for given contents within one process, but not insertion order, and string hashes vary between processes. Options: unordered like `frozenset` (simplest, fastest); insertion-ordered (needs an extra structure and makes deletion costlier); or both, as separate types. Leaning: unordered `PMap` and `PSet`, with sorted variants later for ordered needs.
- **Equality with built-in collections.** Proposed: `PMap == dict` and `PSet == set` compare as mappings and sets do today (`frozenset({1}) == {1}`), while `PVector == list` is False, as `tuple == list` is.
- **Element conformance** for value-typed collections in record fields: how the VM decides that a collection's elements conform without scanning it, and how a collection records its conformance.
- **Names** (`PMap`, `PVector`, `PSet`, `mutate`, `finish`) and whether operations follow `immutables`, pyrsistent or Clojure naming.
- **Sorted collections**, and a persistent queue.
- **Memory overhead** relative to `dict` and `list` for small collections, and whether small collections should use a flat representation.
- **Large read-only tables.** michi builds two pattern tables of about a million entries each, once, then only reads them. Building a `PMap` from a dict must be O(n) through a transient, and lookups must be close to a dict's. Otherwise programs will keep these tables as module-level dicts and lose the value-type guarantees.
- **Numeric vector:** the prototype uses two separate types, `FloatVector` and `IntVector`. Open: more element types (`complex128`, narrower ints), whether `PVector` should instead pick an unboxed representation when all elements are floats or ints, and whether the vectors should be value types for record fields.
- **Pickle format** and interoperability with `immutables` and pyrsistent objects.
- **Interaction with NumPy and buffers.** Object collections hold Python references and expose no buffer protocol. The numeric vectors export a read-only, one-dimensional buffer; since their storage never changes or moves, views need no export count. Zero-copy import from NumPy (a vector viewing a foreign buffer) is not supported: construction copies.
