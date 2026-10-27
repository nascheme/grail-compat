"""DeltaBlue (incremental one-way constraint solver), Grail style: solver
state is a value.

The previous idiomatic version, kept for comparison: the same data,
algorithm and results as idiomatic.py, but each operation edits a Batch of
PVector transients and rebuilds Var records with ``copy.replace`` instead
of editing a ``grail.mutate`` draft of the Solver.  The difference in time
is the cost of drafts.

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
  "<".  Variables and constraints are addressed by integer ids.
* A ``Solver`` is an immutable snapshot: a ``PVector`` of ``Var`` records
  (value, walk strength, stay flag, mark, determining constraint and the
  constraints that mention it), a ``PVector`` of directions and one of
  constraints, indexed by cid, and the last mark issued.  Every operation
  (adding or removing a constraint, extracting a plan, running a plan with
  a new input) takes a snapshot and returns a new one; older snapshots stay
  valid and shareable.
* Within one operation, propagation edits a ``Batch``: thread-confined
  ``PVector`` transients over the snapshot's vectors, published by
  ``finish()`` when the operation ends.  A variable update replaces its
  ``Var`` record (``copy.replace``); the transient edits the trie path in
  place once it owns it, so an operation copies only the leaves it
  touches.  The graph's cycles (variables list constraints, constraints
  name variables) are ids, not references.
* A plan is a tuple of constraints (an immutable value).
* Work queues are ``collections.deque`` (same FIFO order as pop(0)).

The previous design (one record of mutable lists, same algorithm) is kept
as hybrid.py for comparison.

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

import copy
import itertools
from collections import deque

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
class Var:
    value: int | float
    walk: int                    # walkabout strength
    stay: bool                   # value is constant during a plan
    mark: int                    # last mark that visited it
    determined_by: int           # cid of the determining constraint or -1
    constraints: tuple[Stay | Edit | Equal | Scale, ...]   # that mention it


@grail.record
class Solver:
    """One planner state: a value."""
    vars: grail.PVector[Var]                 # var -> Var
    direction: grail.PVector[int]            # cid -> NONE / FORWARD / BACKWARD
    constraints: grail.PVector[Constraint]   # cid -> Constraint
    marks: int                               # last mark issued


@grail.record
class Batch:
    """The working state of one operation on a Solver, confined to its thread:
    transients over the snapshot's vectors and the source of fresh marks."""
    vars: object                 # PVector transient: var -> Var
    direction: object            # PVector transient (or the PVector, read-only)
    by_cid: grail.PVector[Constraint]
    marks: object                # itertools.count of fresh marks, or None


def new_solver() -> Solver:
    return Solver(grail.PVector(), grail.PVector(), grail.PVector(), 0)


def new_variable(s: Solver, value=0) -> tuple[Solver, int]:
    var = Var(value, WEAKEST, True, 0, NO_CONSTRAINT, ())
    return copy.replace(s, vars=s.vars.append(var)), len(s.vars)


def open_batch(s: Solver, vars=None, direction=None, by_cid=None) -> Batch:
    return Batch((s.vars if vars is None else vars).mutate(),
                 (s.direction if direction is None else direction).mutate(),
                 s.constraints if by_cid is None else by_cid,
                 itertools.count(s.marks + 1))


def close_batch(b: Batch) -> Solver:
    return Solver(b.vars.finish(), b.direction.finish(), b.by_cid,
                  next(b.marks) - 1)


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


def output(b: Batch, c: Constraint) -> int:
    match c:
        case Equal() | Scale():
            return c.v2 if b.direction[c.cid] == FORWARD else c.v1
        case _:
            return c.out


def input_of(b: Batch, c: Equal | Scale) -> int:
    return c.v1 if b.direction[c.cid] == FORWARD else c.v2


