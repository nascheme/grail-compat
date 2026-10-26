---
sources:
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_pds.py
  - grail-compat/tests/test_grail_pds.py
---

# PMap reference

`PMap` is an unordered immutable mapping. Updates return a map and leave the
original unchanged. Values are references, not deep-frozen copies.

    >>> from grail import PMap
    >>> old = PMap({"tea": 2})
    >>> new = old.set("tea", 3)
    >>> old["tea"], new["tea"]
    (2, 3)

## Construction and reading

`PMap(source=...)` accepts no argument, a mapping, or an iterable of two-item
entries. Later duplicate keys win. An existing exact PMap is returned unchanged.
Non-iterables raise `TypeError`; malformed entry lengths raise `ValueError`.
Keys need stable hashes and equality. Unhashable keys raise `TypeError`.
`PMap[K, V]` creates a type annotation; it does not validate elements.

| Operation | Result |
| --- | --- |
| `m[key]` | Value; missing key raises `KeyError(key)` |
| `get(key, default=None, /)` | Value, or default if missing |
| `len(m)`, `bool(m)` | Entry count and non-emptiness |
| `key in m`, `iter(m)` | Key membership and unordered key iteration |
| `keys()` | `PMapKeysView`, a `collections.abc.KeysView` |
| `values()` | `PMapValuesView`, a `ValuesView` |
| `items()` | `PMapItemsView`, an `ItemsView` |

Views retain their immutable map. They support length, membership and
iteration. Keys and items views support set-like operations under their ABC
rules; values views are not sets. Neither map nor views promise insertion
order. There is no reversed iteration.

## Updates

| Method or operator | Result |
| --- | --- |
| `set(key, value, /)` | PMap with the entry added or replaced |
| `delete(key, /)` | PMap without the key; missing raises `KeyError` |
| `m \| other`, `other \| m` | PMap union with a mapping; right-hand values win |
| `update_with(other, fn, /)` | PMap union; conflicts become `fn(old, incoming)` |
| `mutate()` | Thread-confined map builder |

`update_with` accepts the same sources as construction. `fn` must be callable
or raises `TypeError`. It is called for conflicting keys only. Its exceptions
propagate and do not mutate the original map. Union requires a mapping, not
an iterable of pairs; unsupported operands raise `TypeError` in expressions.
There is no `update`, `setdefault`, mutating `pop`, or `.copy()` method.

Map builders support `t[key]`, assignment, deletion, `len(t)`, membership,
`finish()`, and the context manager protocol. Missing lookup/deletion raises
`KeyError`. They do not iterate, hash, or pickle. `add` and `discard` on a map
builder raise `TypeError`. See [transients](../guide/transients.md) for lifecycle
and ownership errors.

## Comparison, hashing and copying

Equality compares keys and values with another mapping, including `dict`.
Ordering is unsupported (`TypeError` in ordering expressions).
`hash(m)` equals `hash(frozenset(m.items()))`; unhashable values raise
`TypeError`. Do not mutate hash-relevant children after using a map as a key.
`repr(m)` uses `PMap({...})`; entry order is unspecified.
Types are final and immutable; instances have no dictionary or weak references
and cannot acquire new attributes.

`copy.copy(m)` returns `m`. `copy.deepcopy(m)` copies keys and values and may
return `m` if none changes identity. Pickle reconstructs a PMap from its
contents; iteration order is not part of its contract.

Guides: [persistent collections](../guide/persistent-collections.md),
[transients](../guide/transients.md).
