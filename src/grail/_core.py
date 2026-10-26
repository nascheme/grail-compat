"""Pure-Python stand-in for the native ``_grail`` module.

The facade (``grail/__init__.py``) is a close port of the native one and
calls this module through the same private API: _make_record, _record_info,
_record_values, _is_record_type, _is_conforming, _deepcopy_is_self, Generic,
_default_repr/_default_eq/_default_hash, _configure, _kernel/_is_kernel and
substitutable.

Records are ordinary heap types with one hidden slot per field plus a hidden
conformance bit.  The slot descriptors are removed from the class and fields
are exposed through read-only properties.  On CPython the type is then
hardened through ctypes like the native PyType_Freeze'd type: immutable, not
subclassable, and not instantiable through object.__new__.  Without ctypes
the records still work but those three bypasses stay open.

Generic functions publish an immutable (registry, table, version) state
under a lock; calls read the published state without locking, so they are
safe on free-threaded builds.  Exact tables are keyed by type identity.
There is no inherited-dispatch cache: every inherited call runs the resolver.
"""

import builtins as _builtins
import operator as _operator
import struct as _struct
import sys as _sys
import threading as _threading

backend = "shim"

_SCHEMA = "__grail_schema__"
_CONFORMING = "__grail_conforming__"

_TPFLAGS_IMMUTABLETYPE = 1 << 8
_TPFLAGS_HEAPTYPE = 1 << 9
_TPFLAGS_BASETYPE = 1 << 10
_TPFLAGS_IS_ABSTRACT = 1 << 20


# --------------------------------------------------------------- hardening

def _setup_hardening():
    """Return (alloc, freeze) using ctypes, or None where unavailable."""
    if _sys.implementation.name != "cpython":
        return None
    try:
        import ctypes
        api = ctypes.pythonapi
        alloc = api.PyType_GenericAlloc
        alloc.argtypes = (ctypes.py_object, ctypes.c_ssize_t)
        alloc.restype = ctypes.py_object
        modified = api.PyType_Modified
        modified.argtypes = (ctypes.py_object,)
        modified.restype = None
        word = ctypes.sizeof(ctypes.c_void_p)
        probe = type("_probe", (), {"__slots__": ("a", "b", "c")})
        base = id(probe)
        offset = None
        # PyTypeObject: header, tp_name, tp_basicsize, tp_itemsize, then 15
        # pointer-sized slots before tp_flags (an unsigned long).
        for i in range(2, 12):
            if (ctypes.c_ssize_t.from_address(base + i * word).value
                    == probe.__basicsize__ and
                    ctypes.c_ssize_t.from_address(base + (i + 1) * word).value
                    == probe.__itemsize__):
                candidate = (i + 17) * word
                if (ctypes.c_ulong.from_address(base + candidate).value
                        == probe.__flags__):
                    offset = candidate
                    break
        if offset is None:
            return None
    except Exception:
        return None

    def freeze(cls):
        # Only called before the type is published.
        flags = ctypes.c_ulong.from_address(id(cls) + offset)
        if flags.value != cls.__flags__:
            raise RuntimeError("cannot harden record type")
        flags.value = ((flags.value | _TPFLAGS_IMMUTABLETYPE | _TPFLAGS_IS_ABSTRACT)
                       & ~_TPFLAGS_BASETYPE)
        modified(cls)

    def make(cls):
        return alloc(cls, 0)

    return make, freeze


_hardening = _setup_hardening()
if _hardening is None:
    _alloc = object.__new__
    _freeze = None
else:
    _alloc, _freeze = _hardening
del _hardening


# ------------------------------------------------------------------ state

class _Hooks:
    repr = eq = hash = None
    operators = None


_hooks = _Hooks()
_configure_lock = _threading.Lock()


def _facade():
    return _sys.modules[__package__]


# ---------------------------------------------------------------- schemas

class _Schema:
    __slots__ = ("cls", "names", "plans", "defaults", "annotations",
                 "eligible", "checks", "getters", "setters",
                 "conf_get", "conf_set", "index", "positional")


_type_dict = type.__dict__["__dict__"].__get__
_TPFLAGS_TYPE_SUBCLASS = 1 << 31


def _is_type(obj):
    # Unlike isinstance(), never fooled by a __class__ proxy (GenericAlias).
    return bool(type(obj).__flags__ & _TPFLAGS_TYPE_SUBCLASS)


