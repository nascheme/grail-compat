"""N-Queens, Grail style.

Spec (what bm_nqueens computes): every placement of N non-attacking queens on
an N x N board, each written as a tuple `cols` where cols[row] is the queen's
column, listed in lexicographic order of the tuples.  The original finds them
by filtering all N! permutations; for N=8 that is 40320 candidates and 92
solutions.

Grail design
  * There is almost no data to model.  The problem is one value record,
    `Problem(n)`; a solution is a tuple of ints (a value).  No generics: there
    is no polymorphism.
  * Search state is local: three int bitmasks (columns and both diagonals)
    passed down a recursive backtracking search.  Placing queens row by row in
    increasing column order yields solutions already in lexicographic order.
  * Parallelism: one task per first-row column.  Each task is independent and
    reads only its immutable arguments; results are concatenated in column
    order, so the output is identical for any thread count.
  * `algorithm="brute"` keeps the original's algorithm (filter all
    permutations, here with itertools.permutations), so the original and the
    Grail version can also be compared like for like.
"""

from __future__ import annotations

import itertools

import grail


@grail.record
class Problem:
    n: int          # board size = number of queens


def _extend(n: int, row: int, cols: int, diag1: int, diag2: int,
            placed: list[int], out: list[tuple[int, ...]]) -> None:
    """Place queens in rows row..n-1, appending each full solution to `out`.

    cols, diag1, diag2 are bitmasks of attacked columns, already shifted so
    bit c means "column c in this row is attacked".  `placed` and `out` are
    this task's private working lists.
    """
    if row == n:
        out.append(tuple(placed))
        return
    full = (1 << n) - 1
    free = full & ~(cols | diag1 | diag2)
    while free:
        bit = free & -free                  # lowest free column
        free ^= bit
        placed.append(bit.bit_length() - 1)
        _extend(n, row + 1, cols | bit, ((diag1 | bit) << 1) & full,
                (diag2 | bit) >> 1, placed, out)
        placed.pop()


def solutions_from(problem: Problem, first_col: int) -> list[tuple[int, ...]]:
    """All solutions whose queen in row 0 is in `first_col`, in lex order."""
    n = problem.n
    bit = 1 << first_col
    out: list[tuple[int, ...]] = []
    _extend(n, 1, bit, (bit << 1) & ((1 << n) - 1), bit >> 1, [first_col], out)
    return out


def is_solution(vec: tuple[int, ...]) -> bool:
    """The original's test: all diagonals distinct (columns are a permutation)."""
    n = len(vec)
    return n == len({vec[i] + i for i in range(n)}) == len({vec[i] - i for i in range(n)})


def brute_from(problem: Problem, first_col: int) -> list[tuple[int, ...]]:
    """Like-for-like with the original: filter permutations starting with first_col."""
    rest = [c for c in range(problem.n) if c != first_col]
    return [vec for tail in itertools.permutations(rest)
            if is_solution(vec := (first_col, *tail))]


def n_queens(problem: Problem, *, threads: int = 8,
             algorithm: str = "backtrack") -> list[tuple[int, ...]]:
    """All solutions in lexicographic order; identical for every thread count."""
    task = {"backtrack": solutions_from, "brute": brute_from}[algorithm]
    firsts = range(problem.n)
    if threads <= 1:
        parts = [task(problem, c) for c in firsts]
    else:
        with grail.Pool(threads) as pool:
            parts = grail.pmap(lambda c: task(problem, c), firsts, chunk=1,
                               pool=pool)
    return [s for part in parts for s in part]


def main(queen_count: int = 8, *, threads: int = 8,
         algorithm: str = "backtrack") -> list[tuple[int, ...]]:
    return n_queens(Problem(queen_count), threads=threads, algorithm=algorithm)


if __name__ == "__main__":
    sols = main()
    print(len(sols), "solutions; first", sols[0], "last", sols[-1],
          "checksum", hash(tuple(sols)) & 0xFFFFFFFF)
