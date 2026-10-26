"""Atom: pure-Python stand-in for the native _grail_atom module.

The cell is guarded by a per-Atom reentrant lock.  Reads, reset, CAS and
add's commit each take the lock briefly; swap callbacks, repr and deepcopy
run with no lock held.  Retired values are released after the lock is
dropped, so finalizers may reenter the cell.  The lock is reentrant only so
that a signal handler or finalizer that runs while this thread holds it
cannot deadlock.
"""

import threading
import time
from types import GenericAlias

__all__ = ["Atom"]

_NAME = "grail.Atom"
_MISSING = object()


class _ImmutableType(type):
    """Metaclass emulating Py_TPFLAGS_IMMUTABLETYPE for Atom."""

    def __setattr__(cls, name, value):
        raise TypeError(f"cannot set {name!r} attribute of immutable type {_NAME!r}")

    def __delattr__(cls, name):
        raise TypeError(f"cannot set {name!r} attribute of immutable type {_NAME!r}")


def _readonly(name):
    if name == "value":
        return AttributeError(f"attribute 'value' of {_NAME!r} objects is not writable")
    return AttributeError(f"{_NAME!r} object has no attribute {name!r} and no "
                          f"__dict__ for setting new attributes")


class _ReprGuard(threading.local):
    def __init__(self):
        self.active = set()


_repr_guard = _ReprGuard()


class Atom(metaclass=_ImmutableType):
    """A synchronized reference cell. Mutable contents are not made thread-safe.
swap callbacks must be pure: contention can cause them to run repeatedly."""

    __slots__ = ("_value", "_lock")
    __module__ = "grail"
    __hash__ = None

    def __new__(cls, *args, **kwargs):
        if kwargs:
            if args or len(kwargs) != 1 or "value" not in kwargs:
                if len(args) + len(kwargs) > 1:
                    raise TypeError(f"Atom() takes at most 1 argument "
                                    f"({len(args) + len(kwargs)} given)")
                raise TypeError("Atom() missing required argument 'value' (pos 1)")
            value = kwargs["value"]
        elif len(args) == 1:
            value = args[0]
        elif not args:
            raise TypeError("Atom() missing required argument 'value' (pos 1)")
        else:
            raise TypeError(f"Atom() takes at most 1 argument ({len(args)} given)")
        self = object.__new__(cls)
        _set_lock(self, threading.RLock())
        _set_value(self, value)
        return self

    def __init_subclass__(cls, **kwargs):
        raise TypeError(f"type {_NAME!r} is not an acceptable base type")

    def __setattr__(self, name, value):
        raise _readonly(name)

    def __delattr__(self, name):
        raise _readonly(name)

    __class_getitem__ = classmethod(GenericAlias)

    @property
    def value(self):
        """Read the current value as an owned reference."""
        with self._lock:
            return self._value

    def reset(self, value, /):
        """Replace the value and return the supplied value."""
        with self._lock:
            retired = self._value
            _set_value(self, value)
        # Release after unlocking: __del__ may read or update this Atom.
        del retired
        return value

    def compare_and_set(self, expected, desired, /):
        """Replace only when the current value is the expected object (identity)."""
        with self._lock:
            if self._value is not expected:
                return False
            _set_value(self, desired)
        return True

    def add(self, delta, /):
        """Atomically add exact ints and return the committed new value.
Both the current value and delta must be exact builtin ints.
Grail operator registrations are ignored; allocation failure does not commit."""
        if type(delta) is not int:
            raise TypeError("add delta must be an exact int")
        lock = self._lock
        while True:
            with lock:
                old = self._value
            if type(old) is not int:
                raise TypeError("add value must be an exact int")
            # Exact int + exact int never runs user code.
            new = int.__add__(old, delta)
            with lock:
                if self._value is old:
                    _set_value(self, new)
                    return new

    def swap(self, f=_MISSING, /, *args, **kwargs):
        """Return the committed result of f(value, *args, **kwargs), retrying as needed."""
        if f is _MISSING:
            raise TypeError("swap requires a positional callable")
        if not callable(f):
            raise TypeError("swap's first argument must be callable")
        lock = self._lock
        failures = 0
        while True:
            with lock:
                old = self._value
            candidate = f(old, *args, **kwargs)
            with lock:
                if self._value is old:
                    _set_value(self, candidate)
                    return candidate
            # Lost the race; drop the candidate before retrying.
            del candidate
            failures += 1
            if failures > 2:
                time.sleep(0)

    def __repr__(self):
        key = id(self)
        active = _repr_guard.active
        if key in active:
            return "..."
        active.add(key)
        try:
            return f"Atom({self.value!r})"
        finally:
            active.discard(key)

    def __copy__(self):
        return Atom(self.value)

    def __deepcopy__(self, memo):
        if not isinstance(memo, dict):
            raise TypeError("deepcopy memo must be a dict")
        import copy
        value = self.value
        result = Atom(None)
        key = id(self)
        # Memoize the new cell before descending: an Atom may contain itself.
        memo[key] = result
        try:
            copied = copy.deepcopy(value, memo)
        except BaseException:
            if memo.get(key) is result:
                del memo[key]
            raise
        result.reset(copied)
        return result

    def __reduce__(self):
        # State is serialized after the new Atom is memoized, preserving cycles.
        return (type(self), (None,), self.value)

    def __setstate__(self, value):
        self.reset(value)


_set_value = Atom.__dict__["_value"].__set__
_set_lock = Atom.__dict__["_lock"].__set__
