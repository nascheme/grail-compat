---
sources:
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_vector.py
  - grail-compat/tests/test_grail_vector.py
---

# PVector reference

`PVector` is an immutable sequence of Python objects. It keeps old versions
available when you append, replace, or remove an element.

    >>> from grail import PVector
    >>> v = PVector([1, 2])
    >>> list(v.append(3).set(0, 9)), list(v)
    ([9, 2, 3], [1, 2])

`PVector(source=...)` takes an iterable or no argument. An exact PVector is
returned unchanged. Non-iterables raise `TypeError`. `PVector[T]` creates an
annotation, without checking or converting elements. Mutable elements remain
mutable references.

| Operation | Result |
| --- | --- |
| `v[i]` | Element; negative indices count from the end |
| `v[start:stop:step]` | PVector slice; step zero raises `ValueError` |
| `len(v)`, `bool(v)`, `x in v` | Length, non-emptiness and membership |
| `iter(v)`, `reversed(v)` | Forward and reverse iterators |
| `count(value)` | Number of matching elements |
| `index(value, start=0, stop=..., /)` | First matching index within bounds; absent raises `ValueError` |
| `set(index, value, /)` | PVector with an existing element replaced |
| `append(value, /)` | PVector with an element at the end |
| `pop()` | PVector without its last element, not the removed element |
| `v + w` | Concatenation of two PVectors |
| `mutate()` | Thread-confined builder |

Indices use `__index__`; wrong types raise `TypeError`. Out-of-range indices
and empty `pop` raise `IndexError`. Slicing supports negative steps. Slice
assignment and arbitrary deletion are unsupported. A full forward slice may
return self. Concatenation requires another PVector, not a list or tuple.
Sequence patterns work.

Comparisons require the same exact vector type. Equality is element-wise;
ordering is lexicographic. With other types equality normally returns false
and ordering raises `TypeError`. Like tuples, membership and comparison can
use element identity before equality, including for a shared NaN object.
`hash(v)` equals `hash(tuple(v))`; unhashable elements raise `TypeError`.
Keep hash-relevant children stable. `repr(v)` is `PVector([...])`.

Builders support integer subscription and assignment, `len(t)`,
`append(value)`, `extend(iterable)`, `pop()`, `finish()`, and context management.
Append and extend return `None`; builder `pop()` returns the removed element.
They do not support slices, deletion, iteration, hashing, or pickling.
If generic iterable extension fails, already-appended items may remain.
See [transients](../guide/transients.md) for lifecycle errors.

The type is final and immutable, with no instance dictionary.
`copy.copy(v)` returns `v`. `copy.deepcopy(v)` copies children and may reuse
`v` if unchanged. Pickle reconstructs a PVector from its elements.

Guides: [persistent collections](../guide/persistent-collections.md),
[transients](../guide/transients.md).
