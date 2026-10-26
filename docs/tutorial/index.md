---
sources:
  - docs/design/01-pep-draft-records.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
  - docs/design/07-appendix-concurrency-toolkit.md
  - grail-compat/src/grail/_atom.py
  - grail-compat/tests/test_grail_atom.py
---

# Tutorial: a small shop

Build a small stock tracker in five steps: records, a generic description,
an operator, collection snapshots, and shared updates. Each page repeats the
code it needs so you can run it independently in a fresh Python session.

    >>> from grail import PMap
    >>> PMap({"tea": 2})["tea"]
    2

The tracker keeps counts, not prices, so all arithmetic uses exact integers.
It is a teaching program, not a database or a transaction system for external
payments. We add an operator to teach registration; a named `restock` function
would also be reasonable in application code.

Start with [records](1-records.md), then continue to
[generic functions](2-generic-functions.md), [operators](3-operators.md),
[persistent collections](4-persistent-collections.md), and
[threads and atoms](5-threads-and-atoms.md).
