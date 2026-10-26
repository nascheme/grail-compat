"""Immutable SQLGlot 4.6.0 scope graph and occurrence-path operations.

Sources refer to scopes by integer IDs, never cyclic mutable Scope objects.
Paths identify occurrences even when expression values are shared. Local maps
are builders, not mutable domain trees. Adapted from SQLGlot (MIT); see
../LICENSES/sqlglot-LICENSE.
"""

from collections import deque

import grail

from sqlglot_port import idiomatic as t
from sqlglot_port.generate import argument, items, kind, text
from sqlglot_port.simplify import set_argument


class OptimizeError(ValueError):
    pass


QUERIES = frozenset(("Select", "Union", "Except", "Intersect"))
UNIONS = QUERIES - {"Select"}


@grail.record
class Source:
    name: str
    path: tuple[object, ...]
    scope_id: object


@grail.record
class Selected:
    name: str
    path: tuple[object, ...]
    source: Source


@grail.record
class Scope:
    id: int
    path: tuple[object, ...]
    parent: object
    scope_type: str
    sources: tuple[Source, ...]
    selected: tuple[Selected, ...]
    outer_columns: tuple[str, ...]
    tables: tuple[tuple[object, ...], ...]
    ctes: tuple[tuple[object, ...], ...]
    derived_tables: tuple[tuple[object, ...], ...]
    subqueries: tuple[tuple[object, ...], ...]
    subquery_scopes: tuple[int, ...]
    union: tuple[int, ...]
    raw_columns: tuple[tuple[object, ...], ...]


@grail.record
class ScopeGraph:
    root: object
    scopes: tuple[Scope, ...]
    order: tuple[int, ...]


def get(root, path):
    value = root
    for key in path:
        if type(key) is int:
            value = items(value)[key]
        else:
            value = argument(value, key)
        if value is None:
            raise KeyError(path)
    return value


def put(root, path, replacement):
    """Copy one path, preserving unaffected argument/container occurrences."""
    if not path:
        return replacement
    key, *rest = path
    if type(key) is int:
        values = items(root)
        value = put(values[key], rest, replacement)
        if value is values[key]:
            return root
        values = values[:key] + (value,) + values[key + 1:]
        return t.Sequence(values) if type(root) is t.Sequence else values
    if type(root) is t.Expression:
        return set_argument(root, key, put(argument(root, key), rest, replacement))
    if type(root) in t.CONNECTORS:
        left = put(root.left, rest, replacement) if key == "this" else root.left
        right = put(root.right, rest, replacement) if key == "expression" else root.right
        return root if left is root.left and right is root.right else type(root)(left, right)
    this = put(root.this, rest, replacement)
    return root if this is root.this else type(root)(this)


def child_paths(node, path=()):
    def descend(value, edge):
        if type(value) in t.NODES:
            yield edge, value
        elif type(value) is t.Sequence or type(value) is tuple:
            for i, item in enumerate(items(value)):
                yield from descend(item, edge + (i,))

    if type(node) is t.Expression:
        for arg in node.args:
            yield from descend(arg.value, path + (arg.name,))
    elif type(node) in t.CONNECTORS:
        yield path + ("this",), node.left
        yield path + ("expression",), node.right
    else:
        yield path + ("this",), node.this


def walk(root, path=(), *, bfs=False, prune=None):
    pending = deque(((path, get(root, path)),))
    while pending:
        current, node = pending.popleft() if bfs else pending.pop()
        yield current, node
        if prune is None or not prune(current, node):
            children = tuple(child_paths(node, current))
            pending.extend(children if bfs else reversed(children))


def ancestors(root, path):
    for i in range(len(path) - 1, -1, -1):
        prefix = path[:i]
        value = get(root, prefix)
        if type(value) in t.NODES:
            yield prefix, value


def ancestor(root, path, names):
    return next(((p, node) for p, node in ancestors(root, path) if kind(node) in names), None)


def unnest_path(root, path):
    node = get(root, path)
    wrapper = "Subquery" if kind(node) == "Subquery" else "Paren"
    while kind(node) == wrapper:
        path += ("this",)
        node = argument(node, "this")
    return path


def alias(node):
    value = argument(node, "alias")
    return text(value) if kind(value) == "TableAlias" else text(node, "alias")


def name(node):
    return "*" if kind(node) == "Star" else text(node)


def alias_or_name(node):
    return alias(node) or name(node)


