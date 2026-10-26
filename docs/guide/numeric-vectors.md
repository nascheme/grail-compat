---
sources:
  - docs/design/05-design-persistent-collections.md
  - grail-compat/src/grail/_numvec.py
  - grail-compat/tests/test_grail_numvec.py
---

# Store read-mostly numeric data

Use FloatVector for float64 data and IntVector for signed int64 data.
Both store unboxed numbers in an immutable flat buffer.

    >>> from grail import FloatVector, IntVector
    >>> samples = FloatVector([1, 2.5, 3])
    >>> samples.tolist()
    [1.0, 2.5, 3.0]
    >>> IntVector([1, 2]).set(-1, 7).tolist()
    [1, 7]

FloatVector accepts numeric conversion, not numeric strings. IntVector accepts
`__index__`, rejects floats, and raises `OverflowError` beyond signed 64-bit
range. This is different from Python's arbitrary-size ints and from Atom.add.

Construct from an iterable or a compatible buffer. Construction copies source
data so later changes to an array do not change the vector. Native vectors
export read-only buffers; compatibility export requires Python 3.12+.

Read elements as normal Python numbers. Use `tolist` for a fresh list, slicing
for another vector, and `mutate` for a batch builder. A single immutable edit
copies the flat storage, O(n). This is not a vectorized arithmetic package;
`+` concatenates two vectors of the same type.

FloatVector compares NaN numerically: a vector containing NaN is not equal
even to itself. Different numeric vector types do not compare equal. Numeric
vector fields are ordinary fields in records today, despite their immutable
storage.

[Reference](../reference/numeric-vectors.md), [transients](transients.md).
