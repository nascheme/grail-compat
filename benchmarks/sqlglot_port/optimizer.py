"""Record-native SQLGlot 4.6.0 predicate/join/subquery optimizer tail.

Real scope/source analysis and occurrence-path edits, without mutable reference
nodes, parent backlinks or reference optimizer/simplifier calls. Algorithms
adapted from SQLGlot (MIT); see ../LICENSES/sqlglot-LICENSE.
"""

import re

from sqlglot_port import idiomatic as t
from sqlglot_port import scope as sc
from sqlglot_port import simplify as s
from sqlglot_port.generate import argument as arg, items, kind, text


STAGES = ("expand_multi_table_selects", "pushdown_predicates", "optimize_joins",
          "eliminate_subqueries", "quote_identities")


def expression(name, **kwargs):
    return t.Expression(name, tuple(t.Argument(key, value) for key, value in kwargs.items()))


def identifier(value, *, parsed=False):
    return expression("Identifier", this=value,
                      quoted=False if parsed else not bool(re.match(r"^[a-zA-Z][\w]*$", value)))


def _find(root, names):
    return tuple(path for path, node in sc.walk(root, bfs=True) if kind(node) in names)


def _parent(root, path):
    return next(sc.ancestors(root, path), None)


def _terms(root, path, connector):
    node = sc.get(root, path)
    if kind(node) == connector:
        yield from _terms(root, path + ("this",), connector)
        yield from _terms(root, path + ("expression",), connector)
    else:
        yield sc.unnest_path(root, path)


def table_names(node):
    return tuple(dict.fromkeys(text(value, "table") for _, value in sc.walk(node, bfs=True)
                               if kind(value) == "Column"))


def _append(root, path, predicate):
    node = sc.get(root, path)
    where = kind(node) == "Select"
    key = "where" if where else "on"
    existing = arg(node, key)
    condition = arg(existing, "this") if where and existing is not None else existing
    combined = t.combine(t.And, (condition, predicate)) if condition is not None else predicate
    node = s.set_argument(node, key, expression("Where", this=combined) if where else combined)
    if not where and text(node, "kind").upper() == "CROSS":
        node = s.set_argument(node, "kind", None)
    return sc.put(root, path, node)


def expand_multi_table_selects(root):
    # Bottom-up occurrence edits avoid invalidating paths into moved sources.
    for path in sorted(_find(root, {"From"}), key=len, reverse=True):
        from_ = sc.get(root, path)
        values = list(items(arg(from_, "expressions")))
        if len(values) < 2:
            continue
        parent_path, parent = _parent(root, path)
        joins = list(items(arg(parent, "joins")))
        remaining = list(values)
        for original in values[1:]:
            value = original
            if kind(value) == "Select":
                value = expression("Subquery", this=value, alias=expression("TableAlias", this=None))
            joins.append(expression("Join", this=value, kind="CROSS"))
            # Reference list.remove uses SQL equality, not occurrence identity.
            # Thus FROM t,T can remove the first, case-insensitively equal node.
            index = next(i for i, candidate in enumerate(remaining) if s.sql_equal(original, candidate))
            del remaining[index]
        parent = s.set_argument(parent, "joins", t.Sequence(tuple(joins)))
        parent = s.set_argument(parent, path[-1], s.set_argument(from_, "expressions", t.Sequence(tuple(remaining))))
        root = sc.put(root, parent_path, parent)
    return root


def _nodes_for_predicate(root, predicate_path, sources):
    nodes = {}
    tables = table_names(sc.get(root, predicate_path))
    enclosing = sc.ancestor(root, predicate_path, {"Join", "Where"})
    where = enclosing is not None and kind(enclosing[1]) == "Where"
    for table in tables:
        selected = sources.get(table)
        if selected is None:
            continue
        path = selected.path
        if where:
            parent = sc.ancestor(root, path, {"Join", "From"})
            if parent is None:
                continue
            path = parent[0]
        node = sc.get(root, path)
        if kind(node) == "From" and selected.source.scope_id is not None:
            path = selected.source.path
            node = sc.get(root, path)
        if kind(node) == "Join":
            if text(node, "side"):
                return {}
            nodes[table] = path
        elif kind(node) == "Select" and len(tables) == 1 and not arg(node, "group"):
            nodes[table] = path
    return nodes


