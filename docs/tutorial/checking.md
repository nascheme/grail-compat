---
sources:
  - src/grail/_core.py
  - tests/test_grail.py
  - docs/reference/generic.md
---

# Extend the checker

In [part 1](index.md), we made a workspace with a missing link and two possible
fixes. Now we will check those versions, then add a second kind of document
without changing the workspace traversal.

Continue in the same Python session. If you are starting a fresh session,
run this setup first:

<details markdown="1">
<summary>Setup from part 1</summary>

    >>> from grail import record, replace, PMap
    >>> @record
    ... class Page:
    ...     title: str
    ...     text: str
    ...     links: tuple[str, ...] = ()
    >>> home = Page("Home", "Welcome!", ("guide", "install"))
    >>> published = PMap({
    ...     "home": home,
    ...     "guide": Page("Guide", "Read this first.", ("home",)),
    ...     "faq": Page("FAQ", "Common questions."),
    ... })
    >>> working = published.set(
    ...     "home", replace(published["home"], links=("guide",))
    ... )
    >>> alternative = published.set(
    ...     "install", Page("Install", "Follow the setup instructions.")
    ... )

</details>

## Define the checking operation

A page check will report empty text and missing link targets. It needs the
page and the workspace snapshot in which to interpret its links.

Define a generic function for that operation, with an explicit error for
unsupported document types:

    >>> from grail import generic
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
    >>> check(published["home"], published)
    ('missing target: install',)
    >>> check(working["home"], working)
    ()

`@check.impl` registers an implementation using the first parameter's
annotation. Calling `check` chooses an implementation from the type of
`document`; the second argument supplies context but does not participate in
dispatch. Keep parameter names and calling conventions consistent with the
original function.

The list is private scratch owned by this call. Building it with `append`
is simple and safe; there is no need to make every intermediate step
persistent. The function returns an immutable tuple that can be shared.

## Collect results for a workspace

A result pairs a document path with its problems:

    >>> @record
    ... class CheckResult:
    ...     path: str
    ...     problems: tuple[str, ...]
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
    >>> warnings(check_workspace(published))
    ('home: missing target: install',)
    >>> warnings(check_workspace(working))
    ()
    >>> warnings(check_workspace(alternative))
    ()

`PMap` iteration is unordered, so we sort paths to make reports reproducible.
The checker neither edits documents nor writes output: it returns values.
The application can decide how to display or store them.

Checking the edited versions did not repair the published version behind our
backs. We can still reproduce its warning:

    >>> warnings(check_workspace(published))
    ('home: missing target: install',)

That is the application-level benefit of retaining a snapshot: an earlier
version remains a meaningful input, not just a reference to changing state.

## Support redirects without editing Page

A workspace may also contain redirects. A redirect has no page text to check;
its destination must exist instead.

    >>> @record
    ... class Redirect:
    ...     target: str
    >>> @check.impl
    ... def _(document: Redirect, workspace):
    ...     if document.target not in workspace:
    ...         return (f"missing redirect target: {document.target}",)
    ...     return ()
    >>> with_redirect = working.set("start", Redirect("home"))
    >>> warnings(check_workspace(with_redirect))
    ()
    >>> broken_redirect = with_redirect.set("start", Redirect("removed"))
    >>> warnings(check_workspace(broken_redirect))
    ('start: missing redirect target: removed',)

We added a record and an implementation. The `Page` declaration, page checker,
`check_path`, and workspace traversal did not change. This is why a generic is
useful here: the traversal expresses *check this document*, and implementations
supply the rules for each document type.

The operation belongs to the module, not either record. In a larger program,
one module could define `check`, and another could import it and register
support for its own document type. Import the existing generic to extend it;
creating a new generic with the same name would create a different operation.

Register implementations during program setup, before submitting checks to
workers. Generic registration is synchronized, but the code inside an
implementation is still responsible for its own concurrency behavior.

These checks only test direct target existence. They do not follow redirects,
find redirect cycles, parse URLs, or validate an entire publishing system.
Those would be additional application rules, not additional Grail machinery.

## Why not methods or an ordinary function?

An ordinary function is fine for a single document type. A function with an
`isinstance` chain would also work for this small example. The generic becomes
useful when new document types should supply their own behavior without
editing one central conditional or placing every operation on the data type.

Records deliberately keep their declarations focused on data. We can add
other operations later, such as rendering or extracting search terms, without
changing the document records. Not every helper needs to be generic:
`warnings` and `check_path` remain ordinary functions.

Next, [run checks through a thread pool](concurrency.md). The checker already
has the right shape: it reads one immutable snapshot and returns immutable
results, with no shared accumulator.

Details: [generic functions guide](../guide/generic-functions.md) and
[generic reference](../reference/generic.md).
