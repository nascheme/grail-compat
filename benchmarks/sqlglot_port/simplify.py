"""Record-native SQLGlot 4.6.0 simplification.

All simplify rules operate on immutable records. Canonical SQL sort keys remain
an injected rendering service. No reference simplifier or mutable AST is used.
Algorithms adapted from SQLGlot (MIT); see ../LICENSES/sqlglot-LICENSE.
"""

import datetime
from decimal import Decimal

import grail

from sqlglot_port import idiomatic as tree


@grail.record
class Ancestor:
    kind: str
    path: tuple[object, ...]


def kind(node):
    return node.kind if type(node) is tree.Expression else type(node).__name__


def rewrite_context(node, function, ancestors=()):
    """Preorder one-to-one rewrite with immutable occurrence context.

    Each ancestor describes the parent's kind and the argument/index path from
    that parent to the next expression. Paths distinguish list (Sequence) and
    tuple indices by the parent's schema. A shared value is visited separately
    at each occurrence; context never contains domain nodes or parent pointers.
    Replacements are not revisited. Deletion/splicing is deliberately excluded.
    """
    result = function(node, ancestors)
    if type(result) not in tree.NODES:
        raise TypeError("a rewrite must return an expression record")
    if result is not node:
        return result

    def descend(value, path):
        if type(value) in tree.NODES:
            return rewrite_context(value, function, ancestors + (Ancestor(kind(node), path),))
        if type(value) is tree.Sequence:
            items = descend(value.items, path)
            return value if items is value.items else tree.Sequence(items)
        if type(value) is tuple:
            items = tuple(descend(item, path + (i,)) for i, item in enumerate(value))
            return value if all(a is b for a, b in zip(value, items)) else items
        return value

    if type(node) is tree.Expression:
        args = tuple(
            arg if (value := descend(arg.value, (arg.name,))) is arg.value
            else tree.Argument(arg.name, value)
            for arg in node.args
        )
        return node if all(a is b for a, b in zip(node.args, args)) else tree.Expression(node.kind, args)
    if type(node) in tree.CONNECTORS:
        left = descend(node.left, ("this",))
        right = descend(node.right, ("expression",))
        return node if left is node.left and right is node.right else type(node)(left, right)
    this = descend(node.this, ("this",))
    return node if this is node.this else type(node)(this)


def argument(node, name):
    for arg in node.args:
        if arg.name == name:
            return arg.value
    return None


def sql_equal(left, right):
    """SQLGlot value equality, not record or generated SQL equality.

    Generic expression arguments ignore order and lowercase immediate strings
    (also strings inside list args). Identifier ignores quoting and other args;
    Literal compares text case-sensitively and ignores extra args. These are
    4.6.0 contracts, including their quirks. Kind checks also prevent namedtuple
    controls from conflating And, Or or unary records with equal field tuples.
    """
    if type(left) in tree.NODES or type(right) in tree.NODES:
        if type(left) is not type(right):
            return False
        if type(left) is tree.Expression:
            if left.kind != right.kind:
                return False
            if left.kind == "Identifier":
                return _norm_equal(argument(left, "this"), argument(right, "this"))
            if left.kind == "Literal":
                return (sql_equal(argument(left, "this"), argument(right, "this"))
                        and sql_equal(argument(left, "is_string"), argument(right, "is_string")))
            a = {arg.name: arg.value for arg in left.args}
            b = {arg.name: arg.value for arg in right.args}
            return a.keys() == b.keys() and all(_norm_equal(a[key], b[key]) for key in a)
        if type(left) in tree.CONNECTORS:
            return sql_equal(left.left, right.left) and sql_equal(left.right, right.right)
        return sql_equal(left.this, right.this)
    if type(left) is tree.Sequence or type(right) is tree.Sequence:
        return (type(left) is type(right)
                and sql_equal(left.items, right.items))
    if type(left) is tuple or type(right) is tuple:
        return (type(left) is type(right) and len(left) == len(right)
                and all(sql_equal(a, b) for a, b in zip(left, right)))
    if type(left) is tree.EnumValue or type(right) is tree.EnumValue:
        return (type(left) is type(right) and left.module == right.module
                and left.qualname == right.qualname and left.name == right.name)
    return left == right


def _norm_equal(left, right):
    if type(left) is str:
        left = left.lower()
    if type(right) is str:
        right = right.lower()
    if type(left) is tree.Sequence and type(right) is tree.Sequence:
        return (len(left.items) == len(right.items)
                and all(_norm_equal(a, b) if type(a) is str or type(b) is str
                        else sql_equal(a, b) for a, b in zip(left.items, right.items)))
    return sql_equal(left, right)