def choose_method(b: Batch, c: Constraint, mark: int) -> None:
    """Pick the direction that lets c be enforced, or NONE."""
    vs = b.vars
    match c:
        case Equal() | Scale():
            # The original also has two branches on v1/v2 marks first, but
            # their result is always overwritten by this test (they are dead
            # in the V8 source this was ported from too), so only this runs.
            w1, w2 = vs[c.v1].walk, vs[c.v2].walk
            if w1 > w2:                      # v1 is weaker: write v1
                d = BACKWARD if c.strength < w1 else NONE
            else:
                d = FORWARD if c.strength < w2 else BACKWARD
        case _:
            x = vs[c.out]
            ok = x.mark != mark and c.strength < x.walk
            d = FORWARD if ok else NONE
    b.direction[c.cid] = d


def set_mark(vs, v: int, mark: int) -> None:
    vs[v] = copy.replace(vs[v], mark=mark)


def mark_inputs(b: Batch, c: Constraint, mark: int) -> None:
    match c:
        case Equal():
            set_mark(b.vars, input_of(b, c), mark)
        case Scale():
            set_mark(b.vars, input_of(b, c), mark)
            set_mark(b.vars, c.scale, mark)
            set_mark(b.vars, c.offset, mark)


def inputs_known(b: Batch, c: Constraint, mark: int) -> bool:
    match c:
        case Equal() | Scale():
            x = b.vars[input_of(b, c)]
            return x.mark == mark or x.stay or x.determined_by == NO_CONSTRAINT
        case _:
            return True


def execute(b: Batch, c: Constraint) -> None:
    """Enforce c by computing its output from its inputs."""
    vs = b.vars
    match c:
        case Equal(cid, v1, v2):
            if b.direction[cid] == FORWARD:
                vs[v2] = copy.replace(vs[v2], value=vs[v1].value)
            else:
                vs[v1] = copy.replace(vs[v1], value=vs[v2].value)
        case Scale(cid, v1, v2, scale, offset):
            if b.direction[cid] == FORWARD:
                x = vs[v1].value * vs[scale].value + vs[offset].value
                vs[v2] = copy.replace(vs[v2], value=x)
            else:
                x = (vs[v2].value - vs[offset].value) / vs[scale].value
                vs[v1] = copy.replace(vs[v1], value=x)
        # Stay and Edit compute nothing.


def recalculate(b: Batch, c: Constraint) -> None:
    """Recompute walk strength and stay of c's output, and its value if stay."""
    vs = b.vars
    match c:
        case Equal() | Scale():
            i, out = input_of(b, c), output(b, c)
            xi = vs[i]
            stay = xi.stay
            if type(c) is Scale:
                stay = stay and vs[c.scale].stay and vs[c.offset].stay
            vs[out] = copy.replace(vs[out], walk=max(c.strength, xi.walk),
                                   stay=stay)            # the weaker of the two
            if stay:
                execute(b, c)
        case _:
            vs[c.out] = copy.replace(vs[c.out], walk=c.strength,
                                     stay=type(c) is not Edit)   # edits are inputs


def is_satisfied(b: Batch, c: Constraint) -> bool:
    return b.direction[c.cid] != NONE


# ----------------------------------------------------- the planner proper

def add_constraint(s: Solver, c: Constraint) -> Solver:
    """s with c registered in the graph and incrementally re-satisfied."""
    assert c.cid == len(s.constraints)
    vs = s.vars.mutate()
    for v in variables(c):
        x = vs[v]
        vs[v] = copy.replace(x, constraints=x.constraints + (c,))
    b = open_batch(s, vs.finish(), s.direction.append(NONE),
                   s.constraints.append(c))
    incremental_add(b, c)
    return close_batch(b)


def next_cid(s: Solver) -> int:
    return len(s.constraints)


def remove_from_graph(b: Batch, c: Constraint) -> None:
    vs = b.vars
    for v in variables(c):
        x = vs[v]
        cs = x.constraints
        i = cs.index(c)
        det = NO_CONSTRAINT if x.determined_by == c.cid else x.determined_by
        vs[v] = copy.replace(x, constraints=cs[:i] + cs[i + 1:],
                             determined_by=det)
    b.direction[c.cid] = NONE


