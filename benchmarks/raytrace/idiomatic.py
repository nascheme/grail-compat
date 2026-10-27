"""raytrace, Grail style: a toy Whitted-style ray tracer.

What it computes
----------------
A 100x100 RGB image of a fixed scene: one big yellow sphere, a row of six
small coloured spheres and a checkerboard floor, lit by two point lights.
For each pixel a ray leaves the camera; the nearest hit is shaded with
ambient + Lambert (from every light not in shadow) + a specular term from a
recursively traced reflected ray (at most 4 levels deep).

Structure
---------
* Vec and Point are distinct value records, as in the original, so the
  type of an expression says whether it is a direction or a position.
  Their arithmetic is registered with grail.ops on exact operand pairs
  (Point - Point -> Vec, Point + Vec -> Point, Vec * float -> Vec, ...).
* Shapes (Sphere, Halfspace) and surfaces (SimpleSurface, Checkerboard) are
  value records; the polymorphic operations are generic functions:
  intersection_time and normal_at on shapes, base_colour_at on surfaces.
  New shapes or surfaces are added by registering impls; nothing else
  changes.
* Ray has the invariant "vector is unit length"; it is built through
  make_ray(), the factory that establishes it.
* The scene is one immutable value (a Scene record of tuples), so every
  rendering thread reads it without locks.  The original's mutable
  recursionDepth counter on the scene becomes a `depth` argument.

Parallelism: rows are independent, so each row is one task returning its
bytes, and grail.pmap sizes the chunks of rows; the image is the rows
concatenated.  The result is the same for any
thread count.

Faithfulness notes (quirks of the original kept so pixels match):
* Halfspace.intersectionTime in the original returns 1 / -(d . n), which is
  only right for rays starting at distance 1 from the plane.  Kept as is.
* The original's checkerboard computes v.scale(1/checkSize) and discards the
  result.  Here the scale is applied; with the scene's check_size = 1.0 that
  is a multiply by exactly 1.0, so the pixels are identical.
"""

from __future__ import annotations

import math

import grail

DEFAULT_WIDTH = 100
DEFAULT_HEIGHT = 100
EPSILON = 0.00001


# ------------------------------------------------------- vectors and points

@grail.record
class Vec:
    x: float
    y: float
    z: float


@grail.record
class Point:
    x: float
    y: float
    z: float


@grail.ops.add.impl
def _(a: Vec, b: Vec) -> Vec:
    return Vec(a.x + b.x, a.y + b.y, a.z + b.z)


@grail.ops.sub.impl
def _(a: Vec, b: Vec) -> Vec:
    return Vec(a.x - b.x, a.y - b.y, a.z - b.z)


@grail.ops.mul.impl
def _(a: Vec, k: float) -> Vec:
    return Vec(k * a.x, k * a.y, k * a.z)


@grail.ops.add.impl
def _(a: Point, b: Vec) -> Point:
    return Point(a.x + b.x, a.y + b.y, a.z + b.z)


@grail.ops.sub.impl
def _(a: Point, b: Point) -> Vec:
    return Vec(a.x - b.x, a.y - b.y, a.z - b.z)


def dot(a: Vec, b: Vec) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def cross(a: Vec, b: Vec) -> Vec:
    return Vec(a.y * b.z - a.z * b.y,
               a.z * b.x - a.x * b.z,
               a.x * b.y - a.y * b.x)


def normalized(v: Vec) -> Vec:
    return v * (1.0 / math.sqrt(dot(v, v)))


def reflect(v: Vec, normal: Vec) -> Vec:
    return v - normal * dot(v, normal) * 2.0


UP = Vec(0.0, 1.0, 0.0)
ORIGIN = Point(0.0, 0.0, 0.0)


@grail.record
class Ray:
    point: Point
    vector: Vec          # unit length; build with make_ray()


def make_ray(point: Point, direction: Vec) -> Ray:
    return Ray(point, normalized(direction))


def point_at(ray: Ray, t: float) -> Point:
    return ray.point + ray.vector * t


# ------------------------------------------------------------------ shapes

@grail.record
class Sphere:
    centre: Point
    radius: float


@grail.record
class Halfspace:
    point: Point
    normal: Vec          # unit length; build with make_halfspace()


