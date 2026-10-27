---
sources:
  - docs/design/01-pep-draft-records.md
  - docs/design/02-pep-draft-generic-functions.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
  - docs/design/05-design-persistent-collections.md
  - src/grail/_pds.py
  - tests/test_grail_pds.py
---

# 4. Store inventory snapshots

Store items by name in a PMap. A restock operation replaces one entry and
returns an updated inventory, leaving the original snapshot available.

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
    >>> stock = PMap({"tea": Item("tea", 2)})
    >>> def restock(stock, name, amount):
    ...     return stock.set(name, stock[name] + amount)
    >>> changed = restock(stock, "tea", 3)
    >>> describe(stock["tea"]), describe(changed["tea"])
    ('tea: 2', 'tea: 5')

The map binding and each Item are immutable. Map iteration is unordered.
An unknown product name raises KeyError; the caller must add a new product
explicitly instead of silently inventing one during restock.

Next: [coordinate threads](5-threads-and-atoms.md).
Details: [collections guide](../guide/persistent-collections.md).