def destroy_constraint(s: Solver, c: Constraint) -> Solver:
    """s without c."""
    b = open_batch(s)
    if is_satisfied(b, c):
        incremental_remove(b, c)
    else:
        remove_from_graph(b, c)
    return close_batch(b)


def satisfy(b: Batch, c: Constraint, mark: int):
    """Try to enforce c; return the constraint it overrode, or None."""
    choose_method(b, c, mark)
    if not is_satisfied(b, c):
        if c.strength == REQUIRED:
            print("Could not satisfy a required constraint!")
        return None
    mark_inputs(b, c, mark)
    out = output(b, c)
    vs = b.vars
    x = vs[out]
    overridden = x.determined_by
    if overridden != NO_CONSTRAINT:
        b.direction[overridden] = NONE               # mark it unsatisfied
    vs[out] = copy.replace(x, determined_by=c.cid)
    if not add_propagate(b, c, mark):
        print("Cycle encountered")
    set_mark(vs, out, mark)
    return None if overridden == NO_CONSTRAINT else b.by_cid[overridden]


def incremental_add(b: Batch, c: Constraint) -> None:
    mark = next(b.marks)
    overridden = satisfy(b, c, mark)
    while overridden is not None:
        overridden = satisfy(b, overridden, mark)


def incremental_remove(b: Batch, c: Constraint) -> None:
    out = output(b, c)
    b.direction[c.cid] = NONE
    remove_from_graph(b, c)
    unsatisfied = remove_propagate_from(b, out)
    # The original walks strengths from REQUIRED downwards, but its
    # Strength.next_weaker maps REQUIRED straight to WEAKEST (as in the V8
    # source), so only required constraints are ever re-added.  (The Python
    # port also moved the strength step inside the loop; that is only
    # equivalent because every removal in this benchmark leaves exactly one
    # unsatisfied constraint, which the harness checks.)
    for u in unsatisfied:
        if u.strength == REQUIRED:
            incremental_add(b, u)


def add_consumers(b: Batch, v: int, todo) -> None:
    """Append the enforced constraints that read v (other than its source)."""
    x = b.vars[v]
    det = x.determined_by
    direction = b.direction
    for c in x.constraints:
        if c.cid != det and direction[c.cid] != NONE:
            todo.append(c)


def add_propagate(b: Batch, c: Constraint, mark: int) -> bool:
    """Recalculate everything downstream of c; False if that hits a cycle."""
    todo = deque([c])
    while todo:
        d = todo.popleft()
        out = output(b, d)
        if b.vars[out].mark == mark:
            incremental_remove(b, c)
            return False
        recalculate(b, d)
        add_consumers(b, out, todo)
    return True


def remove_propagate_from(b: Batch, out: int) -> list:
    """Reset everything downstream of out; return constraints left unsatisfied."""
    vs = b.vars
    vs[out] = copy.replace(vs[out], determined_by=NO_CONSTRAINT, walk=WEAKEST,
                           stay=True)
    unsatisfied = []
    todo = deque([out])
    while todo:
        x = vs[todo.popleft()]
        cs = x.constraints
        unsatisfied.extend(c for c in cs if not is_satisfied(b, c))
        det = x.determined_by
        for c in cs:
            if c.cid != det and is_satisfied(b, c):
                recalculate(b, c)
                todo.append(output(b, c))
    return unsatisfied


def make_plan(b: Batch, sources) -> tuple:
    """Order the constraints downstream of sources so inputs come first."""
    mark = next(b.marks)
    plan = []
    todo = deque(sources)
    while todo:
        c = todo.popleft()
        out = output(b, c)
        if b.vars[out].mark != mark and inputs_known(b, c, mark):
            plan.append(c)
            set_mark(b.vars, out, mark)
            add_consumers(b, out, todo)
    return tuple(plan)


