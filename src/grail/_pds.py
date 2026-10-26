"""PMap and PSet: pure-Python stand-ins for the native _grail_pds module.

Storage is a private dict (PMap) or frozenset (PSet) that is never mutated
after the collection is published, so readers need no synchronization on
free-threaded builds.  Updates copy: O(n) rather than the native HAMT's
O(log n).  Iteration is unordered (here: the storage's order).
"""

import builtins
import reprlib
import sys
import threading
import types
from collections.abc import ItemsView, KeysView, Mapping, Set, ValuesView

__all__ = ["PMap", "PSet"]

_gil_enabled = getattr(sys, "_is_gil_enabled", None)
_frozendict = getattr(builtins, "frozendict", None)
_MISSING = object()
_DICT_TYPES = (dict,) if _frozendict is None else (dict, _frozendict)
_SET_TYPES = (set, frozenset)


# ---------------------------------------------------------------- helpers

class _ClosedType(type):
    """Metaclass making the classes immutable and final, as native types are."""

    def __new__(mcls, name, bases, ns):
        for base in bases:
            if isinstance(base, _ClosedType):
                raise TypeError(f"type 'grail.{base.__name__}' is not an "
                                "acceptable base type")
        return super().__new__(mcls, name, bases, ns)

    def __setattr__(cls, name, value):
        raise TypeError(f"cannot set {name!r} attribute of immutable type "
                        f"'{cls.__module__}.{cls.__name__}'")

    def __delattr__(cls, name):
        raise TypeError(f"cannot delete {name!r} attribute of immutable type "
                        f"'{cls.__module__}.{cls.__name__}'")


def _no_setattr(self, name, value):
    tname = f"{type(self).__module__}.{type(self).__name__}"
    raise AttributeError(f"'{tname}' object has no attribute {name!r} and no "
                         "__dict__ for setting new attributes")


def _no_delattr(self, name):
    tname = f"{type(self).__module__}.{type(self).__name__}"
    raise AttributeError(f"'{tname}' object attribute {name!r} is read-only")


class _Iterator:
    """Shared-safe iterator: advancement is serialized by a lock."""
    __slots__ = ("_it", "_lock")
    __module__ = "_grail_pds"

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '_grail_pds._Iterator' instances")

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            return next(self._it)

    def __reduce_ex__(self, protocol):
        raise TypeError("cannot pickle '_grail_pds._Iterator' object")


_new_iterator_obj = object.__new__
_set_it = _Iterator._it.__set__
_set_lock = _Iterator._lock.__set__


def _iterate(iterable):
    # With the GIL, builtin iterators over storage that never changes are
    # already safe to share.  The GIL is never disabled again once enabled.
    if _gil_enabled is None or _gil_enabled():
        return iter(iterable)
    it = _new_iterator_obj(_Iterator)
    _set_it(it, iter(iterable))
    _set_lock(it, threading.Lock())
    return it


def _map_entries(source):
    """A fresh dict of source's entries (mapping or iterable of pairs)."""
    if type(source) is PMap:
        return dict(source._d)
    if type(source) in _DICT_TYPES:
        try:
            return dict(source)
        except RuntimeError:
            return dict(list(source.items()))
    if hasattr(source, "keys"):
        return dict(list(source.items()))
    d = {}
    for item in source:
        pair = tuple(item)
        if len(pair) != 2:
            raise ValueError("map entries must have length two")
        d[pair[0]] = pair[1]
    return d


def _set_entries(source):
    if type(source) is PSet:
        return source._s
    if type(source) is PMap:
        return frozenset(source._d)
    return frozenset(source)


def _is_atomic(value, depth=16):
    """True if copy.deepcopy(value) is value itself."""
    t = type(value)
    if (value is None or value is Ellipsis or value is NotImplemented
            or t in (bool, int, float, complex, str, bytes)):
        return True
    if t is not tuple or depth <= 0:
        return False
    return all(_is_atomic(x, depth - 1) for x in value)


