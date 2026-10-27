---
sources:
  - grail-compat/docs/design/01-pep-draft-records.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
  - grail-compat/docs/design/02-pep-draft-generic-functions.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# Port classes and dataclasses

Port data and behavior separately. Start with immutable leaf objects, then
make state transitions return new values instead of mutating existing ones.

    >>> from grail import record, replace, generic
    >>> @record
    ... class Account:
    ...     owner: str
    ...     balance: int
    >>> def deposit(account, amount):
    ...     return replace(account, balance=account.balance + amount)
    >>> old = Account("ann", 10)
    >>> deposit(old, 5), old.balance
    (Account(owner='ann', balance=15), 10)

Move a method to `operation(obj, ...)`. Use a generic only when multiple types
need different implementations of that operation. Replace inheritance with
composition or a union of separate record types; use match when the set of
alternatives is local and small.

For a frozen dataclass, remove methods, custom initialization, descriptors,
and base classes from the record body. Move validation into an explicit
factory. Replace `default_factory` with `grail.field(factory=...)`. Equality
and hashing need review, especially NaNs, ordinary mutable children, and code
that uses `is` to distinguish separately built data.

A hybrid port can retain private mutable lists, dicts, or existing controller
classes. An idiomatic snapshot port makes externally visible state a value,
uses persistent collections, and confines builders to a batch. Do not turn
every local temporary into an Atom. An Atom is for a shared changing reference,
not a substitute for ordinary local variables.

Keep old behavior tests and add tests for unchanged snapshots. Check the
[backend operator limits](../backends.md) before replacing dunders.
The [worked examples](../examples/index.md) show both styles.

References: [records](../reference/records.md), [generics](../reference/generic.md).
