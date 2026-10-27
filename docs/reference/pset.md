---
sources:
  - docs/design/05-design-persistent-collections.md
  - src/grail/_pds.py
  - tests/test_grail_pds.py
---

# PSet reference

`PSet` is an unordered immutable set of hashable objects. Each update returns
a set without changing earlier versions.

    >>> from grail import PSet
    >>> s = PSet([1, 2])
    >>> sorted(s.add(3).discard(1)), sorted(s)
    ([2, 3], [1, 2])

`PSet(source=...)` accepts an iterable or no argument. Duplicate elements
collapse. An existing exact PSet is returned unchanged. Non-iterables and
unhashable elements raise `TypeError`. Elements need stable hashes and
equality. `PSet[T]` is an annotation, not element validation.

| Operation | Result |
| --- | --- |
| `len(s)`, `bool(s)`, `x in s`, `iter(s)` | Size, non-emptiness, membership, unordered iteration |
| `add(x, /)` | PSet including x; may return self |
| `discard(x, /)` | PSet excluding x; missing is not an error |
| `s \| other` | Union |
| `s & other` | Intersection |
| `s - other` | Difference |
| `s ^ other` | Symmetric difference |
| `mutate()` | Thread-confined set builder |

Algebra accepts arbitrary iterables, including mapping keys, and returns a
PSet. Reflected forms also work with iterables, except that a PMap left operand
does not accept set algebra. Unsupported operands or unhashable elements
raise `TypeError`. No named `union`, `intersection`, `isdisjoint`, `issubset`,
or `remove` method is supplied.

`==`, `!=`, `<`, `<=`, `>`, `>=` compare with set-like objects, including
`set`, `frozenset`, and map keys views. Ordering means proper/non-proper subset
or superset, not lexicographic order. Lists do not compare equal and cannot
be used for subset comparisons (`TypeError` for ordering).
`hash(s)` equals `hash(frozenset(s))`. `repr(s)` is `PSet([...])` with
unspecified element order.

Set builders support `add(x, /)`, `discard(x, /)`, `len(t)`, membership,
`finish()`, and context management. Edits return `None`. Subscription and
assignment/deletion by key raise `TypeError`. Builders cannot iterate, hash,
or pickle. Ownership/lifecycle misuse raises `RuntimeError`; see
[transients](../guide/transients.md).

PSet types are final and immutable; instances cannot acquire new attributes.
`copy.copy(s)` returns `s`; `copy.deepcopy(s)` copies children and may reuse
`s` when they are unchanged. Pickle reconstructs the contents as a PSet.

Guides: [persistent collections](../guide/persistent-collections.md),
[transients](../guide/transients.md).
