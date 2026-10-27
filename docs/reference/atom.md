---
sources:
  - grail-compat/docs/design/07-appendix-concurrency-toolkit.md
  - grail-compat/src/grail/_atom.py
  - grail-compat/tests/test_grail_atom.py
---

# Atom reference

`Atom` is a synchronized reference cell. Read a snapshot or replace the
reference atomically; mutable contents do not become thread-safe.

    >>> from grail import Atom
    >>> counter = Atom(0)
    >>> counter.add(2)
    2
    >>> counter.value
    2

`Atom(value)` (also `Atom(value=value)`) requires exactly one value. It accepts
any Python object. `Atom[T]` is an annotation, not a runtime restriction.

| Member | Contract |
| --- | --- |
| `value` | Read-only property; current object as an owned reference |
| `reset(value, /)` | Store and return supplied value, not the old value |
| `compare_and_set(expected, desired, /)` | Store desired only if current object **is** expected; return bool |
| `swap(f, /, *args, **kwargs)` | Retry `f(old, *args, **kwargs)` until identity CAS succeeds; return committed result |
| `add(delta, /)` | Atomically add exact builtin ints and return the new int |

Reads, resets and successful updates are atomic cell operations. A snapshot
keeps the old object alive after other threads replace it. CAS does not call
`==` or hash; an equal but distinct expected object does not match. CAS has
no version counter: changing away from and back to the same object can still
succeed (the ABA case).

`swap` calls the callback without holding a cell lock. It can run repeatedly
under contention, including when a reentrant callback changes the cell. Use
a pure callback: no I/O, shared mutation, or one-shot actions. Callback
exceptions propagate without committing that attempt's candidate; they do
not undo other operations. Non-callable or missing `f` raises `TypeError`.

`add` accepts only exact builtin ints for both current value and delta.
Bools, subclasses, floats and coercible objects raise `TypeError` without
that operation committing an update. Results have ordinary arbitrary-precision
integer range. No user arithmetic or Grail operator registration is consulted.
Allocation failure leaves the attempt uncommitted.

Cells compare by identity and are unhashable (`TypeError`). The type is
immutable and final. Instances have no dictionary or weak references.
Assignment to `value` raises `AttributeError`; use `reset`. `repr` is
`Atom(value_repr)` and handles recursive contents.

`copy.copy(a)` creates a distinct Atom holding the same snapshot reference.
`copy.deepcopy(a)` creates a new cell and copies the snapshot, preserving
cycles through the normal memo. Pickle reconstructs a new cell and its contents,
including cycles. None of these operations snapshots an entire mutable graph
atomically.

Guides: [atoms](../guide/atoms.md), [concurrency](../guide/concurrency.md).
