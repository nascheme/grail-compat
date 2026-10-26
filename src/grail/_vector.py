"""PVector: pure-Python stand-in for the native _grail_vector module.

The native vector is a 32-way trie; this one holds a plain tuple, so set,
append and pop copy (O(n)).  The observable API follows the native type:
exact-type equality and concatenation, tuple-compatible hashing, a
mutate()/finish() builder owned by one thread, and closed storage.

Thread safety: a published PVector never changes, so concurrent reads need
no lock.  The hash cache is a benign race (every thread computes the same
value).  On free-threaded builds iterators take a lock so a shared
iterator yields each item exactly once, as the native one does.
"""

import operator
import reprlib
import sys
import threading
from collections.abc import Sequence
from types import GenericAlias

__all__ = ["PVector"]

_MISSING = object()
_SSIZE_MAX = sys.maxsize
_SSIZE_MIN = -sys.maxsize - 1
_FREE_THREADED = hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled()


class _ClosedType(type):
    """Metaclass emulating an immutable, final native type."""

    def __new__(mcls, name, bases, ns, **kw):
        for base in bases:
            if isinstance(base, _ClosedType):
                raise TypeError(f"type '{base._native_name}' is not an "
                                "acceptable base type")
        return super().__new__(mcls, name, bases, ns, **kw)

    def __setattr__(cls, name, value):
        raise TypeError(f"cannot set {name!r} attribute of immutable type "
                        f"{cls._native_name!r}")

    def __delattr__(cls, name):
        raise TypeError(f"cannot set {name!r} attribute of immutable type "
                        f"{cls._native_name!r}")


def _no_attr(obj, name):
    return AttributeError(f"{type(obj)._native_name!r} object has no attribute "
                          f"{name!r} and no __dict__ for setting new attributes")


def _check_index(key, count, message):
    """Normalize an integer index as the native normalize_index does.

    Returns None when the index is out of range."""
    if not hasattr(type(key), "__index__"):
        raise TypeError(message)
    i = operator.index(key)
    if not _SSIZE_MIN <= i <= _SSIZE_MAX:
        raise IndexError(f"cannot fit '{type(key).__name__}' into an index-sized integer")
    if i < 0:
        i += count
    if i < 0 or i >= count:
        return None
    return i

# --------------------------------------------------------------- builders

_OPEN, _SEALED, _FINISHED, _ABORTED = range(4)


class _BuilderBase:
    """Lifecycle shared by the PVector and numeric-vector transients."""

    __slots__ = ("_owner", "_busy", "_status", "_entered")
    __hash__ = None
    __iter__ = None

    def __new__(cls, *args, **kw):
        raise TypeError(f"cannot create {cls._native_name!r} instances")

    def _init_state(self):
        self._owner = threading.get_ident()
        self._busy = False
        self._status = _OPEN
        self._entered = False

    def _check(self, allow_sealed=False):
        if self._owner != threading.get_ident():
            raise RuntimeError("transient used from another thread")
        if self._busy:
            raise RuntimeError("reentrant transient operation")
        status = self._status
        if status != _OPEN and not (allow_sealed and status == _SEALED):
            raise RuntimeError("transient is no longer open")

    def __enter__(self):
        self._check()
        if self._entered:
            raise RuntimeError("transient context already entered")
        self._entered = True
        return self

    def __exit__(self, exc, value, tb):
        if self._owner != threading.get_ident() or self._busy or not self._entered:
            raise RuntimeError("invalid transient context exit")
        self._entered = False
        if self._status == _OPEN:
            if exc is None:
                self._status = _SEALED
            else:
                self._status = _ABORTED
                self._release()
        return False

    def finish(self):
        """Return the built vector; the transient is then unusable."""
        self._check(allow_sealed=True)
        result = self._publish()
        self._status = _FINISHED
        return result


class _Transient(_BuilderBase, metaclass=_ClosedType):
    """Transient PVector builder (native: _grail_vector._Transient)."""

    __slots__ = ("_items",)
    __module__ = "grail._vector"
    _native_name = "_grail_vector._Transient"

    @classmethod
    def _new(cls, items):
        self = object.__new__(cls)
        self._init_state()
        self._items = items
        return self

    def _release(self):
        self._items = []

    def _publish(self):
        items, self._items = self._items, []
        return _wrap(tuple(items))

    def _index(self, key):
        self._busy = True
        try:
            i = _check_index(key, len(self._items), "PVector indices must be integers")
        finally:
            self._busy = False
        if i is None:
            raise IndexError("PVector index out of range")
        return i

    def __len__(self):
        self._check()
        return len(self._items)

    def __getitem__(self, key):
        self._check()
        return self._items[self._index(key)]

    def __setitem__(self, key, value):
        self._check()
        self._items[self._index(key)] = value

    def __delitem__(self, key):
        self._check()
        raise TypeError("PVector transients only remove with pop()")

    def append(self, value):
        """Append an item."""
        self._check()
        self._items.append(value)

    def extend(self, iterable):
        """Append the items of an iterable."""
        self._check()
        items = self._items
        if type(iterable) is tuple or type(iterable) is list:
            items.extend(iterable)
            return
        # Iteration may run code that uses this builder; busy rejects it.
        # Items taken before an error stay, as natively.
        self._busy = True
        try:
            append = items.append
            for item in iterable:
                append(item)
        finally:
            self._busy = False

    def pop(self):
        """Remove and return the last item."""
        self._check()
        if not self._items:
            raise IndexError("pop from empty PVector")
        return self._items.pop()


