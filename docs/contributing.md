---
sources:
  - grail-compat/README.md
  - grail-compat/pyproject.toml
  - grail-compat/tools/sync_to_cpython_grail.py
  - grail-compat/tools/check_docs.py
  - grail-compat/zensical.toml
---

# Build, test and improve the docs

Work from the grail-compat checkout. Build the installed package and run the
same behavior tests against Python collections and the native implementations.
The documentation examples are also tests.

    >>> import grail
    >>> "record" in grail.__all__
    True

## Compatibility tests

```sh
uv sync --group dev
uv run --directory tests pytest
GRAIL_PURE=1 uv run --directory tests pytest
```

Use CPython 3.14+ and a compiler to exercise the native PMap/PSet extension.
The project builds with meson-python. Older Pythons build a pure-Python wheel;
`-Csetup-args=-Dnative=disabled` disables the extension explicitly. See the
checkout README for debug builds, sanitizer runs and leak tests.

The native interpreter belongs to the sibling cpython-grail checkout, a
clone of the `grail` branch of [github.com/nascheme/cpython](https://github.com/nascheme/cpython/tree/grail).
Follow that checkout's build notes for its free-threaded configuration.
Do not install grail-compat into it. Its unittest runner can run the shared
contract tests without pytest:

```sh
../cpython-grail/build-grail/python -m unittest discover -s tests -p 'test_grail*.py'
```

Shared C map/set code is edited in `src/grail/_c/`, not in prototype copies.
From grail-compat, synchronize with:

```sh
uv run tools/sync_to_cpython_grail.py ../cpython-grail
```

## Documentation

```sh
uv run zensical build --strict
uv run zensical serve
uv run pytest --doctest-glob='*.md' docs/
GRAIL_PURE=1 uv run pytest --doctest-glob='*.md' docs/
uv run tools/check_docs.py
../cpython-grail/build-grail/python tools/check_docs.py
```

The last two commands run a standard-library-only checker: examples, source
paths, ASCII style and public-name reference coverage. They work without
pytest in the prototype. Reference coverage is a maintenance tripwire, not
a proof that prose specifies every edge case. Markdown doctests use indented
prompts, avoiding fence markers being interpreted as expected output.
Shell blocks are setup instructions, not Python doctests; substitute your
local paths before running installation commands.

Each page's front matter lists source paths relative to the parent Project
Grail workspace. Review pages when their listed tests, implementation or
design sources change. Tests decide what the docs may claim. Keep unresolved
design differences in `docs-discrepancies.md`, outside the published site.
Hosting and stable source-browser URLs remain to be configured.
