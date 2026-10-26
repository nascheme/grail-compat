---
sources:
  - grail-compat/benchmarks/README.md
  - grail-compat/benchmarks/raytrace/idiomatic.py
  - bench/notes/raytrace.md
  - grail-compat/benchmarks/raytrace/upstream.py
---

# Ray tracer: data and open operations

The ray tracer separates points, directions, shapes and surfaces into records.
Exact-pair arithmetic expresses geometric rules; generics handle shape and
surface polymorphism. This small version shows why the type distinction matters.

    >>> from grail import record, ops
    >>> @record
    ... class Point:
    ...     x: float
    >>> @record
    ... class Vec:
    ...     x: float
    >>> @ops.sub.impl
    ... def _(a: Point, b: Point):
    ...     return Vec(a.x - b.x)
    >>> Point(4.0) - Point(1.0)
    Vec(x=3.0)

## From methods to operations

| Upstream | Idiomatic |
| --- | --- |
| `Vector` and `Point` methods with runtime type checks | Distinct `Vec` and `Point` records with exact operator pairs |
| Shape methods `intersectionTime` and `normalAt` | Generics `intersection_time(shape, ray)` and `normal_at(shape, p)` |
| Inherited surface `baseColourAt` hook | `base_colour_at(surface, p)` implementations |
| Mutable scene recursion-depth counter | Explicit `depth` argument |

Read `benchmarks/raytrace/upstream.py` beside
`benchmarks/raytrace/idiomatic.py`. Start with Vec/Point, then the two shape
implementations. `make_ray` and `make_halfspace` normalize directions explicitly;
the record constructor itself does not enforce that invariant.

## Sharing the scene

Scene stores immutable tuples of records. A row task reads the scene and
returns bytes. Joining row results in order preserves pixel order regardless
of scheduling. Do not put a mutable depth counter back into the shared scene.

The `Thing.shape` annotation names a closed union. Adding a new shape requires
both generic implementations and an updated union if you want that field to
remain a value field. Open extension and a closed value whitelist are separate
concerns.

The port deliberately retains upstream arithmetic quirks so pixels match.
Do not treat this teaching tracer as a corrected physical renderer. The
parent `bench/notes/raytrace.md` records the comparison and historical estimates;
those estimates are not current performance guarantees.

See [operators](../guide/operators.md), [generic functions](../guide/generic-functions.md).
