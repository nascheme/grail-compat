<!-- Copied from the Project Grail docs/design/ by tools/copy_design_docs.py; do not edit here. -->

# PEP draft: Generic functions

Oct 25, 2026 · @Neil Schemenauer

*Status: proposed semantics and optimization targets. The native prototype implements exact and guarded inherited dispatch with lock-free reads (`41eac0613ad`); no generic call-site PIC exists yet. See [prototype status](06-cpython-prototype.md).*

## Abstract

This PEP adds generic functions: open protocols that dispatch on the type of their first argument, defined and extended with decorators (`@grail.generic`, `@g.impl`, `@g.override`). Any package may extend a generic function with implementations for types it does not own, including builtin types. This openness does not permit replacing protected builtin protocol behavior. Operators get exact-pair dispatch on both operand types. Record types get their `repr`, equality, hashing, ordering and operators through generic functions, so this PEP must be approved together with the Records PEP. A generic function is a C-level object with a versioned dispatch table that the VM can specialize on; no new syntax is added.

## Motivation and rationale

Python puts behaviour in class namespaces. Extending a type you do not own means subclassing, monkeypatching or a registry invented per library (serializers, renderers, adapters, plugin systems). `functools.singledispatch` is the standard library's answer, but it is pure Python, slow, poorly understood by type checkers, and has no operator story.

Generic functions put behaviour in module namespaces, where any package can extend it explicitly, the design of CLOS, Dylan, Julia and Clojure protocols. Single dispatch keeps the model simple: dispatch is on the first argument, and only operators look at two types. Records have no methods, so this is how they get behaviour; it is also how ordinary classes can gain behaviour without subclassing.

No syntax is needed for speed. CPython's specializer works on what a call's target is at runtime, not how it was written. A generic-function object with a version number and a type-to-implementation table lets a call site guard on that version and the argument's exact type and jump to the cached implementation, the same shape the VM already uses for calls to builtins. `generic def` and `impl` syntax may be proposed later as sugar.

## Specification

### Defining a generic function

```python
@grail.generic
def serialize(obj, protocol=5, *, fix_imports=True):
    raise TypeError(f"cannot serialize {type(obj).__name__}")   # fallback body

@serialize.impl
def _(obj: MyType, protocol=5, *, fix_imports=True):
    ...
```

The decorated function's body is the fallback. An implementation must preserve the generic's call shape; the annotation on its first parameter is the dispatch type, and its other parameters follow the generic's signature.

### Registration and override

Any package may register an implementation for a type it does not own, subject to the protected builtin behavior rules below; there is no orphan or coherence rule. This is what Python protocol ecosystems need.

```python
# third_party_plugin.py
@serialize.impl
def _(obj: ForeignType, protocol=5, *, fix_imports=True): ...

@serialize.override              # intentional replacement of an existing impl
def _(obj: ForeignType, protocol=5, *, fix_imports=True): ...

serialize.register(ForeignType, func)    # plain-call equivalents for plugins
serialize.override(ForeignType, func)
```

A duplicate exact registration is an error unless made with `override`. Registration and override bump the generic's version, so dispatch caches invalidate. A generic is never sealed implicitly when imports finish; optional explicit sealing may be investigated if measurements show a payoff.

### Protected builtin behavior

Generic functions distinguish **extending an operation** from **replacing protected builtin behavior**.

A user-defined generic may register implementations for builtin types. For example, a serialization protocol may define an implementation for `int`. Such registrations follow the ordinary duplicate-registration and explicit-override rules.

Grail's hooks for existing Python protocols must preserve the protection provided by builtin types. A registration cannot replace `repr` or `hash` behavior for exact `int` instances, for example. Both registration and explicit `override` raise `TypeError` when they would replace protected behavior; `override` is not an escape hatch.

Protection applies to effective dispatch, not just exact registry entries. An implementation registered for `object` or an ABC cannot intercept a protected builtin operation through inherited dispatch.

These protections apply to the builtin type itself. Subclasses retain Python's existing ability to define their own protocol behavior.

An immutable type object does not by itself imply protected generic behavior. Records deliberately obtain their behavior through generic functions, and their generated defaults remain replaceable as specified below. This restriction neither seals an entire generic nor introduces an orphan rule.

### Dispatch rules

