---
sources:
  - docs/design/01-pep-draft-records.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
  - src/grail/_mutate.py
  - tests/test_grail_mutate.py
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

## Make several updates with a draft

Using `grail.mutate()` is idiomatic when you need to make a number of updates
to a record. It gives you a temporary draft whose fields support ordinary
assignment. Call `grail.finish()` to produce the updated record; the original
remains unchanged.

    >>> import grail
    >>> @grail.record
    ... class Account:
    ...     balance: int
    ...     transactions: int
    >>> account = Account(balance=100, transactions=0)
    >>> with grail.mutate(account) as draft:
    ...     draft.balance += 50
    ...     draft.balance -= 20
    ...     draft.transactions += 2
    >>> updated = grail.finish(draft)
    >>> updated
    Account(balance=130, transactions=2)
    >>> account
    Account(balance=100, transactions=0)

Prefer `grail.replace()` for a simple replacement and `grail.mutate()` for a
sequence of edits, especially when later edits depend on earlier ones.
Drafts also support nested records, `PVector` elements and `PMap` entries,
so you can update nested state without spelling out each enclosing
replacement.

A draft belongs to its creating thread. Leaving the `with` block seals it;
`grail.finish()` returns the result. If the block raises an exception, the
draft is aborted. Ordinary mutable objects stored in fields are not wrapped
in drafts: mutating those objects still changes them directly.

Drafts are currently experimental. Use them for clarity, not as a guaranteed
performance optimization. See the [drafts reference](../reference/mutate.md)
for supported operations and lifecycle details.

## Deepcopy and pickle

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