# ---------------------------------------------------------------- iterator

class _Iterator:
    """Locked iterator for free-threaded builds: each item exactly once."""

    __slots__ = ("_items", "_index", "_step", "_lock")
    __module__ = "grail._vector"
    _native_name = "_grail_vector._Iterator"

    def __new__(cls, *args, **kw):
        raise TypeError(f"cannot create {cls._native_name!r} instances")

    @classmethod
    def _new(cls, items, reverse):
        self = object.__new__(cls)
        self._items = items
        self._index = len(items) - 1 if reverse else 0
        self._step = -1 if reverse else 1
        self._lock = threading.Lock()
        return self

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            i = self._index
            if i < 0 or i >= len(self._items):
                raise StopIteration
            self._index = i + self._step
        return self._items[i]


def _iterate(items, reverse=False):
    if _FREE_THREADED:
        return _Iterator._new(items, reverse)
    return reversed(items) if reverse else iter(items)


# ----------------------------------------------------------------- PVector

class PVector(metaclass=_ClosedType):
    """Persistent vector (pure-Python stand-in for grail.PVector)."""

    __slots__ = ("_t", "_hash")
    __module__ = "grail"
    _native_name = "grail.PVector"

    def __new__(cls, source=_MISSING):
        if source is _MISSING:
            return _EMPTY if _EMPTY is not None else _wrap(())
        if type(source) is PVector:
            return source
        return _wrap(tuple(source))

    def __setattr__(self, name, value):
        raise _no_attr(self, name)

    def __delattr__(self, name):
        raise _no_attr(self, name)

    __class_getitem__ = classmethod(GenericAlias)

    def __len__(self):
        return len(self._t)

    def __getitem__(self, key):
        t = self._t
        if type(key) is int:
            n = len(t)
            if -n <= key < n:
                return t[key]
            _check_index(key, n, "")
            raise IndexError("PVector index out of range")
        if isinstance(key, slice):
            r = t[key]
            if len(r) == len(t) and key.indices(len(t))[2] == 1:
                return self
            return _wrap(r)
        i = _check_index(key, len(t), "PVector indices must be integers")
        if i is None:
            raise IndexError("PVector index out of range")
        return t[i]

    def __iter__(self):
        return _iterate(self._t)

    def __reversed__(self):
        return _iterate(self._t, True)

    def __contains__(self, value):
        return value in self._t

    def count(self, value):
        return self._t.count(value)

    def index(self, value, start=0, stop=sys.maxsize, /):
        try:
            return self._t.index(value, start, stop)
        except ValueError as e:
            if type(e) is ValueError and e.args == ("tuple.index(x): x not in tuple",):
                raise ValueError("value is not in PVector") from None
            raise

    def set(self, index, value, /):
        """Return a vector with an existing index replaced."""
        t = self._t
        i = _check_index(index, len(t), "PVector indices must be integers")
        if i is None:
            raise IndexError("PVector index out of range")
        return _wrap(t[:i] + (value,) + t[i + 1:])

    def append(self, value, /):
        """Return a vector with one appended element."""
        return _wrap(self._t + (value,))

    def pop(self):
        """Return the vector without its last element."""
        t = self._t
        if not t:
            raise IndexError("pop from empty PVector")
        return _wrap(t[:-1])

    def mutate(self):
        """Return a transient builder starting from this vector."""
        return _Transient._new(list(self._t))

    def __add__(self, other):
        if type(other) is not PVector:
            radd = getattr(type(other), "__radd__", None)
            if radd is not None:
                result = radd(other, self)
                if result is not NotImplemented:
                    return result
            raise TypeError(f"cannot concatenate PVector and {type(other).__name__}")
        if not other._t:
            return self
        if not self._t:
            return other
        return _wrap(self._t + other._t)

    def __eq__(self, other):
        if type(other) is not PVector:
            return NotImplemented
        return self is other or self._t == other._t

    def __ne__(self, other):
        if type(other) is not PVector:
            return NotImplemented
        return self is not other and self._t != other._t

    def __lt__(self, other):
        if type(other) is not PVector:
            return NotImplemented
        return self._t < other._t

    def __le__(self, other):
        if type(other) is not PVector:
            return NotImplemented
        return self._t <= other._t

    def __gt__(self, other):
        if type(other) is not PVector:
            return NotImplemented
        return self._t > other._t

    def __ge__(self, other):
        if type(other) is not PVector:
            return NotImplemented
        return self._t >= other._t

    def __hash__(self):
        h = self._hash
        if h is None:
            h = hash(self._t)
            _HASH.__set__(self, h)
        return h

    @reprlib.recursive_repr("...")
    def __repr__(self):
        return f"PVector({list(self._t)!r})"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        if not isinstance(memo, dict):
            raise TypeError("deepcopy memo must be a dict")
        from copy import deepcopy
        t = self._t
        copied = []
        changed = False
        for item in t:
            new = deepcopy(item, memo)
            if new is not item:
                changed = True
            copied.append(new)
        # A mutable descendant may have finished copying self already.
        found = memo.get(id(self), _MISSING)
        if found is not _MISSING:
            return found
        return _wrap(tuple(copied)) if changed else self

    def __reduce__(self):
        return (PVector, (self._t,))


_T = PVector.__dict__["_t"]
_HASH = PVector.__dict__["_hash"]
_EMPTY = None


def _wrap(t):
    v = object.__new__(PVector)
    _T.__set__(v, t)
    _HASH.__set__(v, None)
    return v


_EMPTY = _wrap(())
Sequence.register(PVector)
