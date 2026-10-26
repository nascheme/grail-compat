"""SQLGlot 4.6.0 partial port: immutable optimizer kernels and compact SQL.

Parsing, dialect-specific output, qualification/projections and unnesting remain
SQLGlot adapters. Compact rendering, normalization, scope analysis and the
five-stage optimizer tail operate on records without reference fallback.
Algorithms adapted from SQLGlot (MIT); see ../LICENSES/sqlglot-LICENSE.
"""

from enum import Enum
from functools import lru_cache
import importlib

import grail


@grail.record
class Argument:
    name: str
    value: object


@grail.record
class Sequence:
    items: tuple[object, ...]


@grail.record
class EnumValue:
    module: str
    qualname: str
    name: str


@grail.record
class Expression:
    kind: str
    args: tuple[Argument, ...]


@grail.record
class And:
    left: object
    right: object


@grail.record
class Or:
    left: object
    right: object


@grail.record
class Not:
    this: object


@grail.record
class Paren:
    this: object


NODES = (Expression, And, Or, Not, Paren)
CONNECTORS = (And, Or)


def _map_value(value, function):
    if type(value) in NODES:
        return function(value)
    if type(value) is Sequence:
        items = _map_value(value.items, function)
        return value if items is value.items else Sequence(items)
    if type(value) is tuple:
        items = tuple(_map_value(item, function) for item in value)
        return value if all(a is b for a, b in zip(value, items)) else items
    return value


@grail.generic
def map_children(node, function):
    raise TypeError(type(node))


@map_children.impl
def _map_expression(node: Expression, function):
    args = []
    changed = False
    for arg in node.args:
        value = _map_value(arg.value, function)
        if value is arg.value:
            args.append(arg)
        else:
            args.append(Argument(arg.name, value))
            changed = True
    return Expression(node.kind, tuple(args)) if changed else node


@map_children.impl
def _map_connector(node: And, function):
    left, right = function(node.left), function(node.right)
    if left is node.left and right is node.right:
        return node
    return type(node)(left, right)


@map_children.impl
def _map_or(node: Or, function):
    return _map_connector(node, function)


@map_children.impl
def _map_unary(node: Not, function):
    this = function(node.this)
    return node if this is node.this else type(node)(this)


@map_children.impl
def _map_paren(node: Paren, function):
    return _map_unary(node, function)


def _value_children(value):
    if type(value) in NODES:
        yield value
    elif type(value) is Sequence:
        for item in value.items:
            yield from _value_children(item)
    elif type(value) is tuple:
        for item in value:
            yield from _value_children(item)


def children(node):
    if type(node) in CONNECTORS:
        yield node.left
        yield node.right
    elif type(node) in (Not, Paren):
        yield node.this
    elif type(node) is Expression:
        for arg in node.args:
            yield from _value_children(arg.value)
    else:
        raise TypeError(type(node))


def walk(node):
    """Depth-first occurrences; a shared value may occur on several paths."""
    yield node
    for child in children(node):
        yield from walk(child)


def transform(node, function):
    """Preorder one-to-one rewrite, without revisiting newly replaced subtrees."""
    result = function(node)
    if type(result) not in NODES:
        raise TypeError("a rewrite must return an expression record")
    if result is not node:
        return result
    return map_children(node, lambda child: transform(child, function))


def unnest(node):
    while type(node) is Paren:
        node = node.this
    return node


def flatten(node):
    """Remove parentheses immediately wrapping same-kind connectors."""
    if type(node) not in CONNECTORS:
        return node
    return map_children(node, lambda child: unnest(child)
                        if type(unnest(child)) is type(node) else child)


def _terms(node, kind):
    if type(node) is kind:
        yield from _terms(node.left, kind)
        yield from _terms(node.right, kind)
    else:
        yield unnest(node)


def _wrap(node):
    return Paren(node) if type(node) in (And, Or, Not) else node


def combine(kind, terms):
    terms = tuple(terms)
    result = terms[0]
    if len(terms) > 1:
        result = _wrap(result)
    for term in terms[1:]:
        result = kind(result, _wrap(term))
    return result


def uniq_sort(node, sort_key):
    if type(node) not in CONNECTORS:
        return node
    flattened = tuple(_terms(node, type(node)))
    deduped = {sort_key(term): term for term in flattened}
    keys = tuple(deduped)
    if any(b < a for a, b in zip(keys, keys[1:])):
        return combine(type(node), (deduped[key] for key in sorted(deduped)))
    if len(keys) < len(flattened):
        return combine(type(node), deduped.values())
    return node


def _predicate_lengths(node, dnf):
    node = unnest(node)
    if type(node) not in CONNECTORS:
        return [1]
    left = _predicate_lengths(node.left, dnf)
    right = _predicate_lengths(node.right, dnf)
    if type(node) is (And if dnf else Or):
        return [a + b for a in left for b in right]
    return left + right


