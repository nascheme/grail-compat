"""Record-native compact base SQLGlot 4.6.0 SQL generation.

No SQLGlot imports, mutable ASTs, parent pointers or reference-renderer fallback.
Pretty printing and dialect-specific generators are not yet ported; unsupported
options/kinds fail explicitly. Algorithms adapted from SQLGlot (MIT); see
../LICENSES/sqlglot-LICENSE. The tokenizer's escape marker is a versioned contract.
"""

from copy import replace
import logging
import re
from types import MappingProxyType

import grail

from sqlglot_port import idiomatic as tree


@grail.record
class Options:
    identify: bool = False
    normalize: bool = False
    identifier: str = '"'
    quote: str = "'"
    escape: str = "'"
    normalize_functions: object = "upper"
    index_offset: int = 0
    unnest_column_only: bool = False
    alias_post_tablesample: bool = False


@grail.record
class Rendered:
    sql: str
    unsupported: tuple[str, ...]


class UnsupportedError(ValueError):
    pass


DEFAULT = Options()
CANONICAL = Options(identify=True, normalize=True)
ESCAPE_CODE = "__sqlglot_escape__"
LOGGER = logging.getLogger("sqlglot")

# Frozen 4.6.0 Func metadata, verified against all upstream classes in tests.
_FUNCTION_GROUPS = (
    ("AggFunc Abs ArrayAgg ArraySize ArraySum Avg Ceil Count DateStrToDate DateToDateStr DateToDi Day DiToDate Exp Explode Floor Initcap Length Ln Log2 Log10 Lower Max Min Month Posexplode SetAgg Sum Sqrt Stddev StddevPop StddevSamp TimeToTimeStr TimeToUnix TimeStrToDate TimeStrToTime TimeStrToUnix TsOrDsToDateStr TsOrDsToDate TsOrDiToDi UnixToTimeStr Upper Variance VariancePop VarianceSamp Week Year", ("this",)),
    ("Anonymous Coalesce Greatest Least", ("this", "expressions")),
    ("ApproxDistinct", ("this", "accuracy")),
    ("Array ConcatWs Struct", ("expressions",)),
    ("ArrayAll ArrayAny ArrayContains ArrayFilter ArraySort Levenshtein Log RegexpLike RegexpSplit SafeDivide Split StructExtract", ("this", "expression")),
    ("CurrentDate CurrentDatetime CurrentTime CurrentTimestamp", ("this",)),
    ("DateAdd DateSub DateDiff DatetimeAdd DatetimeSub DatetimeDiff TimestampAdd TimestampSub TimestampDiff TimeAdd TimeSub TimeDiff TsOrDsAdd", ("this", "expression", "unit")),
    ("DateTrunc DatetimeTrunc TimestampTrunc TimeTrunc", ("this", "unit", "zone")),
    ("If", ("this", "true", "false")),
    ("JSONExtract JSONExtractScalar JSONBExtract JSONBExtractScalar", ("this", "path")),
    ("Map", ("keys", "values")),
    ("Pow", ("this", "power")),
    ("Quantile", ("this", "quantile")),
    ("Reduce", ("this", "initial", "merge", "finish")),
    ("Round", ("this", "decimals")),
    ("SortArray", ("this", "asc")),
    ("Substring", ("this", "start", "length")),
    ("StrPosition", ("this", "substr", "position")),
    ("StrToTime StrToUnix TimeToStr UnixToStr", ("this", "format")),
    ("UnixToTime", ("this", "scale")),
)
_FUNCTION_NAMES = {
    "ArrayFilter": "FILTER", "JSONExtract": "JSON_EXTRACT",
    "JSONExtractScalar": "JSON_EXTRACT_SCALAR", "JSONBExtract": "JSONB_EXTRACT",
    "JSONBExtractScalar": "JSONB_EXTRACT_SCALAR",
}
FUNCTIONS = MappingProxyType({
    name: (_FUNCTION_NAMES.get(name, re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()), args)
    for names, args in _FUNCTION_GROUPS for name in names.split()
})
BINARY = MappingProxyType({
    "Add": "+", "And": "AND", "Or": "OR", "Sub": "-", "Mul": "*", "Div": "/",
    "Mod": "%", "BitwiseAnd": "&", "BitwiseOr": "|", "BitwiseXor": "^",
    "BitwiseLeftShift": "<<", "BitwiseRightShift": ">>", "DPipe": "||",
    "EQ": "=", "NEQ": "<>", "GT": ">", "GTE": ">=", "LT": "<", "LTE": "<=",
    "Escape": "ESCAPE", "ILike": "ILIKE", "Is": "IS", "Like": "LIKE",
})
_ROOT_PROPERTIES = frozenset(("AutoIncrementProperty", "CharacterSetProperty", "CollateProperty", "EngineProperty", "SchemaCommentProperty"))
_WITH_PROPERTIES = frozenset(("AnonymousProperty", "FileFormatProperty", "PartitionedByProperty", "TableFormatProperty"))
_PROPERTY_PREFIX = MappingProxyType({
    "AutoIncrementProperty": "AUTO_INCREMENT=", "CollateProperty": "COLLATE=",
    "EngineProperty": "ENGINE=", "FileFormatProperty": "FORMAT=",
    "LocationProperty": "LOCATION ", "PartitionedByProperty": "PARTITIONED_BY=",
    "SchemaCommentProperty": "COMMENT=", "TableFormatProperty": "TABLE_FORMAT=",
})


