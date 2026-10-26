"""Download michi's large pattern tables (not vendored: about 53 MB).

The files are the `patterns` submodule of michi,
https://github.com/nascheme/michi-patterns (MIT, see
../LICENSES/michi-patterns-LICENSE), pinned to one commit and checked by
sha256.  By default they go to michi/patterns/ next to this script, where
the tests and the Project Grail harness look for them.

    python fetch_patterns.py [--dest DIR]

Files already present with the right hash are skipped.
"""

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

COMMIT = "a5cb01c21af307bf3727d85ecefbf729db78cc60"
URL = f"https://raw.githubusercontent.com/nascheme/michi-patterns/{COMMIT}/"
FILES = {   # name: (sha256, size)
    "prob.zst": ("c3d65d7af13aedbd5f6f46bbdace7280079c1c95784c6b0f7f06f9e6981e9e6d",
                 4891979),
    "spat.zst": ("4e120e9a4ba1ceda465f6c9b58e78b41ffb986491fab1e3eaf5eb701cdfa92fd",
                 48640740),
}
DEFAULT_DEST = Path(__file__).resolve().parent / "patterns"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def fetch(name, dest):
    digest, size = FILES[name]
    target = dest / name
    if target.exists():
        if sha256(target) == digest:
            print(f"{target}: present, hash ok")
            return
        print(f"{target}: hash mismatch, downloading again")
    url = URL + name
    print(f"{target}: downloading {url} ({size} bytes)")
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=dest)
    try:
        h = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url) as response:
            while chunk := response.read(1 << 20):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != digest:
            raise SystemExit(f"error: {url}: sha256 {h.hexdigest()}, expected {digest}")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                        help=f"destination directory (default: {DEFAULT_DEST})")
    args = parser.parse_args(argv)
    args.dest.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        fetch(name, args.dest)


if __name__ == "__main__":
    sys.exit(main())
