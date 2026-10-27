---
sources:
  - grail-compat/docs/design/02-pep-draft-generic-functions.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# Add arithmetic to records

Register operators for exact operand pairs, or derive element-wise arithmetic
for a record whose fields are all annotated float.

    >>> from grail import record, ops
    >>> @record(deriving="+-*/")
    ... class Vec:
    ...     x: float
    ...     y: float
    >>> (Vec(1.0, 2.0) + Vec(2.0, 3.0)) * 2
    Vec(x=6.0, y=10.0)

Derivation provides record addition and subtraction, scalar multiplication in
both orders, and record division by a scalar. It does not provide a dot
product or record-by-record multiplication.

For other meanings, register explicitly:

    >>> @record
    ... class Count:
    ...     n: int
    >>> @ops.add.impl
    ... def _(a: Count, b: Count):
    ...     return Count(a.n + b.n)
    >>> Count(2) + Count(3)
    Count(n=5)

Both types must match exactly. Register a reversed mixed pair separately.
Unary `ops.neg` and `ops.abs` take one record annotation. Register them before
use. Comparisons do not have a Grail operator registry today.

Do not register pairs of two builtin types. Do not use `NotImplemented` to
decline a registered pair: on the prototype it is a final result, while the
compatibility package cannot preserve that behavior. Non-record pairs also
have syntax-integration limits in compatibility mode.

[Reference](../reference/hooks-and-ops.md), [backends](../backends.md).
