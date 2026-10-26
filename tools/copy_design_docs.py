"""Copy the Project Grail design docs into docs/design/.

    python tools/copy_design_docs.py ../docs/design

The source of truth is docs/design/ in the Project Grail workspace (mirrored
to the "Project Grail" Claude Doc); the copies here are overwritten, never
edited.  Links to the workspace's docs/implementation/ notes, which are not
published with this site, become plain path references.  docs/design/index.md
is written by hand and is not touched.
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = ("<!-- Copied from the Project Grail docs/design/ by"
          " tools/copy_design_docs.py; do not edit here. -->\n\n")
IMPL_LINK = re.compile(r"\[([^\]]*)\]\(\.\./implementation/([^)]+)\)")


def fix_links(text):
    return IMPL_LINK.sub(
        lambda m: f"`docs/implementation/{m.group(2)}` (Project Grail workspace)",
        text)


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    src = Path(argv[1])
    if not (src / "00-project-grail-overview.md").is_file():
        sys.exit(f"{src} is not the Project Grail docs/design directory")
    dest = ROOT / "docs" / "design"
    dest.mkdir(exist_ok=True)
    for path in sorted(src.glob("[0-9][0-9]-*.md")):
        (dest / path.name).write_text(HEADER + fix_links(path.read_text()))
        print(f"{path} -> {dest / path.name}")
    for path in sorted(src.glob("*.png")):
        shutil.copyfile(path, dest / path.name)
        print(f"{path} -> {dest / path.name}")


if __name__ == "__main__":
    main(sys.argv)
