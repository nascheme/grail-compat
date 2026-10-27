---
sources:
  - grail-compat/docs/design/02-pep-draft-generic-functions.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# Generic functions reference

A generic selects an implementation using the type of its first argument.
The original function is its fallback.

    >>> from grail import generic
    >>> @generic
    ... def label(obj, suffix="!"):
    ...     return "unknown" + suffix
    >>> @label.impl
    ... def _(obj: int, suffix="!"):
    ...     return str(obj) + suffix
    >>> label(obj=3, suffix="?")
    '3?'

## Creation and dispatch

`generic(fallback)` returns a callable Generic object. The fallback needs a
first parameter that accepts a positional argument; otherwise creation raises
`TypeError`. Ordinary generics also dispatch when the first argument is
passed by its parameter name. Other arguments do not affect selection.

Selection is exact type first, then MRO and applicable virtual ABCs using
`functools.singledispatch` resolution rules, then fallback. Unrelated ambiguous
ABC matches raise `RuntimeError`. Dispatch keys use type identity. Missing,
duplicate or invalid call arguments are rejected by the selected callable.

## Generic object

| Member | Contract |
| --- | --- |
| `register(dispatch, func, override=False)` | Register callable for a type; return `func` |
| `impl(func, /)` | Decorator; infer dispatch from the first parameter's evaluated annotation; return `func` |
| `override(func)` | Like `impl`, but permit replacement |
| `override(dispatch, func)` | Explicit replacement; return `func` |
| `dispatch(dispatch, /)` | Resolve a type without calling; return implementation or fallback |
| `fallback` | Read-only original callable |
| `version` | Read-only integer, starts at zero and increments on each successful registration or override |
| `pair` | Read-only bool; false for user-created generics |
| `__name__` | Read-only fallback name |

`register` needs a type and callable. A duplicate exact registration raises
`TypeError` unless `override=True`. `override` also permits a new registration.
`impl` needs an annotated dispatch parameter. Unresolvable annotations may
raise `NameError`; non-types raise `TypeError`. Signature introspection may
raise `ValueError` for callables without an inspectable signature.

Implementations preserve the fallback's parameter names, parameter kinds,
and presence or absence of defaults. Default **values** and annotations need
not match. Additional parameters are not allowed just because they are
optional. Violations raise `TypeError` at registration. Callbacks' exceptions
propagate normally. The special protocol and operator generics instead check
that implementations can bind their fixed positional arguments.

Generic attributes cannot be rebound. There is no public registry or
unregister operation. Registration retains the implementation and type.
Import a generic from its defining module to extend it elsewhere; do not
redefine it with another `@generic` declaration.

A racing call sees a consistent old or new registry snapshot. User callbacks
and ABC resolution can run arbitrary code; this does not make their bodies
thread-safe. See [concurrency](../guide/concurrency.md).

For binary operators, `dispatch` is an exact `(left_type, right_type)` tuple,
`pair` is true, and inference reads two annotations. These objects are supplied
by `grail.ops`, not by a public multiple-dispatch constructor.

Guide: [generic functions](../guide/generic-functions.md).
See also [hooks and operators](hooks-and-ops.md).
