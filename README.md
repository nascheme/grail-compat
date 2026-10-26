# grail-compat

Pure-Python implementation of the Project Grail API (`import grail`) for
existing CPython versions (3.10+).  It follows the native prototype's
semantics; see the docstring in `src/grail/__init__.py` for the known
deviations.

It adds Python-level overhead the native runtime does not have, so timings
taken with it say nothing about Grail performance.

    uv run pytest

The tests are backend-agnostic: run them with a native Grail interpreter
(and without this package on `sys.path`) to check it against the same
contract.
