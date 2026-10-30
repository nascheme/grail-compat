---
sources:
  - docs/design/00-project-grail-overview.md
  - README.md
  - docs/design/01-pep-draft-records.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
  - docs/design/07-appendix-concurrency-toolkit.md
  - src/grail/_atom.py
  - tests/test_grail_atom.py
---

# Grail

Grail is an experimental Python API for closed immutable records, extensible
generic functions, persistent collections, and synchronized reference cells.
Try it on stock CPython 3.10+ with grail-compat or on the native CPython
prototype (the `grail` branch of [github.com/nascheme/cpython](https://github.com/nascheme/cpython/tree/grail)). The API and design are still changing; this is not released Python
language functionality.

    >>> from grail import record, replace, generic, PMap, Atom
    >>> @record
    ... class Item:
    ...     name: str
    ...     count: int
    >>> @generic
    ... def label(obj):
    ...     return "unknown"
    >>> @label.impl
    ... def _(obj: Item):
    ...     return f"{obj.name}: {obj.count}"
    >>> stock = Atom(PMap({"tea": Item("tea", 2)}))
    >>> def receive(items):
    ...     old = items["tea"]
    ...     return items.set("tea", replace(old, count=old.count + 1))
    >>> updated = stock.swap(receive)
    >>> label(updated["tea"])
    'tea: 3'

The key Grail idea is that data is held by immutable records. Records do not
have methods. Instead, you have generic functions that dispatch off the first
argument type. This allows type-dependent behavior and easy building of custom
protocols. There are new collection types; when updated, they return new
snapshots. Atoms coordinate changes to a shared reference, not mutations inside
arbitrary objects.

- [Why Grail?](why-grail.md) Faster code, better scaling, and fewer opportunities for races.
- [Get started](getting-started.md) with a local installation.
- [Follow the tutorial](tutorial/index.md) to build a small inventory tracker.
- [Declare records](guide/records.md), [extend operations](guide/generic-functions.md),
  or [share snapshots](guide/concurrency.md).
- Use the [records reference](reference/records.md),
  [generic reference](reference/generic.md), and collection references for exact rules.
- Read [backend differences](backends.md) before comparing behavior or timings.
- Read [background](background.md) for design goals and planned work.
- Designing Grail itself? The [design documents](design/index.md) hold the
  overview and PEP drafts.

These pages describe tested behavior, not every feature proposed by the design
drafts. Compatibility timings are not evidence of native Grail performance.
