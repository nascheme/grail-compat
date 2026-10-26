"""FloatVector / IntVector: pure-Python stand-ins for _grail_numvec.

Each vector holds a private array.array ('d' float64 or 'q' int64) that is
never changed after the vector is published, so concurrent reads need no
lock.  Updates copy the buffer, as the native flat form does.  On Python
3.12+ the vectors export a read-only buffer through __buffer__; earlier
versions cannot export a buffer from pure Python.
"""

import math
import operator
import sys
from array import array
from collections.abc import Sequence

from ._vector import _BuilderBase, _ClosedType, _Iterator, _no_attr, _check_index

__all__ = ["FloatVector", "IntVector"]

_MISSING = object()
_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_HAS_BUFFER_DUNDER = sys.version_info >= (3, 12)
_FREE_THREADED = hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled()


def _ssize(value):
    """The "n" argument converter."""
    i = operator.index(value)
    if not -sys.maxsize - 1 <= i <= sys.maxsize:
        raise OverflowError("Python int too large to convert to C ssize_t")
    return i


class _NumIterator(_Iterator):
    """Locked iterator (free-threaded builds) with a length hint."""

    __slots__ = ()
    __module__ = "grail._numvec"
    _native_name = "_grail_numvec._Iterator"

    def __length_hint__(self):
        i, n = self._index, len(self._items)
        if i < 0 or i >= n:
            return 0
        return i + 1 if self._step < 0 else n - i


# ---------------------------------------------------------------- elements

def _unbox_float(value):
    """PyFloat_AsDouble: __float__, else __index__; strings are rejected."""
    if type(value) is float:
        return value
    tp = type(value)
    if hasattr(tp, "__float__"):
        result = tp.__float__(value)
        if not isinstance(result, float):
            raise TypeError(f"{tp.__name__}.__float__ returned non-float "
                            f"(type {type(result).__name__})")
        return float(result)
    if hasattr(tp, "__index__"):
        return float(operator.index(value))
    raise TypeError(f"must be real number, not {tp.__name__}")


def _unbox_int(value):
    """PyLong_AsInt64, rejecting floats outright."""
    if isinstance(value, float):
        raise TypeError("IntVector elements must be integers, not "
                        f"{type(value).__name__}")
    i = operator.index(value)
    if not _INT64_MIN <= i <= _INT64_MAX:
        raise OverflowError("Python int too large to convert to C int64_t")
    return i


def _convert_sequence(kind, items):
    """Convert a list or tuple to a new array; all or nothing."""
    tc = kind._typecode
    if kind is FloatVector:
        exact = all(type(x) is float or type(x) is int for x in items)
    else:
        exact = all(type(x) is int for x in items)
    if exact:
        # No user code runs: exact floats and ints only.
        if kind is IntVector:
            for x in items:
                if not _INT64_MIN <= x <= _INT64_MAX:
                    raise OverflowError("Python int too large to convert to C int64_t")
        return array(tc, items)
    unbox = kind._unbox
    return array(tc, [unbox(x) for x in items])


def _buffer_matches(kind, source):
    """Return a contiguous copy of a matching 1-D buffer, else None."""
    if _HAS_BUFFER_DUNDER:
        if not hasattr(type(source), "__buffer__"):
            return None
        view = memoryview(source)
    else:
        try:
            view = memoryview(source)
        except TypeError:
            return None
    with view:
        fmt = view.format
        if fmt[:1] in ("@", "="):
            fmt = fmt[1:]
        tc = kind._typecode
        if (view.ndim == 1 and view.itemsize == 8 and
                (fmt == tc or (tc == "q" and fmt == "l"))):
            a = array(tc)
            a.frombytes(view.tobytes())
            return a
    return None


def _convert_any(kind, source):
    """Convert any accepted source to a new array; all or nothing."""
    a = _buffer_matches(kind, source)
    if a is not None:
        return a
    if type(source) is list or type(source) is tuple:
        return _convert_sequence(kind, source)
    if type(source) is kind:
        return source._a
    unbox = kind._unbox
    return array(kind._typecode, [unbox(x) for x in source])


