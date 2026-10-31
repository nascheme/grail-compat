"""The complete document-workspace tutorial.

From the grail-compat checkout: uv run docs/tutorial/workspace.py
Or run with a native Grail interpreter, without installing grail-compat.
"""

from grail import Atom, PMap, Pool, generic, record, replace


@record
class Page:
    title: str
    text: str
    links: tuple[str, ...] = ()


@record
class Redirect:
    target: str


@record
class CheckResult:
    path: str
    problems: tuple[str, ...]


@generic
def check(document, workspace):
    raise TypeError(f"unsupported document type: {type(document).__name__}")


@check.impl
def _(document: Page, workspace):
    problems = []
    if not document.text.strip():
        problems.append("empty page")
    for target in document.links:
        if target not in workspace:
            problems.append(f"missing target: {target}")
    return tuple(problems)


@check.impl
def _(document: Redirect, workspace):
    if document.target not in workspace:
        return (f"missing redirect target: {document.target}",)
    return ()


def check_path(path, workspace):
    return CheckResult(path, check(workspace[path], workspace))


def check_workspace(workspace):
    return tuple(check_path(path, workspace) for path in sorted(workspace))


def warnings(results):
    return tuple(
        f"{result.path}: {problem}"
        for result in results
        for problem in result.problems
    )


def check_parallel(workspace, pool):
    def run(path):
        return check_path(path, workspace)

    return tuple(pool.map(run, sorted(workspace)))


def edit_page(workspace, path, **changes):
    return workspace.set(path, replace(workspace[path], **changes))


def main():
    home = Page("Home", "Welcome!", ("guide", "install"))
    published = PMap({
        "home": home,
        "guide": Page("Guide", "Read this first.", ("home",)),
        "faq": Page("FAQ", "Common questions."),
    })
    working = published.set("home", replace(home, links=("guide",)))
    alternative = published.set(
        "install", Page("Install", "Follow the setup instructions.")
    )
    with_redirect = working.set("start", Redirect("home"))
    broken_redirect = with_redirect.set("start", Redirect("removed"))

    assert working["guide"] is published["guide"]
    assert published["home"].links == ("guide", "install")
    assert "install" not in published and "install" in alternative
    assert warnings(check_workspace(with_redirect)) == ()
    assert check(Page("Empty", " "), published) == ("empty page",)

    # All registrations and initial snapshots exist before task submission.
    with Pool(2) as pool:
        for label, workspace in (
            ("Published", published),
            ("Working", working),
            ("Alternative", alternative),
            ("Redirect", broken_redirect),
        ):
            results = check_parallel(workspace, pool)
            assert results == check_workspace(workspace)
            print(f"{label}: {warnings(results)}")

        # Publication is separate from batch computation.
        current = Atom(published)
        reader_snapshot = current.value
        current.swap(edit_page, "home", links=("guide",))
        earlier_report = check_parallel(reader_snapshot, pool)
        latest_snapshot = current.value
        latest_report = check_parallel(latest_snapshot, pool)
        assert warnings(earlier_report) == ("home: missing target: install",)
        assert warnings(latest_report) == ()
        print(f"Retained reader: {warnings(earlier_report)}")
        print(f"Current reader: {warnings(latest_report)}")


if __name__ == "__main__":
    main()
