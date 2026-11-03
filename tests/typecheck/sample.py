"""Static typing contract: `type: ignore[...]` marks each expected error.

Checked by mypy with --warn-unused-ignores (see docs/contributing.md), so a
stub change that loses an error fails as an unused ignore. This file is not run.
"""

from typing import assert_type

import grail
from grail import field, finish, generic, mutate, record, replace


@record
class Point:
    x: float
    y: float = 0.0
    tags: tuple[str, ...] = field(default=())


@record(deriving="+-")
class Vec:
    x: float


p = Point(1.0)
assert_type(p, Point)
assert_type(replace(p, x=2.0), Point)
assert_type(Vec(1.0), Vec)
replace(p, z=2.0)  # type: ignore[call-arg]
replace(p, x="no")  # type: ignore[arg-type]
Point("no")  # type: ignore[arg-type]
p.x = 2.0  # type: ignore[misc]
replace(3, x=1)  # type: ignore[arg-type]


@generic
def area(shape: object, scale: float = 1.0) -> float:
    raise TypeError


@grail.generic_impl(area)
def _(shape: Point, scale: float = 1.0) -> float:
    return shape.x * scale


assert_type(area(p), float)
assert_type(area(p, 2.0), float)
area(p, "no")  # type: ignore[arg-type]
area()  # type: ignore[call-arg]
grail.generic_register(area, Vec, lambda v, scale=1.0: 0.0)
assert_type(grail.generic_version(area), int)
assert_type(grail.generic_fallback(area)(p), float)
area.impl  # type: ignore[attr-defined]


@grail.generic_override(area)
def _(shape: Vec, scale: float = 1.0) -> float:
    return shape.x


def vec_area(shape: Vec, scale: float = 1.0) -> float:
    return shape.x


assert_type(grail.generic_override(area, Vec, vec_area)(Vec(1.0)), float)


@generic
def step[S](shape: object, state: S, dt: float) -> S:
    return state


assert_type(step(p, 3, 0.5), int)
assert_type(step(p, "s", 0.5), str)
assert_type(grail.repr.version, int)

draft = mutate(p)
draft.x += 1.0
assert_type(finish(draft), Point)
assert_type(grail.original(draft), Point)


assert_type(grail.MISSING is grail.record_fields(Point)[0].default, bool)
assert_type(grail.Atom(1).value, int)
assert_type(grail.PMap({"a": 1}).set("b", 2)["a"], int)
assert_type(grail.PVector([1])[0], int)
assert_type(grail.FloatVector([1.0])[0], float)
