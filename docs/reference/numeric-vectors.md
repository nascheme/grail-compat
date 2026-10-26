---
sources:
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_numvec.py
  - grail-compat/tests/test_grail_numvec.py
---

# Numeric vectors reference

`FloatVector` and `IntVector` store immutable, unboxed 64-bit numbers.
They suit data built once and read many times, not repeated single-element edits.

    >>> from grail import FloatVector, IntVector
    >>> FloatVector([1, 2.5]).tolist()
    [1.0, 2.5]
    >>> IntVector([1, 2]).append(3).tolist()
    [1, 2, 3]

## Elements and construction

`FloatVector(source=...)` and `IntVector(source=...)` take an iterable or no
argument. Passing the same exact vector type returns it unchanged. Other
sources are copied, including buffers. Matching one-dimensional native-format
buffers are copied as numbers; other sources use element conversion.
There is no zero-copy import of mutable foreign storage.

Float elements use numeric `__float__`, or `__index__` if absent; strings are
not parsed. Reads return Python floats. Huge integer conversion can raise
`OverflowError`. NaN and infinity are allowed.

Integer elements use `__index__`, including bools, but reject floats with
`TypeError`. Reads return Python ints. The range is `-2**63` through
`2**63 - 1`; overflow raises `OverflowError`. Invalid element types raise
`TypeError`; exceptions raised by conversion callbacks propagate.

## Sequence API (both types)

| Operation | Result |
| --- | --- |
| `v[i]`, `v[start:stop:step]` | Python number or same-type vector slice |
| `len(v)`, `bool(v)`, `x in v`, `iter(v)`, `reversed(v)` | Sequence reads |
| `count(value)` | Matching element count |
| `index(value, start=0, stop=..., /)` | First match; absent raises `ValueError` |
| `tolist()` | Fresh Python list |
| `set(index, value, /)` | Vector with an existing element replaced |
| `append(value, /)` | Vector with an element added |
| `pop()` | Vector without its last element |
| `v + w` | Concatenation; same exact type only |
| `mutate()` | Numeric transient builder |

Indices accept `__index__` and negatives. Invalid indices raise `TypeError`,
out-of-range indices or empty pop raise `IndexError`, and a zero slice step
raises `ValueError`. `index` bounds must fit an index-sized integer
(`OverflowError` otherwise). Search uses Python equality without converting
the query: `1.0` can find integer 1; a NaN is never found.
Sequence patterns work. Item assignment and deletion are not supported.

Comparison requires the same exact vector type; ordering is lexicographic.
Different vector types and lists do not compare equal; ordering them raises
`TypeError`. Float comparison has no identity shortcut: a vector containing
NaN is unequal even to itself. NaN-free hashes equal `hash(tuple(v))`; NaN
lanes hash as zero for stable vector hashing. `repr` shows the type and a list.

`FloatVector` and `IntVector` are final immutable types without instance
dictionaries. They are not parameterized generic types.

## Buffers and builders

Native vectors export read-only, contiguous, one-dimensional buffers with
item size 8 and format `d` (float64) or `q` (int64). Writable buffer requests
raise `BufferError`; writes through a memoryview raise `TypeError`.
Compatibility buffer export requires Python 3.12+. Builders export no buffer.

Numeric builders support integer subscription/assignment, `len`, `append`,
`extend`, `pop`, `finish`, and context management, like PVector builders.
Values are converted before committing an edit. `extend` converts all added
elements before adding any, so a conversion error leaves it unchanged.
Builder `pop` returns the removed number; immutable `pop` returns a vector.
Ownership/lifecycle errors raise `RuntimeError`.

`copy.copy` and `copy.deepcopy` return self. Pickle reconstructs from numeric
contents. Updates copy the flat buffer, O(n), on both backends.

Guides: [numeric vectors](../guide/numeric-vectors.md),
[transients](../guide/transients.md).