- For records and other closed exact types, an implementation registered for the exact type is the fast path.
- For ordinary classes, normal MRO fallback applies, virtual subclasses registered with `abc.ABC.register()` participate, the most specific applicable registration wins, and genuinely ambiguous unrelated ABC matches raise an error.
- Otherwise the fallback body runs.

This matches `functools.singledispatch` expectations for ordinary classes. `functools.singledispatch` is unchanged; interoperability is an open issue.

### Operators

Operators are the one deliberate exception to single dispatch: an operator implementation dispatches on the exact pair of operand types.

```python
@grail.ops.add.impl
def _(a: Vec, b: Vec) -> Vec: ...

@grail.ops.add.impl
def _(a: Vec, b: float) -> Vec: ...

@grail.ops.add.impl
def _(a: float, b: Vec) -> Vec: ...
```

There is no MRO ranking or two-argument specificity calculation, which avoids the ambiguity of general multiple dispatch. Lookup for `a + b` is: the exact Grail implementation for `(type(a), type(b))`, whose result is final; if no pair is registered, the normal `__add__` / `__radd__` / `NotImplemented` protocol; then `TypeError`. A registered implementation cannot decline: returning `NotImplemented` makes that the value. This lets the interpreter enter the implementation as an ordinary call, with no frame to inspect its result. Comparison operators follow the same principle, with details settled separately because rich comparison differs.

Builtin operator behavior is protected as well. Grail rejects registrations that replace an operator on a pair of protected builtin operand types: for example, `add(int, int)` and `add(int, float)`.

Mixed pairs involving an extensible user-defined type remain open: `add(Vec, int)` and `add(int, Vec)` are permitted. This preserves the ability to introduce numeric types that interoperate with builtins.

An allowed exact-pair registration retains the precedence specified above: it runs before the ordinary `__add__` / `__radd__` protocol and its result is final. Registering a mixed pair therefore explicitly defines that pair's behavior, rather than merely adding a reflected-operation fallback.

Protection covers builtin-pair behavior even when the ordinary Python result is `NotImplemented` or an error; the absence of a successful builtin result does not make a protected pair open for registration.

Native central arithmetic in `cpython-grail` implements exact-pair-first ordering without an import hook. A future compatibility implementation on older interpreters will need to specify which ordering guarantees it can preserve; source rewriting is one possible approach, not an implemented or settled requirement.

The prototype's supported central surface is `+`, `-`, `*`, `/` and augmented equivalents. Exact-pair lookup precedes `__iop__` for augmented operations; a registered result is final, and a miss runs normal Python fallback. Direct dunder/raw-slot calls and explicit `PySequence_*` APIs keep slot/sequence semantics. Comparisons and separate in-place registries remain deferred.

### Record behaviour and builtin protocols

Records have no methods, so their type slots for `repr`, `==`, hashing, ordering and the numeric operators call generic functions. Every record type gets the default implementations (generated `repr`, structural `==` and hash, as the Records PEP specifies); registering an implementation for a record type replaces the default:

```python
@grail.repr.impl                   # hook names are open
def _(p: Point) -> str:
    return f"Point({p.x}, {p.y})"
```

Builtin function objects such as `repr`, `hash` and `len` do not themselves become generic objects. How far builtins such as `iter`, `bool`, `format` and `copy.replace` participate is an open issue.

### Concurrency guarantees

These hold in both the GIL and free-threaded builds:

1. **Atomic registration.** A call that races with a registration or override uses either the old dispatch table or the new one, never a mixture.
2. **Visibility.** A registration that completes before a call starts, in happens-before order, is seen by that call.
3. **Read-mostly cost (implementation target).** Stable exact dispatch should read an atomically published immutable lookup snapshot without acquiring a shared registry lock. Registration may serialize writers. Safe reclamation and selected-callable ownership are part of the protocol, not implied by the atomic load. Cold MRO/ABC resolution can execute arbitrary Python and is not covered by a blanket lock-free progress guarantee.

The prototype implements all three for exact and cached hits. Registration publishes immutable tables with release stores and retires old ones through QSBR. Callers hold the selected callable in a GC-visible stack reference, and implementations get deferred reference counts. Writers still serialize; a cold miss runs the resolver outside all locks. As a quality-of-implementation goal, stable exact selection should also avoid shared refcount writes where lifetime rules permit it. This does not claim that argument binding, Python frame entry, or the implementation body writes no shared memory.