def _schema_of(cls):
    """The schema of record type cls (a type), or None."""
    schema = _type_dict(cls).get(_SCHEMA)
    if type(schema) is _Schema and schema.cls is cls:
        return schema
    return None


def _is_record_type(obj):
    return _is_type(obj) and _schema_of(obj) is not None


def _require_record_type(obj):
    schema = _schema_of(obj) if _is_type(obj) else None
    if schema is None:
        raise TypeError("expected a Grail record type")
    return schema


def _require_record(obj):
    return _require_record_type(type(obj))


def _values(schema, obj):
    return [get(obj) for get in schema.getters]


# ------------------------------------------------------------------ plans

_SCALARS = (type(None), bool, int, float, str, bytes)


def _plan_check(plan):
    """1 for a valid plan, 0 otherwise (None is not a plan here)."""
    if plan is None:
        return 0
    if _is_type(plan):
        if any(plan is t for t in _SCALARS):
            return 1
        schema = _schema_of(plan)
        return int(schema is not None and schema.eligible)
    if (type(plan) is not tuple or len(plan) != 2 or type(plan[0]) is not int
            or type(plan[1]) is not tuple):
        return 0
    tag, children = plan
    if tag < 1 or tag > 3:
        return 0
    if (tag == 1 and not children) or (tag == 3 and len(children) != 1):
        return 0
    for child in children:
        if _plan_check(child) != 1:
            return 0
    return 1


def _conforms(plan, value):
    if plan is None:
        return True
    if type(plan) is not tuple:
        if type(value) is not plan:
            return False
        schema = _schema_of(plan)
        return schema is None or schema.conf_get(value)
    tag, children = plan
    if tag == 1:
        for child in children:
            if _conforms(child, value):
                return True
        return False
    if type(value) is not tuple:
        return False
    if tag == 3:
        child = children[0]
        for item in value:
            if not _conforms(child, item):
                return False
        return True
    if len(value) != len(children):
        return False
    for child, item in zip(children, value):
        if not _conforms(child, item):
            return False
    return True


def _compile(plan):
    """A conformance check for plan: None (always), a type (exact), or a
    callable."""
    if plan is None:
        return None
    if type(plan) is not tuple and _schema_of(plan) is None:
        return plan
    return lambda value: _conforms(plan, value)


def _check_all(schema, values):
    for check, value in zip(schema.checks, values):
        if check is None:
            continue
        if type(check) is type:
            if type(value) is not check:
                return False
        elif not check(value):
            return False
    return True


def _build(schema, values, conforming):
    obj = _alloc(schema.cls)
    for setter, value in zip(schema.setters, values):
        setter(obj, value)
    schema.conf_set(obj, conforming)
    return obj


def _finish(schema, values):
    return _build(schema, values, _check_all(schema, values))


# --------------------------------------------------- equality and hashing

_pack_double = _struct.Struct("d").pack


def _record_substitutable(a, b):
    if a is b:
        return True
    if type(a) is not type(b):
        return False
    schema = _schema_of(type(a))
    both = schema.conf_get(a) and schema.conf_get(b)
    for plan, get in zip(schema.plans, schema.getters):
        if not _value_substitutable(plan if both else None, get(a), get(b)):
            return False
    return True


def _value_substitutable(plan, a, b):
    if a is b:
        return True
    if plan is None or type(a) is not type(b):
        return False
    if type(plan) is not tuple:
        if type(a) is not plan:
            return False
        if _schema_of(plan) is not None:
            return _record_substitutable(a, b)
        if type(a) is float:
            return _pack_double(a) == _pack_double(b)
        return bool(a == b)
    tag, children = plan
    if tag == 1:
        for child in children:
            if _conforms(child, a):
                return _value_substitutable(child, a, b)
        return False
    if type(a) is not tuple or len(a) != len(b):
        return False
    for i, (x, y) in enumerate(zip(a, b)):
        if not _value_substitutable(children[0 if tag == 3 else i], x, y):
            return False
    return True


def substitutable(a, b, /):
    _require_record(a)
    _require_record(b)
    return _record_substitutable(a, b)