def replace_aliases(source, predicate):
    aliases = {sc.alias(value) if kind(value) == "Alias" else sc.name(value):
               arg(value, "this") if kind(value) == "Alias" else value
               for value in items(arg(source, "expressions"))}
    return t.transform(predicate, lambda value: aliases.get(sc.name(value), value)
                       if kind(value) == "Column" else value)


def _pushdown(root, path, sources):
    condition = s.simplify(sc.get(root, path), t.canonical_sql)
    root = sc.put(root, path, condition)
    cnf = t.normalized(condition) or not t.normalized(condition, dnf=True)
    connector = "And" if cnf else "Or"
    predicates = tuple(_terms(root, path, connector)) if kind(condition) == connector else (path,)
    if cnf:
        for predicate_path in predicates:
            predicate = sc.get(root, predicate_path)
            targets = _nodes_for_predicate(root, predicate_path, sources)
            for target in targets.values():
                node = sc.get(root, target)
                root = sc.put(root, predicate_path, s.TRUE)
                root = _append(root, target, predicate if kind(node) == "Join" else replace_aliases(node, predicate))
                if kind(node) == "Join":
                    break
    else:
        common = set(table_names(sc.get(root, predicates[0])))
        for predicate_path in predicates[1:]:
            common.intersection_update(table_names(sc.get(root, predicate_path)))
        conditions = {}
        for table in sorted(common):
            nodes = {}
            for predicate_path in predicates:
                nodes = _nodes_for_predicate(root, predicate_path, sources)
                if table not in nodes:
                    continue
                predicate_condition = None
                for column_path, column in sc.walk(root, predicate_path, bfs=True):
                    if kind(column) != "Column" or text(column, "table") != table:
                        continue
                    parent = sc.ancestor(root, column_path, s.CONDITION_KINDS)
                    if parent is None:
                        continue
                    condition = parent[1]
                    predicate_condition = (t.combine(t.And, (predicate_condition, condition))
                                           if predicate_condition is not None else condition)
                if predicate_condition is not None:
                    conditions[table] = (t.combine(t.Or, (conditions[table], predicate_condition))
                                         if table in conditions else predicate_condition)
            # Preserve the upstream loop's use of the final block's target map.
            for name, target in nodes.items():
                if name in conditions:
                    node = sc.get(root, target)
                    predicate = conditions[name]
                    root = _append(root, target, predicate if kind(node) == "Join" else replace_aliases(node, predicate))
    return root


def pushdown_predicates(root):
    graph = sc.build_scopes(root)
    for sid in reversed(graph.order):
        scope = graph.scopes[sid]
        path = scope.path
        try:
            select = sc.get(root, path)
        except (KeyError, IndexError):
            # A parent predicate edit may detach an entire subquery. There is
            # no live occurrence to optimize, unlike a stale mutable Scope.
            continue
        sources = {source.name: source for source in scope.selected}
        if arg(select, "where") is not None:
            root = _pushdown(root, path + ("where", "this"), sources)
        for i, join in enumerate(items(arg(sc.get(root, path), "joins"))):
            if arg(join, "on") is not None:
                name = sc.alias_or_name(arg(join, "this"))
                if name not in sources:
                    raise KeyError(name)
                root = _pushdown(root, path + ("joins", i, "on"), {name: sources[name]})
    return root


def _other_tables(join, exclude):
    return tuple(name for name in table_names(arg(join, "on") or s.TRUE) if name != exclude)


def _tsort(dag):
    result, complete = [], set()

    def visit(node, active):
        if node in complete:
            return
        if node in active:
            raise ValueError("Cycle error")
        active.add(node)
        for dependency in dag.get(node, ()):
            visit(dependency, active)
        active.remove(node)
        complete.add(node)
        result.append(node)

    for node in dag:
        visit(node, set())
    return result


