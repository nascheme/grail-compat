"""Copy the shared C sources into a cpython-proto checkout.

    python tools/sync_to_proto.py ../cpython-proto

The files land in Modules/_grail/, where Modules/Setup.stdlib.in builds them
as the _grail_pds module.  grail-compat is the source of truth: the copies
in the prototype are overwritten, never edited.
"""

import shutil
import sys
from pathlib import Path

FILES = ["grail_compat.h", "grail_hamt.h", "grail_hamt.c", "pds.c"]
HEADER = ("/* Copied from grail-compat src/grail/_c/ by tools/sync_to_proto.py;"
          " do not edit here. */\n")


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    proto = Path(argv[1])
    if not (proto / "Modules" / "Setup.stdlib.in").is_file():
        sys.exit(f"{proto} is not a CPython source tree")
    src = Path(__file__).resolve().parent.parent / "src" / "grail" / "_c"
    dest = proto / "Modules" / "_grail"
    dest.mkdir(exist_ok=True)
    for name in FILES:
        text = (src / name).read_text()
        (dest / name).write_text(HEADER + text)
        print(f"{src / name} -> {dest / name}")


if __name__ == "__main__":
    main(sys.argv)