def _default_eq(a, b, /):
    schema = _require_record(a)
    if type(a) is not type(b):
        return NotImplemented
    both = schema.conf_get(a) and schema.conf_get(b)
    for plan, get in zip(schema.plans, schema.getters):
        x, y = get(a), get(b)
        if not (_value_substitutable(plan if both else None, x, y) or x == y):
            return False
    return True


def _hash_proxy(plan, value):
    # An object whose hash is the native lane hash of value under plan:
    # NaNs hash by representation (like Py_HashBuffer), and a conforming
    # tuple combines its lanes exactly like tuple_hash.
    if plan is None:
        return value
    if type(value) is float and value != value:
        return _pack_double(value)
    if type(plan) is tuple:
        tag, children = plan
        if tag == 1:
            for child in children:
                if _conforms(child, value):
                    return _hash_proxy(child, value)
            return value
        return tuple(_hash_proxy(children[0 if tag == 3 else i], item)
                     for i, item in enumerate(value))
    return value


def _default_hash(obj, /):
    schema = _require_record(obj)
    lanes = []
    for plan, get in zip(schema.plans, schema.getters):
        value = get(obj)
        lanes.append(_hash_proxy(plan, value) if _conforms(plan, value) else value)
    return hash(tuple(lanes))


class _ReprGuard(_threading.local):
    def __init__(self):
        self.active = set()


_repr_guard = _ReprGuard()


def _default_repr(obj, /):
    schema = _schema_of(type(obj))
    if schema is None:
        return _builtins.repr(obj)
    active = _repr_guard.active
    key = id(obj)
    if key in active:
        return "..."
    active.add(key)
    try:
        parts = ", ".join(f"{name}={value!r}"
                          for name, value in zip(schema.names, _values(schema, obj)))
    finally:
        active.discard(key)
    return f"{type(obj).__name__}({parts})"


# ------------------------------------------------------- record behaviour

def _record_new(cls, *args, **kwargs):
    schema = _require_record_type(cls)
    n = len(schema.names)
    if not kwargs and len(args) == n:
        return _finish(schema, args)
    if len(args) > n:
        raise TypeError(f"{cls.__name__} expects at most {n} positional arguments")
    values = list(args) + [_MISSING] * (n - len(args))
    for key, value in kwargs.items():
        i = schema.index.get(key)
        if i is None:
            raise TypeError(f"unexpected record field {key!r}")
        if values[i] is not _MISSING:
            raise TypeError(f"multiple values for field {key!r}")
        values[i] = value
    for i, value in enumerate(values):
        if value is _MISSING:
            kind, default = schema.defaults[i]
            if kind == 0:
                raise TypeError(f"missing required record field {schema.names[i]!r}")
            values[i] = default() if kind == 2 else default
    return _finish(schema, values)


_MISSING = object()


def _record_setattr(self, name, value):
    raise AttributeError("record fields are immutable")


def _record_delattr(self, name):
    raise AttributeError("record fields are immutable")


def _record_repr(self):
    return _hooks.repr(self)


def _record_eq(self, other):
    return _hooks.eq(self, other)


def _record_ne(self, other):
    result = _hooks.eq(self, other)
    if result is NotImplemented:
        return result
    return not result


def _record_hash(self):
    return _hooks.hash(self)


def _make_binary(index, symbol):
    def forward(self, other):
        func = _hooks.operators[index]._state[1].get((id(type(self)), id(type(other))))
        if func is None:
            return NotImplemented
        return func(self, other)

    def reflected(self, other):
        # A record left operand already tried the pair in its own method.
        if _schema_of(type(other)) is not None:
            return NotImplemented
        func = _hooks.operators[index]._state[1].get((id(type(other)), id(type(self))))
        if func is None:
            return NotImplemented
        return func(other, self)
    return forward, reflected


def _make_unary(index, symbol):
    def unary(self):
        func = _hooks.operators[index]._state[1].get(id(type(self)))
        if func is None:
            # Native falls back to the operator itself and recurses.
            raise TypeError(f"bad operand type for unary {symbol}: "
                            f"'{type(self).__name__}'")
        return func(self)
    return unary


def _record_copy(self):
    return self


def _record_deepcopy(self, memo):
    if not isinstance(memo, dict):
        raise TypeError("deepcopy memo must be a dict")
    schema = _schema_of(type(self))
    if schema.eligible and schema.conf_get(self):
        return self
    return _facade()._deepcopy_record(self, memo)


def _record_reduce(self):
    return type(self), tuple(_values(_schema_of(type(self)), self))


