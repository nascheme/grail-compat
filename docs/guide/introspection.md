---
sources:
  - docs/design/01-pep-draft-records.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Inspect records

Use `record_fields` for the schema and `record_values` for a shallow snapshot.
Neither requires access to private type attributes.

    >>> from grail import record, record_fields, record_values
    >>> @record
    ... class Item:
    ...     name: str
    ...     count: int = 0
    >>> [(f.name, f.kind) for f in record_fields(Item)]
    [('name', 'value'), ('count', 'value')]
    >>> item = Item("tea", 2)
    >>> values = record_values(item)
    >>> values['count'] = 10
    >>> item.count
    2

Each `FieldInfo` has `name`, `annotation`, `kind`, `default`, and `factory`.
Entries keep declaration order. Absent values are `grail.MISSING`, not
None, so a field is required when both `default` and `factory` are
`MISSING`. A values snapshot contains the
original child references, so changing a mutable child still changes that
child.

Pass a type to `record_fields` and `is_value_record`, and an instance to
`record_values` and `conforms`. Invalid arguments raise `TypeError`.
`is_record(obj)` tests for a record type and accepts anything. To handle any
record in a generic function, register `grail.Record`, a virtual base class of
every record type. The metadata reflects
the original declaration, not later edits to a source annotation dictionary.

[Reference](../reference/records.md).