def optimize_joins(root):
    for path in _find(root, {"Select"}):
        select = sc.get(root, path)
        references, cross = {}, []
        for i, join in enumerate(items(arg(select, "joins"))):
            name = sc.alias_or_name(arg(join, "this"))
            tables = _other_tables(join, name)
            join_path = path + ("joins", i)
            if tables:
                for table in tables:
                    references.setdefault(table, []).append(join_path)
            else:
                cross.append((name, join_path))
        for name, target in cross:
            for dependent in references.get(name, ()):
                on_path = dependent + ("on",)
                condition = s.simplify(sc.get(root, on_path), t.canonical_sql)
                root = sc.put(root, on_path, condition)
                if type(condition) in t.CONNECTORS:
                    for predicate_path in tuple(_terms(root, on_path, kind(condition))):
                        predicate = sc.get(root, predicate_path)
                        if name in table_names(predicate):
                            root = sc.put(root, predicate_path, s.TRUE)
                            root = _append(root, target, predicate)
    for path in sorted(_find(root, {"From"}), key=len, reverse=True):
        from_ = sc.get(root, path)
        head = items(arg(from_, "expressions"))[0]
        parent_path, parent = _parent(root, path)
        joins = {sc.alias_or_name(arg(join, "this")): join for join in items(arg(parent, "joins"))}
        dag = {sc.alias_or_name(head): ()}
        dag.update((name, _other_tables(join, name)) for name, join in joins.items())
        values = tuple(joins[name] for name in _tsort(dag) if name != sc.alias_or_name(head))
        root = sc.put(root, parent_path, s.set_argument(parent, "joins", t.Sequence(values)))
    def normalize(node):
        node = t.map_children(node, normalize)
        return (s.set_argument(node, "kind", None)
                if kind(node) == "Join" and text(node, "kind").upper() != "CROSS" else node)

    return normalize(root)


def _table(name, *, parsed=False):
    return expression("Table", this=identifier(name, parsed=parsed), db=None, catalog=None)


def eliminate_subqueries(root):
    root = s.simplify(root, t.canonical_sql)
    graph = sc.build_scopes(root)
    queries = {}
    memo = {}
    for sid in graph.order:
        scope = graph.scopes[sid]
        query = sc.get(root, scope.path)
        queries.setdefault(s.equality_key(query, memo), []).append(scope.path)
    sequence = 0
    for duplicates in queries.values():
        if len(duplicates) < 2:
            continue
        name = f"_e_{sequence}"
        sequence += 1
        query = sc.get(root, duplicates[0])
        for path in duplicates:
            parent = _parent(root, path)
            if parent is None:
                continue
            parent_path, parent_node = parent
            if kind(parent_node) == "Subquery":
                replacement = expression("Alias", this=_table(name),
                                         alias=expression("TableAlias", this=identifier(sc.alias_or_name(parent_node))))
                root = sc.put(root, parent_path, replacement)
            elif kind(parent_node) in sc.UNIONS:
                replacement = expression("Select", expressions=t.Sequence((expression("Star", **{"except": None, "replace": None}),)),
                                         **{"from": expression("From", expressions=t.Sequence((_table(name, parsed=True),)))})
                root = sc.put(root, path, replacement)
        with_ = arg(root, "with")
        cte = expression("CTE", this=query, alias=expression("TableAlias", this=identifier(name, parsed=True), columns=None))
        with_ = expression("With", expressions=t.Sequence(items(arg(with_, "expressions")) + (cte,)), recursive=False)
        root = s.set_argument(root, "with", with_)
    return root


def quote_identities(root):
    return t.transform(root, lambda node: s.set_argument(node, "quoted", True)
                       if kind(node) == "Identifier" else node)


def optimize_tail(root):
    for name in STAGES:
        root = globals()[name](root)
    return root