def _deepcopy_item(value, memo, state):
    if _is_atomic(value):
        return value
    import copy
    result = copy.deepcopy(value, memo)
    if result is not value:
        state[0] = True
    return result


# ------------------------------------------------------------------- PMap

class PMap(metaclass=_ClosedType):
    """Persistent (immutable) hash map."""
    __slots__ = ("_d", "_h")
    __module__ = "grail"

    def __new__(cls, source=_MISSING):
        if source is _MISSING:
            return _wrap_map({})
        if type(source) is cls:
            return source
        return _wrap_map(_map_entries(source))

    __setattr__ = _no_setattr
    __delattr__ = _no_delattr
    __class_getitem__ = classmethod(types.GenericAlias)

    # -- reading
    def __len__(self):
        return len(self._d)

    def __contains__(self, key):
        return key in self._d

    def __getitem__(self, key):
        return self._d[key]

    def get(self, key, default=None, /):
        return self._d.get(key, default)

    def __iter__(self):
        return _iterate(self._d)

    def keys(self):
        return PMapKeysView(self)

    def values(self):
        return PMapValuesView(self)

    def items(self):
        return PMapItemsView(self)

    # -- updating
    def set(self, key, value, /):
        d = dict(self._d)
        d[key] = value
        return _wrap_map(d)

    def delete(self, key, /):
        if key not in self._d:
            raise KeyError(key)
        d = dict(self._d)
        del d[key]
        return _wrap_map(d)

    def update_with(self, other, fn, /):
        if not callable(fn):
            raise TypeError("update_with requires a callable")
        if type(other) is PMap:
            if not self._d:
                return other
            entries = other._d
        else:
            entries = _map_entries(other)
        d = dict(self._d)
        for key, value in entries.items():
            if key in d:
                d[key] = fn(d[key], value)
            else:
                d[key] = value
        return _wrap_map(d)

    def __or__(self, other):
        if type(other) is PMap:
            if not other._d:
                return self
            if not self._d:
                return other
        elif type(other) not in _DICT_TYPES and not isinstance(other, Mapping):
            return NotImplemented
        d = dict(self._d)
        d.update(_map_entries(other))
        return _wrap_map(d)

    def __ror__(self, other):
        if type(other) not in _DICT_TYPES and not isinstance(other, Mapping):
            return NotImplemented
        d = _map_entries(other)
        d.update(self._d)
        return _wrap_map(d)

    def mutate(self):
        return _new_transient(PMap, self._d)

    # -- comparison and hashing
    def __eq__(self, other):
        if type(other) is PMap:
            if self is other or self._d is other._d:
                return True
            h1, h2 = self._h, other._h
            if h1 is not None and h2 is not None and h1 != h2:
                return False
            return self._d == other._d
        if type(other) in _DICT_TYPES or isinstance(other, Mapping):
            return self._d == dict(other)
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        h = self._h
        if h is None:
            # Equal to hash(frozendict(m)): frozenset of (key, value) tuples.
            h = hash(frozenset(self._d.items()))
            _set_h(self, h)
        return h

    @reprlib.recursive_repr("...")
    def __repr__(self):
        return f"PMap({self._d!r})"

    # -- copy and pickle
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        if not isinstance(memo, dict):
            raise TypeError("deepcopy memo must be a dict")
        state = [False]
        copied = {}
        for key, value in list(self._d.items()):
            k = _deepcopy_item(key, memo, state)
            copied[k] = _deepcopy_item(value, memo, state)
        done = memo.get(id(self))
        if done is not None:
            return done
        return PMap(copied) if state[0] else self

    def __reduce__(self):
        return (PMap, (dict(self._d),))

    def __reduce_ex__(self, protocol):
        return self.__reduce__()


_new_obj = object.__new__
_set_d = PMap._d.__set__
_set_h = PMap._h.__set__


def _wrap_map(d):
    m = _new_obj(PMap)
    _set_d(m, d)
    _set_h(m, None)
    return m


