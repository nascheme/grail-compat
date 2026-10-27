# PEP draft: Records

Oct 27, 2026 · @Neil Schemenauer

*Status: proposed language semantics. The free-threaded CPython prototype implements a documented subset; see [prototype status](06-cpython-prototype.md). Stable ABI, racy publication, unboxing, and automatic substitution are not implemented promises.*

## Abstract

This PEP adds record types: closed, immutable data types created with a `@grail.record` class decorator (the module name is a working name). A record has a fixed set of fields, no instance `__dict__`, no subclassing, no methods and an immutable type object, and it never changes after construction. Fields annotated with value types are value fields; annotations are not enforced at construction, though the runtime may inspect values to establish conformance. An instance whose value fields hold values of their declared types is a value record, which the VM may copy, intern and scalar-replace; any other instance behaves like a record with ordinary fields. After safe publication, record field bindings can be read across threads without per-field synchronization; mutable children retain their own synchronization requirements. Behaviour for records is provided by generic functions, specified in the companion PEP, and this PEP must be approved together with it. No new syntax is added; the `record` statement is a separate, later PEP.

## Motivation

Python has many ways to declare data (`namedtuple`, frozen `dataclass`, attrs, Pydantic models), but none gives the VM guarantees it can rely on. A frozen dataclass can be subclassed, its class can be mutated, `object.__setattr__` bypasses its freezing, its fields may hold anything, and its `__eq__` may be overridden arbitrarily. The optimizer must validate mutable layout/behavior assumptions, and copying or eliminating an instance generally requires additional escape and identity-use analysis. CPython already specializes ordinary slotted reads; records simplify the proof rather than invent fixed-offset access.

The cost grows with free threading. Any object may be mutated by another thread, so a reader needs either synchronization or a proof that nobody writes. Programmers have no language-level way to say "this never changes", and no way to see at a glance which state is shared and mutable.

Records give both the VM and the programmer that statement. A record type is closed and immutable, so field access is a load at a fixed offset, and dispatch on a record can be resolved from its exact type. A value record holds only immutable values all the way down, so the VM may treat it as a value rather than an object with an identity.

## Rationale

- **Frozen-dataclass behaviour by default.** A field annotated with a value type is a value field; any other field, annotated with another type or not at all, behaves like a frozen dataclass field. Nothing new is needed for scripts, and the VM can tell value records from their annotations.
- **Annotations are not enforced.** Construction never raises because a value field holds the wrong type; that instance is simply not a value record, and the VM must not apply value-only optimizations to it. Layout-only optimizations, such as reading its immutable field bindings, remain valid. Deciding at run time what counts as a type error (is an `int` a `float`? how deeply is `tuple[int, ...]` checked? what about protocols, type variables and forward references?) opens a large can of complexity. Static type checkers already report these mistakes, and the VM only needs to know whether it can optimize an instance.
- **Identity is not a promise.** As Python already does for `int` and `str`, whether two separately produced records are the same object is implementation-defined. This is the single permission that unlocks copying, interning and scalar replacement; `is` and `id()` keep working.
- **Substitutability, not a new equality.** The VM's freedom is defined by a specification relation stricter than `==`, so user-overridable `==` never constrains the optimizer.
- **No methods.** Behaviour lives in generic functions in module namespaces, which any package can extend. This is a deliberate design choice, not an optimization requirement: methods on a closed type would optimize just as well. It is why this PEP and the Generic functions PEP must be approved together; without generic functions a record could not have a custom `repr`, `==` or operators. If the Generic functions PEP were not accepted, this PEP would be revised to work without it.
- **A decorator, not syntax.** The VM optimizes the type the decorator produces; type checkers understand it today through `dataclass_transform` (PEP 681). Syntax is a convenience proposed separately.
- **Precise concurrency guarantees.** Records are specified against a small memory model of their own (safe publication, no mutation), with a reference-transfer protocol that must be aligned with CPython's general memory and lifetime model.

## Specification

### Record types

```python
@grail.record
class Point:
    x: float
    y: float = 0.0

Point(1.0)            # Point(x=1.0, y=0.0), a value record
Point("a")            # allowed; this instance is not a value record
p = Point(1.0)
p.x = 2.0             # error: record fields are never rebound
```

The decorator builds a new type, as `dataclass(slots=True)` does, and enforces:

- a fixed set of fields, no instance `__dict__`, no undeclared attributes;
- no base classes other than `object`, and no subclassing of the record type;
- an immutable type object (the C implementation sets the immutable-type flag);
- no methods, `__new__`, `__init__` or `__del__` in the class body: a function in the body is a definition-time error; behaviour is registered with generic functions;
- a generated constructor taking positional and keyword arguments in field declaration order;
- `__match_args__` generated from field order, so class patterns work;
- not weak-referenceable;
- `grail.replace(record, **changes)` constructs an updated record; the generated `__replace__` hook also supports `copy.replace()`.