def _record_reduce_ex(self, protocol):
    return type(self), tuple(_values(_schema_of(type(self)), self))


def _record_replace(self, *args, **changes):
    if args:
        raise TypeError("__replace__ accepts only keyword fields")
    schema = _schema_of(type(self))
    values = _values(schema, self)
    for key, value in changes.items():
        i = schema.index.get(key)
        if i is None:
            raise TypeError(f"unexpected record field {key!r}")
        values[i] = value
    return _finish(schema, values)


def _init_subclass(cls, **kwargs):
    raise TypeError("records cannot be subclassed")


_add, _radd = _make_binary(0, "+")
_sub, _rsub = _make_binary(1, "-")
_mul, _rmul = _make_binary(2, "*")
_div, _rdiv = _make_binary(3, "/")

_METHODS = {
    "__new__": _record_new,
    "__setattr__": _record_setattr,
    "__delattr__": _record_delattr,
    "__repr__": _record_repr,
    "__eq__": _record_eq,
    "__ne__": _record_ne,
    "__hash__": _record_hash,
    "__add__": _add, "__radd__": _radd,
    "__sub__": _sub, "__rsub__": _rsub,
    "__mul__": _mul, "__rmul__": _rmul,
    "__truediv__": _div, "__rtruediv__": _rdiv,
    "__neg__": _make_unary(4, "-"),
    "__abs__": _make_unary(5, "abs()"),
    "__copy__": _record_copy,
    "__deepcopy__": _record_deepcopy,
    "__reduce__": _record_reduce,
    "__reduce_ex__": _record_reduce_ex,
    "__replace__": _record_replace,
}


def _make_record(source, names, plans, defaults, annotations, /):
    if type(source) is not type:
        raise TypeError("records require the standard type metaclass")
    parts = (names, plans, defaults, annotations)
    if any(type(p) is not tuple or len(p) != len(names) for p in parts):
        raise TypeError("record schema requires equal-sized tuples")
    eligible = True
    for i, name in enumerate(names):
        if type(name) is not str or not name.isidentifier():
            raise TypeError("record fields must be identifiers")
        if name.startswith("__"):
            raise TypeError("dunder record fields are reserved")
        if name in names[:i]:
            raise TypeError("duplicate record field")
        plan = plans[i]
        if plan is not None and not _plan_check(plan):
            raise TypeError("invalid native value plan")
        eligible = eligible and plan is not None
        entry = defaults[i]
        if (type(entry) is not tuple or len(entry) != 2
                or type(entry[0]) is not int):
            raise TypeError("invalid record default")
        if entry[0] not in (0, 1, 2) or (entry[0] == 2 and not callable(entry[1])):
            raise TypeError("invalid record default kind/factory")
    if _hooks.operators is None:
        raise RuntimeError("import grail before creating records")
    name = source.__name__
    modname = source.__module__
    if not isinstance(name, str) or not isinstance(modname, str):
        raise TypeError("record name and module must be strings")
    names = tuple(_sys.intern(n) for n in names)
    namespace = dict(_METHODS)
    namespace.update({
        "__slots__": names + (_CONFORMING,),
        "__module__": modname,
        "__qualname__": source.__qualname__,
        "__doc__": source.__doc__,
        "__match_args__": names,
    })
    if _freeze is None:
        namespace["__init_subclass__"] = classmethod(_init_subclass)
    cls = type(name, (object,), namespace)
    members = [cls.__dict__[n] for n in names]
    conforming = cls.__dict__[_CONFORMING]
    for n in names:
        delattr(cls, n)
    delattr(cls, _CONFORMING)
    delattr(cls, "__slots__")
    for n, member in zip(names, members):
        field = property(member.__get__)
        try:
            field.__name__ = n  # 3.13+: names the field in error messages
        except AttributeError:
            pass
        setattr(cls, n, field)
    schema = _Schema()
    schema.cls = cls
    schema.names = names
    schema.plans = plans
    schema.defaults = defaults
    schema.annotations = annotations
    schema.eligible = eligible
    schema.checks = tuple(_compile(p) for p in plans)
    schema.getters = tuple(m.__get__ for m in members)
    schema.setters = tuple(m.__set__ for m in members)
    schema.conf_get = conforming.__get__
    schema.conf_set = conforming.__set__
    schema.index = {n: i for i, n in enumerate(names)}
    setattr(cls, _SCHEMA, schema)
    if _freeze is not None:
        _freeze(cls)
    return cls