def _collect(root, path):
    tables, ctes, derived, subqueries, columns = [], [], [], [], []
    for current, node in walk(root, path, prune=lambda p, n: p != path and kind(n) in QUERIES | {"Subquery", "CTE"}):
        if current == path:
            continue
        node_kind = kind(node)
        if node_kind == "Column" and kind(argument(node, "this")) != "Star":
            columns.append(current)
        elif node_kind == "Table":
            tables.append(current)
        elif node_kind == "CTE":
            ctes.append(current)
        elif node_kind in ("Subquery", "Unnest", "Lateral"):
            derived.append(current)
        elif node_kind in QUERIES:
            subqueries.append(current)
    return tuple(tables), tuple(ctes), tuple(derived), tuple(subqueries), tuple(columns)


def build_scopes(root):
    scopes, order = [], []

    def visit(path, parent, scope_type, inherited, outer=()):
        sid = len(scopes)
        scopes.append(None)  # Builder slot only; no mutable Scope is published.
        node = get(root, path)
        if kind(node) not in QUERIES | {"Lateral", "Unnest"}:
            raise OptimizeError(f"Unexpected expression type: {kind(node)}")
        tables, ctes, derived, subqueries, raw = _collect(root, path)
        sources = dict(inherited)
        children, union = [], ()

        def derived_scopes(paths, child_type):
            added = {}
            for derived_path in paths:
                value = get(root, derived_path)
                special = kind(value) in ("Unnest", "Lateral")
                child_path = derived_path if special else unnest_path(root, derived_path + ("this",))
                visible = {**sources, **added} if child_type == "CTE" else sources
                columns = tuple(name(column) for column in items(argument(argument(value, "alias"), "columns")))
                child = visit(child_path, sid, "UNNEST" if kind(value) == "Unnest" else child_type, visible, columns)
                source_name = alias(value)
                added[source_name] = Source(source_name, child_path, child)
            sources.update(added)

        if kind(node) == "Select":
            derived_scopes(ctes, "CTE")
            for subquery in subqueries:
                children.append(visit(unnest_path(root, subquery), sid, "SUBQUERY", sources))
            derived_scopes(derived, "DERIVED_TABLE")
            added = {}
            for table_path in tables:
                table = get(root, table_path)
                parent_node = get(root, table_path[:-1])
                source_name = alias(parent_node) if kind(parent_node) == "Alias" else name(table)
                if name(table) in sources:
                    source = sources[name(table)]
                    sources[source_name] = Source(source_name, source.path, source.scope_id)
                elif source_name in sources:
                    raise OptimizeError(f"Duplicate table name: {source_name}")
                else:
                    added[source_name] = Source(source_name, table_path, None)
            sources.update(added)
        elif kind(node) in UNIONS:
            derived_scopes(ctes, "CTE")
            union = tuple(visit(unnest_path(root, path + (key,)), sid, "UNION", sources)
                          for key in ("this", "expression"))

        references = []
        for table_path in tables:
            table = get(root, table_path)
            parent_node = get(root, table_path[:-1])
            references.append((alias(parent_node) if kind(parent_node) == "Alias" else name(table), table_path))
        for derived_path in derived:
            references.append((alias(get(root, derived_path)), unnest_path(root, derived_path)))
        selected = {}
        for source_name, source_path in references:
            if source_name in sources:
                selected[source_name] = Selected(source_name, source_path, sources[source_name])
        scopes[sid] = Scope(sid, path, parent, scope_type, tuple(sources.values()),
                            tuple(selected.values()), outer, tables, ctes, derived,
                            subqueries, tuple(children), union, raw)
        order.append(sid)
        return sid

    visit((), None, "ROOT", {})
    return ScopeGraph(root, tuple(scopes), tuple(order))


def columns(graph, scope_id, *, external=False):
    """Column occurrence paths, including correlated references from subqueries."""
    memo = {}

    def collect(sid):
        if sid in memo:
            return memo[sid]
        scope = graph.scopes[sid]
        node = get(graph.root, scope.path)
        values = list(scope.raw_columns)
        for child in scope.subquery_scopes:
            values.extend(p for p in collect(child)
                          if text(get(graph.root, p), "table") not in {s.name for s in graph.scopes[child].selected})
        outputs = {alias_or_name(value) for value in items(argument(node, "expressions"))}
        result = tuple(p for p in values if text(get(graph.root, p), "table")
                       or not (ancestor(graph.root, p, {"Group", "Order", "Qualify"})
                               and name(get(graph.root, p)) in outputs))
        memo[sid] = result
        return result

    values = collect(scope_id)
    if external:
        selected = {source.name for source in graph.scopes[scope_id].selected}
        return tuple(p for p in values if text(get(graph.root, p), "table") not in selected)
    return values
