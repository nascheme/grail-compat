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
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_pds.py
  - grail-compat/tests/test_grail_pds.py
  - docs/design/07-appendix-concurrency-toolkit.md
  - grail-compat/src/grail/_atom.py
  - grail-compat/tests/test_grail_atom.py
---

# 5. Coordinate stock updates

Put the inventory in one Atom when workers need to update it. Swap retries
the pure restock function so a competing update does not get overwritten.

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
    >>> from concurrent.futures import ThreadPoolExecutor
    >>> inventory = Atom(stock)
    >>> def receive(amount):
    ...     inventory.swap(restock, "tea", amount)
    >>> with ThreadPoolExecutor(max_workers=2) as pool:
    ...     completed = list(pool.map(receive, [1, 2, 3]))
    >>> snapshot = inventory.value
    >>> describe(snapshot["tea"])
    'tea: 8'
    >>> stock["tea"].count
    2

A worker never mutates stock or an Item. It returns a new map, so retries
are harmless. Logging a shipment inside restock would not be harmless: it
could log more than once. Atom does not make a delivery to an external system
atomic with an inventory update.

You now have data in records, behavior in functions, snapshots in a persistent
map, and one explicit shared reference. Continue with the
[concurrency guide](../guide/concurrency.md) or [Atom reference](../reference/atom.md).
