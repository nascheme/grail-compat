---
sources:
  - docs/design/01-pep-draft-records.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Records reference

Use records for fixed data and the introspection functions to inspect it.
Annotations describe value fields; they do not validate constructor arguments.

    >>> from grail import record, record_values
    >>> @record
    ... class Point:
    ...     x: float
    ...     y: float = 0.0
    >>> record_values(Point(1.0))
    {'x': 1.0, 'y': 0.0}

## Declaration and fields

`record(cls=None, /, *, deriving=())` returns a new record type, or a decorator
when `cls` is omitted. Both `@record` and `@record()` work. The source class
needs the standard `type` metaclass and only `object` as a base.

The body accepts a docstring, annotations, and field defaults. Methods,
properties, descriptors, callable defaults, and user-defined dunder names
raise `TypeError`. Unannotated assignments are ordinary fields. Annotated
fields come first, in annotation order, followed by unannotated fields in
namespace order. Field names are identifiers and cannot start with `__`.

`field(*, default=<missing>, factory=<missing>)` supplies a default or a
zero-argument factory. Supplying both, or a non-callable factory, raises
`TypeError`. Omitting both creates a required field. Defaults whose type has
`__hash__ = None` raise `ValueError` at record definition. This is an
approximation, not a proof of immutability. Factories run in field order for
omitted arguments only; their exceptions propagate. Default expressions run
once at definition time.

The constructor accepts positional and keyword fields. Missing required
fields, unknown keywords, duplicate values, or too many positional arguments
raise `TypeError`. Required fields may follow fields with defaults. No
conversion or annotation-based rejection takes place.

`deriving` is a sequence of `+`, `-`, `*`, `/`, such as `"+-*/"`. Unknown or
duplicate operators raise `ValueError`; a field not annotated exactly `float`
raises `TypeError` when deriving is requested. See [operators](hooks-and-ops.md).

## Value fields and conformance

Recognized value annotations are `None`, `type(None)`, `bool`, `int`, `float`,
`str`, `bytes`, already-defined value record types, unions entirely of value
types, fixed tuples of value types, `tuple[T, ...]`, and `tuple[()]`.
Bare `tuple`, `object`, `complex`, `frozenset`, collections, and unresolved
forward references are ordinary fields today. An unresolved annotation can
make the declaration's annotation processing conservative; define referenced
types first. Plans are fixed when the record type is created.

Conformance uses exact scalar types: an `int` in a `float` field and a `bool`
in an `int` field do not conform. Tuple lengths and elements and nested
record conformance are checked recursively. Mismatches are stored unchanged.
If either of two records does not conform, their fields use ordinary-field
rules for substitutability and equality.

## Introspection

| Call | Result |
| --- | --- |
| `record_fields(record_type)` | Tuple of `FieldInfo` entries in field order |
| `record_values(obj)` | Fresh `dict` of field names to current references, in field order |
| `is_value_record(record_type)` | `bool`: every declared field has a value plan; true for an empty record |
| `substitutable(a, b, /)` | `bool`: same exact record type and substitutable fields |

`FieldInfo` is a named tuple with `name`, `annotation`, `kind`, `default`,
and `factory`. `kind` is `"value"` or `"ordinary"`. Absent annotation,
default, or factory entries use an internal missing sentinel; there is no
public sentinel API. Do not interpret `None` as missing. `FieldInfo` is
available as `grail.FieldInfo`, but is not in `grail.__all__`.

The first and third functions require a record **type**. The second requires
a record **instance**. `substitutable` requires two record instances; different
record types return false. Invalid inputs raise `TypeError`.
`is_value_record` does not report instance conformance. There is no public
instance-conformance query.

## Generated behavior

Field descriptors are read-only. Setting or deleting a field or adding an
instance attribute raises `AttributeError`. Instances have no `__dict__` and
cannot be weak-referenced (`TypeError`). Record types are immutable and final:
type changes or subclassing raise `TypeError`, subject to the compatibility
hardening limitation in [backends](../backends.md).

`__module__`, `__qualname__`, and `__doc__` preserve source metadata.
`__match_args__` is the tuple of field names. Class patterns support positional
and keyword fields; too many positional patterns or repeated fields raise
`TypeError`. Records do not become sequences or mappings.

Generated `repr` is `Type(field=value, ...)`, with a recursion guard.
Default `==` returns `NotImplemented` for another exact type. For the same
type, each field first tests substitutability, then its own `==`.
Ordinary fields substitute by identity. Value fields substitute recursively;
floats compare bits, including the sign of zero and NaN payloads.
Hashing is structural and raises `TypeError` for unhashable children.
Equal conforming NaN representations have equal hashes. The native runtime
caches the default hash of a value record (the compatibility package does
not), so a hash hook must be a pure function of the value. Ordering is not
supplied. Hooks can replace repr, equality and hashing; keep equality and
hashing consistent.

## Replacement and copying

`replace(obj, /, **changes)` returns a Grail record of the same type with the
supplied fields replaced. Import it from `grail`; it works on all supported
Python versions. Unchanged children are shared, defaults and factories are
not replayed, and conformance is recomputed. The original record is unchanged.
No changes gives an equivalent record; do not rely on distinct identity.

A non-record first argument, unknown fields, or additional positional
arguments raise `TypeError`. The positional-only `obj` parameter lets you
replace a field named `obj`. This function is for Grail records, not arbitrary
objects that implement a replacement protocol.

Records retain `__replace__(**changes)` as the interoperability hook used by
`copy.replace` on Python 3.13+. Prefer `grail.replace` over direct dunder calls
in Grail code.

`copy.copy` returns the record itself. `copy.deepcopy` returns a
conforming instance of a value record type unchanged; otherwise it copies
children with the normal memo protocol and reconstructs the record.
Pickle reconstructs through the public record type and all field values;
the type must be importable under its module and qualified name. Do not rely
on independently constructed records having distinct identities.

Guides: [records](../guide/records.md), [defaults](../guide/defaults.md),
[value records](../guide/value-records.md), [equality](../guide/equality-and-hashing.md),
[copying](../guide/copying.md), [introspection](../guide/introspection.md).