TRUE = tree.Expression("Boolean", (tree.Argument("this", True),))
FALSE = tree.Expression("Boolean", (tree.Argument("this", False),))
NULL = tree.Expression("Null", ())


def always_true(node):
    # Faithful to 4.6.0: even numeric zero and empty strings count as true here.
    return sql_equal(node, TRUE) or (type(node) is tree.Expression and node.kind == "Literal")


def simplify_not(node):
    if type(node) is tree.Not:
        if type(node.this) is tree.Paren:
            condition = tree.unnest(node.this)
            if type(condition) in tree.CONNECTORS:
                result_kind = tree.Or if type(condition) is tree.And else tree.And
                return tree.combine(result_kind, (tree.Not(tree._wrap(condition.left)),
                                                  tree.Not(tree._wrap(condition.right))))
        if always_true(node.this):
            return FALSE
        if sql_equal(node.this, FALSE):
            return TRUE
        if type(node.this) is tree.Not:
            return node.this.this
    return node


def simplify_connectors(node):
    if type(node) not in tree.CONNECTORS:
        return node
    left, right = node.left, node.right
    if sql_equal(left, right):
        return left
    if type(node) is tree.And:
        # Preserve upstream's NULL-before-FALSE rule, not SQL three-valued AND.
        if sql_equal(left, NULL) or sql_equal(right, NULL):
            return NULL
        if sql_equal(left, FALSE) or sql_equal(right, FALSE):
            return FALSE
        if always_true(left) and always_true(right):
            return TRUE
        if always_true(left):
            return right
        if always_true(right):
            return left
    else:
        if always_true(left) or always_true(right):
            return TRUE
        left_false, right_false = sql_equal(left, FALSE), sql_equal(right, FALSE)
        if left_false and right_false:
            return FALSE
        left_null, right_null = sql_equal(left, NULL), sql_equal(right, NULL)
        if (left_null and right_null) or (left_null and right_false) or (left_false and right_null):
            return NULL
        if left_false:
            return right
        if right_false:
            return left
    return node


def is_complement(a, b):
    return type(b) is tree.Not and sql_equal(b.this, a)


def remove_complements(node):
    if type(node) in tree.CONNECTORS:
        terms = tuple(tree._terms(node, type(node)))
        for i, a in enumerate(terms):
            for j, b in enumerate(terms):
                if i != j and is_complement(a, b):
                    return FALSE if type(node) is tree.And else TRUE
    return node


def boolean_pass(node, sort_key):
    """One partial pass in upstream rule order; not a full simplifier.

    No absorption, literals, parenthesis/WHERE/JOIN cleanup or convergence yet.
    Sort keys are explicitly injected; the rules themselves need no SQLGlot.
    """
    node = tree.uniq_sort(node, sort_key)
    node = tree.map_children(node, lambda child: boolean_pass(child, sort_key))
    node = simplify_not(node)
    node = tree.flatten(node)
    node = simplify_connectors(node)
    return remove_complements(node)


# Frozen 4.6.0 hierarchy metadata, not a runtime dependency on SQLGlot classes.
# Tests compare these tables with every built-in expression class.
BINARY_KINDS = frozenset("""
    Binary Add Connector And Or BitwiseAnd BitwiseLeftShift BitwiseOr
    BitwiseRightShift BitwiseXor Div Dot DPipe EQ Escape GT GTE ILike IntDiv Is
    Like LT LTE Mod Mul NEQ Sub
""".split())
CONDITION_KINDS = frozenset("""
    Condition Predicate Column Literal Null Boolean SubqueryPredicate All Any
    Exists Connector And Or EQ GT GTE ILike Is Like LT LTE NEQ Not Paren Between
    Bracket Case In Func AggFunc Abs Anonymous ApproxDistinct Array ArrayAgg
    ArrayAll ArrayAny ArrayContains ArrayFilter ArraySize ArraySort ArraySum Avg
    Ceil Coalesce ConcatWs Count CurrentDate CurrentDatetime CurrentTime
    CurrentTimestamp DateAdd DateSub DateDiff DateTrunc DatetimeAdd DatetimeSub
    DatetimeDiff DatetimeTrunc TimestampAdd TimestampSub TimestampDiff
    TimestampTrunc TimeAdd TimeSub TimeDiff TimeTrunc DateStrToDate DateToDateStr
    DateToDi Day DiToDate Exp Explode Floor Greatest If Initcap JSONExtract
    JSONExtractScalar JSONBExtract JSONBExtractScalar Least Length Levenshtein
    Ln Log Log2 Log10 Lower Map Max Min Month Posexplode Pow Quantile Reduce
    RegexpLike RegexpSplit Round SafeDivide SetAgg SortArray Split Substring
    StrPosition StrToTime StrToUnix Struct StructExtract Sum Sqrt Stddev
    StddevPop StddevSamp TimeToStr TimeToTimeStr TimeToUnix TimeStrToDate
    TimeStrToTime TimeStrToUnix TsOrDsAdd TsOrDsToDateStr TsOrDsToDate TsOrDiToDi
    UnixToStr UnixToTime UnixToTimeStr Upper Variance VariancePop VarianceSamp
    Week Year
""".split())


