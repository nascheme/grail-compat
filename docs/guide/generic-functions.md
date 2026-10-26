---
sources:
  - docs/design/02-pep-draft-generic-functions.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# Define and extend generic functions

Define an operation once, then register behavior for each supported type.
This works with records, ordinary classes and builtins.

    >>> from grail import generic
    >>> @generic
    ... def describe(obj, prefix=""):
    ...     return prefix + "unknown"
    >>> @describe.impl
    ... def _(obj: int, prefix=""):
    ...     return prefix + str(obj)
    >>> describe(3, prefix="count: ")
    'count: 3'

The original body is the fallback, so decide whether unsupported values
should produce a result or raise an error. Only the first argument's type
selects the implementation. Additional annotations do not add multiple dispatch.

Keep parameter names, kinds and default presence unchanged. For example,
renaming `prefix` to `label` is not allowed even if you normally call it
positionally. Default values may differ, but doing so can surprise callers.

Exact type registration wins. Ordinary subclasses then use MRO and virtual
ABC resolution like `functools.singledispatch`. Ambiguous unrelated ABCs raise
`RuntimeError`; an exact registration resolves that ambiguity.

For plugins, import the original generic and call
`describe.register(ForeignType, implementation)`. Do not redeclare the generic
in the plugin. Duplicate exact registrations raise `TypeError`; use
`describe.override(ForeignType, implementation)` or `@describe.override` when
you intend replacement. Registrations remain open after imports finish.

    >>> describe.dispatch(str) is describe.fallback
    True

Use `dispatch(Type)` to inspect selection without calling the implementation.
Use ordinary functions when there is no need for type-dependent behavior.

[Reference](../reference/generic.md).
