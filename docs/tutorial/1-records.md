---
sources:
  - docs/design/01-pep-draft-records.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# 1. Describe an item

An Item contains a name and a stock count. Replacing its count gives a new
item while earlier references keep the old count.

    >>> from grail import record, replace, generic, ops, PMap, Atom
    >>> @record
    ... class Item:
    ...     name: str
    ...     count: int
    >>> tea = Item("tea", 2)
    >>> more = replace(tea, count=5)
    >>> tea.count, more.count
    (2, 5)

Keep Item's body limited to data. Its annotations make it a value record
type; they do not validate arguments. The program is responsible for passing
an integer count. You can add an explicit validation factory if needed.

Next: [describe different types](2-generic-functions.md).
Details: [records guide](../guide/records.md).
