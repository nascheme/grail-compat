---
sources:
  - docs/design/01-pep-draft-records.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
  - docs/design/02-pep-draft-generic-functions.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# 2. Describe items with a generic

The describe operation belongs to the module, not the Item type. A generic
lets another module add descriptions for more types without editing Item.

    >>> from grail import record, replace, generic, ops, PMap, Atom
    >>> @record
    ... class Item:
    ...     name: str
    ...     count: int
    >>> @generic
    ... def describe(obj):
    ...     raise TypeError("unsupported item")
    >>> @describe.impl
    ... def _(obj: Item):
    ...     return f"{obj.name}: {obj.count}"
    >>> describe(Item("tea", 2))
    'tea: 2'

The original body raises for unsupported types. `@describe.impl` uses the
first annotation to choose Item. Its parameter name stays `obj`, matching
the fallback. A normal function is enough if Item is the only supported type;
we use a generic to leave room for more product kinds.

Next: [an exact-pair operator](3-operators.md).
Details: [generic functions guide](../guide/generic-functions.md).
