---
sources:
  - README.md
  - pyproject.toml
  - docs/design/01-pep-draft-records.md
  - docs/design/02-pep-draft-generic-functions.md
  - src/grail/__init__.py
  - src/grail/_core.py
  - tests/test_grail.py
---

# Getting started

Install the local grail-compat checkout to try Grail without a custom
interpreter. The distribution is named grail-compat; the import is `grail`.

    >>> from grail import record, generic
    >>> @record
    ... class Greeting:
    ...     name: str
    >>> @generic
    ... def greet(obj):
    ...     return "Hello"
    >>> @greet.impl
    ... def _(obj: Greeting):
    ...     return "Hello, " + obj.name
    >>> greet(Greeting("Ann"))
    'Hello, Ann'

## Install from source

From the grail-compat checkout, run these shell commands:

```sh
uv sync --group dev
uv run python -c 'import grail; print(grail.backend, grail.pds_backend)'
```

Stock CPython 3.10+ is supported. On 3.14+ a C compiler is needed for the native
map/set extension. To disable that extension when installing into a separate
project, use a local source path:

```sh
uv pip install /path/to/grail-compat -Csetup-args=-Dnative=disabled
```

This assumes an active uv-managed environment. Setting `GRAIL_PURE=1` selects
the pure-Python collections at import; it does not disable compilation during
installation. No package-index release is assumed by these instructions.

## Native prototype

The prototype CPython implementation of Grail is the `grail` branch of
[github.com/nascheme/cpython](https://github.com/nascheme/cpython/tree/grail). Build it as CPython, configured with
`--disable-gil` for the free-threaded build.

Run your program with the interpreter you built, without installing
grail-compat or adding its `src` directory to PYTHONPATH. The native prototype
provides `grail` as part of the interpreter. See [contributing](contributing.md)
for source build and test notes, and [backends](backends.md) for differences.

Continue with the [tutorial](tutorial/index.md).