def set_argument(node, name, value):
    """Path-copy a general expression argument, retaining unaffected Arguments."""
    args = []
    found = False
    for arg in node.args:
        if arg.name == name:
            if arg.value is value:
                return node
            args.append(tree.Argument(name, value))
            found = True
        else:
            args.append(arg)
    if not found:
        args.append(tree.Argument(name, value))
    return tree.Expression(node.kind, tuple(args))


def _connector_terms(node, connector, path=()):
    # Retain paths through parentheses, since replacing an unnested operand
    # must not replace its wrapper or another occurrence of a shared value.
    if type(node) is connector:
        yield from _connector_terms(node.left, connector, path + ("left",))
        yield from _connector_terms(node.right, connector, path + ("right",))
    else:
        while type(node) is tree.Paren:
            node, path = node.this, path + ("this",)
        yield path, node


def _at(node, path):
    for name in path:
        node = getattr(node, name)
    return node


def _replace(node, path, value):
    if not path:
        return value
    name, *rest = path
    if type(node) in tree.CONNECTORS:
        left = _replace(node.left, rest, value) if name == "left" else node.left
        right = _replace(node.right, rest, value) if name == "right" else node.right
        return node if left is node.left and right is node.right else type(node)(left, right)
    this = _replace(node.this, rest, value)
    return node if this is node.this else type(node)(this)


def _operand_path(node, name):
    path = (name,)
    operand = getattr(node, name)
    while type(operand) is tree.Paren:
        path += ("this",)
        operand = operand.this
    return path, operand


def equality_key(value, memo=None):
    """Hashable SQL-equality key, NOT a canonical operand sort/rendering key.

    An optional, call-local identity memo retains its immutable inputs alongside
    keys, so temporary record IDs cannot be recycled under a cached entry.
    Argument order is ignored just as in sql_equal; upstream's inconsistent
    hashes for reordered argument dictionaries are deliberately not reproduced.
    """
    if memo is not None and id(value) in memo:
        return memo[id(value)][1]

    def key(child):
        return equality_key(child, memo)

    def norm(child):
        if type(child) is str:
            return key(child.lower())
        if type(child) is tree.Sequence:
            return "list", tuple(key(item.lower()) if type(item) is str else key(item)
                                 for item in child.items)
        return key(child)

    if type(value) is tree.Expression:
        if value.kind == "Identifier":
            result = "Expression", value.kind, norm(argument(value, "this"))
        elif value.kind == "Literal":
            result = "Expression", value.kind, key(argument(value, "this")), key(argument(value, "is_string"))
        else:
            result = "Expression", value.kind, tuple(sorted((arg.name, norm(arg.value)) for arg in value.args))
    elif type(value) in tree.CONNECTORS:
        result = kind(value), key(value.left), key(value.right)
    elif type(value) in (tree.Not, tree.Paren):
        result = kind(value), key(value.this)
    elif type(value) is tree.Sequence:
        result = "list", tuple(key(item) for item in value.items)
    elif type(value) is tuple:
        result = "tuple", tuple(key(item) for item in value)
    elif type(value) is tree.EnumValue:
        result = "enum", value.module, value.qualname, value.name
    else:
        result = "scalar", value
    if memo is not None:
        memo[id(value)] = value, result
    return result


def _proper_subset(left, right, memo):
    return {equality_key(value, memo) for value in left} < {equality_key(value, memo) for value in right}