def _make_view(name, base, iterate):
    def __iter__(self):
        mapping = self._mapping
        if type(mapping) is PMap:
            return _iterate(iterate(mapping._d))
        return base.__iter__(self)
    return type(base)(name, (base,), {"__slots__": (), "__module__": "grail",
                                      "__iter__": __iter__})


PMapKeysView = _make_view("PMapKeysView", KeysView, lambda d: d)
PMapValuesView = _make_view("PMapValuesView", ValuesView, dict.values)
PMapItemsView = _make_view("PMapItemsView", ItemsView, dict.items)


# ------------------------------------------------------------------- PSet

class PSet(metaclass=_ClosedType):
    """Persistent (immutable) hash set."""
    __slots__ = ("_s",)
    __module__ = "grail"

    def __new__(cls, source=_MISSING):
        if source is _MISSING:
            return _wrap_set(frozenset())
        if type(source) is cls:
            return source
        return _wrap_set(_set_entries(source))

    __setattr__ = _no_setattr
    __delattr__ = _no_delattr
    __class_getitem__ = classmethod(types.GenericAlias)

    def __len__(self):
        return len(self._s)

    def __contains__(self, x):
        return x in self._s

    def __iter__(self):
        return _iterate(self._s)

    def add(self, x, /):
        if x in self._s:
            return self
        return _wrap_set(self._s | {x})

    def discard(self, x, /):
        if x not in self._s:
            return self
        return _wrap_set(self._s - {x})

    def mutate(self):
        return _new_transient(PSet, self._s)

    # -- algebra
    def __or__(self, other):
        if type(other) is PSet:
            if not other._s:
                return self
            if not self._s:
                return other
        return _wrap_set(self._s | _set_entries(other))

    def __ror__(self, other):
        if type(other) is PMap:
            return NotImplemented
        return _wrap_set(_set_entries(other) | self._s)

    def __and__(self, other):
        if type(other) is PSet:
            a, b = self._s, other._s
            if not a or (b and a is b):
                return self
            return _wrap_set(a & b)
        return _wrap_set(self._s & frozenset(other))

    def __rand__(self, other):
        if type(other) is PMap:
            return NotImplemented
        return _wrap_set(frozenset(other) & self._s)

    def __sub__(self, other):
        if type(other) is PSet:
            if not other._s:
                return self
            return _wrap_set(self._s - other._s)
        return _wrap_set(self._s - frozenset(other))

    def __rsub__(self, other):
        if type(other) is PMap:
            return NotImplemented
        return _wrap_set(frozenset(other) - self._s)

    def __xor__(self, other):
        if type(other) is PSet:
            if not other._s:
                return self
            return _wrap_set(self._s ^ other._s)
        return _wrap_set(self._s ^ frozenset(other))

    def __rxor__(self, other):
        if type(other) is PMap:
            return NotImplemented
        return _wrap_set(frozenset(other) ^ self._s)

    # -- comparison and hashing
    def _other(self, other):
        if type(other) is PSet:
            return other._s
        if type(other) in _SET_TYPES:
            return other
        if isinstance(other, Set):
            return frozenset(other)
        return None

    def __eq__(self, other):
        o = self._other(other)
        return NotImplemented if o is None else self._s == o

    def __ne__(self, other):
        o = self._other(other)
        return NotImplemented if o is None else self._s != o

    def __lt__(self, other):
        o = self._other(other)
        return NotImplemented if o is None else self._s < o

    def __le__(self, other):
        o = self._other(other)
        return NotImplemented if o is None else self._s <= o

    def __gt__(self, other):
        o = self._other(other)
        return NotImplemented if o is None else self._s > o

    def __ge__(self, other):
        o = self._other(other)
        return NotImplemented if o is None else self._s >= o

    def __hash__(self):
        return hash(self._s)

    @reprlib.recursive_repr("...")
    def __repr__(self):
        return f"PSet({list(self._s)!r})"

    # -- copy and pickle
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        if not isinstance(memo, dict):
            raise TypeError("deepcopy memo must be a dict")
        state = [False]
        copied = [_deepcopy_item(x, memo, state) for x in list(self._s)]
        done = memo.get(id(self))
        if done is not None:
            return done
        return PSet(copied) if state[0] else self

    def __reduce__(self):
        return (PSet, (list(self._s),))

    def __reduce_ex__(self, protocol):
        return self.__reduce__()


