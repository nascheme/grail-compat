"""DeltaBlue (incremental one-way constraint solver), Grail style: hybrid control.

The earlier conversion, kept as a comparison control for idiomatic.py:
value records for constraint definitions, but all solver state in one
record of plain mutable lists.  Same algorithm and final state.

What it computes
----------------
DeltaBlue maintains a set of variables and multi-way constraints with
strengths (required ... weakest).  Adding or removing a constraint updates
incrementally which constraints are enforced and in which direction (a
"walkabout strength" per variable decides who may override whom), and a
*plan* (an ordered list of constraint methods) can be extracted that
recomputes every variable downstream of a set of edit constraints.

The benchmark runs two tests with n = 100:

* chain: v0 = v1 = ... = v100 (required equalities), a strong-default stay on
  v100, a preferred edit on v0; extract the plan and execute it for 100 new
  values of v0, checking v100 follows.
* projection: 100 pairs dst_i = src_i * scale + offset (required), a normal
  stay on each src_i; then "change" (add an edit, extract a plan, execute it
  10 times, remove the edit) src_99, dst_99, scale and offset in turn,
  checking the values that result (1170, 5, i*5+1000, i*5+2000).

The original prints only on failure; the observable result is those checks.
For verification the harness also compares the complete final solver state
(every variable's value, walk strength, stay flag, mark and determining
constraint, every constraint's direction, and the mark counter).

The original
------------
A JavaScript -> Python port of the Smalltalk benchmark: a class hierarchy
(Constraint > Unary/Binary > Stay/Edit/Equality/Scale) with virtual
methods, mutable Variable objects that point at constraints and back, a
global planner, ``list.pop(0)`` work queues and Strength objects compared by
identity.  Two quirks of the port are reproduced exactly (see comments):
the dead mark-based branches of BinaryConstraint.choose_method, and the
strength loop in incremental_remove.

Design
------
* A constraint's *definition* never changes, so each kind is a value record
  of variable ids and a strength, with a unique ``cid`` (records have no
  identity, and two constraints may otherwise be equal).  The kinds are a
  closed set, ``Constraint = Stay | Edit | Equal | Scale``, interpreted with
  ``match``; there is no open extension point, so no generic functions.
* Strengths are small ints (0 = required ... 6 = weakest); "stronger" is
  "<".  Variables are ints indexing the solver's arrays.
* Everything that changes during solving (variable values, walk strengths,
  stay flags, marks, determining constraint, the constraint lists per
  variable and each constraint's direction) lives in one ``Solver`` record
  whose ordinary fields are plain lists: struct-of-arrays working state,
  confined to the thread running one test.  DeltaBlue is an in-place
  incremental algorithm over a cyclic graph; making that graph persistent
  would add an allocation per update and gain nothing, since no other
  thread or past version ever looks at it.  Honest choice: local mutable
  working state.
* A plan is a tuple of constraints (an immutable value).
* Work queues are ``collections.deque`` (same FIFO order as pop(0)).

Parallelism
-----------
Solving is a sequence of dependent worklist propagations; nothing inside a
test divides.  The two tests are independent (each builds its own solver),
so given a thread pool ``delta_blue`` runs chain and projection concurrently.
That is within one benchmark call, but it is only 2-way and unbalanced
(chain is about 60% of the time), so it caps near 1.6x.  Running many
pyperformance iterations concurrently would be a throughput benchmark, not
this one.
"""

import itertools
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import grail

# Strengths: smaller is stronger.
REQUIRED, STRONG_PREFERRED, PREFERRED, STRONG_DEFAULT, NORMAL, WEAK_DEFAULT, \
    WEAKEST = range(7)

# Directions of a binary constraint.  A unary constraint uses FORWARD for
# "satisfied", so "is satisfied" is ``direction != NONE`` for every kind.
NONE, FORWARD, BACKWARD = 0, 1, -1
NO_CONSTRAINT = -1               # determined_by of an undetermined variable


# ------------------------------------------------------------------ data

@grail.record
class Stay:                      # keep `out` at its value (weak anchor)
    cid: int
    out: int
    strength: int


@grail.record
class Edit:                      # `out` is set from outside: a plan input
    cid: int
    out: int
    strength: int