### VM optimization

In the CPython fork the generic-function object is implemented in C. The planned call-site specialization would guard on the generic's version and the argument's exact type and call the cached implementation directly. For records, the exact-type guard is sufficient because a record type is closed and immutable: no subclass can intervene and no attribute can change. For ordinary classes the guard also covers the type's version tag. Registered exact-pair operator calls could use the same approach later. Current operator specialization instead preserves existing builtin fast paths for unregistered exact pairs, using per-operator monotonic pair masks. It does not inline registered implementations.

Call sites must not assume one type. A generic used as a dispatcher over a closed set of kinds (scheduler steps, interpreter nodes, visitors) sees several exact types at one site; richards' `step` sees 4. The fork should use a polymorphic inline cache, about 4-8 entries, with a cheap table lookup on a miss, rather than the monomorphic caches CPython uses for method calls.

## Backwards compatibility

The PEP adds a module and decorators; no syntax changes, and `functools.singledispatch` is unchanged. Operator lookup changes only for operand pairs that have a registered Grail implementation, which no existing code has.

## How to teach this

Write behaviour as functions that dispatch on the type of their first argument, the way `functools.singledispatch` already works, then register implementations for your types, or for types you do not own. Use `override` when you mean to replace someone else's implementation.

## Reference implementation

`cpython-grail` is the current implementation and testing target. The free-threaded fork implements native exact dispatch, a bounded guarded inherited/fallback cache, and central arithmetic. Exact and cached lookups are lock-free; generic call-site specialization remains planned. See [prototype status](06-cpython-prototype.md) for measurements and validation. A new compatibility implementation may follow later; no behavior or performance equivalence is assumed.

## Rejected ideas

| Idea | Why rejected |
| --- | --- |
| `generic def`, `impl` and `override impl` syntax in this PEP | The VM needs a recognizable object, not syntax; decorators keep this a library-plus-VM proposal. Syntax may come later as sugar |
| Full multiple dispatch (CLOS, Julia) | Ambiguity and specificity rules are complex; single dispatch plus exact-pair operators covers the need |
| A Rust-style orphan or coherence rule | Python protocol ecosystems depend on extending types and generics you do not own |
| Sealing generics automatically after import | Breaks plugins and lazy imports; versioned caches make it unnecessary unless measurements say otherwise |
| MRO ranking for operator pairs | Reintroduces multiple-dispatch ambiguity; the existing `__add__`/`__radd__` protocol remains the fallback |
| Making `functools.singledispatch` a thin wrapper | Its semantics and users differ; it stays as it is |
| Protocol dunder methods on records instead of generic hooks | Records have no methods by design; generic functions are the one way to add behaviour |

## Open issues

- **Type checking.** Checkers handle `singledispatch.register` poorly. A typing spec, not per-checker plugins, should let checkers type calls to a generic from its registered implementations.
- **Hook names and semantics** for record `repr`, `==`, hash and ordering, and how far builtins such as `iter`, `bool`, `format` and `copy.replace` participate.
- **Protected builtin surface.** Specify the protected builtin types and protocol hooks, including extension types that provide the same immutability guarantees. Protection must be defined explicitly rather than inferred solely from whether a type's namespace is immutable.
- **Operator surface.** Comparison-operator details remain open. Protecting builtin pairs also lets the interpreter preserve builtin arithmetic fast paths without an extra dispatch guard: in the prototype that guard slowed unchanged numeric code by up to 14% once any operator was registered.
- **Exact-pair-first ordering.** Continue testing native bytecode, `operator` and C API agreement for builtin and subclass combinations, reflected precedence on a miss, and final registered results. A future compatibility implementation needs its own ordering tests and documented limits.
- **Registry behaviour** on module reload, plugin import and unimport, introspection of a generic's registry, and mocking hooks for tests.
- **Cache layout and cost** of dynamic registration and ABC cache invalidation. Exact-read reclamation must handle safepoints, retired callables, callbacks, GC cycles and interpreter shutdown; loading a pointer and then increfing it is not sufficient. The prototype's QSBR scheme is tested for concurrent registration, GC and subinterpreters, but not specifically at interpreter shutdown.
- **Interoperability with `functools.singledispatch`.**
- **Dispatch on a class object** (`type[C]` patterns), useful for factories and deserializers; not required now.