_set_s = PSet._s.__set__


def _wrap_set(s):
    p = _new_obj(PSet)
    _set_s(p, s)
    return p


Mapping.register(PMap)
Set.register(PSet)


# -------------------------------------------------------------- transient

_OPEN, _SEALED, _FINISHED, _ABORTED = range(4)


class _Builder:
    """Thread-confined mutable builder returned by PMap/PSet.mutate()."""
    __slots__ = ("_kind", "_base", "_data", "_owner", "_status", "_busy",
                 "_entered")
    __module__ = "_grail_pds"
    __qualname__ = "_Transient"
    __hash__ = None
    __iter__ = None

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '_grail_pds._Transient' instances")

    def __reduce_ex__(self, protocol):
        raise TypeError("cannot pickle '_grail_pds._Transient' object")

    def _check(self, allow_sealed=False):
        if self._owner != threading.get_ident():
            raise RuntimeError("transient used from another thread")
        if self._busy:
            raise RuntimeError("reentrant transient operation")
        status = self._status
        if status != _OPEN and not (allow_sealed and status == _SEALED):
            raise RuntimeError("transient is no longer open")

    def _writable(self):
        # Copy the shared base storage on the first edit.
        data = self._data
        if data is None:
            data = self._data = (dict(self._base) if self._kind is PMap
                                 else set(self._base))
            self._base = None
        return data

    def _current(self):
        data = self._data
        return self._base if data is None else data

    def __getitem__(self, key):
        self._check()
        if self._kind is PSet:
            raise TypeError("set transients are not subscriptable")
        self._busy = True
        try:
            return self._current()[key]
        finally:
            self._busy = False

    def __setitem__(self, key, value):
        self._check()
        if self._kind is PSet:
            raise TypeError("use add/discard on a set transient")
        self._busy = True
        try:
            self._writable()[key] = value
        finally:
            self._busy = False

    def __delitem__(self, key):
        self._check()
        if self._kind is PSet:
            raise TypeError("use add/discard on a set transient")
        self._busy = True
        try:
            if key not in self._current():
                raise KeyError(key)
            del self._writable()[key]
        finally:
            self._busy = False

    def __len__(self):
        self._check()
        return len(self._current())

    def __contains__(self, key):
        self._check()
        self._busy = True
        try:
            return key in self._current()
        finally:
            self._busy = False

    def _set_change(self, key, remove):
        self._check()
        if self._kind is not PSet:
            raise TypeError("add/discard require a set transient")
        self._busy = True
        try:
            if remove:
                if key in self._current():
                    self._writable().discard(key)
            else:
                if key not in self._current():
                    self._writable().add(key)
        finally:
            self._busy = False

    def add(self, key, /):
        self._set_change(key, False)

    def discard(self, key, /):
        self._set_change(key, True)

    def finish(self):
        self._check(allow_sealed=True)
        data = self._data
        if self._kind is PMap:
            result = _wrap_map(self._base if data is None else data)
        else:
            result = _wrap_set(self._base if data is None else frozenset(data))
        self._status = _FINISHED
        self._base = self._data = None
        return result

    def __enter__(self):
        self._check()
        if self._entered:
            raise RuntimeError("transient context already entered")
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb, /):
        if (self._owner != threading.get_ident() or self._busy
                or not self._entered):
            raise RuntimeError("invalid transient context exit")
        self._entered = False
        if self._status == _OPEN:
            if exc_type is None:
                self._status = _SEALED
            else:
                self._status = _ABORTED
                self._base = self._data = None
        return False


_Builder.__name__ = "_Transient"


def _new_transient(kind, base):
    t = _new_obj(_Builder)
    t._kind = kind
    t._base = base
    t._data = None
    t._owner = threading.get_ident()
    t._status = _OPEN
    t._busy = False
    t._entered = False
    return t
