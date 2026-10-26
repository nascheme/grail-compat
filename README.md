# grail-compat

Implementation of the Project Grail API (`import grail`) for existing
CPython versions (3.10+).  It follows the native prototype's semantics; see
the docstring in `src/grail/__init__.py` for the known deviations.

On CPython 3.14+ the persistent `PMap` and `PSet` are the prototype's C
implementation (`grail._pds_native`, built over a copy of CPython's HAMT in
`src/grail/_c/`).  Everything else, and `PMap`/`PSet` on older versions, is
pure Python.  `grail.pds_backend` is `"native"` or `"python"`; set
`GRAIL_PURE=1` to force the pure-Python collections.

The pure-Python parts add overhead the native runtime does not have, so
timings taken with this package say nothing about Grail performance.

## Building and testing

The build uses meson-python.  A C compiler is needed on 3.14+ unless the
native backend is disabled (`-Csetup-args=-Dnative=disabled`); older
versions build a pure-Python wheel.

    uv venv -p 3.14 .venv-3.14
    uv pip install --python .venv-3.14 . pytest
    cd tests && ../.venv-3.14/bin/python -m pytest

Run the tests from `tests/` so that the installed package, not `src/`, is
imported.  They are the specification of Grail's behaviour and are
backend-agnostic: run them with `GRAIL_PURE=1` for the pure-Python
collections, or with a native Grail interpreter (and without this package on
`sys.path`) to check it against the same contract.  The prototype runs them
as copies in its `Lib/test/` (see below); tests of its native internals live
only there.

Leak and sanitizer checks for the C code, with a debug CPython
(`uv python install 3.14+debug`):

    uv pip install --python .venv-3.14d -Csetup-args=-Dbuildtype=debug . pytest
    .venv-3.14d/bin/python -m test --testdir tests -R 3:3 test_grail_pds test_grail_hamt

    uv pip install --python .venv-asan -Csetup-args=-Dbuildtype=debug \
        -Csetup-args=-Db_sanitize=address,undefined -Csetup-args=-Db_lundef=false . pytest
    cd tests && LD_PRELOAD="$(gcc -print-file-name=libasan.so) $(gcc -print-file-name=libubsan.so)" \
        ASAN_OPTIONS=detect_leaks=0 ../.venv-asan/bin/python -m pytest test_grail_pds.py test_grail_hamt.py

## Sharing code with cpython-proto

`src/grail/_c/` is the single source for the prototype's `_grail_pds`
module.  The same files build into the interpreter with `Py_BUILD_CORE`
(module name `_grail_pds`, internal fast paths) and here as an extension
(`grail._pds_native`).  Likewise `tests/test_grail*.py` are the prototype's
`Lib/test/test_grail*.py`, except its native-only tests
(`test_grail_native.py`, the specialization and JIT tests).  Copy both to
the prototype with

    python tools/sync_to_proto.py ../cpython-proto

and do not edit the copies there.

## License

`src/grail/_c/grail_hamt.c` and `grail_hamt.h` are derived from CPython's
`Python/hamt.c` and are under the Python Software Foundation License.
