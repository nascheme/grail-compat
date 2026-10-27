---
sources:
  - grail-compat/docs/design/01-pep-draft-records.md
  - grail-compat/src/grail/__init__.py
  - grail-compat/src/grail/_core.py
  - grail-compat/tests/test_grail.py
---

# Equality and hashing

Record equality compares fields, not object identity. Substitutability is
stricter: it asks whether records have the same observable field representations.

    >>> from grail import record, substitutable
    >>> @record
    ... class Reading:
    ...     value: float
    >>> Reading(0.0) == Reading(-0.0)
    True
    >>> substitutable(Reading(0.0), Reading(-0.0))
    False

Default equality requires the same exact record type. Each field first tests
substitutability, then its own equality. Ordinary fields therefore compare
with their object's `==`, even though they substitute only by identity.

Conforming float fields with the same NaN bits compare equal and hash equally.
This differs from a bare float NaN, and from numeric vector NaNs.

    >>> a, b = Reading(float("nan")), Reading(float("nan"))
    >>> a == b, hash(a) == hash(b)
    (True, True)

Hashing is structural. An unhashable ordinary child makes the record
unhashable in use. A mutable hash-relevant child can invalidate dictionary
keys; do not mutate it while the record is used as a key.

Use [protocol hooks](protocol-hooks.md) for custom equality and hashing.
Always register a matching hash when changing equality. Substitutability
does not call these hooks.

[Reference](../reference/records.md).
