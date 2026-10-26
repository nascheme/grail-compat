"""N-body (Computer Language Benchmarks Game), written Grail style.

What it computes
----------------
A Jovian planet system (Sun, Jupiter, Saturn, Uranus, Neptune) in units of
AU, years and solar masses.  The Sun's velocity is first set so the total
momentum is zero.  The system is then integrated with a fixed time step
(symplectic Euler: update all velocities from pairwise gravity using the
current positions, then move every body by its new velocity).  The total
energy (kinetic + potential) is reported before and after; for a correct
integrator the two agree to about 1e-4 relative.

Design
------
* ``Vec3`` and ``Body`` are value records (every field is a float or a value
  record), so a Grail VM may unbox their floats and scalar-replace the many
  short-lived ``Vec3`` temporaries the arithmetic produces.
* The state of the simulation is a tuple of ``Body`` values.  ``step`` is a
  pure function from one state to the next.  Inside ``step`` the velocities
  being accumulated live in a local list (working state of one function).
* Vector arithmetic goes through ``grail.ops`` because ``v - d * b2m`` reads
  better than three scalar lines.  The operations are applied component-wise
  in exactly the same order as the original, so the result is bit-identical.
* There is no real polymorphism here, so there are no generic functions.

Parallelism
-----------
There is essentially none.  One time step is 10 pair interactions (about
30 ns each in a good JIT) and every step depends on the previous one, so
any cross-thread hand-off per step costs more than the step itself.  The
benchmark's spirit is a single, sequential simulation, so ``main`` runs one
simulation on one thread.  ``ensemble`` shows the only honest parallel use:
independent simulations (here, one per choice of reference body) run on
separate threads.  That is a different workload, a throughput benchmark,
and is not used by ``main``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import grail
from grail import ops

DEFAULT_ITERATIONS = 20000
DEFAULT_REFERENCE = "sun"
DT = 0.01


# ------------------------------------------------------------------ data

@grail.record
class Vec3:
    x: float
    y: float
    z: float


@grail.record
class Body:
    name: str
    pos: Vec3        # AU
    vel: Vec3        # AU / year
    mass: float      # solar masses * 4 pi^2  (so G == 1)


# --------------------------------------------------------- vector algebra
# Component-wise, matching the original's scalar code operation for operation.

@ops.add.impl
def _(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(a.x + b.x, a.y + b.y, a.z + b.z)


@ops.sub.impl
def _(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(a.x - b.x, a.y - b.y, a.z - b.z)


@ops.mul.impl
def _(a: Vec3, k: float) -> Vec3:
    return Vec3(a.x * k, a.y * k, a.z * k)


def dot(a: Vec3, b: Vec3) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


# --------------------------------------------------------- initial state

PI = 3.14159265358979323
SOLAR_MASS = 4 * PI * PI
DAYS_PER_YEAR = 365.24


def _planet(name, pos, vel_per_day, mass_in_suns):
    vel = Vec3(*(c * DAYS_PER_YEAR for c in vel_per_day))
    return Body(name, Vec3(*pos), vel, mass_in_suns * SOLAR_MASS)


INITIAL: tuple[Body, ...] = (
    Body("sun", Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 0.0), SOLAR_MASS),
    _planet("jupiter",
            (4.84143144246472090e+00, -1.16032004402742839e+00, -1.03622044471123109e-01),
            (1.66007664274403694e-03, 7.69901118419740425e-03, -6.90460016972063023e-05),
            9.54791938424326609e-04),
    _planet("saturn",
            (8.34336671824457987e+00, 4.12479856412430479e+00, -4.03523417114321381e-01),
            (-2.76742510726862411e-03, 4.99852801234917238e-03, 2.30417297573763929e-05),
            2.85885980666130812e-04),
    _planet("uranus",
            (1.28943695621391310e+01, -1.51111514016986312e+01, -2.23307578892655734e-01),
            (2.96460137564761618e-03, 2.37847173959480950e-03, -2.96589568540237556e-05),
            4.36624404335156298e-05),
    _planet("neptune",
            (1.53796971148509165e+01, -2.59193146099879641e+01, 1.79258772950371181e-01),
            (2.68067772490389322e-03, 1.62824170038242295e-03, -9.51592254519715870e-05),
            5.15138902046611451e-05),
)

# All unordered pairs (i < j), in the same order as itertools.combinations.
PAIRS: tuple[tuple[int, int], ...] = tuple(
    (i, j) for i in range(len(INITIAL)) for j in range(i + 1, len(INITIAL)))


# --------------------------------------------------------------- physics

def offset_momentum(bodies: tuple[Body, ...], reference: str) -> tuple[Body, ...]:
    """Give the reference body the velocity that makes total momentum zero."""
    px = py = pz = 0.0
    for b in bodies:
        px -= b.vel.x * b.mass
        py -= b.vel.y * b.mass
        pz -= b.vel.z * b.mass
    return tuple(
        Body(b.name, b.pos, Vec3(px / b.mass, py / b.mass, pz / b.mass), b.mass)
        if b.name == reference else b
        for b in bodies)


def energy(bodies: tuple[Body, ...]) -> float:
    """Total energy: pairwise potential, then kinetic (original's order)."""
    e = 0.0
    for i, j in PAIRS:
        a, b = bodies[i], bodies[j]
        d = a.pos - b.pos
        e -= (a.mass * b.mass) / (dot(d, d) ** 0.5)
    for b in bodies:
        e += b.mass * dot(b.vel, b.vel) / 2.
    return e


def step(bodies: tuple[Body, ...], dt: float) -> tuple[Body, ...]:
    """One symplectic-Euler step; returns the next state."""
    vel = [b.vel for b in bodies]               # local working state
    for i, j in PAIRS:
        a, b = bodies[i], bodies[j]
        d = a.pos - b.pos
        mag = dt * (dot(d, d) ** (-1.5))
        vel[i] = vel[i] - d * (b.mass * mag)
        vel[j] = vel[j] + d * (a.mass * mag)
    return tuple(Body(b.name, b.pos + v * dt, v, b.mass)
                 for b, v in zip(bodies, vel))


def simulate(bodies: tuple[Body, ...], dt: float, n: int) -> tuple[Body, ...]:
    for _ in range(n):
        bodies = step(bodies, dt)
    return bodies


# ------------------------------------------------------------------ main

def run(iterations: int = DEFAULT_ITERATIONS,
        reference: str = DEFAULT_REFERENCE):
    """One benchmark loop: energy, advance, energy.  Returns (e0, e1, state)."""
    bodies = offset_momentum(INITIAL, reference)
    e0 = energy(bodies)
    bodies = simulate(bodies, DT, iterations)
    return e0, energy(bodies), bodies


def main(iterations: int = DEFAULT_ITERATIONS,
         reference: str = DEFAULT_REFERENCE, threads: int = 8):
    """Same workload as one pyperf loop of the original.

    ``threads`` is accepted for a uniform interface but unused: a single
    simulation is inherently sequential (see the module docstring).
    """
    return run(iterations, reference)


def ensemble(references=("sun", "jupiter", "saturn", "uranus", "neptune"),
             iterations: int = DEFAULT_ITERATIONS, threads: int = 8):
    """Throughput variant (NOT the benchmark): independent simulations in
    parallel.  Inputs are shared immutable records; results are the same
    as running each simulation alone, in any thread count."""
    if threads <= 1:
        return [run(iterations, r) for r in references]
    with ThreadPoolExecutor(threads) as pool:
        return list(pool.map(lambda r: run(iterations, r), references))


if __name__ == "__main__":
    e0, e1, final = main()
    print(f"energy before: {e0:.9f}")
    print(f"energy after:  {e1:.9f}")
    print(f"sun position:  {final[0].pos}")