def _record_info(record_type, /):
    s = _require_record_type(record_type)
    return (s.names, s.plans, s.defaults, s.annotations), s.eligible


def _record_values(obj, /):
    schema = _require_record(obj)
    return dict(zip(schema.names, _values(schema, obj)))


def _is_conforming(obj, /):
    schema = _require_record(obj)
    return bool(schema.eligible and schema.conf_get(obj))


def _deepcopy_is_self(obj, /):
    schema = _schema_of(type(obj))
    return bool(schema is not None and schema.eligible and schema.conf_get(obj))


# ---------------------------------------------------------------- kernels

_KERNEL_OPS = (_operator.add, _operator.sub, _operator.mul, _operator.truediv)


class _Kernel:
    """Element-wise arithmetic for records whose fields are annotated float.

    kind 0: (R, R); kind 1: (R, scalar); kind 2: (scalar, R)."""
    __slots__ = ("_type", "_op", "_kind")

    def __call__(self, *args, **kwargs):
        if kwargs or len(args) != 2:
            raise TypeError("element-wise kernel takes exactly two positional arguments")
        a, b = args
        cls, op, kind = self._type, self._op, self._kind
        rec, other = (b, a) if kind == 2 else (a, b)
        if (type(rec) is not cls or
                (type(other) is not cls if kind == 0 else
                 type(other) is not float and type(other) is not int)):
            raise TypeError("element-wise kernel does not support "
                            f"'{type(a).__name__}' and '{type(b).__name__}'")
        schema = _schema_of(cls)
        x = _values(schema, rec)
        func = _KERNEL_OPS[op]
        if kind == 0:
            values = [func(p, q) for p, q in zip(x, _values(schema, other))]
        else:
            if (type(other) is int and schema.conf_get(rec)
                    and not (op == 3 and other == 0)):
                other = float(other)  # the native fast path converts first
            if kind == 1:
                values = [func(p, other) for p in x]
            else:
                values = [func(other, p) for p in x]
        return _finish(schema, values)

    def __repr__(self):
        name = self._type.__name__
        lhs = "scalar" if self._kind == 2 else name
        rhs = "scalar" if self._kind == 1 else name
        return f"<grail element-wise kernel {lhs} {'+-*/'[self._op]} {rhs}>"


def _kernel(record_type, op, kind, /):
    schema = _require_record_type(record_type)
    if (op not in (0, 1, 2, 3) or kind not in (0, 1, 2)
            or (kind == 0) != (op in (0, 1)) or (kind == 2 and op != 2)):
        raise ValueError("unsupported element-wise operation")
    if any(plan is not float for plan in schema.plans):
        raise TypeError("element-wise arithmetic needs every field of "
                        f"{record_type.__name__} to be annotated float")
    kernel = object.__new__(_Kernel)
    kernel._type, kernel._op, kernel._kind = record_type, op, kind
    return kernel


def _is_kernel(obj, /):
    return type(obj) is _Kernel


# --------------------------------------------------------------- generics

def _is_static(cls):
    return not (cls.__flags__ & _TPFLAGS_HEAPTYPE)


def _type_name(cls):
    module = getattr(cls, "__module__", "builtins")
    return cls.__name__ if module == "builtins" else f"{module}.{cls.__qualname__}"


