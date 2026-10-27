---
sources:
  - docs/design/00-project-grail-overview.md
  - docs/design/01-pep-draft-records.md
  - docs/design/02-pep-draft-generic-functions.md
  - docs/design/03-pep-draft-record-syntax.md
  - docs/design/05-design-persistent-collections.md
  - src/grail/__init__.py
  - docs/design/07-appendix-concurrency-toolkit.md
---

# Background and planned work

Grail explores an object model with explicit immutable data and shared
updates. The current library is usable for experiments; design drafts also
contain proposals that are not implemented.

    >>> from grail import record, is_value_record
    >>> @record
    ... class Size:
    ...     width: int
    ...     height: int
    >>> is_value_record(Size)
    True

## Why records?

Closed fields, immutable bindings and immutable record types give readers
stable data and give a runtime fewer mutable assumptions to guard. Value
annotations can also show that an instance holds only recognized values.
These are design opportunities, not a promise that every idiomatic Grail version runs faster.

## Why no methods or inheritance?

Records separate data from behavior. Generic functions keep extension in
module namespaces and let another package add an implementation without
changing a type. Composition and unions replace record inheritance. Ordinary
classes remain appropriate for mutable objects and existing protocols.
The no-method choice is a design choice, not a prerequisite for optimization.

## Are annotations checked?

No. The implementation records conformance, rather than rejecting mismatches.
Use static checking or explicit validation factories. See
[value records](guide/value-records.md) for the smaller implemented value whitelist.
Persistent collection annotations are not value fields yet.

## Are atoms transactions?

An Atom atomically changes one reference. Put related state into one immutable
snapshot if it must change together. It does not transact across separate
atoms or external systems, and swap callbacks can retry.

## Planned, not available

A dedicated `record` statement and later sum-type syntax are proposals, not
syntax accepted by this package. Promise, Grail-specific channels, random
stream helpers are also proposals, not exported APIs; use `queue.SimpleQueue`
and an explicit `random.Random` per task today. The executor is available as
[`grail.Pool`](reference/pool.md), with `pmap` and `parallel_reduce`. A stable opaque
C API is separate design work; this site covers only the Python API.

## Design sources

The [design documents](design/index.md) are included in this repository and site:

- [Overview and memory model](design/00-project-grail-overview.md).
- [Records](design/01-pep-draft-records.md): rationale and proposed semantics.
- [Generic functions](design/02-pep-draft-generic-functions.md): open protocols.
- [Record syntax](design/03-pep-draft-record-syntax.md): proposed statement.
- [Persistent collections](design/05-design-persistent-collections.md).
- [Concurrency toolkit](design/07-appendix-concurrency-toolkit.md).

These are proposals, not claims that every feature ships. See
[contributing](contributing.md) for maintenance commands.