def normalization_distance(node, dnf=False):
    return sum(_predicate_lengths(node, dnf)) - (
        sum(type(child) in CONNECTORS for child in walk(node)) + 1)


def normalized(node, dnf=False):
    ancestor, root = (And, Or) if dnf else (Or, And)

    def visit(current, under_ancestor):
        if type(current) is root and under_ancestor:
            return False
        under_ancestor = under_ancestor or type(current) is ancestor
        return all(visit(child, under_ancestor) for child in children(current))

    return visit(node, False)


def _tidy(node, sort_key):
    node = uniq_sort(flatten(node), sort_key)
    return map_children(node, lambda child: _tidy(child, sort_key))


def _distribute(a, b, from_kind, to_kind, sort_key):
    if type(a) in CONNECTORS:
        a = map_children(a, lambda child: combine(to_kind, (
            Paren(combine(from_kind, (child, b.left))),
            Paren(combine(from_kind, (child, b.right))))))
    else:
        a = combine(to_kind, (combine(from_kind, (a, b.left)),
                              combine(from_kind, (a, b.right))))
    return _tidy(a, sort_key)


def distributive_law(node, dnf, max_distance, sort_key):
    """Pure port of SQLGlot's guarded recursive distributive-law pass."""
    if type(unnest(node)) in CONNECTORS:
        if normalization_distance(node, dnf) > max_distance:
            return node
    node = map_children(node, lambda child: distributive_law(child, dnf, max_distance, sort_key))
    to_kind, from_kind = (Or, And) if dnf else (And, Or)
    if type(node) is from_kind:
        a, b = unnest(node.left), unnest(node.right)
        if type(a) is to_kind and type(b) is to_kind:
            count_a = sum(type(child) in CONNECTORS for child in walk(a))
            count_b = sum(type(child) in CONNECTORS for child in walk(b))
            if count_a > count_b:
                return _distribute(a, b, from_kind, to_kind, sort_key)
            return _distribute(b, a, from_kind, to_kind, sort_key)
        if type(a) is to_kind:
            return _distribute(b, a, from_kind, to_kind, sort_key)
        if type(b) is to_kind:
            return _distribute(a, b, from_kind, to_kind, sort_key)
    return node


def distribute(node, sort_key, dnf=False, max_distance=128):
    """Distribute to a structural fixed point; input snapshots remain usable."""
    while True:
        result = distributive_law(node, dnf, max_distance, sort_key)
        if result == node:
            return result
        node = result


@lru_cache(maxsize=1)
def reference():
    """Load the versioned compatibility boundary, never an implicit substitute."""
    import sqlglot

    if sqlglot.__version__ != "4.6.0":
        raise RuntimeError("this port requires sqlglot==4.6.0")
    return sqlglot


def from_sqlglot(expression):
    """Snapshot args, omit parent backlinks, and preserve shared child values.

    Unknown mutable metadata is rejected rather than retained in record fields.
    Only canonical binary/unary argument shapes are specialized as variants.
    """
    exp = reference().exp
    memo, active = {}, set()
    binary = {exp.And: And, exp.Or: Or}
    unary = {exp.Not: Not, exp.Paren: Paren}

    def freeze(value):
        if isinstance(value, exp.Expression):
            key = id(value)
            if key in memo:
                return memo[key]
            if key in active:
                raise ValueError("cyclic SQLGlot expression arguments")
            active.add(key)
            kind = type(value)
            if kind in binary:
                if set(value.args) != {"this", "expression"}:
                    raise ValueError("noncanonical connector arguments")
                if not all(isinstance(child, exp.Expression) for child in (value.this, value.expression)):
                    raise TypeError("connector operands must be expressions")
                result = binary[kind](freeze(value.this), freeze(value.expression))
            elif kind in unary:
                if set(value.args) != {"this"}:
                    raise ValueError("noncanonical unary arguments")
                if not isinstance(value.this, exp.Expression):
                    raise TypeError("unary operand must be an expression")
                result = unary[kind](freeze(value.this))
            else:
                if getattr(exp, kind.__name__, None) is not kind:
                    raise TypeError("unregistered SQLGlot extension expression")
                result = Expression(kind.__name__, tuple(
                    Argument(name, freeze(child)) for name, child in value.args.items()))
            active.remove(key)
            memo[key] = result
            return result
        if isinstance(value, Enum):
            kind = type(value)
            return EnumValue(kind.__module__, kind.__qualname__, value.name)
        if type(value) in (list, tuple):
            key = id(value)
            if key in active:
                raise ValueError("cyclic SQLGlot argument sequence")
            active.add(key)
            items = tuple(freeze(item) for item in value)
            active.remove(key)
            return Sequence(items) if type(value) is list else items
        if value is None or type(value) in (bool, int, float, str, bytes):
            return value
        raise TypeError(f"unsupported SQLGlot argument: {type(value)}")

    if not isinstance(expression, exp.Expression):
        raise TypeError("expected a SQLGlot expression")
    return freeze(expression)


