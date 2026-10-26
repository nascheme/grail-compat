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

# 3. Add stock with an operator

For this tutorial, adding an integer to an Item means increasing its count.
The operator returns another Item; it never changes the original.

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
    >>> @ops.add.impl
    ... def _(a: Item, b: int):
    ...     return replace(a, count=a.count + b)
    >>> tea = Item("tea", 2)
    >>> describe(tea + 3), tea.count
    ('tea: 5', 2)

Only `(Item, int)` is registered. Neither `(int, Item)` nor `(Item, bool)`
is inferred. This is exact-pair dispatch, not general multiple dispatch.
Production code may prefer a named function to make the operation clearer.

Next: [store snapshots](4-persistent-collections.md).
Details: [operators guide](../guide/operators.md).
