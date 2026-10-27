---
sources:
  - docs/design/02-pep-draft-generic-functions.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Hooks and operators reference

Use Grail hooks to customize record protocols without adding methods.
Use `ops` to register arithmetic for exact operand types.

    >>> import grail
    >>> @grail.record(deriving="+*")
    ... class Vec:
    ...     x: float
    ...     y: float
    >>> Vec(1.0, 2.0) + Vec(3.0, 4.0)
    Vec(x=4.0, y=6.0)
    >>> 2 * Vec(1.0, 2.0)
    Vec(x=2.0, y=4.0)

## Protocol hooks

`grail.repr(obj)`, `grail.eq(a, b)`, and `grail.hash(obj)` are Generic objects.
Their implementations receive one, two, and one positional arguments,
respectively. Registration is keyed only by the record type of the first
argument, even for `eq`. Only record types may be registered for these hooks
today; other keys raise `TypeError`. No inherited interception applies.

Builtin `repr`, `==`, `!=`, and `hash` on records enter these hooks.
Defaults generate repr, structural equality and structural hashing as
specified in [records](records.md). Equality implementations should return
`NotImplemented` for unsupported other types. A repr implementation returns a
string; a hash implementation returns an integer, as Python requires.
Keep equal records' hashes equal. The substitutability test is independent
of user equality hooks. Default `grail.repr` also accepts non-record objects;
default `grail.eq` and `grail.hash` require a record first argument.

## Arithmetic

| Generic | Python syntax | Dispatch key |
| --- | --- | --- |
| `ops.add(a, b)` | `a + b` | `(type(a), type(b))` |
| `ops.sub(a, b)` | `a - b` | exact pair |
| `ops.mul(a, b)` | `a * b` | exact pair |
| `ops.truediv(a, b)` | `a / b` | exact pair |
| `ops.neg(obj)` | `-obj` | exact record type |
| `ops.abs(obj)` | `abs(obj)` | exact record type |

Use two positional operands for direct binary calls. On a miss the fallback
is the matching `operator` function and normal Python slot behavior applies.
Binary registration needs a two-type tuple, or two annotated dispatch
parameters with `@ops.add.impl`. Duplicate registrations require explicit
`override`. There is no MRO resolution, commutative inference, or comparison
operator registry. Register reversed pairs separately.

Pairs of two protected builtin types, including pairs that normally fail,
cannot be registered or overridden (`TypeError`). Mixed user-defined and
builtin pairs are allowed. Unary registrations require record types and
implementations callable with one positional argument.

On the prototype, a registered binary pair runs before Python's forward,
reflected, or in-place slots. Its result is final, including `NotImplemented`.
The compatibility implementation has narrower syntax integration and cannot
preserve that final-result rule. Read [backend differences](../backends.md)
before registering non-record pairs or using `NotImplemented`.

Register unary operations before using them on records. An unregistered
record unary operation raises `TypeError` in compatibility mode; the native
fallback currently recurses. Errors from implementations propagate.

`record(deriving="+-*/")` registers element-wise operations for an all-`float`
record: `R + R`, `R - R`, `R * float`, `R * int`, `float * R`, `int * R`,
`R / float`, and `R / int`, for the requested symbols only. No reverse division
or implicit subclass matches exist. Integer scalar conversion may raise
`OverflowError`; division by zero raises `ZeroDivisionError`. Field annotations
do not coerce inputs: wrong field values still follow ordinary Python arithmetic.
Derived registrations can be explicitly overridden through the same generics.

`ops` is a named tuple of the six generics. Its inherited `count(value)` and
`index(value, start=0, stop=...)` are tuple utilities, not arithmetic protocols.
They count or locate a generic object; `index` raises `ValueError` if absent.

Guides: [protocol hooks](../guide/protocol-hooks.md),
[operators](../guide/operators.md).