def kind(node):
    return node.kind if type(node) is tree.Expression else type(node).__name__


def argument(node, key):
    if type(node) is tree.Expression:
        return next((arg.value for arg in node.args if arg.name == key), None)
    if type(node) in tree.CONNECTORS:
        return node.left if key == "this" else node.right if key == "expression" else None
    if type(node) in (tree.Not, tree.Paren):
        return node.this if key == "this" else None
    return None


def items(value):
    if type(value) is tree.Sequence:
        return value.items
    if type(value) is tuple:
        return value
    return () if value is None else (value,)


def truthy(value):
    # Record truthiness (notably Sequence under controls) is not SQLGlot/list
    # truthiness. Handle argument containers/scalars explicitly.
    if type(value) in tree.NODES:
        return True
    if type(value) is tree.Sequence:
        return bool(value.items)
    return bool(value)


def text(node, key="this"):
    value = argument(node, key)
    if type(value) is str:
        return value
    if type(value) is tree.Expression and value.kind in ("Identifier", "Literal", "Var"):
        return argument(value, "this")
    return ""


def csv(*values, sep=", "):
    return sep.join(value for value in values if value)


def _sql(value, options, messages, arg_key=None):
    if not truthy(value):
        return ""
    if type(value) is str:
        return value
    if type(value) not in tree.NODES:
        raise ValueError(f"Expected an expression record, received {type(value)}")
    return sql_node(value, options, messages, arg_key)


def _expressions(node, options, messages, key="expressions", sep=", "):
    return sep.join(_sql(value, options, messages, key) for value in items(argument(node, key)))


def _wrap(node, options, messages):
    # Reference wrap accepts strings too, and wraps Select itself, but otherwise
    # wraps its 'this' child. This includes a few surprising empty-wrap cases.
    value = node if type(node) is str or kind(node) == "Select" else argument(node, "this")
    return f"({_sql(value, options, messages, None if value is node else 'this')})"


def _window_spec(node, options, messages):
    def field(key):
        return _sql(argument(node, key), options, messages, key)

    start = csv(field("start"), field("start_side"), sep=" ")
    end = csv(field("end"), field("end_side"), sep=" ")
    return f"{field('kind')} BETWEEN {start} AND {end}"


def _order(node, options, messages, flat=False):
    this = _sql(argument(node, "this"), options, messages, "this")
    expressions = _expressions(node, options, messages)
    if flat or this:
        return f"{this + ' ' if this else ''}ORDER BY {expressions}"
    return " ORDER BY" + (f" {expressions}" if expressions else "")


def _normalize_func(name, options):
    if options.normalize_functions == "upper":
        return name.upper()
    if options.normalize_functions == "lower":
        return name.lower()
    return name


@grail.generic
def sql_node(node, options, messages, arg_key):
    raise TypeError(type(node))


@sql_node.impl
def _and(node: tree.And, options, messages, arg_key):
    return f"{_sql(node.left, options, messages, 'this')} AND {_sql(node.right, options, messages, 'expression')}"