### Fields

| Annotation | Kind | Substitutability | `==` and hash |
| --- | --- | --- | --- |
| A value type | Value field | By representation | Structural |
| Any other type, or none | Ordinary field | Identity | The field's own `==` and `hash()`, as in a frozen dataclass |

A record type whose fields are all value fields is a value record type; `grail.is_value_record(R)` reports it. A record with any ordinary field is shallowly immutable: its bindings never change, but an object in an ordinary field may mutate. Editing an annotation can therefore turn a value record into an ordinary one silently; linters should explain why a record is not a value record. Ordinary fields are not converted or wrapped: an `Atom[int]` field takes an atom.

Construction does not enforce value annotations or reject mismatches. It may inspect fields to record conformance. An instance *conforms* when every value field holds a value of its declared type, and a conforming instance of a value record type is a value record. A non-conforming instance is still a correct record: it behaves exactly as if its value fields were ordinary fields, and the permissions this PEP grants for value records (substitution on copy, implementation-defined identity, leaving them untracked by the garbage collector, per-thread copies) do not apply to it. Conformance is defined only for the types the VM knows how to optimize; an implementation may treat anything it does not recognize, such as an `int` in a `float` field, as non-conforming, since the permissions are never requirements. Whether the VM tracks conformance per instance or per type is left to the implementation (see Open issues). CPython does not warn about non-conforming instances; reporting them is the job of static type checkers.

### Value types

The initial value types are: `None`, `bool`, `int`, `float`, `complex`, `str`, `bytes`; `frozenset` whose declared element type is a value type; value records; unions whose members are all value types (`Point | None`, `int | str`); tuples whose declared element types are value types; and, once they exist, the persistent map, set and vector (with value-typed elements) from the `grail` package. `int`, `float` and `complex` keep Python semantics; an implementation may unbox them invisibly. Recursive value records are allowed; the runtime may store an internal pointer, which has no observable effect.

### Defaults

The class body evaluates each default once, when the record is defined. A mutable default (such as `{}` or `Atom(0)`) is a `ValueError` at definition time; use `grail.field(factory=...)` to get a fresh object per construction. Like `dataclass`, the current prototype approximates mutable-default detection by rejecting unhashable defaults; it does not prove arbitrary objects immutable. A value default with side effects, such as `time.time()`, is evaluated once; linters should flag call expressions in defaults. The Record syntax PEP evaluates default expressions at each construction instead.

### Identity

Records support `is` and `id()`, but whether two separately produced substitutable records are the same object is implementation-defined, as it already is for `int` and `str`:

```python
p1 = Point(1, 2)
p2 = Point(1, 2)
p1 is p2       # implementation-defined
p1 == p2       # True
p1 is p1       # always True; id(p1) is stable while p1 is alive
```

A given reference always denotes the same object. The VM may intern, deduplicate or rematerialize records. Linters can warn on `is` between records, as CPython already warns on `is` with a literal.

### Substitutability

The VM's permissions are defined by substitutability, a relation in this specification, not a public function. Two records are substitutable when they have the same exact type and their fields are pairwise substitutable:

- a value-record field: recursively substitutable;
- a scalar value field: the same value representation, so for `float` the same bits (`-0.0` and `0.0` differ; NaNs with the same payload match);
- a tuple or other value container: element-wise, by its declared element types;
- an ordinary field: the same object (`is`).

Substitutability is stricter than `==`: `0.0 == -0.0`, but `str()`, `math.copysign` and `1/x` tell them apart. The `grail` package provides `grail.substitutable(a, b)` for tests and debugging.

### Equality

Records get structural `==`: value fields by value, ordinary fields with their own `==`. Because identity is implementation-defined, `==` must not depend on it: each field is first tested for substitutability, then compared with `==`. A record holding a NaN therefore equals a copy of itself. The converse need not hold: `Point(0.0, 0) == Point(-0.0, 0)` but they are not substitutable. Two record types with identical fields are never `==` to each other by default.

A value record's `==` may be overridden by registering an implementation with the generic `==` hook (Generic functions PEP). An override must return True for substitutable records, since the VM may replace one with the other, and must come with a matching hash.

### Hashing

Value records hash structurally. Records with ordinary fields hash like frozen dataclasses, calling `hash()` on each field, so an unhashable field such as a dict makes hashing the record raise `TypeError`. The implementation may cache a record's hash.

### Copying and pickling

`grail.replace(obj, /, **changes)` is the everyday record-update API. It returns a record of the same type with the named fields replaced, sharing unchanged children and leaving the original record unchanged. It does not replay defaults or factories, and it recomputes conformance for the result. Unknown fields, non-record arguments, or extra positional arguments raise `TypeError`. The first argument is positional-only so a field named `obj` can be replaced. This is a Grail-record operation, not a general replacement protocol dispatcher.