def extract_plan(s: Solver, constraints) -> tuple[Solver, tuple]:
    """The plan for the satisfied edits among constraints, and s with the
    marks it used."""
    b = open_batch(s)
    plan = make_plan(b, [c for c in constraints
                         if type(c) is Edit and is_satisfied(b, c)])
    return close_batch(b), plan


def execute_plan(s: Solver, plan: tuple, var: int, value) -> Solver:
    """s with input `var` set to value and the plan run."""
    vs = s.vars.mutate()
    vs[var] = copy.replace(vs[var], value=value)
    b = Batch(vs, s.direction, s.constraints, None)
    for c in plan:
        execute(b, c)
    return Solver(vs.finish(), s.direction, s.constraints, s.marks)


# ------------------------------------------------------------------ tests

def chain_test(n: int) -> Solver:
    s = new_solver()
    vs = []
    for _ in range(n + 1):
        s, v = new_variable(s)
        vs.append(v)
    for prev, v in itertools.pairwise(vs):
        s = add_constraint(s, Equal(next_cid(s), prev, v, REQUIRED))
    first, last = vs[0], vs[-1]
    s = add_constraint(s, Stay(next_cid(s), last, STRONG_DEFAULT))
    edit = Edit(next_cid(s), first, PREFERRED)
    s = add_constraint(s, edit)
    s, plan = extract_plan(s, [edit])
    for i in range(100):
        s = execute_plan(s, plan, first, i)
        if s.vars[last].value != i:
            print("Chain test failed.")
    return s


def change(s: Solver, v: int, new_value) -> Solver:
    edit = Edit(next_cid(s), v, PREFERRED)
    s = add_constraint(s, edit)
    s, plan = extract_plan(s, [edit])
    for _ in range(10):
        s = execute_plan(s, plan, v, new_value)
    return destroy_constraint(s, edit)


def projection_test(n: int) -> Solver:
    s = new_solver()
    s, scale = new_variable(s, 10)
    s, offset = new_variable(s, 1000)
    dests = []
    for i in range(n):
        s, src = new_variable(s, i)
        s, dst = new_variable(s, i)
        dests.append(dst)
        s = add_constraint(s, Stay(next_cid(s), src, NORMAL))
        s = add_constraint(s, Scale(next_cid(s), src, dst, scale, offset, REQUIRED))

    s = change(s, src, 17)
    if s.vars[dst].value != 1170:
        print("Projection 1 failed")
    s = change(s, dst, 1050)
    if s.vars[src].value != 5:
        print("Projection 2 failed")
    s = change(s, scale, 5)
    for i in range(n - 1):
        if s.vars[dests[i]].value != i * 5 + 1000:
            print("Projection 3 failed")
    s = change(s, offset, 2000)
    for i in range(n - 1):
        if s.vars[dests[i]].value != i * 5 + 2000:
            print("Projection 4 failed")
    return s


def delta_blue(n: int, pool: grail.Pool | None = None):
    """One benchmark call: both tests, concurrently when given a thread pool."""
    if pool is not None:
        chain, projection = grail.pmap(lambda test: test(n),
                                       (chain_test, projection_test),
                                       chunk=1, pool=pool)
        return chain, projection
    return chain_test(n), projection_test(n)


def checksum(s: Solver) -> int:
    """A digest of the final solver state (variables and directions)."""
    return hash((tuple(s.vars), tuple(s.direction)))


def main(n=100, threads=8):
    """Same workload as pyperformance's delta_blue(100).

    Only two tasks exist, so more than two threads add nothing.
    """
    if threads > 1:
        with grail.Pool(min(threads, 2)) as pool:
            return delta_blue(n, pool)
    return delta_blue(n)


if __name__ == "__main__":
    chain, projection = main()
    print(f"deltablue: chain last={chain.vars[-1].value} "
          f"projection src99={projection.vars[-2].value} "
          f"dst99={projection.vars[-1].value} "
          f"checksum={checksum(chain) ^ checksum(projection):#x}")
