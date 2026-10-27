# Documentation discrepancies and follow-up

Not published. Tests and current implementation take precedence over design
proposals. This list records differences; it does not propose changing runtime
behavior as part of the documentation pass.

## Implemented behavior versus design text

| Topic | Sources | What the user docs say |
| --- | --- | --- |
| Value whitelist | `docs/design/01-pep-draft-records.md`, both `grail/__init__.py` facades, `tests/test_grail.py` | Only None/bool/int/float/str/bytes, already-defined value records, recognized unions and typed tuples have value plans. Complex, frozenset, recursive forward references and collection annotations are not recognized today. |
| Typed collection conformance | `docs/design/05-design-persistent-collections.md`, facade `_value_plan` | Persistent and numeric collection fields are ordinary fields today. Immutable application snapshots are not necessarily value record types. In particular, benchmark Solver/Patterns records need this distinction. |
| Compatibility availability | Overview delivery section and records/generics/collections drafts versus this repository | grail-compat exists and supports stock CPython 3.10+. Several design sections still describe it as future work. |
| Atom.add status | Overview roadmap/memory-model table and toolkit appendix versus `tests/test_grail_atom.py`, `_atom.py`, native implementation | `add` is implemented, exact-int-only, arbitrary precision, and ignores open arithmetic. Some paragraphs still call it proposed; the appendix status already calls it implemented. |
| Collection hash caching | Collections draft versus `_pds.py`, `_vector.py`, `_numvec.py` and tests | Do not repeat the draft's blanket claim that collection hash caching is unimplemented. Current implementations cache collection hashes. Child hash stability remains the application's responsibility. The native runtime caches value-record hashes; the shim does not. |
| Transient implementation description | Collections API section | The draft contains both path-copy-only and exclusive-node-editing descriptions. The user docs specify lifecycle/API, not a universal O(1) finish or optimal edit complexity claim. |
| Protocol and comparison surface | Generic-functions abstract/specification versus facade `ops`, `tests/test_grail.py` | Repr, equality, hash and six arithmetic generics are available. There is no ordering/comparison registration API. |
| Operator protection | Design protected-builtin rules versus compatibility `_core.Generic.register` | Runtime protection is based on current implementation rules (including static-type checks), not a finalized list of every protected extension/builtin type. Do not generalize it into a stable complete classification. |
| No registration unary fallback | Facade deviation list | Unregistered native record unary operations currently recurse; compatibility raises TypeError. Docs advise registering before use, not relying on either failure mode. |
| Substitutability wording | Overview/design principles and records substitutability section | Some overview text says this is not a public function; both facades export `substitutable` for debugging/tests. |

## Benchmark documentation

Current program designs are in `benchmarks/README.md` and the runnable source;
measured native results and limitations are summarized in
`docs/design/06-cpython-prototype.md`. Do not publish projected speedups or
claims that records avoid all shared reference-count writes/GC tracking.

## First-pass scope and open decisions

- The published site is https://python.ca/cpython-grail. Source paths refer
  to this repository; native source links use the public CPython fork.
- The tutorial uses a shop inventory, exact integer counts, and a deliberately
  simple Item + int operator. It is runnable page by page; review whether the
  operator feels natural enough for the introductory story.
- Benchmark pages are design walkthroughs with small executable adaptations,
  not full differential runs or literal side-by-side copies of entire programs.
  A later pass can deepen the annotated source excerpts after source URLs exist.
- A standalone grail-compat checkout can build, doctest and validate all source
  metadata. Documentation must not depend on a separate development workspace.
- The reference name checker checks exports, public attributes of exported
  types/ops, and the Generic object's public attributes against inline-code
  mentions on their reference pages. This is not proof of semantic completeness;
  exception propagation and callback-heavy edge cases still need human review.
- This pass checks the locally available CPython 3.14 compatibility environment
  and built native prototype. The full supported-version matrix, particularly
  Python 3.10/3.11 without numeric buffer export, remains a CI follow-up.
- The oldest-version docs toolchain may resolve an older Zensical. Only the
  locally resolved version was built here. No deployment or CI was added.

## Validation of this pass

- `uv run zensical build --strict`: no issues.
- `uv run pytest --doctest-glob='*.md' docs/`: 40 pages pass.
- Same pytest command with `GRAIL_PURE=1`: 40 pages pass.
- `uv run tools/check_docs.py`: 211 doctest statements pass with compatibility
  native PMap/PSet, and source/style/reference checks pass.
- Same checker with `GRAIL_PURE=1`: 211 statements and all checks pass.
- The checker can also run with a separately built native Grail interpreter,
  without pytest or compatibility imports.

## Record-update naming follow-up

`grail.replace(obj, /, **changes)` is now the documented everyday record-update
API and is exported by both facades. It accepts Grail records only; the
`__replace__` hook remains for `copy.replace` interoperability. Tutorials and
small examples use the public function. Existing benchmark sources still use
`copy.replace`; historical measurements and descriptions retain that spelling.
The shared record tests cover the new entry point, including conformance,
field names, shared children, factory suppression, and non-record rejection.
