---
sources:
  - grail-compat/README.md
  - grail-compat/src/grail/__init__.py
  - cpython-grail/Lib/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/src/grail/_atom.py
  - grail-compat/src/grail/_pool.py
  - grail-compat/src/grail/_pds.py
  - grail-compat/src/grail/_vector.py
  - grail-compat/tests/test_grail.py
  - grail-compat/tests/test_grail_atom.py
  - grail-compat/docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_numvec.py
  - grail-compat/tests/test_grail_numvec.py
---

# Backends and compatibility

The compatibility package and the native prototype share an API, not an
implementation or a performance profile. Check backend information before
interpreting results.

    >>> import grail
    >>> grail.backend in ("shim", "native-phase1")
    True

| Runtime | Records and generics | Collections and cells |
| --- | --- | --- |
| grail-compat, stock CPython 3.10+ | Python implementation (`backend == "shim"`) | Python, except optional native PMap/PSet on 3.14+ |
| Native prototype (currently CPython 3.15 development) | C implementation (`backend == "native-phase1"`) | Native implementations |

`grail.pds_backend` is `"native"` or `"python"` in grail-compat. The native
prototype facade does not expose that attribute. `GRAIL_PURE=1` forces Python
PMap/PSet in grail-compat when set before import. It does not select a different
record, vector, or Atom implementation, and has no such role in the prototype.

## Known differences

- Compatibility arithmetic syntax consults registrations through record slots.
  Two non-record operands ignore registrations in `a + b`; direct `ops.add(a, b)`
  still consults them. For `(non-record, record)` the left Python method runs
  first. Native central arithmetic runs exact registrations first.
- A registered binary `NotImplemented` is a final value on the prototype.
  Compatibility syntax instead follows Python slot rules and can raise TypeError.
- Without a unary registration, compatibility raises TypeError for a record;
  the prototype fallback currently recurses. Register before use.
- Record names in compatibility errors omit a module prefix.
- Compatibility inherited generic dispatch resolves on each exact miss; the
  prototype caches guarded inherited resolutions. Observable dispatch rules
  are intended to match, but costs differ.
- Compatibility uses ctypes to harden record types. If unavailable, immutable
  type, no-subclass and no-object.__new__ bypass protections are incomplete.
- `grail.replace` works on all supported versions. Records also implement the
  `__replace__` interoperability hook for `copy.replace`, available on 3.13+.
- Compatibility numeric buffer export requires Python 3.12+.
- Pure-Python maps, sets and vectors copy backing storage for edits (O(n)).
  Native maps/sets use a HAMT and PVector uses a trie. Numeric-vector edits
  copy their flat buffer on both backends.
- Compatibility Atom operations use a per-cell lock. Native value reads use a
  different synchronization strategy. User-facing atomic operations and
  identity-CAS semantics are shared; lock-free performance is not.
- The compatibility `Pool` is pure Python on `threading`: about 0.7-0.9 us
  of overhead per task on free-threaded 3.14, against about 0.15 us for the
  native C pool. With the GIL it only helps tasks that release the GIL.
  Before Python 3.11, which has no exception groups, a failed scope raises
  its first task error instead of an exception group.

Do not use compatibility timings to predict native runtime performance.
Free-threaded execution is needed for CPU parallelism, but does not guarantee
a speedup. The docs avoid promises about unboxing records, automatic
substitution, or future JIT optimizations.

See [operators](reference/hooks-and-ops.md) and [concurrency](guide/concurrency.md).