# ---------------------------------------------------------------- builder

class _Transient(_BuilderBase, metaclass=_ClosedType):
    """Transient numeric-vector builder (native: _grail_numvec._Transient)."""

    __slots__ = ("_a", "_kind")
    __module__ = "grail._numvec"
    _native_name = "_grail_numvec._Transient"

    @classmethod
    def _new(cls, kind, a):
        self = object.__new__(cls)
        self._init_state()
        self._kind = kind
        self._a = a
        return self

    def _release(self):
        self._a = array(self._kind._typecode)

    def _publish(self):
        a, self._a = self._a, array(self._kind._typecode)
        return self._kind._wrap(a)

    def _index(self, key):
        name = self._kind.__name__
        i = _check_index(key, len(self._a),
                         f"{name} indices must be integers or slices, not "
                         f"{type(key).__name__}")
        if i is None:
            raise IndexError(f"{name} index out of range")
        return i

    def __len__(self):
        self._check()
        return len(self._a)

    def __getitem__(self, key):
        self._check()
        self._busy = True
        try:
            i = self._index(key)
        finally:
            self._busy = False
        return self._a[i]

    def __setitem__(self, key, value):
        self._check()
        self._busy = True
        try:
            item = self._kind._unbox(value)
            i = self._index(key)
        finally:
            self._busy = False
        self._a[i] = item

    def __delitem__(self, key):
        self._check()
        raise TypeError(f"{self._kind.__name__} transients only remove with pop()")

    def append(self, value):
        """Append an element."""
        self._check()
        self._busy = True
        try:
            item = self._kind._unbox(value)
        finally:
            self._busy = False
        self._a.append(item)

    def extend(self, iterable):
        """Append the elements of an iterable; on failure nothing is added."""
        self._check()
        # Iteration may run code that uses this builder; busy rejects it.
        self._busy = True
        try:
            extra = _convert_any(self._kind, iterable)
        finally:
            self._busy = False
        self._a.extend(extra)

    def pop(self):
        """Remove and return the last element."""
        self._check()
        if not self._a:
            raise IndexError(f"pop from empty {self._kind.__name__}")
        return self._a.pop()


# ---------------------------------------------------------------- vectors