class Generic:
    """Dispatch on the exact type of the first argument (or of both
    arguments for a pair), then through the resolver, then the fallback."""
    __slots__ = ("_fallback", "_first_name", "_arity", "_resolver",
                 "_central", "_lock", "_state")

    def __new__(cls, fallback, first_name, pair=False, resolver=None):
        if not isinstance(first_name, str):
            raise TypeError("Generic() argument 'first_name' must be str")
        if not callable(fallback):
            raise TypeError("fallback must be callable")
        pair = bool(pair)
        if resolver is not None and (not callable(resolver) or pair):
            raise TypeError("resolver requires single dispatch and a callable")
        self = object.__new__(cls)
        setattr_ = object.__setattr__
        setattr_(self, "_fallback", fallback)
        setattr_(self, "_first_name", first_name)
        setattr_(self, "_arity", 2 if pair else 1)
        setattr_(self, "_resolver", resolver)
        setattr_(self, "_central", False)
        setattr_(self, "_lock", _threading.RLock())
        # (registry snapshot, exact table, version); never mutated once
        # published, so readers need no lock.
        setattr_(self, "_state", ({}, {}, 0))
        return self

    def __setattr__(self, name, value):
        raise AttributeError(f"'{type(self).__name__}' object attribute "
                             f"'{name}' is read-only")

    __delattr__ = __setattr__

    def __call__(self, *args, **kwargs):
        if self._central:
            if len(args) == 2 and not kwargs:
                a, b = args
                func = self._state[1].get((id(type(a)), id(type(b))))
                if func is not None:
                    return func(a, b)
            return self._fallback(*args, **kwargs)
        if self._arity == 1:
            if args:
                first = args[0]
            elif self._first_name in kwargs:
                first = kwargs[self._first_name]
            else:
                return self._fallback(*args, **kwargs)
            state = self._state
            cls = type(first)
            func = state[1].get(id(cls))
            if func is None:
                resolver = self._resolver
                func = (self._fallback if resolver is None else
                        resolver(cls, state[0], self._fallback))
        elif len(args) >= 2:
            func = self._state[1].get((id(type(args[0])), id(type(args[1]))))
            if func is None:
                func = self._fallback
        else:
            func = self._fallback
        return func(*args, **kwargs)

    def _types(self, dispatch, message):
        if self._arity == 2:
            if type(dispatch) is not tuple or len(dispatch) != 2:
                raise TypeError(message)
            return dispatch
        return (dispatch,)

    def register(self, dispatch, func, override=False):
        override = bool(override)
        if not callable(func):
            raise TypeError("implementation must be callable")
        types = self._types(dispatch, "operator dispatch requires two types")
        if (self._central and all(_is_type(t) for t in types)
                and all(_is_static(t) for t in types)):
            raise TypeError("cannot register an operator for two built-in types "
                            f"({_type_name(types[0])}, {_type_name(types[1])})")
        _facade()._validate_registration(self, dispatch, func)
        if not all(_is_type(t) for t in types):
            raise TypeError("dispatch keys must be types")
        key = tuple(id(t) for t in types)
        entry = (tuple(types), func)
        table_key = key if self._arity == 2 else key[0]
        while True:
            # Reentrant: a finalizer run during the copy may register too.
            # Publish only over the state this update was built from.
            with self._lock:
                state = self._state
                registry = state[0]
                if key in registry and not override:
                    raise TypeError("duplicate implementation; use override")
                replacement = dict(registry)
                replacement[key] = entry
                table = dict(state[1])
                table[table_key] = func
                if self._state is state:
                    object.__setattr__(self, "_state",
                                       (replacement, table, state[2] + 1))
                    break
        # Retired states (and callables) are released outside the lock.
        del state, registry
        return func

    def dispatch(self, dispatch, /):
        types = self._types(dispatch, "expected a pair of types")
        if not all(_is_type(t) for t in types):
            raise TypeError("dispatch keys must be types")
        state = self._state
        if self._arity == 2:
            func = state[1].get((id(types[0]), id(types[1])))
        else:
            func = state[1].get(id(types[0]))
            if func is None and self._resolver is not None:
                return self._resolver(types[0], state[0], self._fallback)
        return self._fallback if func is None else func

    def impl(self, func, /):
        return _facade()._register_decorator(self, (func,), False)

    def override(self, *args):
        return _facade()._register_decorator(self, args, True)

    @property
    def fallback(self):
        return self._fallback

    @property
    def version(self):
        return self._state[2]

    @property
    def pair(self):
        return self._arity == 2

    @property
    def __name__(self):
        return self._fallback.__name__


def _configure(repr, eq, hash, operators, /):
    if (type(repr) is not Generic or type(eq) is not Generic
            or type(hash) is not Generic or type(operators) is not tuple
            or len(operators) != 6):
        raise TypeError("invalid Grail hooks")
    if any(type(op) is not Generic for op in operators):
        raise TypeError("operators must be native generics")
    with _configure_lock:
        if _hooks.operators is not None:
            raise RuntimeError("Grail hooks are already configured")
        for op in operators[:4]:
            object.__setattr__(op, "_central", True)
        _hooks.repr, _hooks.eq, _hooks.hash = repr, eq, hash
        _hooks.operators = operators
