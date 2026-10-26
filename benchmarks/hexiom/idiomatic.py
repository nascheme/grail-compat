"""Immutable Hexiom positions, preserving pyperformance's search algorithm.

Based on Laurent Vaucher's Hexiom solver (slowfrog/hexiom, hexiom2.py), as
adapted for pyperformance by Armin Rigo. Topology and branch states are
records. Propagation edits private scratch and publishes candidate tuples at
fixed points. Tuple and PVector snapshots run the same propagation/search.
"""

from copy import replace
import io
from types import MappingProxyType

import grail

DEFAULT_LEVEL = 25
EMPTY = 7
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1))
MIN_CHOICE_STRATEGY = 0
MAX_CHOICE_STRATEGY = 1
HIGHEST_VALUE_STRATEGY = 2
FIRST_STRATEGY = 3
MAX_NEIGHBORS_STRATEGY = 4
MIN_NEIGHBORS_STRATEGY = 5
ASCENDING = 1
DESCENDING = -1
OPEN = 0
SOLVED = 1
IMPOSSIBLE = -1


@grail.record
class Node:
    pos: tuple[int, int]
    links: tuple[int, ...]


@grail.record
class Hex:
    size: int
    nodes: tuple[Node, ...]
    rows: tuple[tuple[int, ...], ...]


@grail.record
class Position:
    hex: Hex
    tiles: tuple[int, ...]
    cells: tuple[tuple[int, ...], ...]


@grail.record
class PersistentPosition:
    hex: Hex
    tiles: tuple[int, ...]
    # Ordinary field: the prototype has no PVector value-conformance plan.
    cells: grail.PVector[tuple[int, ...]]


def make_hex(size):
    if size < 1:
        raise ValueError("Hex size must be positive")
    positions = []
    rows = []
    for y in range(size):
        row = []
        for x in range(size + y):
            row.append(len(positions))
            positions.append((x, y))
        rows.append(tuple(row))
    for y in range(1, size):
        row = []
        for x in range(y, size * 2 - 1):
            row.append(len(positions))
            positions.append((x, size + y - 1))
        rows.append(tuple(row))
    by_pos = {pos: i for i, pos in enumerate(positions)}
    nodes = tuple(Node((x, y), tuple(by_pos[(x + dx, y + dy)]
                                   for dx, dy in DIRS if (x + dx, y + dy) in by_pos))
                  for x, y in positions)
    return Hex(size, nodes, tuple(rows))


def _publish(pos, cells):
    if type(pos) is Position:
        return replace(pos, cells=tuple(cells))
    # Batch the sparse edits, preserving unchanged leaves in the source root.
    with pos.cells.mutate() as builder:
        for i, cell in enumerate(cells):
            if cell is not pos.cells[i]:
                builder[i] = cell
    return replace(pos, cells=builder.finish())


def _remove(cells, i, value):
    cell = cells[i]
    if value not in cell:
        return False
    index = cell.index(value)
    cells[i] = cell[:index] + cell[index + 1:]
    return True


def _remove_unfixed(cells, value):
    changed = False
    for i in range(len(cells)):
        if len(cells[i]) != 1:
            if _remove(cells, i, value):
                changed = True
    return changed


def _constraint_pass(hex, tiles, cells, last_move=None):
    """One upstream pass, mutating only a propagation-local outer list.

    Candidate sets remain ordered tuples, and updates are immediately visible
    to later rules in this pass. Do not use simultaneous/batched rule semantics.
    """
    changed = False
    left = list(tiles)
    free_cells = range(len(cells)) if last_move is None else hex.nodes[last_move].links
    for i in free_cells:
        if len(cells[i]) != 1:
            vmax = 0
            vmin = 0
            for nid in hex.nodes[i].links:
                if len(cells[nid]) == 1:
                    if cells[nid][0] != EMPTY:
                        vmin += 1
                        vmax += 1
                else:
                    vmax += 1
            for num in range(7):
                if num < vmin or num > vmax:
                    if _remove(cells, i, num):
                        changed = True

    for cell in cells:
        if len(cell) == 1:
            left[cell[0]] -= 1

    for value in range(8):
        if tiles[value] > 0 and left[value] == 0:
            if _remove_unfixed(cells, value):
                changed = True
        else:
            possible = sum(1 if value in cell else 0 for cell in cells)
            if tiles[value] == possible:
                for i in range(len(cells)):
                    if len(cells[i]) != 1 and value in cells[i]:
                        cells[i] = (value,)
                        changed = True

    filled_cells = range(len(cells)) if last_move is None else [last_move]
    for i in filled_cells:
        if len(cells[i]) == 1:
            num = cells[i][0]
            empties = 0
            filled = 0
            unknown = []
            for nid in hex.nodes[i].links:
                if len(cells[nid]) == 1:
                    if cells[nid][0] == EMPTY:
                        empties += 1
                    else:
                        filled += 1
                else:
                    unknown.append(nid)
            if len(unknown) > 0:
                if num == filled:
                    for nid in unknown:
                        if EMPTY in cells[nid]:
                            cells[nid] = (EMPTY,)
                            changed = True
                elif num == filled + len(unknown):
                    for nid in unknown:
                        if _remove(cells, nid, EMPTY):
                            changed = True
    return changed


