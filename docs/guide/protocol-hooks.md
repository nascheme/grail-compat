---
sources:
  - docs/design/02-pep-draft-generic-functions.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Customize record protocols

Register `grail.repr`, `grail.eq`, or `grail.hash` to change a record's builtin
protocols. The record type itself stays immutable.

    >>> import grail
    >>> @grail.record
    ... class User:
    ...     name: str
    >>> @grail.repr.impl
    ... def _(obj: User):
    ...     return "User<" + obj.name + ">"
    >>> repr(User("ann"))
    'User<ann>'

These hooks accept record registrations only today. They are not replacements
for builtin function objects, and registering for `int` or `object` raises
`TypeError`.

Equality dispatches on its first argument only. Check the other argument and
return `NotImplemented` for unsupported types. Keep substitutable records
equal and give equal records equal hashes. Those responsibilities are not
automatically enforced by registration.

Use an ordinary custom generic for a serializer, formatter, or other operation
that should also handle builtin types. Keep hook extensions in an explicit
module: importing it changes protocol behavior for the registered type.

[Reference](../reference/hooks-and-ops.md).