```python
p = Point(1.0, 2.0)
q = grail.replace(p, y=3.0)  # Point(x=1.0, y=3.0); p is unchanged
```

Records retain the generated `__replace__` hook for interoperability with Python's `copy.replace` (3.13+). Grail code should use `grail.replace`, available on every supported Python version, rather than call the dunder directly. As with construction, a replacement is not a promise of distinct identity.

`copy.copy`, `copy.deepcopy` and pickle may gain or lose aliasing, provided every object in the result is substitutable for the object an alias-preserving copy would have put there. This is a permission, not a requirement.

- **Value records:** `deepcopy(r)` may return `r`; two references to one record may come back as two objects; two substitutable records may come back as one.
- **Records with ordinary fields:** the record may be substituted, but the objects in its ordinary fields keep normal aliasing.
- **Atoms and ordinary objects:** unchanged; copying an atom copies its current value into a new atom.

Python already behaves this way for immutable builtins: `deepcopy` returns the same object for `int`, `str` and tuples of immutables, and pickle does not memoize `int` or `float`. Cycles through mutable descendants use ordinary memoization. The prototype reconstructs immutable records tuple-style without exposing partial records; both record-root and mutable-root cycles are tested. Even when value-field edges are acyclic, heap-type and metadata edges may form GC cycles.

### Concurrency guarantees

These hold in both the GIL and free-threaded builds:

1. **Safe publication.** Transfer through supported synchronizing operations establishes that the receiving thread sees every field initialized and acquires a valid reference. A stronger Java-like final-field guarantee for unsynchronized transfer is an open issue, not supplied by a constructor release fence alone. The reference store, receiver acquisition, and lifetime protocol must all be specified.
2. **Immutability.** A published record's field bindings never change, so loading a field pointer needs no per-record lock or mutation recheck. Acquiring ownership of the child may still require reference-count operations; mutable children are not made immutable.
3. **Immutable type objects.** A record type's attributes never change, so specialized code needs no invalidation while it runs.
4. **Conditional hash caching.** Caching requires proof or a contract that fields and overrides produce a stable hash; arbitrary ordinary-field hash callbacks do not provide that automatically. Where caching is valid, concurrent first computations must agree and publish atomically. The prototype caches the default hash of conforming value records, invalidated by any `grail.hash` registration; registered hooks are trusted to be pure functions of the value.
5. **Ordinary fields promise nothing new.** An object in an ordinary field has exactly the thread-safety CPython gives that object. A mutable object in an ordinary field of a record shared between threads is the place where races remain; the safe alternative is an atom holding an immutable value.

An implementation may omit cyclic-GC tracking only after accounting for all owned edges, including the heap type, metadata and defaults, not just the value-field graph. The prototype currently tracks every record. Because identity is implementation-defined, an implementation may also give each thread its own copy of a heavily shared value record to avoid reference-count contention.

### Introspection

Records add no magic attributes for tooling. Instead:

- `grail.record_fields(R)` returns field metadata for a record type: names, annotations, kinds, defaults;
- `grail.record_values(r)` returns an ordered snapshot mapping, not a live view;
- `grail.is_value_record(R)` reports whether every field is a value field.

## Backwards compatibility

Nothing existing changes: the PEP adds a module and a decorator, no syntax and no change to classes, dataclasses or builtins. Code that relies on `is` between records, or on `deepcopy` preserving aliasing of records, gets implementation-defined results; this is new behaviour for new types only.

## How to teach this

A record is a frozen dataclass that cannot be subclassed and has no methods. Annotate fields with value types to get a value record. Write behaviour as generic functions. Compare records with `==`, never `is`. Use `grail.replace(r, field=value)` to produce an updated record. When state must change, hold it in an atom, not in a mutable object inside a record.

## Reference implementation

`cpython-grail` is the current implementation and testing target. The native fork (free-threaded, also built with the GIL for JIT experiments) implements boxed records, per-instance conformance, native construction and replacement, copy/pickle, and interpreter and JIT specializations of record operations. `LOAD_ATTR_RECORD` shows a small isolated benefit, not a general application speedup. Unboxed fields, automatic substitution, and the full identity-use audit remain outstanding. See [prototype status](06-cpython-prototype.md) for measured results and limits.

## Rejected ideas