def make_halfspace(point: Point, normal: Vec) -> Halfspace:
    return Halfspace(point, normalized(normal))


@grail.generic
def intersection_time(shape, ray: Ray) -> float | None:
    """Distance along ray to the shape, or None if it misses."""
    raise TypeError(f"not a shape: {type(shape).__name__}")


@grail.generic
def normal_at(shape, p: Point) -> Vec:
    """Unit surface normal of shape at point p on it."""
    raise TypeError(f"not a shape: {type(shape).__name__}")


@intersection_time.impl
def _(shape: Sphere, ray: Ray) -> float | None:
    s = shape
    cp = s.centre - ray.point
    v = dot(cp, ray.vector)
    discriminant = s.radius * s.radius - (dot(cp, cp) - v * v)
    if discriminant < 0:
        return None
    return v - math.sqrt(discriminant)


@normal_at.impl
def _(shape: Sphere, p: Point) -> Vec:
    return normalized(p - shape.centre)


@intersection_time.impl
def _(shape: Halfspace, ray: Ray) -> float | None:
    h = shape
    # The original's formula (see module docstring): ignores h.point.
    v = dot(ray.vector, h.normal)
    return 1 / -v if v else None


@normal_at.impl
def _(shape: Halfspace, p: Point) -> Vec:
    return shape.normal


# ---------------------------------------------------------------- surfaces

@grail.record
class Colour:
    r: float
    g: float
    b: float


BLACK = Colour(0.0, 0.0, 0.0)


def mix(a: Colour, k: float, b: Colour) -> Colour:
    """a + k*b"""
    return Colour(a.r + k * b.r, a.g + k * b.g, a.b + k * b.b)


@grail.record
class Finish:
    """How light is split between reflection, diffuse and ambient terms."""
    specular: float = 0.2
    lambert: float = 0.6
    ambient: float = 1.0 - 0.2 - 0.6     # the rest; same rounding as original


@grail.record
class SimpleSurface:
    colour: Colour
    finish: Finish = Finish()


@grail.record
class Checkerboard:
    colour: Colour = Colour(1.0, 1.0, 1.0)
    other_colour: Colour = BLACK
    check_size: float = 1.0
    finish: Finish = Finish()


@grail.generic
def base_colour_at(surface, p: Point) -> Colour:
    """Unlit colour of the surface at point p."""
    raise TypeError(f"not a surface: {type(surface).__name__}")


@base_colour_at.impl
def _(surface: SimpleSurface, p: Point) -> Colour:
    return surface.colour


@base_colour_at.impl
def _(surface: Checkerboard, p: Point) -> Colour:
    v = (p - ORIGIN) * (1.0 / surface.check_size)
    odd = (int(abs(v.x) + 0.5) + int(abs(v.y) + 0.5) + int(abs(v.z) + 0.5)) % 2
    return surface.other_colour if odd else surface.colour


# ------------------------------------------------------------------- scene

# Closed unions, so Thing stays a value record.  (Adding a shape means
# extending this union as well as registering impls; see notes.)
Shape = Sphere | Halfspace
Surface = SimpleSurface | Checkerboard


@grail.record
class Thing:
    shape: Shape
    surface: Surface


@grail.record
class Camera:
    position: Point
    looking_at: Point
    field_of_view: float      # degrees, horizontal


@grail.record
class Scene:
    camera: Camera
    things: tuple[Thing, ...]
    lights: tuple[Point, ...]


def first_hit(scene: Scene, ray: Ray) -> tuple[Thing, float] | None:
    """Nearest thing hit (t > -EPSILON); earlier things win ties."""
    best, best_t = None, 0.0
    for thing in scene.things:
        t = intersection_time(thing.shape, ray)
        if t is not None and t > -EPSILON and (best is None or t < best_t):
            best, best_t = thing, t
    return None if best is None else (best, best_t)


def light_is_visible(scene: Scene, light: Point, p: Point) -> bool:
    ray = make_ray(p, light - p)
    for thing in scene.things:
        t = intersection_time(thing.shape, ray)
        if t is not None and t > EPSILON:
            return False
    return True