class _NumVector:
    """Shared implementation; FloatVector and IntVector are final."""

    __slots__ = ()

    def __new__(cls, source=_MISSING):
        if source is _MISSING:
            return cls._wrap(array(cls._typecode))
        if type(source) is cls:
            return source
        return cls._wrap(_convert_any(cls, source))

    @classmethod
    def _wrap(cls, a):
        v = object.__new__(cls)
        cls._A.__set__(v, a)
        cls._HASH.__set__(v, None)
        return v

    def __setattr__(self, name, value):
        raise _no_attr(self, name)

    def __delattr__(self, name):
        raise _no_attr(self, name)

    def __len__(self):
        return len(self._a)

    def _index(self, key):
        name = type(self).__name__
        i = _check_index(key, len(self._a),
                         f"{name} indices must be integers or slices, not "
                         f"{type(key).__name__}")
        if i is None:
            raise IndexError(f"{name} index out of range")
        return i

    def __getitem__(self, key):
        a = self._a
        if type(key) is int:
            n = len(a)
            if -n <= key < n:
                return a[key]
            _check_index(key, n, "")
            raise IndexError(f"{type(self).__name__} index out of range")
        if isinstance(key, slice):
            start, stop, step = key.indices(len(a))
            r = a[key]
            if step == 1 and len(r) == len(a):
                return self
            return self._wrap(r)
        return a[self._index(key)]

    def __iter__(self):
        if _FREE_THREADED:
            return _NumIterator._new(self._a, False)
        return iter(self._a.tolist())

    def __reversed__(self):
        if _FREE_THREADED:
            return _NumIterator._new(self._a, True)
        return reversed(self._a.tolist())

    def __contains__(self, value):
        return value in self._a

    def count(self, value):
        return self._a.count(value)

    def index(self, value, start=0, stop=sys.maxsize, /):
        n = len(self._a)
        start = _ssize(start)
        stop = _ssize(stop)
        if start < 0:
            start = max(start + n, 0)
        if stop < 0:
            stop = max(stop + n, 0)
        try:
            return self._a.index(value, start, stop)
        except ValueError as e:
            if type(e) is ValueError and e.args == ("array.index(x): x not in array",):
                raise ValueError(f"{type(self).__name__}.index(x): x not in vector") from None
            raise

    def tolist(self):
        """Return the elements as a list."""
        return self._a.tolist()

    def set(self, index, value, /):
        """Return a vector with an existing index replaced."""
        item = self._unbox(value)
        i = self._index(index)
        a = array(self._typecode, self._a)
        a[i] = item
        return self._wrap(a)

    def append(self, value, /):
        """Return a vector with one appended element."""
        item = self._unbox(value)
        a = array(self._typecode, self._a)
        a.append(item)
        return self._wrap(a)

    def pop(self):
        """Return the vector without its last element."""
        if not self._a:
            raise IndexError(f"pop from empty {type(self).__name__}")
        return self._wrap(self._a[:-1])

    def mutate(self):
        """Return a transient builder starting from this vector."""
        return _Transient._new(type(self), array(self._typecode, self._a))

    def __add__(self, other):
        if type(other) is not type(self):
            radd = getattr(type(other), "__radd__", None)
            if radd is not None:
                result = radd(other, self)
                if result is not NotImplemented:
                    return result
            raise TypeError(f"cannot concatenate {type(self).__name__} and "
                            f"{type(other).__name__}")
        if not other._a:
            return self
        if not self._a:
            return other
        return self._wrap(self._a + other._a)

    # Elements compare by value (NaN unequal to itself): array comparison
    # boxes fresh objects, so no identity shortcut applies.
    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._a == other._a

    def __ne__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._a != other._a

    def __lt__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._a < other._a

    def __le__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._a <= other._a

    def __gt__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._a > other._a

    def __ge__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._a >= other._a

    def __hash__(self):
        h = self._hash
        if h is None:
            items = self._a.tolist()
            if self._typecode == "d":
                # Equal to hash(tuple(self)) for NaN-free contents; a NaN
                # element hashes as 0, as natively.
                items = [0 if x != x else x for x in items]
            h = hash(tuple(items))
            type(self)._HASH.__set__(self, h)
        return h

    def __repr__(self):
        return f"{type(self).__name__}({self._a.tolist()!r})"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return (type(self), (self._a.tolist(),))

    if _HAS_BUFFER_DUNDER:
        def __buffer__(self, flags):
            if flags & 1:  # inspect.BufferFlags.WRITABLE
                raise BufferError(f"{type(self).__name__} is not writable")
            return memoryview(self._a).toreadonly()


class FloatVector(_NumVector, metaclass=_ClosedType):
    """Immutable vector of unboxed float64 elements."""

    __slots__ = ("_a", "_hash")
    __module__ = "grail"
    _native_name = "grail.FloatVector"
    _typecode = "d"
    _unbox = staticmethod(_unbox_float)


class IntVector(_NumVector, metaclass=_ClosedType):
    """Immutable vector of unboxed int64 elements."""

    __slots__ = ("_a", "_hash")
    __module__ = "grail"
    _native_name = "grail.IntVector"
    _typecode = "q"
    _unbox = staticmethod(_unbox_int)


for _cls in (FloatVector, IntVector):
    type.__setattr__(_cls, "_A", _cls.__dict__["_a"])
    type.__setattr__(_cls, "_HASH", _cls.__dict__["_hash"])
    Sequence.register(_cls)
del _cls