@grail.record
class Equal:                     # v1 == v2, in either direction
    cid: int
    v1: int
    v2: int
    strength: int


@grail.record
class Scale:                     # v2 == v1 * scale + offset, either direction
    cid: int
    v1: int
    v2: int
    scale: int
    offset: int
    strength: int


Constraint = Stay | Edit | Equal | Scale


@grail.record
class Solver:
    """Mutable working state of one planner, confined to one thread.

    The fields are ordinary (plain lists, an iterator), so this is not a value
    record; the record only fixes the set of fields.
    """
    value: list                  # var -> int | float
    walk: list                   # var -> walkabout strength
    stay: list                   # var -> bool: value is constant during a plan
    mark: list                   # var -> last mark that visited it
    determined_by: list          # var -> cid of the determining constraint or -1
    constraints: list            # var -> list[Constraint] that mention it
    direction: list              # cid -> NONE / FORWARD / BACKWARD
    by_cid: list                 # cid -> Constraint
    marks: itertools.count       # source of fresh marks 1, 2, 3, ...


def new_solver() -> Solver:
    return Solver([], [], [], [], [], [], [], [], itertools.count(1))


def new_variable(s: Solver, value=0) -> int:
    s.value.append(value)
    s.walk.append(WEAKEST)
    s.stay.append(True)
    s.mark.append(0)
    s.determined_by.append(NO_CONSTRAINT)
    s.constraints.append([])
    return len(s.value) - 1


# ------------------------------------------- per-kind constraint behaviour

def variables(c: Constraint) -> tuple:
    """The variables a constraint mentions, in the original's graph order."""
    match c:
        case Equal(_, v1, v2):
            return (v1, v2)
        case Scale(_, v1, v2, scale, offset):
            return (v1, v2, scale, offset)
        case _:
            return (c.out,)


def output(s: Solver, c: Constraint) -> int:
    match c:
        case Equal() | Scale():
            return c.v2 if s.direction[c.cid] == FORWARD else c.v1
        case _:
            return c.out


def input_of(s: Solver, c: Equal | Scale) -> int:
    return c.v1 if s.direction[c.cid] == FORWARD else c.v2


def choose_method(s: Solver, c: Constraint, mark: int) -> None:
    """Pick the direction that lets c be enforced, or NONE."""
    walk = s.walk
    match c:
        case Equal() | Scale():
            # The original also has two branches on v1/v2 marks first, but
            # their result is always overwritten by this test (they are dead
            # in the V8 source this was ported from too), so only this runs.
            w1, w2 = walk[c.v1], walk[c.v2]
            if w1 > w2:                      # v1 is weaker: write v1
                d = BACKWARD if c.strength < w1 else NONE
            else:
                d = FORWARD if c.strength < w2 else BACKWARD
        case _:
            out = c.out
            ok = s.mark[out] != mark and c.strength < walk[out]
            d = FORWARD if ok else NONE
    s.direction[c.cid] = d


def mark_inputs(s: Solver, c: Constraint, mark: int) -> None:
    match c:
        case Equal():
            s.mark[input_of(s, c)] = mark
        case Scale():
            s.mark[input_of(s, c)] = mark
            s.mark[c.scale] = mark
            s.mark[c.offset] = mark


def inputs_known(s: Solver, c: Constraint, mark: int) -> bool:
    match c:
        case Equal() | Scale():
            i = input_of(s, c)
            return (s.mark[i] == mark or s.stay[i]
                    or s.determined_by[i] == NO_CONSTRAINT)
        case _:
            return True


def execute(s: Solver, c: Constraint) -> None:
    """Enforce c by computing its output from its inputs."""
    value = s.value
    match c:
        case Equal(cid, v1, v2):
            if s.direction[cid] == FORWARD:
                value[v2] = value[v1]
            else:
                value[v1] = value[v2]
        case Scale(cid, v1, v2, scale, offset):
            if s.direction[cid] == FORWARD:
                value[v2] = value[v1] * value[scale] + value[offset]
            else:
                value[v1] = (value[v2] - value[offset]) / value[scale]
        # Stay and Edit compute nothing.


