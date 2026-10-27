# PEP draft: C API

Oct 25, 2026 · @Neil Schemenauer

*Status: an unimplemented API sketch. Names, ownership rules, signatures and ABI policy are provisional. The prototype exposes private helpers, not this public API; do not build extensions against these examples as a stable interface.*

## Abstract

This PEP adds a C API for Grail: extension modules can define record types, create records, read their fields, and define and extend generic functions, including operators. The API hides every layout and implementation detail behind opaque types, field handles and functions, returns only strong references, constructs records through builders so a half-built record is never visible, and generates the garbage-collector and deallocation code that extension authors write by hand today. An eventual stable ABI, including free-threaded support, is a design goal, not a promise for the experimental prototype. It depends on the Records and Generic functions PEPs and is proposed after their Python-level semantics settle.

## Motivation

Extensions are where Python's performance-critical data lives: NumPy, Arrow, database drivers, parsers, serializers. If records and generic functions exist only at the Python level, extensions keep defining classes by hand, and Grail's guarantees stop at the extension boundary.

The existing C API is also the main reason CPython is hard to change. It exposes struct layouts (`ob_refcnt`, `PyListObject.ob_item`), macros that read those fields directly, borrowed references, and slot semantics that froze implementation choices for decades. Free threading, a faster object layout and a JIT have all been slowed by what extensions are allowed to touch; PEP 733 catalogues these problems, and HPy shows a design that avoids them. Grail's types are new, so their C API can be designed right from the start, and it must be, because exposing a record's layout would forbid exactly the optimizations records exist to allow: unboxing, flattening, copying and scalar replacement.

## Design principles

| Mistake in today's C API | Grail rule |
| --- | --- |
| Struct layouts are public, and macros read fields at fixed offsets | All Grail types are opaque. Fields are reached through field handles resolved once (as JNI field IDs are), never through offsets |
| Borrowed references are easy to misuse and block changes to object lifetime | Every function returns a strong reference; there are no borrowed references |
| Objects can be observed half-initialized, then mutated in place | Records are built with a builder and published once, finished; there is no API to change a record after that |
| Extension authors hand-write `tp_traverse`, `tp_clear` and `tp_dealloc` | Layout is declared, so the runtime generates GC support and deallocation |
| Static types and mutable type objects | Record types are created from a declarative spec as immutable heap types, per module, compatible with multi-phase initialization (PEP 489) and subinterpreters |
| The GIL is assumed implicitly | Every function's thread-safety is specified, and the API works the same in the free-threaded build |
| Fast paths require the unstable API | Aim for one opaque API suitable for eventual stable-ABI inclusion; validate it experimentally before freezing signatures |
| Misuse fails silently or crashes far from the bug | A debug mode, as in HPy, checks handles, builders and reference ownership |

## Specification

All names are working names. Object-returning functions return strong references or NULL with an exception set. Status-returning functions use 0/-1 unless specified otherwise; predicates need a defined error convention. Opaque field handles and builders require explicit ownership/release rules before implementation. Setters do not steal Python object references; `Finish` consumes its builder, which is a separate lifetime rule.

### Defining record types

A record type is declared, not laid out. The spec names each field and its annotation; the runtime chooses the layout and generates GC support and deallocation.

```c
static PyRecordField_Spec point_fields[] = {
    {"x", "float"},                       /* value field */
    {"y", "float", .default_value = "0.0"},
    {NULL}
};

static PyRecord_Spec point_spec = {
    .name = "geometry.Point",
    .fields = point_fields,
};

/* in the module's exec slot (PEP 489): */
PyObject *Point = PyRecord_NewType(module, &point_spec);
```

The result is an immutable heap type owned by the module, with the same rules as `@grail.record`: no methods, no subclassing, no finalizer. Annotations may be given as strings or as type objects; how a value type is named from C is an open issue.

### Creating records

Records are built with a builder and published once, finished. A record that is still being built is never visible to Python code or to other threads.

```c
PyRecordField *fx = PyRecord_GetFieldHandle(Point, "x");   /* resolve once */
PyRecordField *fy = PyRecord_GetFieldHandle(Point, "y");

PyRecordBuilder *b = PyRecordBuilder_New(Point);
PyRecordBuilder_SetDouble(b, fx, 1.5);
PyRecordBuilder_Set(b, fy, some_float);       /* any object; not stolen */
PyObject *p = PyRecordBuilder_Finish(b);      /* fills defaults, never type-checks,
                                                 publishes; consumes b */
```

- `PyRecordBuilder_Finish` evaluates missing defaults in declaration order, notes whether the value fields conform to their declared types (it never raises for a mismatch), and returns a fully initialized record for transfer through supported synchronizing operations. On error it returns NULL, sets an exception and frees the builder.
- `PyRecordBuilder_FromRecord(r)` starts a builder from an existing record, the C equivalent of `grail.replace`.
- `PyRecord_New(type, args, nargs, kwnames)` calls the generated constructor with vectorcall conventions for the simple case.
- Typed setters (`SetInt64`, `SetDouble`, `SetBool`) let the runtime store unboxed values when it can, without the extension knowing whether it does.

### Reading fields

- `PyRecord_GetFieldHandle(type, name)` resolves a field once. The final API must specify whether a handle owns the type, how it is released, and how incompatible record/handle pairs are rejected; a raw offset or dangling type pointer is not an acceptable handle.
- `PyRecord_GetField(record, field)` returns a strong reference to the field's value.
- Typed getters (`PyRecord_GetInt64(record, field, &out)`, `GetDouble`, `GetBool`) return 0 or -1, and fail if the value is not representable.
- `PyRecord_GetFields(record, fields, n, out)` reads several fields in one call, for hot loops.
- `PyRecord_Check(obj)`, `PyRecordType_Check(type)` and `PyRecordType_IsValueRecord(type)` test kinds.