def to_sqlglot(node):
    """Materialize fresh mutable occurrences with valid parent/arg-key links."""
    exp = reference().exp

    def thaw(value):
        if type(value) is Expression:
            kind = getattr(exp, value.kind)
            return kind(**{arg.name: thaw(arg.value) for arg in value.args})
        if type(value) in CONNECTORS:
            kind = exp.And if type(value) is And else exp.Or
            return kind(this=thaw(value.left), expression=thaw(value.right))
        if type(value) in (Not, Paren):
            return (exp.Not if type(value) is Not else exp.Paren)(this=thaw(value.this))
        if type(value) is Sequence:
            return [thaw(item) for item in value.items]
        if type(value) is tuple:
            return tuple(thaw(item) for item in value)
        if type(value) is EnumValue:
            kind = importlib.import_module(value.module)
            for name in value.qualname.split("."):
                kind = getattr(kind, name)
            return kind[value.name]
        return value

    if type(node) not in NODES:
        raise TypeError("expected an expression record")
    return thaw(node)


def canonical_sql_reference(node):
    """Reference-renderer control, including fresh operand materialization."""
    from sqlglot.generator import Generator

    return Generator(normalize=True, identify=True, pretty=False).generate(to_sqlglot(node))


def canonical_sql(node):
    from sqlglot_port.generate import canonical_sql as generate

    return generate(node)


def normalize_reference_simplify(expression, dnf=False, max_distance=128):
    """Stage-1 control: reference simplify -> records -> reference simplify.

    Never calls SQLGlot normalize/distributive_law. The broader simplifier is
    still upstream, and adapter/sort-key costs are intentionally included.
    The caller's mutable expression is copied before the first simplification.
    """
    reference()
    from sqlglot.optimizer.simplify import simplify

    node = from_sqlglot(simplify(expression.copy()))
    node = distribute(node, canonical_sql_reference, dnf, max_distance)
    return simplify(to_sqlglot(node))


def normalize_records(node, sort_key=canonical_sql, dnf=False, max_distance=128):
    """Record-native simplify -> distribute -> simplify, retaining input values."""
    from sqlglot_port.simplify import simplify

    node = simplify(node, sort_key)
    node = distribute(node, sort_key, dnf, max_distance)
    return simplify(node, sort_key)


def normalize(expression, dnf=False, max_distance=128):
    """Snapshot once, normalize immutable values, then export fresh occurrences."""
    node = normalize_records(from_sqlglot(expression), canonical_sql, dnf, max_distance)
    return to_sqlglot(node)


def normalize_reference_keys(expression, dnf=False, max_distance=128):
    """Stage-2 control: native rules, but reference SQL-key rendering/adapters."""
    node = normalize_records(from_sqlglot(expression), canonical_sql_reference, dnf, max_distance)
    return to_sqlglot(node)


def _optimize(expression, schema, db, catalog, native_tail):
    reference()
    from sqlglot.optimizer import optimizer as upstream

    expression = expression.copy()
    expression = upstream.qualify_tables(expression, db=db, catalog=catalog)
    expression = upstream.isolate_table_selects(expression)
    expression = upstream.qualify_columns(expression, schema)
    expression = upstream.pushdown_projections(expression)
    expression = normalize(expression)
    expression = upstream.unnest_subqueries(expression)
    if native_tail:
        from sqlglot_port.optimizer import optimize_tail

        return to_sqlglot(optimize_tail(from_sqlglot(expression)))
    expression = upstream.expand_multi_table_selects(expression)
    expression = upstream.pushdown_predicates(expression)
    expression = upstream.optimize_joins(expression)
    expression = upstream.eliminate_subqueries(expression)
    return upstream.quote_identities(expression)


def optimize_normalization_only(expression, schema=None, db=None, catalog=None):
    """Stage-3 control: only normalization uses record-native internals."""
    return _optimize(expression, schema, db, catalog, False)


def optimize(expression, schema=None, db=None, catalog=None):
    """Native normalization and five-stage optimizer tail; prefix/unnest upstream."""
    return _optimize(expression, schema, db, catalog, True)


def main(sql, *, dnf=False, max_distance=128, read=None, write=None):
    sqlglot = reference()
    expression = sqlglot.parse_one(sql, read=read)
    node = normalize_records(from_sqlglot(expression), canonical_sql, dnf, max_distance)
    if not write:
        from sqlglot_port.generate import generate

        return generate(node)
    return to_sqlglot(node).sql(dialect=write)


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    import sys

    # The simplifier and all adapters must use the same record class identities,
    # including when invoked as a file rather than as a package module.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main = importlib.import_module("sqlglot_port.idiomatic").main

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sql")
    parser.add_argument("--dnf", action="store_true")
    parser.add_argument("--max-distance", type=int, default=128)
    parser.add_argument("--read")
    parser.add_argument("--write")
    args = parser.parse_args()
    print(main(args.sql, dnf=args.dnf, max_distance=args.max_distance,
               read=args.read, write=args.write))
