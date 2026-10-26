"""Check executable docs, source paths, and public-name reference coverage.

Run from any directory with the intended interpreter. No third-party imports
are needed beyond the grail implementation being tested. Source paths assume
the parent Project Grail workspace; use --workspace for another source root.
Name coverage is a tripwire, not a semantic completeness proof.
"""

import argparse
import doctest
from pathlib import Path
import re

import grail


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PAGES = {
    "PMap": "pmap.md",
    "PSet": "pset.md",
    "PVector": "pvector.md",
    "FloatVector": "numeric-vectors.md",
    "IntVector": "numeric-vectors.md",
    "Atom": "atom.md",
    "ops": "hooks-and-ops.md",
}


def code_names(text):
    """Names mentioned in inline code, not just in prose or source metadata."""
    return set(re.findall(r"\b\w+\b", " ".join(re.findall(r"`([^`\n]+)`", text))))


def check_reference():
    errors = []
    reference = ROOT / "docs" / "reference"
    all_names = code_names("\n".join(p.read_text() for p in reference.glob("*.md")))
    for name in grail.__all__:
        if name not in all_names:
            errors.append(f"reference: missing exported name {name}")
    for name, filename in REFERENCE_PAGES.items():
        names = code_names((reference / filename).read_text())
        for attr in dir(getattr(grail, name)):
            if not attr.startswith("_") and attr not in names:
                errors.append(f"reference/{filename}: missing {name}.{attr}")
    # Generic is returned by generic(), but is not itself an exported name.
    generic = grail.generic(lambda obj: obj)
    names = code_names((reference / "generic.md").read_text())
    for attr in dir(generic):
        if (not attr.startswith("_") or attr == "__name__") and attr not in names:
            errors.append(f"reference/generic.md: missing Generic.{attr}")
    return errors


def check_page(path, workspace):
    errors = []
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(ROOT)
    if not text.isascii():
        errors.append(f"{relative}: non-ASCII text")
    if not text.startswith("---\nsources:\n"):
        return errors + [f"{relative}: missing sources front matter"]
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return errors + [f"{relative}: unterminated front matter"]
    front, body = parts
    sources = re.findall(r"^  - (.+)$", front, re.MULTILINE)
    if not sources:
        errors.append(f"{relative}: empty sources")
    for source in sources:
        source_path = workspace / source.split("#", 1)[0]
        if not source_path.is_file():
            errors.append(f"{relative}: missing source {source}")
    if not re.search(r"^    >>> ", body, re.MULTILINE):
        errors.append(f"{relative}: no executable example")
    # Python examples use prompts so pytest and the stdlib runner agree.
    if re.search(r"^```(?:python|py)\s*$", body, re.MULTILINE):
        errors.append(f"{relative}: use doctest prompts instead of untested Python fences")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT.parent)
    parser.add_argument("--no-doctest", action="store_true")
    args = parser.parse_args()
    files = sorted((ROOT / "docs").rglob("*.md"))
    errors = check_reference()
    failed = attempted = 0
    for path in files:
        errors.extend(check_page(path, args.workspace))
        if not args.no_doctest:
            result = doctest.testfile(
                str(path), module_relative=False, encoding="utf-8",
                globs={"__name__": "__main__"},
                optionflags=doctest.ELLIPSIS,
            )
            failed += result.failed
            attempted += result.attempted
    for error in errors:
        print(error)
    print(f"{len(files)} pages; {attempted} doctest statements; {failed} failed; "
          f"{len(errors)} source/style/reference errors; backend={grail.backend}; "
          f"pds={getattr(grail, 'pds_backend', 'native')}")
    return 1 if failed or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
