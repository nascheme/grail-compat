---
sources:
  - docs/design/01-pep-draft-records.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Copy, replace and pickle

Use `grail.replace` to create a record with changed fields. Shallow copy can
reuse the record; deepcopy also reuses conforming value records.

    >>> from grail import record, replace
    >>> from copy import copy, deepcopy
    >>> @record
    ... class Point:
    ...     x: float
    ...     y: float
    >>> p = Point(1.0, 2.0)
    >>> replace(p, y=3.0)
    Point(x=1.0, y=3.0)
    >>> copy(p) is p, deepcopy(p) is p
    (True, True)

`replace(p, y=3.0)` works on all supported Python versions. Replacement
shares unchanged children and does not replay defaults or factories.
Unknown fields and non-record arguments raise `TypeError`. The first
argument is positional-only, so you can replace a field named `obj`.

Records also support Python's `copy.replace` on 3.13+ through their
`__replace__` protocol hook. Use `grail.replace` in everyday Grail code;
you do not need to call the dunder directly.

Deepcopy of a record with ordinary fields copies those children using the
normal memo protocol. The immutable record is reconstructed only when its
children are available. Cycles through mutable children are supported.
Record identity and aliasing are not a portable way to distinguish equal
values.

Pickle needs the type to be available at its module and qualified name.
Define picklable record types at module scope, not inside a local function.
Pickle stores all field values, rather than replaying omitted defaults.
Do not load untrusted pickle data.

[Reference](../reference/records.md).
