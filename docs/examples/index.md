---
sources:
  - benchmarks/README.md
---

# Worked program designs

The benchmark programs show how records, generics and snapshots fit into
larger applications. These walkthroughs focus on design choices, not timing
claims. Their small examples isolate a pattern; they do not run the full
benchmark workload.

    >>> from grail import PMap
    >>> dict(PMap({"snapshots": 1}))
    {'snapshots': 1}

- [Ray tracer](raytrace.md): separate geometric types and open operations.
- [Richards](richards.md): pure task steps and a snapshot kernel.
- [DeltaBlue](deltablue.md): graph IDs and drafts of the solver.
- [Aggregate](aggregate.md): private folds and a pure merge.
- [Michi](michi.md): path-copying trees and task-local randomness.

Find the complete programs under `benchmarks/` in the grail-compat repository. Each directory
has `idiomatic.py`; most also have `upstream.py`. Some keep an earlier
`hybrid.py` with private mutable control state. Aggregate has no vendored
upstream file: its serial baseline accumulates values in a local dict.
The benchmark README records upstream provenance and licenses.

From the grail-compat checkout you can run the benchmark correctness suite:

```sh
uv run python -m unittest discover -s benchmarks/tests
```

Default workloads target the native prototype and can be slow with
compatibility implementations. Source-browser links await a repository URL;
the paths on these pages identify exact local files. Older design notes
sometimes describe what is now `hybrid.py`, not the current idiomatic version.
See [porting](../guide/porting.md) for a smaller migration checklist.