@sql_node.impl
def _or(node: tree.Or, options, messages, arg_key):
    return f"{_sql(node.left, options, messages, 'this')} OR {_sql(node.right, options, messages, 'expression')}"


@sql_node.impl
def _not(node: tree.Not, options, messages, arg_key):
    return f"NOT {_sql(node.this, options, messages, 'this')}"


@sql_node.impl
def _paren(node: tree.Paren, options, messages, arg_key):
    return f"({_sql(node.this, options, messages, 'this')})"


@sql_node.impl
def _expression(node: tree.Expression, options, messages, arg_key):
    name = node.kind

    def arg(key):
        return argument(node, key)

    def field(key):
        return _sql(arg(key), options, messages, key)

    def sql(value, key=None):
        return _sql(value, options, messages, key)

    def expressions(key="expressions", sep=", "):
        return _expressions(node, options, messages, key, sep)

    def ctes(value):
        with_ = field("with")
        return f"{with_} {value}" if with_ else value

    if name in BINARY:
        return f"{field('this')} {BINARY[name]} {field('expression')}"
    if name in ("Column", "Table"):
        keys = ("db", "table", "this") if name == "Column" else ("catalog", "db", "this")
        return csv(*(field(key) for key in keys), sep=".")
    if name == "Identifier":
        value = text(node)
        value = value.lower() if options.normalize else value
        return f"{options.identifier}{value}{options.identifier}" if arg("quoted") or options.identify else value
    if name == "Literal":
        value = arg("this") or ""
        if arg("is_string"):
            value = value.replace("\\", "\\\\") if options.escape == "\\" else value
            return f"{options.quote}{value.replace(ESCAPE_CODE, options.escape)}{options.quote}"
        return value
    if name == "Null":
        return "NULL"
    if name == "Boolean":
        return "TRUE" if arg("this") else "FALSE"
    if name == "Var":
        return field("this")
    if name == "Placeholder":
        return "?"
    if name == "Annotation":
        return field("expression")
    if name in ("Neg", "BitwiseNot"):
        return ("-" if name == "Neg" else "~") + field("this")
    if name == "Dot":
        return f"{field('this')}.{field('expression')}"
    if name in ("Cast", "TryCast"):
        operator = "CAST" if name == "Cast" else "TRY_CAST"
        return f"{operator}({field('this')} AS {field('to')})"
    if name == "IntDiv":
        return f"CAST({field('this')} / {field('expression')} AS INT)"
    if name == "DataType":
        dtype = arg("this")
        if type(dtype) is not tree.EnumValue or dtype.module != "sqlglot.expressions" or dtype.qualname != "DataType.Type":
            raise ValueError("expected a SQLGlot data type enum")
        interior = expressions()
        nested = f"<{interior}>" if arg("nested") else f"({interior})"
        return dtype.name + (nested if interior else "")
    if name in ("Tuple", "Schema"):
        this = field("this") if name == "Schema" else ""
        return f"{this + ' ' if this else ''}({expressions()})"
    if name == "Alias":
        alias = field("alias")
        return field("this") + (f" AS {alias}" if alias else "")
    if name == "Aliases":
        return f"{field('this')} AS ({expressions()})"
    if name == "TableAlias":
        columns = expressions("columns")
        return field("this") + (f"({columns})" if columns else "")
    if name == "AtTimeZone":
        return f"{field('this')} AT TIME ZONE {field('zone')}"
    if name == "IgnoreNulls":
        return f"{field('this')} IGNORE NULLS"
    if name == "Between":
        return f"{field('this')} BETWEEN {field('low')} AND {field('high')}"
    if name == "In":
        value = _wrap(arg("query"), options, messages) if truthy(arg("query")) else f"({expressions()})"
        return f"{field('this')} IN {value}"
    if name == "Interval":
        return f"INTERVAL {field('this')} {field('unit')}"
    if name == "Extract":
        return f"EXTRACT({field('this')} FROM {field('expression')})"
    if name == "Bracket":
        values = items(arg("expressions"))
        if options.index_offset and len(values) == 1:
            value = values[0]
            if kind(value) == "Literal" and not argument(value, "is_string") and text(value).isdigit():
                LOGGER.warning("Applying array index offset (%s)", options.index_offset)
                # Change only the rendered index; the immutable input is reused.
                return f"{field('this')}[{int(text(value)) + options.index_offset}]"
        return f"{field('this')}[{expressions()}]"
    if name in ("All", "Any", "Exists"):
        prefix = {"All": "ALL ", "Any": "ANY ", "Exists": "EXISTS"}[name]
        return prefix + _wrap(node, options, messages)
    if name in ("Case", "If"):
        this = field("this") if name == "Case" else ""
        conditions = items(arg("ifs")) if name == "Case" else (node,)
        clauses = "".join(f" WHEN {sql(argument(value, 'this'), 'this')} THEN {sql(argument(value, 'true'), 'true')}" for value in conditions)
        default_key = "default" if name == "Case" else "false"
        default = f" ELSE {field(default_key)}" if arg(default_key) is not None else ""
        return f"CASE{' ' + this if this else ''}{clauses}{default} END"
    if name == "Lambda":
        args = expressions()
        args = f"({args})" if len(args.split(",")) > 1 else args
        return f"{args} -> {_sql(arg('this'), replace(options, identify=False), messages, 'this')}"
    if name == "Anonymous":
        return f"{_normalize_func(field('this'), options)}({expressions()})"
    if name == "CurrentDate":
        zone = field("this")
        return f"CURRENT_DATE({zone})" if zone else "CURRENT_DATE"
    if name in ("DateAdd", "DateDiff", "TsOrDsAdd"):
        keys = ("this", "expression") if name == "DateDiff" else ("this", "expression", "unit")
        return f"{FUNCTIONS[name][0]}({', '.join(field(key) for key in keys)})"
    if name == "Distinct":
        this, on = field("this"), field("on")
        return f"DISTINCT{' ' + this if this else ''}{' ON ' + on if on else ''}"
    if name == "Star":
        except_, replace_ = expressions("except"), expressions("replace")
        return f"*{' EXCEPT (' + except_ + ')' if except_ else ''}{' REPLACE (' + replace_ + ')' if replace_ else ''}"
    if name == "From":
        return f" FROM {expressions()}"
    if name in ("Where", "Having", "Qualify"):
        return f" {name.upper()} {field('this')}"
    if name in ("Limit", "Offset"):
        return f"{field('this')} {name.upper()} {field('expression')}"
    if name == "Order":
        return _order(node, options, messages)
    if name in ("Cluster", "Distribute", "Sort"):
        values = expressions()
        return f" {name.upper()} BY{' ' + values if values else ''}"
    if name == "Ordered":
        return field("this") + (" DESC" if arg("desc") else "")
    if name == "Group":
        value = " GROUP BY" + (f" {expressions()}" if expressions() else "")
        for key, operator in (("grouping_sets", "GROUPING SETS"), ("cube", "CUBE"), ("rollup", "ROLLUP")):
            group = expressions(key)
            if group:
                value += f" {operator} ({group})"
        return value
    if name == "Join":
        operator = csv(text(node, "side").upper(), text(node, "kind").upper(), "JOIN", sep=" ")
        on, using = field("on"), arg("using")
        if not on and truthy(using):
            on = expressions("using")
        condition = f" USING ({on})" if truthy(using) else f" ON {on}"
        return f"{field('expression')} {operator} {field('this')}{condition if on else ''}"
    if name == "Lateral":
        alias = arg("alias")
        table = text(alias)
        columns = _expressions(alias, options, messages, "columns")
        return f" LATERAL VIEW{' OUTER' if arg('outer') else ''} {field('this')}{' ' + table if table else ''}{' AS ' + columns if columns else ''}"
    if name == "With":
        return f"WITH {'RECURSIVE ' if arg('recursive') else ''}{expressions()}"
    if name == "CTE":
        return f"{field('alias')} AS {_wrap(node, options, messages)}"
    if name in ("Select", "Subquery"):
        if name == "Select":
            distinct = field("distinct")
            fields = expressions()
            value = f"SELECT{field('hint')}{' ' + distinct if distinct else ''}{' ' + fields if fields else ''}{field('from')}"
            value += "".join(sql(child, "laterals") for child in items(arg("laterals")))
        else:
            alias = field("alias")
            value = _wrap(node, options, messages) + (f" AS {alias}" if alias else "")
        value += "".join(sql(child, "joins") for child in items(arg("joins")))
        keys = (("where", "group", "having", "qualify", "window") if name == "Select" else ())
        value += "".join(field(key) for key in keys + ("distribute", "sort", "cluster", "order", "limit", "offset"))
        return ctes(value) if name == "Select" else value
    if name in ("Union", "Intersect", "Except"):
        suffix = ("" if arg("distinct") else " ALL") if name == "Union" else (" DISTINCT" if arg("distinct") else "")
        return ctes(f"{field('this')} {name.upper()}{suffix} {field('expression')}")
    if name == "Unnest":
        alias = arg("alias")
        if truthy(alias) and options.unnest_column_only:
            columns = items(argument(alias, "columns"))
            alias_sql = sql(columns[0]) if columns else ""
        else:
            alias_sql = field("alias")
        return f"UNNEST({expressions()}){' WITH ORDINALITY' if arg('ordinality') else ''}{' AS ' + alias_sql if alias_sql else ''}"
    if name == "Window":
        this, partition, order, spec, alias = field("this"), expressions("partition_by"), arg("order"), arg("spec"), field("alias")
        partition_sql = f"PARTITION BY {partition}" if partition else ""
        order_sql = _order(order, options, messages, flat=True) if truthy(order) else ""
        spec_sql = " " + _window_spec(spec, options, messages) if truthy(spec) else ""
        this = f" WINDOW {this} AS" if arg_key == "window" else f"{this} OVER"
        if not partition and not truthy(order) and not truthy(spec) and alias:
            return f"{this} {alias}"
        return f"{this} ({alias}{partition_sql}{' ' if partition and truthy(order) else ''}{order_sql}{spec_sql})"
    if name in ("WithinGroup", "Filter"):
        operator = "WITHIN GROUP" if name == "WithinGroup" else "FILTER"
        space = " " if name == "WithinGroup" else ""
        return f"{field('this')} {operator}{space}({field('expression')[1:]})"
    if name == "TableSample":
        this, alias = field("this"), ""
        if options.alias_post_tablesample and kind(arg("this")) == "Alias":
            this = sql(argument(arg("this"), "this"), "this")
            alias = f" AS {sql(argument(arg('this'), 'alias'), 'alias')}"
        method = field("method")
        method = f" {method.upper()} " if method else ""
        numerator, field_sql = field("bucket_numerator"), field("bucket_field")
        bucket = f"BUCKET {numerator} OUT OF {field('bucket_denominator')}{' ON ' + field_sql if field_sql else ''}" if numerator else ""
        percent, rows = field("percent"), field("rows")
        return f"{this} TABLESAMPLE{method}({bucket}{percent + ' PERCENT' if percent else ''}{rows + ' ROWS' if rows else ''}{field('size')}){alias}"
    if name == "Values":
        return f"VALUES {expressions()}"
    if name == "Fetch":
        direction, count = arg("direction"), arg("count")
        # Reference interpolates count directly rather than rendering it.
        if truthy(count) and type(count) in tree.NODES:
            count = generate(count)
        return f" FETCH{' ' + direction.upper() if direction else ''}{' ' + str(count) if truthy(count) else ''} ROWS ONLY"
    if name == "Hint":
        if field("this"):
            messages.append("Hints are not supported")
        return ""
    if name == "Command":
        return f"{field('this').upper()} {text(node, 'expression').strip()}"
    if name == "Delete":
        return ctes(f"DELETE FROM {field('this')}{field('where')}")
    if name == "Update":
        return ctes(f"UPDATE {field('this')} SET {expressions()}{field('from')}{field('where')}")
    if name == "Insert":
        operator = "OVERWRITE TABLE" if arg("overwrite") else "INTO"
        partition = field("partition") if truthy(arg("partition")) else ""
        exists = " IF EXISTS " if arg("exists") else " "
        return ctes(f"INSERT {operator} {field('this')}{exists}{partition}{' ' if partition else ''}{field('expression')}")
    if name == "Drop":
        return f"DROP {arg('kind')}{' IF EXISTS ' if arg('exists') else ' '}{field('this')}"
    if name == "Create":
        expression = field("expression")
        value = f"CREATE{' OR REPLACE' if arg('replace') else ''}{' TEMPORARY' if arg('temporary') else ''} {field('kind').upper()}{' IF NOT EXISTS' if arg('exists') else ''} {field('this')}{field('properties')} {'AS ' + expression if expression else ''}"
        return ctes(value)
    if name == "ColumnDef":
        value = f"{field('this')} {field('kind')}{' NOT NULL' if arg('not_null') else ''}"
        for key, prefix in (("default", " DEFAULT "), ("collate", " COLLATE "), ("comment", " COMMENT ")):
            part = field(key)
            if part:
                value += prefix + part
        return value + (" UNIQUE" if arg("unique") else "") + (" PRIMARY KEY" if arg("primary") else "") + (" AUTO_INCREMENT" if arg("auto_increment") else "")
    if name == "Constraint":
        return f"CONSTRAINT {field('this')} {expressions()}"
    if name == "ForeignKey":
        value = f"FOREIGN KEY ({expressions()})"
        for key, prefix in (("reference", " "), ("delete", " ON DELETE "), ("update", " ON UPDATE ")):
            part = field(key)
            if part:
                value += prefix + part
        return value
    if name == "Reference":
        return f"REFERENCES {field('this')}({expressions()})"
    if name == "StructKwarg":
        return f"{field('this')} {field('expression')}"
    if name == "CharacterSet":
        return f"{'DEFAULT ' if arg('default') else ''}CHARACTER SET={field('this')}"
    if name == "CharacterSetProperty":
        return f"{'DEFAULT ' if arg('default') else ''}CHARACTER SET={field('value')}"
    if name in _PROPERTY_PREFIX:
        return _PROPERTY_PREFIX[name] + field("value")
    if name in ("AnonymousProperty", "Property"):
        return f"{text(node)} = {field('value')}"
    if name == "Properties":
        root = [sql(p, "expressions") for p in items(arg("expressions")) if kind(p) in _ROOT_PROPERTIES]
        with_ = [sql(p, "expressions") for p in items(arg("expressions")) if kind(p) in _WITH_PROPERTIES]
        return (" " + " ".join(root) if root else "") + (" WITH (" + ", ".join(with_) + ")" if with_ else "")
    if name == "Partition":
        pairs = []
        for pair in items(arg("this")):
            k, v = items(pair)
            pairs.append(f"{argument(k, 'this')}='{argument(v, 'this')}'" if truthy(v) else argument(k, "this"))
        return f"PARTITION({csv(*pairs)})"
    if name == "Uncache":
        return f"UNCACHE TABLE{' IF EXISTS' if arg('exists') else ''} {field('this')}"
    if name == "Cache":
        cache_options = items(arg("options"))
        option_sql = f" OPTIONS({sql(cache_options[0])} = {sql(cache_options[1])})" if cache_options else ""
        expression = field("expression")
        return ctes(f"CACHE{' LAZY' if arg('lazy') else ''} TABLE {field('this')}{option_sql}{' AS ' + expression if expression else ''}")
    if name == "Func":
        raise NotImplementedError("SQL name is only supported by concrete function implementations")
    if name in FUNCTIONS:
        sql_name, keys = FUNCTIONS[name]
        args = [sql(value, key) for key in keys for value in items(arg(key)) if truthy(arg(key))]
        return f"{_normalize_func(sql_name, options)}({', '.join(args)})"
    raise ValueError(f"Unsupported expression type {name}")


def render(node, options=DEFAULT):
    """Value output with call-local diagnostic scratch, never AST mutation."""
    if type(node) not in tree.NODES:
        raise TypeError("expected an expression record")
    messages = []
    return Rendered(_sql(node, options, messages).strip(), tuple(messages))


def generate(node, options=DEFAULT, *, pretty=False, unsupported="warn"):
    """Compact base SQL, not a dialect-specific or pretty-printing API."""
    if pretty:
        raise NotImplementedError("record-native pretty printing is not yet ported")
    if unsupported not in ("warn", "ignore", "raise"):
        raise ValueError("unsupported must be warn, ignore or raise")
    result = render(node, options)
    if unsupported == "raise" and result.unsupported:
        raise UnsupportedError(result.unsupported[0])
    if unsupported == "warn":
        for message in result.unsupported:
            LOGGER.warning(message)
    return result.sql


def canonical_sql(node):
    return generate(node, CANONICAL)