def recalculate(s: Solver, c: Constraint) -> None:
    """Recompute walk strength and stay of c's output, and its value if stay."""
    match c:
        case Equal() | Scale():
            i, out = input_of(s, c), output(s, c)
            s.walk[out] = max(c.strength, s.walk[i])     # the weaker of the two
            stay = s.stay[i]
            if type(c) is Scale:
                stay = stay and s.stay[c.scale] and s.stay[c.offset]
            s.stay[out] = stay
            if stay:
                execute(s, c)
        case _:
            out = c.out
            s.walk[out] = c.strength
            s.stay[out] = type(c) is not Edit            # edits are inputs


def is_satisfied(s: Solver, c: Constraint) -> bool:
    return s.direction[c.cid] != NONE


# ----------------------------------------------------- the planner proper

def add_constraint(s: Solver, c: Constraint) -> Constraint:
    """Register c in the graph and incrementally re-satisfy."""
    assert c.cid == len(s.by_cid)
    s.by_cid.append(c)
    s.direction.append(NONE)
    for v in variables(c):
        s.constraints[v].append(c)
    incremental_add(s, c)
    return c


def next_cid(s: Solver) -> int:
    return len(s.by_cid)


def remove_from_graph(s: Solver, c: Constraint) -> None:
    for v in variables(c):
        s.constraints[v].remove(c)
        if s.determined_by[v] == c.cid:
            s.determined_by[v] = NO_CONSTRAINT
    s.direction[c.cid] = NONE


def destroy_constraint(s: Solver, c: Constraint) -> None:
    if is_satisfied(s, c):
        incremental_remove(s, c)
    else:
        remove_from_graph(s, c)


def satisfy(s: Solver, c: Constraint, mark: int):
    """Try to enforce c; return the constraint it overrode, or None."""
    choose_method(s, c, mark)
    if not is_satisfied(s, c):
        if c.strength == REQUIRED:
            print("Could not satisfy a required constraint!")
        return None
    mark_inputs(s, c, mark)
    out = output(s, c)
    overridden = s.determined_by[out]
    if overridden != NO_CONSTRAINT:
        s.direction[overridden] = NONE               # mark it unsatisfied
    s.determined_by[out] = c.cid
    if not add_propagate(s, c, mark):
        print("Cycle encountered")
    s.mark[out] = mark
    return None if overridden == NO_CONSTRAINT else s.by_cid[overridden]


def incremental_add(s: Solver, c: Constraint) -> None:
    mark = next(s.marks)
    overridden = satisfy(s, c, mark)
    while overridden is not None:
        overridden = satisfy(s, overridden, mark)


def incremental_remove(s: Solver, c: Constraint) -> None:
    out = output(s, c)
    s.direction[c.cid] = NONE
    remove_from_graph(s, c)
    unsatisfied = remove_propagate_from(s, out)
    # The original walks strengths from REQUIRED downwards, but its
    # Strength.next_weaker maps REQUIRED straight to WEAKEST (as in the V8
    # source), so only required constraints are ever re-added.  (The Python
    # port also moved the strength step inside the loop; that is only
    # equivalent because every removal in this benchmark leaves exactly one
    # unsatisfied constraint, which the harness checks.)
    for u in unsatisfied:
        if u.strength == REQUIRED:
            incremental_add(s, u)


def add_consumers(s: Solver, v: int, todo) -> None:
    """Append the enforced constraints that read v (other than its source)."""
    det = s.determined_by[v]
    direction = s.direction
    for c in s.constraints[v]:
        if c.cid != det and direction[c.cid] != NONE:
            todo.append(c)


def add_propagate(s: Solver, c: Constraint, mark: int) -> bool:
    """Recalculate everything downstream of c; False if that hits a cycle."""
    todo = deque([c])
    while todo:
        d = todo.popleft()
        out = output(s, d)
        if s.mark[out] == mark:
            incremental_remove(s, c)
            return False
        recalculate(s, d)
        add_consumers(s, out, todo)
    return True


