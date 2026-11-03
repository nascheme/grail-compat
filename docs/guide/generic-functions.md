---
sources:
  - docs/design/02-pep-draft-generic-functions.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Define and extend generic functions

Define an operation once, then register behavior for each supported type.
This works with records, ordinary classes and builtins.

    >>> from grail import generic, generic_impl
    >>> @generic
    ... def describe(obj, prefix=""):
    ...     return prefix + "unknown"
    >>> @generic_impl(describe)
    ... def _(obj: int, prefix=""):
    ...     return prefix + str(obj)
    >>> describe(3, prefix="count: ")
    'count: 3'

The original body is the fallback, so decide whether unsupported values
should produce a result or raise an error. Only the first argument's type
selects the implementation. Additional annotations do not add multiple dispatch.

Pass the dispatch argument positionally. An implementation may name that
parameter as it likes, so an existing function can be registered without a
wrapper. Keep the other parameters' names, kinds and default presence
unchanged. For example, renaming `prefix` to `label` is not allowed even if
you normally call it positionally. Default values may differ, but doing so
can surprise callers.

A union registers each of its members, with `@generic_impl(describe)` on
`def _(obj: int | float, prefix=""):` or with
`generic_register(describe, int | float, f)`.

Exact type registration wins. Ordinary subclasses then use MRO and virtual
ABC resolution like `functools.singledispatch`. Ambiguous unrelated ABCs raise
`RuntimeError`; an exact registration resolves that ambiguity.

For plugins, import the original generic and call
`generic_register(describe, ForeignType, implementation)`. Do not redeclare
the generic in the plugin. Duplicate exact registrations raise `TypeError`;
use `generic_override(describe, ForeignType, implementation)` or
`@generic_override(describe)` when you intend replacement. Registrations
remain open after imports finish.

    >>> from grail import generic_dispatch, generic_fallback
    >>> generic_dispatch(describe, str) is generic_fallback(describe)
    True

Use `generic_dispatch(op, Type)` to inspect selection without calling the
implementation. Use ordinary functions when there is no need for
type-dependent behavior.

## Static typing

Type checkers see `@generic` as returning the fallback unchanged. Calls are
checked against the fallback's signature, and a fallback with its own type
parameters keeps them:

    >>> from typing import TypeVar
    >>> S = TypeVar("S")
    >>> @generic
    ... def advance(shape, state: S, dt: float) -> S:
    ...     return state

Here `advance(shape, 3, 0.5)` types as `int`. The registration and
introspection functions (`generic_impl`, `generic_register`,
`generic_override`, `generic_dispatch`, `generic_fallback`, `generic_version`
and `is_generic`) are therefore module-level functions. At runtime a generic is
a dispatch object whose methods (`describe.impl`, `describe.register` and so
on) work the same, but type checkers report them as missing attributes. The
checkers do not compare implementation signatures against the fallback;
registration checks them at runtime.

[Reference](../reference/generic.md).
