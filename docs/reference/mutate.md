---
sources:
  - src/grail/_mutate.py
  - tests/test_grail_mutate.py
---

# Drafts reference

Experimental. `mutate(value)` returns a draft of a record, `PVector` or
`PMap`. You edit the draft with ordinary assignments, and `finish(draft)`
builds the new value. The original value never changes.

    >>> import grail
    >>> from grail import PVector, finish, mutate
    >>> @grail.record
    ... class Task:
    ...     name: str
    ...     queue: tuple[int, ...]
    >>> @grail.record
    ... class State:
    ...     tasks: PVector
    ...     count: int
    >>> s = State(PVector([Task("a", ()), Task("b", ())]), 0)
    >>> with mutate(s) as d:
    ...     d.tasks[1].queue += (7,)
    ...     d.count += 1
    >>> t = finish(d)
    >>> t.tasks[1], t.count, s.tasks[1]
    (Task(name='b', queue=(7,)), 1, Task(name='b', queue=()))
    >>> t.tasks[0] is s.tasks[0]
    True

Reading a field, element or entry that holds a record, `PVector` or `PMap`
returns a child draft of it. The draft is created on first use, and later
reads return the same one, so `a = d.tasks[1]` and `d.tasks[1]` edit the
same element. Any other value is returned as stored. A record draft
accepts only its type's field names and does not allow deletion. A
`PVector` draft supports `len`, integer indexing (negative indices
included), item assignment, `append` and `extend`. A `PMap` draft
supports `len`, `in`, `get`, item access, assignment and deletion.
Assigning a draft from the same `mutate` call stores the value it
finishes to.

`finish(draft)` builds the result bottom-up with `grail.replace`,
`PVector.set` and `PMap` transients. Unassigned parts are shared. An
assignment of the object already there is not a change. A draft with no
changes finishes to its original object, by identity. Conformance is
recomputed as for `replace`. `finish` takes the root draft only, closes
it, and returns the same result if you call it again. `original(draft)`
returns the value any draft was made from, without its edits.

A draft belongs to the thread that created it. Leaving a `with` block
seals it: after that you can only call `finish`. An exception aborts the
draft, and `finish` then raises `RuntimeError`. Only the root draft is a
context manager. A draft is not the value it describes: `match`,
`isinstance`, `==` and `hash` see the draft. Drafts cannot be copied or
pickled, and should not be stored in published state. Record drafts have
no methods. Their names belong to fields, which is why `finish` and
`original` are functions.

Drafts are native on cpython-grail and pure Python in grail-compat. They
make code shorter, not faster: finishing builds the same records as
`grail.replace` and `PVector.set`, and each edit also goes through a draft
object. On the native backend, Richards' scheduler runs about 1.3x slower
with a draft than with direct `replace` calls, so use those in the hottest
loops.

| Misuse | Error |
| --- | --- |
| `mutate` of anything else, or of a draft; `finish` or `original` of a non-draft | `TypeError` |
| Unknown or deleted record field | `AttributeError` |
| Index out of range, missing map key | `IndexError`, `KeyError` |
| Use after sealing or finishing, from another thread, `finish` of a child draft or an aborted draft | `RuntimeError` |
| Assigning a draft from a different `mutate` call | `ValueError` |