def absorb_and_eliminate(node):
    """Ordered absorption/elimination using explicit occurrence-path edits.

    Edits never escape the current connector, even when input values are shared.
    Subsequent pairs read current values, not mutable aliases to detached nodes.
    The enclosing simplify fixed point revisits exposed opportunities.
    """
    if type(node) not in tree.CONNECTORS:
        return node
    opposite = tree.Or if type(node) is tree.And else tree.And
    paths = tuple(path for path, _ in _connector_terms(node, type(node)))
    memo = {}  # Scratch equality keys for this connector only, never published.
    for i, a_path in enumerate(paths):
        for j, b_path in enumerate(paths):
            if i == j:
                continue
            a, b = _at(node, a_path), _at(node, b_path)
            if type(a) is not opposite:
                continue
            aa_path, aa = _operand_path(a, "left")
            ab_path, ab = _operand_path(a, "right")
            if is_complement(b, aa):
                node = _replace(node, a_path + aa_path, TRUE if opposite is tree.And else FALSE)
            elif is_complement(b, ab):
                node = _replace(node, a_path + ab_path, TRUE if opposite is tree.And else FALSE)
            elif _proper_subset(tree._terms(b, opposite) if type(b) is opposite else (b,),
                                tree._terms(a, opposite), memo):
                node = _replace(node, a_path, FALSE if opposite is tree.And else TRUE)
            elif type(b) is opposite:
                ba, bb = tree.unnest(b.left), tree.unnest(b.right)
                common = None
                if (sql_equal(aa, ba) or sql_equal(aa, bb)) and (is_complement(ab, ba) or is_complement(ab, bb)):
                    common = aa
                elif (sql_equal(ab, ba) or sql_equal(ab, bb)) and (is_complement(aa, ba) or is_complement(aa, bb)):
                    common = ab
                if common is not None:
                    node = _replace(node, a_path, common)
                    node = _replace(node, b_path, common)
    return node


def _is(node, name):
    return (type(node) is tree.Expression
            and (node.kind == name or (name == "Cast" and node.kind == "TryCast")))


def text(node, name="this"):
    value = argument(node, name)
    if type(value) is str:
        return value
    if type(value) is tree.Expression and value.kind in ("Identifier", "Literal", "Var"):
        return argument(value, "this")
    return ""


def literal(value, is_string=False):
    return tree.Expression("Literal", (tree.Argument("this", str(value)),
                                       tree.Argument("is_string", is_string)))


def _binary(kind_name, left, right):
    if kind_name in ("And", "Or"):
        return (tree.And if kind_name == "And" else tree.Or)(left, right)
    return tree.Expression(kind_name, (tree.Argument("this", left), tree.Argument("expression", right)))


def eval_boolean(node, a, b):
    operator = kind(node)
    if operator in ("EQ", "Is"):
        result = sql_equal(a, b)
    elif operator == "NEQ":
        result = not sql_equal(a, b)
    elif operator == "GT":
        result = a > b
    elif operator == "GTE":
        result = a >= b
    elif operator == "LT":
        result = a < b
    elif operator == "LTE":
        result = a <= b
    else:
        return None
    return TRUE if result else FALSE


def extract_date(cast):
    dtype = argument(argument(cast, "to"), "this")
    if (type(dtype) is tree.EnumValue and dtype.module == "sqlglot.expressions"
            and dtype.qualname == "DataType.Type" and dtype.name == "DATE"):
        return datetime.date.fromisoformat(text(cast))
    return None


def extract_interval(interval):
    try:
        from dateutil.relativedelta import relativedelta
    except ModuleNotFoundError:
        return None
    n, unit = int(text(interval)), text(interval, "unit").lower()
    field = {"year": "years", "month": "months", "week": "weeks", "day": "days"}.get(unit)
    return relativedelta(**{field: n}) if field else None


def date_literal(value):
    dtype = tree.Expression("DataType", (tree.Argument("this", tree.EnumValue(
        "sqlglot.expressions", "DataType.Type", "DATE")),))
    return tree.Expression("Cast", (tree.Argument("this", literal(value, True)), tree.Argument("to", dtype)))