def ray_colour(scene: Scene, ray: Ray, depth: int = 0) -> Colour:
    if depth > 3:
        return BLACK
    hit = first_hit(scene, ray)
    if hit is None:
        return BLACK                         # background
    thing, t = hit
    p = point_at(ray, t)
    return colour_at(scene, thing.surface, ray, p,
                     normal_at(thing.shape, p), depth)


def colour_at(scene: Scene, surface: Surface, ray: Ray, p: Point,
              normal: Vec, depth: int) -> Colour:
    base = base_colour_at(surface, p)
    f = surface.finish
    c = BLACK
    if f.specular > 0:
        reflected = make_ray(p, reflect(ray.vector, normal))
        c = mix(c, f.specular, ray_colour(scene, reflected, depth + 1))
    if f.lambert > 0:
        amount = 0.0
        for light in scene.lights:
            if light_is_visible(scene, light, p):
                contribution = dot(normalized(light - p), normal)
                if contribution > 0:
                    amount += contribution
        c = mix(c, f.lambert * min(1.0, amount), base)
    if f.ambient > 0:
        c = mix(c, f.ambient, base)
    return c


# --------------------------------------------------------------- rendering

@grail.record
class View:
    """Per-image constants derived from the camera, shared by all rows."""
    eye: Ray
    right: Vec
    up: Vec
    half_width: float
    half_height: float
    pixel_width: float
    pixel_height: float


def make_view(cam: Camera, width: int, height: int) -> View:
    half_w = math.tan(math.pi * (cam.field_of_view / 2.0) / 180.0)
    half_h = 0.75 * half_w
    eye = make_ray(cam.position, cam.looking_at - cam.position)
    right = normalized(cross(eye.vector, UP))
    up = normalized(cross(right, eye.vector))
    return View(eye, right, up, half_w, half_h,
                half_w * 2 / (width - 1), half_h * 2 / (height - 1))


def to_byte(c: float) -> int:
    return max(0, min(255, int(c * 255)))


def render_row(scene: Scene, view: View, width: int, y: int) -> bytes:
    """RGB bytes of image row y (y = 0 is the bottom row)."""
    row = bytearray()                         # local working buffer
    ycomp = view.up * (y * view.pixel_height - view.half_height)
    for x in range(width):
        xcomp = view.right * (x * view.pixel_width - view.half_width)
        ray = make_ray(view.eye.point, view.eye.vector + xcomp + ycomp)
        c = ray_colour(scene, ray)
        row += bytes((to_byte(c.r), to_byte(c.g), to_byte(c.b)))
    return bytes(row)


def render(scene: Scene, width: int, height: int, threads: int = 8) -> bytes:
    """The image as width*height*3 RGB bytes, top row first (PPM order)."""
    view = make_view(scene.camera, width, height)
    ys = range(height - 1, -1, -1)            # top row first
    if threads == 1:
        rows = [render_row(scene, view, width, y) for y in ys]
    else:
        with grail.Pool(threads) as pool:
            rows = grail.pmap(lambda y: render_row(scene, view, width, y), ys,
                              pool=pool)
    return b"".join(rows)


def default_scene() -> Scene:
    small = tuple(
        Thing(Sphere(Point(-3 - y * 0.4, 2.3, -5.0), 0.4),
              SimpleSurface(Colour(y / 6.0, 1 - y / 6.0, 0.5)))
        for y in range(6))
    return Scene(
        camera=Camera(Point(0.0, 1.8, 10.0), Point(0.0, 3.0, 0.0), 45.0),
        things=(Thing(Sphere(Point(1.0, 3.0, -10.0), 2.0),
                      SimpleSurface(Colour(1.0, 1.0, 0.0))),
                *small,
                Thing(make_halfspace(ORIGIN, UP), Checkerboard())),
        lights=(Point(30.0, 30.0, 10.0), Point(-10.0, 100.0, 30.0)))


def write_ppm(pixels: bytes, width: int, height: int, filename: str) -> None:
    with open(filename, "wb") as fp:
        fp.write(b"P6 %d %d 255\n" % (width, height))
        fp.write(pixels)


def main(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, threads=8) -> bytes:
    """Same workload as one loop of the original: build scene, render."""
    return render(default_scene(), width, height, threads)


if __name__ == "__main__":
    import hashlib
    img = main()
    print(f"{len(img)} bytes, sha256={hashlib.sha256(img).hexdigest()[:16]}")
