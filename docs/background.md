---
sources:
  - docs/design/00-project-grail-overview.md
  - docs/design/01-pep-draft-records.md
  - docs/design/02-pep-draft-generic-functions.md
  - docs/design/03-pep-draft-record-syntax.md
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/__init__.py
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
These are design opportunities, not a promise that every rewrite runs faster.

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
stream helpers and an executor are also proposals, not exported APIs.
Use `queue.SimpleQueue` and standard-library executors today. A stable opaque
C API is separate design work; this site covers only the Python API.

## Design sources

The source-of-truth drafts live in the parent Project Grail checkout:

- `docs/design/00-project-grail-overview.md`: overview and memory model.
- `docs/design/01-pep-draft-records.md`: record rationale and proposed semantics.
- `docs/design/02-pep-draft-generic-functions.md`: open generic protocols.
- `docs/design/03-pep-draft-record-syntax.md`: proposed record statement.
- `docs/design/05-design-persistent-collections.md`: collections and typed conformance.
- `docs/design/07-appendix-concurrency-toolkit.md`: planned library helpers.

Public source URLs are not configured yet. These paths are navigation aids
for the workspace checkout, not claims that the proposals ship. See
[contributing](contributing.md) for local maintenance commands.