def constraint_pass(pos, last_move=None):
    """Pure single-pass adapter, useful for comparing intermediate states."""
    cells = list(pos.cells)
    changed = _constraint_pass(pos.hex, pos.tiles, cells, last_move)
    return (_publish(pos, cells) if changed else pos), changed


def propagate(pos, last_move=None):
    cells = list(pos.cells)
    changed = False
    while _constraint_pass(pos.hex, pos.tiles, cells, last_move):
        changed = True
    return _publish(pos, cells) if changed else pos


def next_cell(pos, strategy=HIGHEST_VALUE_STRATEGY):
    cells = pos.cells
    count = len(cells)
    if strategy == MIN_CHOICE_STRATEGY:
        minlen, chosen = 10, -1
        for i in range(count):
            if 1 < len(cells[i]) < minlen:
                minlen, chosen = len(cells[i]), i
        return chosen
    if strategy == MAX_CHOICE_STRATEGY:
        maxlen, chosen = 1, -1
        for i in range(count):
            if maxlen < len(cells[i]):
                maxlen, chosen = len(cells[i]), i
        return chosen
    if strategy == HIGHEST_VALUE_STRATEGY:
        maxval, chosen = -1, -1
        for i in range(count):
            if len(cells[i]) != 1:
                value = max(k for k in cells[i] if k != EMPTY)
                if maxval < value:
                    maxval, chosen = value, i
        return chosen
    if strategy == FIRST_STRATEGY:
        for i in range(count):
            if len(cells[i]) != 1:
                return i
        return -1
    if strategy in (MAX_NEIGHBORS_STRATEGY, MIN_NEIGHBORS_STRATEGY):
        best = -1 if strategy == MAX_NEIGHBORS_STRATEGY else 7
        chosen = -1
        for i in range(count):
            if len(cells[i]) != 1:
                neighbors = sum(1 if len(cells[nid]) == 1 and cells[nid][0] != EMPTY else 0
                                for nid in pos.hex.nodes[i].links)
                if ((neighbors > best) if strategy == MAX_NEIGHBORS_STRATEGY
                        else (neighbors < best)):
                    best, chosen = neighbors, i
        return chosen
    raise Exception("Wrong strategy: %d" % strategy)


def find_moves(pos, strategy, order):
    cell_id = next_cell(pos, strategy)
    if cell_id < 0:
        return ()
    if order == ASCENDING:
        return tuple((cell_id, value) for value in pos.cells[cell_id])
    moves = list(reversed([(cell_id, value) for value in pos.cells[cell_id] if value != EMPTY]))
    if EMPTY in pos.cells[cell_id]:
        moves.append((cell_id, EMPTY))
    return tuple(moves)


def play_move(pos, move):
    cell_id, value = move
    if type(pos) is Position:
        if cell_id < 0:
            cell_id += len(pos.cells)
        if not 0 <= cell_id < len(pos.cells):
            raise IndexError("cell index out of range")
        cells = pos.cells[:cell_id] + ((value,),) + pos.cells[cell_id + 1:]
    else:
        cells = pos.cells.set(cell_id, (value,))
    return replace(pos, cells=cells)


def print_pos(pos, output):
    size = pos.hex.size
    for y, row in enumerate(pos.hex.rows):
        indent = size - y - 1 if y < size else y - size + 1
        print(" " * indent, end="", file=output)
        for i in row:
            cell = pos.cells[i]
            if len(cell) == 1:
                char = str(cell[0]) if cell[0] != EMPTY else "."
            else:
                char = "?"
            print("%s " % char, end="", file=output)
        print(file=output)


def solved(pos, output, verbose=False):
    tiles = list(pos.tiles)
    cells = pos.cells
    exact = True
    all_done = True
    for i in range(len(cells)):
        if len(cells[i]) == 0:
            return IMPOSSIBLE
        if len(cells[i]) == 1:
            num = cells[i][0]
            tiles[num] -= 1
            if tiles[num] < 0:
                return IMPOSSIBLE
            vmax = 0
            vmin = 0
            if num != EMPTY:
                for nid in pos.hex.nodes[i].links:
                    if len(cells[nid]) == 1:
                        if cells[nid][0] != EMPTY:
                            vmin += 1
                            vmax += 1
                    else:
                        vmax += 1
                if num < vmin or num > vmax:
                    return IMPOSSIBLE
                if num != vmin:
                    exact = False
        else:
            all_done = False
    if not all_done or not exact:
        return OPEN
    print_pos(pos, output)
    return SOLVED


