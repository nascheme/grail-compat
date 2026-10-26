---
sources:
  - docs/design/01-pep-draft-records.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# Declare and use records

Declare data as fields and put behavior in functions. A record's bindings
never change, but an ordinary field can still hold a mutable object.

    >>> from grail import record
    >>> @record
    ... class Item:
    ...     name: str
    ...     stock: int = 0
    >>> Item("tea", stock=4)
    Item(name='tea', stock=4)

Use positional or keyword arguments. An annotation does not validate or
convert an argument: `Item("tea", "many")` is allowed. Use a factory function
when you need validation, conversion, or a computed field.

    >>> item = Item("tea", 4)
    >>> item.stock = 5
    Traceback (most recent call last):
    ...
    AttributeError: record fields are immutable

Records have no user methods, no subclassing, and no instance dictionary.
Write `restock(item, count)` rather than `item.restock(count)`. When several
record types support one operation, use a [generic](generic-functions.md).
Use composition rather than a record base class; use ordinary classes for
objects that need mutation or inheritance.

Class patterns work with field order through `__match_args__`:

    >>> match item:
    ...     case Item(name, stock=n):
    ...         result = (name, n)
    >>> result
    ('tea', 4)

Do not rely on records being distinct objects just because you constructed
them separately. Compare with `==`, not `is`.

See [defaults](defaults.md), [copying and replacement](copying.md), and
[records reference](../reference/records.md).