| Idea | Why rejected |
| --- | --- |
| Redefine `is` for value records and make `id()` raise | Breaks too much existing code; implementation-defined identity, as for `int` and `str`, gives the VM the same freedom |
| A public `eql(a, b)` function (from Common Lisp) | The rules here need only a specification relation; Python's `int`/`str` precedent shows users manage with `==`; a function can be added later, not removed |
| Using `==` as the substitutability relation | `==` is user-overridable, `0.0 == -0.0`, and `nan != nan`; the VM needs representation equality |
| Methods, or only protocol dunders, in the record body | Behaviour belongs in generic functions; single dispatch is the intended way to write programs. Would be reconsidered only if the Generic functions PEP were not accepted |
| `ref` and `var` field keywords | The kind of a field is better stated by its type; keywords added syntax for a distinction annotations already make |
| A required `Shared[T]` marker on non-value fields | Turned an accidental `dict` annotation into a definition-time error, but added a concept; frozen-dataclass behaviour plus `is_value_record` and lint rules is simpler |
| Mutable records, or rebindable `var` fields | Changing state belongs in atoms, following Clojure; quick scripts use globals or classes |
| Automatic wrapping of a plain argument into an `Atom` field, or a generic `field_init` hook | Hidden conversion; `= Atom(0)` defaults and factory functions are explicit. Could be added later without breaking code |
| Defaults that refer to earlier fields (`let*`-style) | Ordering and scoping complexity; derived values belong in a factory function |
| `deepcopy` and pickle must preserve record aliasing | Blocks interning and scalar replacement; the substitutability rule is enough |
| Check value fields at construction and raise TypeError, or warn at run time | Defining a run-time type error (int for float, nested generics, protocols, forward references) is a large source of complexity, and Python does not enforce annotations elsewhere. Static type checkers report the mistake; the VM only needs to know whether it can optimize an instance |

## Open issues

- **Identity-use audit.** Classify every CPython site that relies on pointer identity as physical-identity-required, substitutability candidate or semantically delicate, and measure what replacing the candidates unlocks.
- **Value-type whitelist.** Is it large enough for real programs? Which existing immutable types qualify, and by what rule? Exact representation rules for `str` and `int`, NaN payloads and tuple element declarations.
- **Tracking conformance and layout.** The prototype uses one public heap type and a per-instance conformance bit, with boxed fields for every instance. This avoids making `Py_TYPE`, `type(r)`, patterns, C extensions and generic dispatch disagree. Unboxed layouts may later need a private per-instance discriminator and corresponding guards. Hidden alternate types are not the baseline and would require a separate identity/C API compatibility audit. The specification should not freeze an internal representation.
- **Stronger publication.** Whether a Java-like final-field rule can cover unsynchronized reference transfer safely, and at what cost. A release fence alone does not establish receiver synchronization or prevent use-after-free.
- **Construction-time checking needs more thought.** Not checking matches `dataclasses`, which never enforces annotations. Other libraries do check: pydantic validates on construction (coercing by default, with an optional strict mode that rejects mismatches), and attrs runs opt-in per-field validators such as `instance_of`. An optional strict mode, for example `@grail.record(strict=True)`, could check value fields and raise at creation time for programs that want it, leaving the default unchecked. Open questions: exactly what strict mode checks (`int` for `float`, depth of nested generics), whether it belongs in the core PEP or a later one, and whether a strict record is guaranteed to be a value record. In the current boxed prototype, conformance inspection already runs during construction; a strict option could reject a mismatch instead of recording it. That does not establish the cost or representation of a future strict/unboxed implementation. Its case has to rest on catching bugs early, which also makes it a candidate for a later PEP or a library.
- **Overriding `==`.** Whether the substitutable-implies-equal rule is checked at runtime, what happens when `==` is overridden without a hash, and whether overriding is allowed for records with ordinary fields.
- **Recursive records.** Layout strategy for recursive value records and future recursive sum types.
- **Hash caching.** Whether to cache beyond value records, and whether hash hooks must be declared pure.
- **Reference-count contention.** Profile shared-scene reads in the native prototype, now that dispatch locks are gone. The overview proposes avoiding shared-refcount writes as a quality-of-implementation goal. The record permissions allow candidate mechanisms such as deferred counts or per-thread copies; their benefit and lifetime costs remain to be measured.
- **Performance commitments for `match` and `grail.replace`.** These are the idioms this PEP teaches. Profile class patterns and the outer `grail.replace` call, as well as existing `copy.replace` calls in native deltablue and richards, and specialize them where the measured cost justifies it. The native replacement helper already exists; its cost must not be confused with library-call overhead. Any fast path must preserve constructor, conformance and lifetime semantics.
- **Names.** The module (`grail` is a working name), `record_fields`, `record_values`, `is_value_record`, `substitutable`.
- **Validation libraries.** Whether Pydantic and similar libraries can consume and produce records through introspection without inheritance.
- **Buffers and arrays.** Interaction between value-record layout and NumPy, Arrow and the buffer protocol; do not freeze a public C layout early.