def _visit(pos, output, on_visit):
    if on_visit is not None:
        on_visit(pos)
    return solved(pos, output)


def solve_step(prev, strategy, order, output, first=False, on_visit=None):
    pos = propagate(prev) if first else prev
    moves = find_moves(pos, strategy, order)
    if len(moves) == 0:
        return _visit(pos, output, on_visit)
    for move in moves:
        new_pos = propagate(play_move(pos, move), move[0])
        status = _visit(new_pos, output, on_visit)
        if status == OPEN:
            status = solve_step(new_pos, strategy, order, output, on_visit=on_visit)
        if status == SOLVED:
            return SOLVED
    return IMPOSSIBLE


def check_valid(pos):
    tiles = tuple(max(0, value) for value in pos.tiles)
    total = sum(tiles)
    count = len(pos.hex.nodes)
    if total != count:
        raise Exception("Invalid input. Expected %d tiles, got %d." % (count, total))
    return replace(pos, tiles=tiles)


def solve(pos, strategy, order, output, on_visit=None):
    return solve_step(check_valid(pos), strategy, order, output, first=True, on_visit=on_visit)


def read_file(source, storage="tuple"):
    if storage not in ("tuple", "pvector"):
        raise ValueError("unknown snapshot storage: %s" % storage)
    lines = [line.strip("\r\n") for line in source.splitlines()]
    hex = make_hex(int(lines[0]))
    tiles = [0] * 8
    cells = [tuple(range(8)) for _ in hex.nodes]
    for y, row in enumerate(hex.rows):
        indent = hex.size - y - 1 if y < hex.size else y - hex.size + 1
        line = lines[y + 1][indent:]
        for x, i in enumerate(row):
            tile = line[2 * x:2 * x + 2]
            value = EMPTY if tile[1] == "." else int(tile)
            tiles[value] += 1
            if tile[0] == "+":
                cells[i] = (value,)
    for value in range(8):
        if tiles[value] == 0:
            for i in range(len(cells)):
                _remove(cells, i, value)
    if storage == "pvector":
        return PersistentPosition(hex, tuple(tiles), grail.PVector(tuple(cells)))
    return Position(hex, tuple(tiles), tuple(cells))


def solve_file(source, strategy, order, output, storage="tuple", on_visit=None):
    return solve(read_file(source, storage), strategy, order, output, on_visit)


# The six upstream puzzles and their default-strategy first solutions.
LEVELS = MappingProxyType({
    2: ("""
2
  . 1
 . 1 1
  1 .
""", """\
 1 1
. . .
 1 1
"""),
    10: ("""
3
  +.+. .
 +. 0 . 2
 . 1+2 1 .
  2 . 0+.
   .+.+.
""", """\
  . . 1
 . 1 . 2
0 . 2 2 .
 . . . .
  0 . .
"""),
    20: ("""
3
   . 5 4
  . 2+.+1
 . 3+2 3 .
 +2+. 5 .
   . 3 .
""", """\
  3 3 2
 4 5 . 1
3 5 2 . .
 2 . . .
  . . .
"""),
    25: ("""
3
   4 . .
  . . 2 .
 4 3 2 . 4
  2 2 3 .
   4 2 4
""", """\
  3 4 2
 2 4 4 .
. . . 4 2
 . 2 4 3
  . 2 .
"""),
    30: ("""
4
    5 5 . .
   3 . 2+2 6
  3 . 2 . 5 .
 . 3 3+4 4 . 3
  4 5 4 . 5 4
   5+2 . . 3
    4 . . .
""", """\
   3 4 3 .
  4 6 5 2 .
 2 5 5 . . 2
. . 5 4 . 4 3
 . 3 5 4 5 4
  . 2 . 3 3
   . . . .
"""),
    36: ("""
4
    2 1 1 2
   3 3 3 . .
  2 3 3 . 4 .
 . 2 . 2 4 3 2
  2 2 . . . 2
   4 3 4 . .
    3 2 3 3
""", """\
   3 4 3 2
  3 4 4 . 3
 2 . . 3 4 3
2 . 1 . 3 . 2
 3 3 . 2 . 2
  3 . 2 . 2
   2 2 . 1
"""),
})


def main(level=DEFAULT_LEVEL, storage="tuple"):
    board, expected = LEVELS[level]
    output = io.StringIO()
    status = solve_file(board.strip(), FIRST_STRATEGY, DESCENDING, output, storage)
    result = "\n".join(line.rstrip() for line in output.getvalue().splitlines())
    if status != SOLVED or result != expected.rstrip():
        raise AssertionError("got a wrong answer:\n%s\nexpected: %s" % (result, expected.rstrip()))
    return result


if __name__ == "__main__":
    print(main())
