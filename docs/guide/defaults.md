---
sources:
  - docs/design/01-pep-draft-records.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# Defaults and factories

Use a default for a reusable value. Use `field(factory=...)` for a new object
on each construction, especially an ordinary mutable child.

    >>> from grail import record, field
    >>> @record
    ... class Basket:
    ...     owner: str = "guest"
    ...     items: list = field(factory=list)
    >>> a, b = Basket(), Basket()
    >>> a.items.append("tea")
    >>> a.items, b.items
    (['tea'], [])

The record binding is immutable; its list is not. Keep such scratch state
private to one thread. Prefer persistent collections for shared snapshots.

Default expressions run once, at definition. A factory takes no arguments
and runs only for an omitted field. Supplying `items=` skips its factory.
Factories run in field order; their exceptions abort construction.

    >>> field(default=0, factory=list)
    Traceback (most recent call last):
    ...
    TypeError: specify a default or a factory, not both

A bare unhashable default, such as `[]`, raises `ValueError` when declaring
the record. Hashability is only an approximation: a hashable mutable object
is not thereby safe as a shared default. Use `field(default=None)` when None
is the intended value, not a factory result.

[Reference](../reference/records.md).