There is no setter after construction, and no function that returns a field's offset or a pointer into a record.

### Generic functions

```c
/* define a generic with a fallback */
PyObject *area = PyGeneric_New(module, "area", fallback_func);

/* register a C implementation (METH_FASTCALL) for a record type */
PyGeneric_Register(area, Circle, circle_area_func);
PyGeneric_Override(area, Circle, better_circle_area_func);

/* extend a generic defined elsewhere, e.g. the record repr hook */
PyObject *repr_hook = PyGeneric_Import("grail", "repr");
PyGeneric_Register(repr_hook, Point, point_repr_func);
```

- A generic function is callable with `PyObject_Vectorcall`, like any callable.
- `PyGeneric_Register` fails if an exact registration exists; `PyGeneric_Override` replaces it. Both have the Python-level semantics, including the version bump.
- C code that calls a generic in a hot loop can cache dispatch without seeing the table: `PyGeneric_Lookup(gen, type, &token)` returns the implementation and a version token, and `PyGeneric_IsCurrent(gen, token)` would check validity. For inherited/ABC lookup, registry version alone is insufficient: a token must cover relevant type/MRO/metaclass and ABC invalidation dependencies too. An exact-only token API is an alternative. Check/use races and implementation lifetime must be specified before this can be a safe public cache API.

### Operators

`PyGenericOp_Register(Py_GRAIL_OP_ADD, Vec, Vec, vec_add_func)` registers an exact-pair operator implementation, with the same lookup order as at Python level. The initial prototype supports add/subtract/multiply/true divide and their augmented forms. Comparison identifiers and their ordering/identity/fallback contracts remain proposed, not implemented.

### Atoms and persistent collections

Atoms and the persistent collections ship in the `grail` package first. Their C API follows the same rules, read functions plus builders, and is specified when they are proposed: for example `PyAtom_Get`, `PyAtom_CompareAndSet`, and a `PyPMap` builder that is the C form of a transient.

### Threads and the memory model

The C API carries the same guarantees as the Python-level PEPs, in the GIL and free-threaded builds alike:

| Operation | Thread-safety |
| --- | --- |
| Reading record fields, field handles, type checks | Safe from any thread, concurrently, without locks |
| A record builder | Confined to the thread that created it; the debug mode checks this |
| `PyRecordBuilder_Finish` | Returns a fully initialized owned record; cross-thread transfer must follow a supported publication/acquisition protocol |
| Calling a generic function | Safe concurrently; sees the old or the new dispatch table, never a mix |
| `PyGeneric_Register`, `PyGeneric_Override` | Atomic; visible to subsequent calls ordered after completion by synchronization |

All functions require an attached thread state, as the rest of the C API does.

### ABI

The intended public API consists of functions and opaque types only, with no layout-reading macros. Stable-ABI inclusion is deferred until the Python semantics, handle lifetimes, free-threaded ownership rules and binding-generator experience have settled. No cross-version binary compatibility is promised for private prototype helpers. The fork currently adds a private pointer to the non-limited heap-type layout, so extensions using that layout must be rebuilt against it. Resolving handles once and offering bulk reads should amortize call overhead; performance must be measured rather than assumed.

### Native resources

Records have no finalizers, and copying or substituting a record must never duplicate ownership of a native resource. Extensions that wrap native resources (file handles, C structs, GPU buffers) therefore use ordinary extension types in this version. Whether records may carry an opaque native payload, with a release callback that runs after the record is freed and cannot touch Python fields, is an open issue; such a record would have to keep a stable identity.

## Backwards compatibility

The API is new and changes nothing in the existing C API. Records and generic functions are ordinary objects to existing C code: `PyObject_GetAttr`, `PyObject_Call` and `PyObject_Repr` work on them, at existing-API speed.

## Reference implementation

No implementation of this public API or compatibility header currently ships. The fork's private helpers implement native Python-facing records and generics; they are not a stable extension interface. A compatibility library, analogous in purpose to `pythoncapi-compat` or HPy, is a future experiment. Binding generators (Cython, pybind11, nanobind, PyO3) should exercise the opaque interface before ABI commitments. See [prototype status](06-cpython-prototype.md).

## Rejected ideas

| Idea | Why rejected |
| --- | --- |
| A public `PyRecordObject` struct, or macros that read fields at offsets | Freezes the layout and forbids unboxing, flattening and scalar replacement |
| Borrowed references from getters | Ties the API to object lifetimes the VM wants to change; HPy and PEP 733 identify them as a main source of bugs |
| Setting fields after construction | Records are immutable; a mutable C path would break every guarantee, including safe publication |
| Extension-written `tp_traverse`, `tp_clear`, `tp_dealloc` for records | The runtime knows the layout and can generate them correctly |
| A separate unstable fast API | It becomes the API everyone uses, and the layout is frozen again |

## Open issues

- **Naming value types from C.** Strings, type objects, or a small enum for the builtin value types.
- **Error and ownership model.** Object results versus status/predicate results, field-handle ownership and release, builder cancellation, bulk-output cleanup on partial failure, and invalid handle/type combinations.
- **Publication.** Complete sender/receiver synchronization and lifetime rules; a constructor release fence alone does not validate arbitrary racy reference transfer.
- **Native payloads** in records, and whether such records keep a stable identity.
- **Typed accessor set.** Which unboxed getters and setters to offer, and how they report non-representable values.
- **Bulk operations.** The shape of multi-field reads and builder sets.
- **Relationship to HPy** and to a future stable ABI for the free-threaded build.
- **Debug mode** scope and how it is enabled.
- **APIs for atoms and persistent collections**, once those are proposed.
