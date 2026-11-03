"""Generic functions, shared by both record cores (grail._core and the C
grail._core_native).

Generic functions publish an immutable (registry, table, version) state
under a lock; calls read the published state without locking, so they are
safe on free-threaded builds.  Exact tables are keyed by type identity.
There is no inherited-dispatch cache: every inherited call runs the resolver.

A core's _configure may set _watch on the protocol and operator generics: it
is called with (generic, dispatch types) after each registration publishes.
"""

import sys as _sys
import threading as _threading
import types as _types
import typing as _typing

_TPFLAGS_HEAPTYPE = 1 << 9
_TPFLAGS_TYPE_SUBCLASS = 1 << 31


def _facade():
    return _sys.modules[__package__]


def _is_type(obj):
    # Unlike isinstance(), never fooled by a __class__ proxy (GenericAlias).
    return bool(type(obj).__flags__ & _TPFLAGS_TYPE_SUBCLASS)


def _is_static(cls):
    return not (cls.__flags__ & _TPFLAGS_HEAPTYPE)


def _type_name(cls):
    module = getattr(cls, "__module__", "builtins")
    return cls.__name__ if module == "builtins" else f"{module}.{cls.__qualname__}"


class Generic:
    """Dispatch on the exact type of the first argument (or of both
    arguments for a pair), then through the resolver, then the fallback."""
    __slots__ = ("_fallback", "_first_name", "_arity", "_resolver",
                 "_central", "_watch", "_lock", "_state")

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
        setattr_(self, "_watch", None)
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
                raise TypeError(f"dispatch argument {self._first_name!r} "
                                "must be positional")
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
        if (self._arity == 1 and
                _typing.get_origin(dispatch) in (_typing.Union, _types.UnionType)):
            # One registration per member, as functools.singledispatch does.
            for member in _typing.get_args(dispatch):
                self.register(member, func, override=override)
            return func
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
        watch = self._watch
        if watch is not None:
            watch(self, tuple(types))
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