def _simplify_binary(node, a, b):
    operator = kind(node)
    if operator == "Is":
        c, negate = (b.this, True) if type(b) is tree.Not else (b, False)
        if sql_equal(c, NULL):
            if _is(a, "Literal"):
                return TRUE if negate else FALSE
            if sql_equal(a, NULL):
                return FALSE if negate else TRUE
    elif sql_equal(a, NULL) or sql_equal(b, NULL):
        return NULL
    if operator == "EQ" and sql_equal(a, b):
        return TRUE
    if _is(a, "Literal") and _is(b, "Literal"):
        a_string, b_string = argument(a, "is_string"), argument(b, "is_string")
        if not a_string and not b_string:
            a_text, b_text = text(a), text(b)
            a = int(a_text) if a_text.isdigit() else Decimal(a_text)
            b = int(b_text) if b_text.isdigit() else Decimal(b_text)
            if operator == "Add":
                return literal(a + b)
            if operator == "Sub":
                return literal(a - b)
            if operator == "Mul":
                return literal(a * b)
            if operator == "Div":
                return literal(a // b if type(a) is int and type(b) is int else a / b)
            return eval_boolean(node, a, b)
        if a_string and b_string:
            # 4.6.0 compares Literal objects here, not their text. EQ/NEQ work;
            # ordering raises TypeError. Do not silently repair that behavior.
            if operator in ("GT", "GTE", "LT", "LTE"):
                raise TypeError("ordering SQLGlot Literal expressions is unsupported")
            return eval_boolean(node, a, b)
    elif _is(a, "Cast") and _is(b, "Interval"):
        a, b = extract_date(a), extract_interval(b)
        if b:
            if operator == "Add":
                return date_literal(a + b)
            if operator == "Sub":
                return date_literal(a - b)
    elif _is(a, "Interval") and _is(b, "Cast"):
        a, b = extract_interval(a), extract_date(b)
        if a and operator == "Add":
            return date_literal(a + b)
    return None


def _binary_terms(node, operator):
    if kind(node) == operator:
        for child in tree.children(node):
            yield from _binary_terms(child, operator)
    else:
        yield node


def simplify_literals(node):
    operator = kind(node)
    if operator not in BINARY_KINDS:
        return node
    queue = list(_binary_terms(node, operator))
    size, operands = len(queue), []
    while queue:
        a = queue.pop(0)
        for i, b in enumerate(queue):
            result = _simplify_binary(node, a, b)
            if result is not None:
                del queue[i]
                queue.append(result)
                break
        else:
            operands.append(a)
    if len(operands) < size:
        result = operands[0]
        for operand in operands[1:]:
            result = _binary(operator, result, operand)
        return result
    return node


def simplify_parens(node, parent_kind=None):
    if (type(node) is tree.Paren and not _is(node.this, "Select")
            and (parent_kind not in CONDITION_KINDS | BINARY_KINDS
                 or kind(node.this) in ("Is", "Like") or kind(node.this) not in BINARY_KINDS)):
        return node.this
    return node


def remove_where_true(node):
    # Edit parent arguments rather than returning None from expression traversal.
    node = tree.map_children(node, remove_where_true)
    if type(node) is tree.Expression:
        for arg in node.args:
            if _is(arg.value, "Where") and always_true(argument(arg.value, "this")):
                node = set_argument(node, "where", None)
        if node.kind == "Join" and always_true(argument(node, "on")):
            node = set_argument(node, "kind", "CROSS")
            node = set_argument(node, "on", None)
    return node


def same_value(a, b):
    """Exact value convergence, including variant kinds under all controls."""
    if type(a) is not type(b):
        return False
    if type(a) is tree.Expression:
        return a.kind == b.kind and same_value(a.args, b.args)
    if type(a) is tree.Argument:
        return a.name == b.name and same_value(a.value, b.value)
    if type(a) in tree.CONNECTORS:
        return same_value(a.left, b.left) and same_value(a.right, b.right)
    if type(a) in (tree.Not, tree.Paren):
        return same_value(a.this, b.this)
    if type(a) is tree.Sequence:
        return same_value(a.items, b.items)
    if type(a) is tuple:
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    return a == b


def simplify_pass(node, sort_key, parent_kind=None):
    node = tree.uniq_sort(node, sort_key)
    node = absorb_and_eliminate(node)
    parent = kind(node)
    node = tree.map_children(node, lambda child: simplify_pass(child, sort_key, parent))
    node = simplify_not(node)
    node = tree.flatten(node)
    node = simplify_connectors(node)
    node = remove_complements(node)
    node = simplify_literals(node)
    return simplify_parens(node, parent_kind)


def simplify(node, sort_key):
    """Full rule pipeline to an exact value fixed point; no reference rewrites.

    Unlike upstream's hash-only stop test, collisions cannot stop iteration.
    Parent context describes actual occurrences, never discarded mutable roots.
    """
    while True:
        result = simplify_pass(node, sort_key)
        if same_value(node, result):
            return remove_where_true(result)
        node = result
