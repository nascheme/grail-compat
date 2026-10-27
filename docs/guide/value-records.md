---
sources:
  - docs/design/01-pep-draft-records.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Value records and ordinary fields

A value record type has a recognized value annotation for every field.
An instance must also hold conforming values before value-only behavior,
such as reuse by deepcopy, applies.

    >>> from grail import record, is_value_record, record_fields
    >>> @record
    ... class Sample:
    ...     values: tuple[float, ...]
    >>> is_value_record(Sample)
    True
    >>> record_fields(Sample)[0].kind
    'value'

Today the scalar value types are None, bool, int, float, str and bytes.
You can nest already-defined value record types, unions of value types,
and typed tuples. `object`, lists, complex numbers, frozensets, Atoms and
persistent collections are ordinary fields today. A persistent collection
can still be an excellent shared snapshot; its annotation just does not
make its containing record a value record yet.

Conformance uses exact types. `Sample((1,))` stores an int unchanged and does
not conform to `tuple[float, ...]`. Neither construction nor replacement
raises for that mismatch. `is_value_record` inspects the type declaration,
not a particular instance. There is no public conformance query.

    >>> from copy import deepcopy
    >>> sample = Sample((1.0, 2.0))
    >>> deepcopy(sample) is sample
    True

Ordinary fields give shallow immutability only. A list inside a record still
needs its own synchronization when shared. Prefer immutable children and
publish the whole snapshot through an Atom or a queue. This rule matters
more for thread safety than whether the type qualifies for value optimizations.
Define referenced value types before their containers; unresolved annotation
plans are not upgraded later.

See [concurrency](concurrency.md), [introspection](introspection.md), and
[reference](../reference/records.md).
