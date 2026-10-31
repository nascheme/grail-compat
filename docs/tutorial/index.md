---
sources:
  - src/grail/_core.py
  - src/grail/_pds.py
  - tests/test_grail.py
  - tests/test_grail_pds.py
---

# Tutorial: check a document workspace

Build a small document checker while learning Grail's style: describe data
with records, put behavior in functions, and work with immutable snapshots.
We will keep published and working versions of a workspace, add checks for
different document types, and run those checks through a thread pool.

The tutorial has three parts:

1. **Values and versions** (this page): model pages and retain workspace snapshots.
2. [Extend the checker](checking.md): add behavior with a generic function.
3. [Check snapshots concurrently](concurrency.md): use a pool, then optionally
   publish versions through an Atom.

You need basic Python knowledge, but no prior functional-programming experience.
Follow [Getting started](../getting-started.md) to install Grail. All examples
work with grail-compat and the native Grail interpreter. The small inputs teach
program structure, not benchmark performance.

## Describe a page

Our workspace is an in-memory collection of documents keyed by path. We will
check links between them. To keep the example focused, a page stores its link
targets explicitly; we will not write a Markdown parser or read files.

    >>> from grail import record, replace, PMap
    >>> @record
    ... class Page:
    ...     title: str
    ...     text: str
    ...     links: tuple[str, ...] = ()
    >>> home = Page("Home", "Welcome!", ("guide", "install"))
    >>> home.title, home.links
    ('Home', ('guide', 'install'))

A record body declares data, not methods. Grail supplies construction, field
access, representation, equality, and hashing. Record fields cannot be rebound:

    >>> home.title = "Start here"
    Traceback (most recent call last):
        ...
    AttributeError: ...

A record does not deep-freeze its children. A list in a field would still be
mutable, so we use a tuple of strings for links. These field annotations
also describe recognized value types, making `Page` a value record. Annotations
do not validate or convert constructor arguments: our program must supply
strings and tuples of strings.

## Make an edit without changing the original

Use `replace` to describe the fields that change:

    >>> revised_home = replace(home, title="Start here")
    >>> home.title, revised_home.title
    ('Home', 'Start here')
    >>> revised_home.text == home.text
    True

The old page is still available. Unchanged children are shared, rather than
deep-copied. Put an operation in a normal function when it makes an edit clearer:

    >>> def rename(page, title):
    ...     return replace(page, title=title)
    >>> rename(home, "Start here") == revised_home
    True

We do not need a generic function for this operation yet: it only handles pages.
We will introduce a generic when there are different document types to check.

## Keep a published workspace

A single immutable page is useful, but a document checker needs a consistent
collection of pages. Use a `PMap`, Grail's persistent mapping:

    >>> published = PMap({
    ...     "home": home,
    ...     "guide": Page("Guide", "Read this first.", ("home",)),
    ...     "faq": Page("FAQ", "Common questions."),
    ... })
    >>> published["guide"].title
    'Guide'

Here, *persistent* means that old collection versions remain available after
an update. It does not mean that the collection is saved to disk.

The home page links to `install`, which does not exist. Suppose an editor
removes that link in a working version:

    >>> working = published.set(
    ...     "home", replace(published["home"], links=("guide",))
    ... )
    >>> published["home"].links
    ('guide', 'install')
    >>> working["home"].links
    ('guide',)

Both the map and the page need an update: `replace` makes the new page, and
`set` puts it in a new map. Neither operation changes its input. Always keep
or return the result of an update.

The guide did not change, so both versions refer to the same page:

    >>> working["guide"] is published["guide"]
    True

This is safe because the page and its children are immutable.

## Explore an alternative without copying the workspace

Another way to fix the missing target is to add an installation page. Start
from `published` again, not from the first editor's working version:

    >>> alternative = published.set(
    ...     "install", Page("Install", "Follow the setup instructions.")
    ... )
    >>> "install" in published, "install" in working, "install" in alternative
    (False, False, True)
    >>> alternative["home"].links
    ('guide', 'install')

We now have a published version and two alternative edits. Retaining or
switching versions only requires keeping references to their maps; there is
no undo operation to implement and no deep copy to arrange.

Why not use a dictionary? A dictionary is a good choice for private scratch
work. But mutating one dictionary would change what every holder of that
dictionary sees. Copying it before each edit preserves earlier bindings, but
copies the mapping's storage. A shallow copy also leaves mutable children
shared; a deep copy can duplicate a much larger graph.

Native persistent maps instead share unchanged internal structure between
versions, creating only the paths needed for an update. That makes retaining
many nearby versions practical without copying every entry for every edit.
This is distinct from sharing the unchanged page objects, which a shallow
dictionary copy would do too.

The snapshot behavior is the same across Grail backends, but the costs differ:
the pure-Python compatibility map copies storage on updates. Do not infer
native performance from that backend. See [backends](../backends.md).

Persistent containers are not automatically better than builtins. If you only
need one mutable dictionary owned by one function, use it. Our reason for a
persistent map is that **published and edited versions must coexist**.

## What we have so far

The workspace contains immutable records inside an immutable mapping. An edit
is a function from one snapshot to another, and earlier snapshots remain valid.
We have not needed a lock or a shared mutable object.

Next, [write the checker and extend it to redirects](checking.md). We will
make the difference between these workspace versions visible in its output.

Details: [records](../guide/records.md), [value records](../guide/value-records.md),
and [persistent collections](../guide/persistent-collections.md).