def remove_propagate_from(s: Solver, out: int) -> list:
    """Reset everything downstream of out; return constraints left unsatisfied."""
    s.determined_by[out] = NO_CONSTRAINT
    s.walk[out] = WEAKEST
    s.stay[out] = True
    unsatisfied = []
    todo = deque([out])
    while todo:
        v = todo.popleft()
        cs = s.constraints[v]
        unsatisfied.extend(c for c in cs if not is_satisfied(s, c))
        det = s.determined_by[v]
        for c in cs:
            if c.cid != det and is_satisfied(s, c):
                recalculate(s, c)
                todo.append(output(s, c))
    return unsatisfied


def make_plan(s: Solver, sources) -> tuple:
    """Order the constraints downstream of sources so inputs come first."""
    mark = next(s.marks)
    plan = []
    todo = deque(sources)
    while todo:
        c = todo.popleft()
        out = output(s, c)
        if s.mark[out] != mark and inputs_known(s, c, mark):
            plan.append(c)
            s.mark[out] = mark
            add_consumers(s, out, todo)
    return tuple(plan)


def extract_plan(s: Solver, constraints) -> tuple:
    return make_plan(s, [c for c in constraints
                         if type(c) is Edit and is_satisfied(s, c)])


def execute_plan(s: Solver, plan: tuple) -> None:
    for c in plan:
        execute(s, c)


# ------------------------------------------------------------------ tests

def chain_test(n: int) -> Solver:
    s = new_solver()
    vs = [new_variable(s) for _ in range(n + 1)]
    for prev, v in itertools.pairwise(vs):
        add_constraint(s, Equal(next_cid(s), prev, v, REQUIRED))
    first, last = vs[0], vs[-1]
    add_constraint(s, Stay(next_cid(s), last, STRONG_DEFAULT))
    edit = add_constraint(s, Edit(next_cid(s), first, PREFERRED))
    plan = extract_plan(s, [edit])
    for i in range(100):
        s.value[first] = i
        execute_plan(s, plan)
        if s.value[last] != i:
            print("Chain test failed.")
    return s


def change(s: Solver, v: int, new_value) -> None:
    edit = add_constraint(s, Edit(next_cid(s), v, PREFERRED))
    plan = extract_plan(s, [edit])
    for _ in range(10):
        s.value[v] = new_value
        execute_plan(s, plan)
    destroy_constraint(s, edit)


def projection_test(n: int) -> Solver:
    s = new_solver()
    scale = new_variable(s, 10)
    offset = new_variable(s, 1000)
    dests = []
    for i in range(n):
        src = new_variable(s, i)
        dst = new_variable(s, i)
        dests.append(dst)
        add_constraint(s, Stay(next_cid(s), src, NORMAL))
        add_constraint(s, Scale(next_cid(s), src, dst, scale, offset, REQUIRED))

    change(s, src, 17)
    if s.value[dst] != 1170:
        print("Projection 1 failed")
    change(s, dst, 1050)
    if s.value[src] != 5:
        print("Projection 2 failed")
    change(s, scale, 5)
    for i in range(n - 1):
        if s.value[dests[i]] != i * 5 + 1000:
            print("Projection 3 failed")
    change(s, offset, 2000)
    for i in range(n - 1):
        if s.value[dests[i]] != i * 5 + 2000:
            print("Projection 4 failed")
    return s


def delta_blue(n: int, pool: ThreadPoolExecutor | None = None):
    """One benchmark call: both tests, concurrently when given a thread pool."""
    if pool is not None:
        chain = pool.submit(chain_test, n)
        projection = pool.submit(projection_test, n)
        return chain.result(), projection.result()
    return chain_test(n), projection_test(n)


def checksum(s: Solver) -> int:
    """A digest of the final solver state (values, strengths, directions)."""
    return hash((tuple(s.value), tuple(s.walk), tuple(s.stay),
                 tuple(s.determined_by), tuple(s.direction)))


def main(n=100, threads=8):
    """Same workload as pyperformance's delta_blue(100).

    Only two tasks exist, so more than two threads add nothing.
    """
    if threads > 1:
        with ThreadPoolExecutor(min(threads, 2)) as pool:
            return delta_blue(n, pool)
    return delta_blue(n)


if __name__ == "__main__":
    chain, projection = main()
    print(f"deltablue: chain last={chain.value[-1]} "
          f"projection src99={projection.value[-2]} dst99={projection.value[-1]} "
          f"checksum={checksum(chain) ^ checksum(projection):#x}")
