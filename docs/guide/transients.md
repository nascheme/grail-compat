---
sources:
  - docs/design/05-design-persistent-collections.md
  - src/grail/_pds.py
  - tests/test_grail_pds.py
  - src/grail/_vector.py
  - tests/test_grail_vector.py
  - src/grail/_numvec.py
  - tests/test_grail_numvec.py
---

# Build collections with transients

A transient is a temporary mutable builder owned by its creating thread.
Finish it once to publish an immutable collection.

Collection `.mutate()` methods return the builders described here. To make
several updates to a record, use a
[`grail.mutate(record)` draft](copying.md#make-several-updates-with-a-draft)
instead. Drafts use the function `grail.finish(draft)`, not a `.finish()`
method, and can also edit nested records, `PVector` elements and `PMap` entries.

    >>> from grail import PMap
    >>> base = PMap({"tea": 2})
    >>> with base.mutate() as edits:
    ...     edits["tea"] = 3
    ...     edits["coffee"] = 1
    >>> result = edits.finish()
    >>> result["tea"], base["tea"]
    (3, 2)

Successful context exit seals the builder: you cannot read or edit it, but
you can still call `finish()`. An exceptional exit aborts an open builder;
there is no result to finish. The exception is not suppressed. You can also
use a builder without `with` and call `finish()` directly.

    >>> edits.finish()
    Traceback (most recent call last):
    ...
    RuntimeError: transient is no longer open

Using a builder from another thread, after finish or abort, or reentrantly
from a callback raises `RuntimeError`. Nested entry of the same context is
also an error. Builders are not iterable, hashable, or picklable; do not put
them in published state. A record can hold a builder in an ordinary field
for a private batch, but that does not make it shareable.

| Builder | Reads and edits |
| --- | --- |
| Map | Hashable keys, subscription, assignment, deletion, membership, length |
| Set | `add`, `discard`, membership, length |
| Vector and numeric vector | Integer subscription and assignment, `append`, `extend`, `pop`, length |

Vector builders do not slice or delete arbitrary positions. Their `pop`
returns the removed element. Append, extend and set edits return None.
PVector extension from a failing iterator can keep the already-added items;
numeric-vector extension converts the whole input first and adds nothing on
conversion failure. An error in a single operation does not by itself abort
a builder, unless it escapes the context manager.

References: [PMap](../reference/pmap.md), [PSet](../reference/pset.md),
[PVector](../reference/pvector.md), [numeric vectors](../reference/numeric-vectors.md).
