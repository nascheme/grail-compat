"""Copy the shared C sources and tests into a cpython-proto checkout.

    python tools/sync_to_proto.py ../cpython-proto

The C files land in Modules/_grail/, where Modules/Setup.stdlib.in builds
them as the _grail_pds module.  The tests (tests/test_grail*.py), which
specify Grail's behaviour for every implementation, land in Lib/test/.
grail-compat is the source of truth: the copies in the prototype are
overwritten, never edited.  The prototype's own tests of native internals
(test_grail_native.py, the specialization and JIT tests) are not touched.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
C_FILES = ["grail_compat.h", "grail_hamt.h", "grail_hamt.c", "pds.c"]
C_HEADER = ("/* Copied from grail-compat src/grail/_c/ by tools/sync_to_proto.py;"
            " do not edit here. */\n")
TEST_HEADER = ("# Copied from grail-compat tests/ by tools/sync_to_proto.py;"
               " do not edit here.\n")


def copy(src, dest, header):
    dest.write_text(header + src.read_text())
    print(f"{src} -> {dest}")


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    proto = Path(argv[1])
    if not (proto / "Modules" / "Setup.stdlib.in").is_file():
        sys.exit(f"{proto} is not a CPython source tree")
    modules = proto / "Modules" / "_grail"
    modules.mkdir(exist_ok=True)
    for name in C_FILES:
        copy(ROOT / "src" / "grail" / "_c" / name, modules / name, C_HEADER)
    for test in sorted((ROOT / "tests").glob("test_grail*.py")):
        copy(test, proto / "Lib" / "test" / test.name, TEST_HEADER)


if __name__ == "__main__":
    main(sys.argv)
