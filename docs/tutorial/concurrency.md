---
sources:
  - src/grail/_pool.py
  - src/grail/_atom.py
  - tests/test_grail_pool.py
  - tests/test_grail_atom.py
  - docs/tutorial/workspace.py
---

# Check snapshots concurrently

The [checker from part 2](checking.md) reads a workspace and returns results.
Now we will run the same checks on worker threads. Then, as an optional step,
we will coordinate publication when an application has concurrent editors
and readers.

Continue in the same session, or run this compact setup in a fresh one. The
[complete runnable program](workspace.py) also brings all three parts together.

<details markdown="1">
<summary>Setup from parts 1 and 2</summary>

    >>> from grail import record, replace, generic, PMap
    >>> @record
    ... class Page:
    ...     title: str
    ...     text: str
    ...     links: tuple[str, ...] = ()
    >>> @record
    ... class Redirect:
    ...     target: str
    >>> @record
    ... class CheckResult:
    ...     path: str
    ...     problems: tuple[str, ...]
    >>> @generic
    ... def check(document, workspace):
    ...     raise TypeError(f"unsupported document type: {type(document).__name__}")
    >>> @check.impl
    ... def _(document: Page, workspace):
    ...     problems = []
    ...     if not document.text.strip():
    ...         problems.append("empty page")
    ...     for target in document.links:
    ...         if target not in workspace:
    ...             problems.append(f"missing target: {target}")
    ...     return tuple(problems)
    >>> @check.impl
    ... def _(document: Redirect, workspace):
    ...     if document.target not in workspace:
    ...         return (f"missing redirect target: {document.target}",)
    ...     return ()
    >>> def check_path(path, workspace):
    ...     return CheckResult(path, check(workspace[path], workspace))
    >>> def check_workspace(workspace):
    ...     return tuple(check_path(path, workspace) for path in sorted(workspace))
    >>> def warnings(results):
    ...     return tuple(
    ...         f"{result.path}: {problem}"
    ...         for result in results
    ...         for problem in result.problems
    ...     )
    >>> published = PMap({
    ...     "home": Page("Home", "Welcome!", ("guide", "install")),
    ...     "guide": Page("Guide", "Read this first.", ("home",)),
    ...     "faq": Page("FAQ", "Common questions."),
    ... })
    >>> working = published.set(
    ...     "home", replace(published["home"], links=("guide",))
    ... )

</details>

## Give each task the same snapshot

Each document can be checked independently, but it must resolve links against
the same workspace version as its peers. Capture that workspace in the task
function and submit the document paths through `Pool.map`:

    >>> from grail import Pool
    >>> def check_parallel(workspace, pool):
    ...     def run(path):
    ...         return check_path(path, workspace)
    ...     return tuple(pool.map(run, sorted(workspace)))
    >>> with Pool(2) as pool:
    ...     old_results = check_parallel(published, pool)
    ...     new_results = check_parallel(working, pool)
    >>> warnings(old_results)
    ('home: missing target: install',)
    >>> warnings(new_results)
    ()
    >>> old_results == check_workspace(published)
    True

The workspace passed to `check_parallel` is fixed for that call. Workers may
finish in any order, but `Pool.map` returns results in input order by default.
Sorting paths therefore gives the same report ordering as the sequential
checker.

Consume the result iterator inside the `with` block. Leaving the block shuts
down the pool; keeping one pool open for several batches avoids creating a
new set of threads for each check.

Notice what is absent:

- No lock around a page read: published pages and their children do not change.
- No shared list of warnings: each check returns its own immutable result.
- No Atom update for every document: the caller collects the returned values.

Task submission transfers references to the workers through the pool's
synchronization. Immutability is not a substitute for a supported way of
communicating references between threads.

## What this does and does not make faster

This example is intentionally too small to justify thread overhead. On a
GIL-enabled interpreter, these Python checks do not execute in parallel.
A free-threaded interpreter permits CPU parallelism, but useful speedup still
requires enough independent work to outweigh scheduling and allocation costs.

For a real checker, a task might parse and analyze a substantial document.
For many very small documents, grouping several checks per task may be more
appropriate. Measure before choosing task sizes or worker counts.

The useful lesson here is the structure: **share a snapshot, work privately,
return values**. The same worker functions remain usable sequentially.

## Optional: publish workspace versions through an Atom

A batch checker needs no shared mutable reference. A long-running application
may need one: editors publish new versions while readers check the currently
published workspace. Use an `Atom` to hold that current reference.

    >>> from grail import Atom
    >>> current = Atom(published)
    >>> reader_snapshot = current.value
    >>> def edit_page(workspace, path, **changes):
    ...     return workspace.set(path, replace(workspace[path], **changes))
    >>> updated = current.swap(edit_page, "home", links=("guide",))
    >>> current.value is updated
    True
    >>> with Pool(2) as pool:
    ...     earlier_report = check_parallel(reader_snapshot, pool)
    ...     latest_snapshot = current.value
    ...     latest_report = check_parallel(latest_snapshot, pool)
    >>> warnings(earlier_report)
    ('home: missing target: install',)
    >>> warnings(latest_report)
    ()

The old snapshot remains valid after publication. A reader captures
`current.value` **once per batch**, then passes that value through the whole
computation. Reading the Atom again inside every task could mix document
versions and link targets from different publications.

`swap` applies `edit_page` to the current workspace and tries to publish the
result atomically. If another editor publishes in the meantime, it retries
against the newer workspace rather than blindly replacing it with an edit
based on stale state. Concurrent changes to different pages can therefore
be retained. Conflicting edits to the same field still need an application
policy; Atom does not resolve their meaning for you.

The callback may run more than once. Ours only constructs values, so retries
are harmless. Do not log an edit, write a file, charge a customer, or mutate
shared scratch inside it. Perform external effects outside the callback,
with whatever additional coordination those effects require. Atom publication
is not a filesystem or database transaction.

Avoid reading `current.value`, constructing an edit, and then using `reset`
when concurrent edits must be preserved: an intervening publication could be
lost. A pure `swap` callback makes that read-modify-publish operation explicit.
An Atom also does not freeze its contents; our snapshots are safe to read
because we deliberately built them from immutable children.

## Run the complete program

Download [workspace.py](workspace.py). From the grail-compat checkout, run:

```sh
uv run docs/tutorial/workspace.py
```

Or run the downloaded file with your Grail-enabled interpreter. It prints:

```text
Published: ('home: missing target: install',)
Working: ()
Alternative: ()
Redirect: ('start: missing redirect target: removed',)
Retained reader: ('home: missing target: install',)
Current reader: ()
```

The program includes assertions that compare sequential and pooled results
and verify that earlier versions survive edits. Try adding an empty page,
fixing a redirect, or implementing another operation over both document types.

## Where to go next

You have used Grail's pieces together, rather than making every helper a
special API:

- **Records** describe immutable document and result values.
- **Ordinary functions** express edits and organize the computation.
- **A generic function** separates checking rules from document declarations.
- **A persistent map** retains published and alternative workspace versions.
- **A pool** distributes independent work over one snapshot.
- **An Atom**, only where needed, coordinates publication of new versions.

For larger batches of edits, see [transients](../guide/transients.md). For
more concurrency patterns, see [the concurrency guide](../guide/concurrency.md)
and [the aggregate example](../examples/aggregate.md). Consult the
[Pool](../reference/pool.md) and [Atom](../reference/atom.md) references for
their complete contracts.
