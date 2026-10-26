"""Go (9x9) Monte Carlo tree search, Grail style: positions and tree are values.

Spec (what bm_go computes): from the empty 9x9 board, choose Black's first
move with UCT Monte Carlo tree search: GAMES=200 iterations, each of which
walks the tree from the root (unexplored moves first, chosen at random, then
the child with the best UCB score), adds one node, finishes the game with
random "useful" moves (not filling own eyes, no positional superko repeat via
Zobrist hashing, at most 3*81 moves), scores it by area with komi 7.5 and
updates win/loss counts on the path.  The answer is the most visited root
move.  With random.seed(1) the original returns one fixed point.

Grail design
  * Shared, immutable inputs are value records: `Geometry` (board size and the
    neighbour table, a tuple of tuples), `Zobrist` (a tuple of 3*81 random
    ints) and `Config` (games, komi, move cap).  `Rules` pairs the geometry
    with one Zobrist table.  They are built once and read by every playout,
    on any thread, without synchronisation.
  * A game state is a `Position` record: per-point colour and group-label
    tuples, pseudo-liberty counts by group, the ordered empty-point tuple,
    the Zobrist hash and a persistent `PSet` of every hash seen in the game
    (positional superko).  `play` and `random_play` return new positions;
    nothing is mutated.  Move application builds the next position in local
    list scratch and publishes tuples, so one move copies a few 81-element
    arrays (the cost of the immutable design here; see measure_go.py).
    Every stone carries its group's label instead of a union-find with
    path compression (which mutates on read); a move joins the label of its
    first friendly neighbour and relabels any other groups it connects.  `used` is a bitmask int.
  * Because positions are values, the search position is built once and every
    iteration starts from it; the original resets and replays a board per
    game.
  * The search tree is a `Node` record: counts, the untried moves, the cached
    best child's point and an 81-slot children tuple.  An iteration descends,
    then rebuilds the path bottom-up (path copying), so each iteration
    returns a new root and older roots stay valid.  The original's parent
    links are replaced by passing the parent's visit count down.
  * No generic functions: there is no polymorphism anywhere in this program.
  * Randomness is an explicit random.Random passed in, not the global module.

The previous design (mutable `Board`/`Node` structs, same algorithm) is kept
as hybrid.py for comparison.

Determinism and parallelism
  * mode="exact" (threads=1): one tree, one RNG seeded like the original and
    consumed in the same order (including the separate Zobrist table the
    original draws for its search board, and the original's reordering of
    the empty-point list when random moves are rejected).  It returns the
    original's move, and the per-move visit counts match exactly.
  * mode="root-parallel" (threads=N): root parallelisation.  The GAMES
    iterations are split across N independent trees, worker i using its own
    RNG seeded from (seed, i); the root visit counts are summed in worker
    order and the most visited move wins.  This CHANGES THE RESULT relative to
    the original (different random streams, N smaller trees instead of one
    big one), but it is deterministic for a given (seed, N) and independent of
    thread scheduling.  Leaf parallelism (one tree, N playouts per leaf) also
    changes the result; with value trees it would need the search thread to
    merge playout results into the current root, as michi does.
"""

from __future__ import annotations

import copy
import math
import random
from concurrent.futures import ThreadPoolExecutor

import grail

EMPTY, WHITE, BLACK = 0, 1, 2
PASS = -1
ZOBRIST_LIMIT = 9223372036854775807


# ------------------------------------------------------------ value records

@grail.record
class Geometry:
    size: int
    neighbours: tuple[tuple[int, ...], ...]   # neighbours[pos], order W, E, N, S


@grail.record
class Zobrist:
    keys: tuple[int, ...]                     # keys[3*pos + colour]


@grail.record
class Config:
    games: int = 200
    komi: float = 7.5
    max_moves: int = 9 * 9 * 3


@grail.record
class MoveStats:
    pos: int
    wins: int
    losses: int


def make_geometry(size: int) -> Geometry:
    nbs = []
    for pos in range(size * size):
        x, y = pos % size, pos // size
        nbs.append(tuple((y + dy) * size + (x + dx)
                         for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
                         if 0 <= x + dx < size and 0 <= y + dy < size))
    return Geometry(size, tuple(nbs))


def make_zobrist(geo: Geometry, rng: random.Random) -> Zobrist:
    # Same draws, in the same order, as the original's Square constructors.
    return Zobrist(tuple(rng.randrange(ZOBRIST_LIMIT)
                         for _ in range(3 * geo.size * geo.size)))


@grail.record
class Rules:
    geo: Geometry
    zob: Zobrist


# ------------------------------------------------------------ game positions

@grail.record
class Position:
    """One game state.  Stones of a group all carry the group's label (a
    stone of the group), and libs[label] is its pseudo-liberty count
    (liberties counted once per adjacent stone)."""
    rules: Rules
    colour: tuple[int, ...]       # per point: EMPTY, WHITE or BLACK
    group: tuple[int, ...]        # per point: group label, -1 if empty
    libs: tuple[int, ...]         # per label: pseudo-liberties
    used: int                     # bitmask: points that have ever held a stone
    empties: tuple[int, ...]      # empty points; the order drives the RNG
    hash: int
    seen: grail.PSet[int]         # every hash of this game (positional superko)
    to_move: int
    passes: int                   # consecutive passes; 2 ends the game


def new_position(rules: Rules) -> Position:
    n = rules.geo.size * rules.geo.size
    h = 0
    for pos in range(n):
        h ^= rules.zob.keys[3 * pos + EMPTY]
    return Position(rules, n * (EMPTY,), n * (-1,), n * (0,), 0,
                    tuple(range(n)), h, grail.PSet((h,)), BLACK, 0)


def finished(p: Position) -> bool:
    return p.passes >= 2


def _relabel(nbs, group, q: int, old: int, new: int) -> None:
    group[q] = new
    for nb in nbs[q]:
        if group[nb] == old:
            _relabel(nbs, group, nb, old, new)


def _capture(nbs, keys, colour, group, libs, empties, q: int, label: int,
             h: int) -> int:
    """Take group `label` off the board from stone q; returns the new hash.

    Depth first in neighbour order, like the original, so captured points
    join the empty list in the same order."""
    h ^= keys[3 * q + colour[q]] ^ keys[3 * q + EMPTY]
    colour[q] = EMPTY
    group[q] = -1
    empties.append(q)
    for nb in nbs[q]:
        g = group[nb]
        if g == label:
            h = _capture(nbs, keys, colour, group, libs, empties, nb, label, h)
        elif g >= 0:
            libs[g] += 1                    # a neighbouring group gains a liberty
    return h


def play(p: Position, pos: int) -> Position:
    """The position after the side to move plays `pos` (or PASS)."""
    other = WHITE if p.to_move == BLACK else BLACK
    if pos == PASS:
        return Position(p.rules, p.colour, p.group, p.libs, p.used, p.empties,
                        p.hash, p.seen, other, p.passes + 1)
    nbs = p.rules.geo.neighbours
    keys = p.rules.zob.keys
    me = p.to_move
    colour, group, libs = list(p.colour), list(p.group), list(p.libs)
    empties = list(p.empties)
    h = p.hash ^ keys[3 * pos + EMPTY] ^ keys[3 * pos + me]
    colour[pos] = me
    group[pos] = label = pos
    n_libs = 0
    for nb in nbs[pos]:
        c = colour[nb]
        if c == EMPTY:
            n_libs += 1
            continue
        g = group[nb]
        if c == me:
            if g != label:                  # join a friendly group
                n_libs += libs[g]
                if label == pos:            # the first: adopt its label
                    group[pos] = label = g
                else:
                    _relabel(nbs, group, nb, g, label)
            n_libs -= 1
        else:
            libs[g] -= 1
            if libs[g] == 0:
                libs[label] = n_libs        # visible to the capture's liberty updates
                h = _capture(nbs, keys, colour, group, libs, empties, nb, g, h)
                n_libs = libs[label]
    libs[label] = n_libs
    last = empties.pop()                    # remove pos from the empty list
    if last != pos:
        empties[empties.index(pos)] = last
    return Position(p.rules, tuple(colour), tuple(group), tuple(libs),
                    p.used | (1 << pos), tuple(empties), h, p.seen.add(h),
                    other, 0)


def _group_key(p: Position, start: int, label: int) -> int:
    """XOR of the hash changes that would take group `label` off the board."""
    nbs = p.rules.geo.neighbours
    keys, colour, group = p.rules.zob.keys, p.colour, p.group
    k, seen, todo = 0, {start}, [start]
    while todo:
        q = todo.pop()
        k ^= keys[3 * q + colour[q]] ^ keys[3 * q + EMPTY]
        for nb in nbs[q]:
            if group[nb] == label and nb not in seen:
                seen.add(nb)
                todo.append(nb)
    return k


def is_useful(p: Position, pos: int) -> bool:
    """Whether pos is worth playing: not an own eye, not suicide, no superko."""
    nbs = p.rules.geo.neighbours[pos]
    colour = p.colour
    if not (p.used >> pos) & 1:
        for nb in nbs:
            if colour[nb] == EMPTY:
                return True
    me = p.to_move
    keys = p.rules.zob.keys
    group, libs = p.group, p.libs
    h = p.hash ^ keys[3 * pos + EMPTY] ^ keys[3 * pos + me]
    left: dict[int, int] = {}               # group -> liberties after the move
    empties = opps = weak_opps = neighs = weak_neighs = 0
    for nb in nbs:
        c = colour[nb]
        if c == EMPTY:
            empties += 1
            continue
        g = group[nb]
        n = left.get(g)
        if n is None:                       # first time this group is touched
            if c == me:
                neighs += 1
            else:
                opps += 1
            n = libs[g]
        left[g] = n = n - 1
        if n == 0:                          # the move takes its last liberty
            if c == me:
                weak_neighs += 1
            else:
                weak_opps += 1
                h ^= _group_key(p, nb, g)   # the position after the capture
    repeat = h in p.seen
    strong_neighs = neighs - weak_neighs
    strong_opps = opps - weak_opps
    return not repeat and bool(
        empties or weak_opps or (strong_neighs and (strong_opps or weak_neighs)))


def useful_moves(p: Position) -> tuple[int, ...]:
    return tuple(pos for pos in p.empties if is_useful(p, pos))


def random_move(p: Position, rng: random.Random) -> tuple[int, Position]:
    """A random useful move (or PASS), and p with its empty list reordered.

    Rejected points are moved to the end of the empty list, as in the
    original, so later draws (and the RNG stream) match it."""
    empties = p.empties
    choices = len(empties)
    scratch = None
    while choices:
        i = int(rng.random() * choices)
        pos = empties[i] if scratch is None else scratch[i]
        if is_useful(p, pos):
            break
        if scratch is None:
            scratch = list(empties)
        choices -= 1
        scratch[i], scratch[choices] = scratch[choices], pos
    else:
        pos = PASS
    if scratch is not None:
        p = copy.replace(p, empties=tuple(scratch))
    return pos, p


def random_play(p: Position, rng: random.Random) -> Position:
    pos, p = random_move(p, rng)
    return play(p, pos)


def area_score(p: Position, colour: int, komi: float) -> float:
    count = komi if colour == WHITE else 0
    board = p.colour
    nbs = p.rules.geo.neighbours
    for pos, c in enumerate(board):
        if c == colour:
            count += 1
        elif c == EMPTY:
            if all(board[nb] == colour for nb in nbs[pos]):
                count += 1
    return count


# ------------------------------------------------------------- search tree

@grail.record
class Node:
    """One UCT tree node: the move into it and its results so far."""
    pos: int
    wins: int
    losses: int
    unexplored: tuple[int, ...]   # useful moves not yet tried, in draw order
    best: int                     # point of the best child by UCB, or PASS
    children: tuple               # per point: child Node or None


def new_node(pos: int, unexplored: tuple[int, ...], npoints: int) -> Node:
    return Node(pos, 0, 0, unexplored, PASS, npoints * (None,))


def ucb(node: Node, parent_visits: int) -> float:
    winrate = node.wins / float(node.wins + node.losses)
    if not parent_visits:
        return winrate
    visits = node.wins + node.losses
    return winrate + math.sqrt(math.log(parent_visits) / (5 * visits))


def best_child(children: tuple, parent_visits: int) -> int:
    best, best_score = PASS, -1
    for child in children:
        if child is not None:
            s = ucb(child, parent_visits)
            if s > best_score:
                best, best_score = child.pos, s
    return best


def iterate(root: Node, start: Position, rng: random.Random, cfg: Config) -> Node:
    """One MCTS iteration from the position `start`; returns the new root."""
    npoints = len(start.colour)
    p = start
    path = []                                     # updated copies, root first
    node = root
    while True:                                   # selection + expansion
        u = node.unexplored
        if u:                                     # a random untried move
            i = rng.randrange(len(u))
            pos = u[i]
            node = copy.replace(node, unexplored=u[:i] + u[-1:] + u[i + 1:-1]
                                if i < len(u) - 1 else u[:-1])
        else:
            pos = node.best
        path.append(node)
        if pos == PASS:
            break
        p = play(p, pos)
        child = node.children[pos]
        if child is None:
            path.append(new_node(pos, useful_moves(p), npoints))
            break
        node = child
    for _ in range(cfg.max_moves):                # random playout
        if finished(p):
            break
        p = random_play(p, rng)
    black_wins = area_score(p, BLACK, cfg.komi) >= area_score(p, WHITE, cfg.komi)
    # Back-propagation, rebuilding the path from the leaf up.  The side that
    # moved into the node at depth d is the root's side to move for odd d.
    child = None
    for depth in range(len(path) - 1, -1, -1):
        node = path[depth]
        moved = start.to_move if depth % 2 else (
            WHITE if start.to_move == BLACK else BLACK)
        won = black_wins == (moved == BLACK)
        wins, losses = node.wins + won, node.losses + (not won)
        if child is None:
            node = Node(node.pos, wins, losses, node.unexplored, node.best,
                        node.children)
        else:
            c = child.pos
            children = node.children[:c] + (child,) + node.children[c + 1:]
            node = Node(node.pos, wins, losses, node.unexplored,
                        best_child(children, wins + losses), children)
        child = node
    return child


def search(rules: Rules, history: tuple[int, ...],
           root_moves: tuple[int, ...], games: int, cfg: Config,
           rng: random.Random) -> tuple[MoveStats, ...]:
    """Run `games` iterations on a private tree; return root child stats."""
    start = new_position(rules)
    for pos in history:
        start = play(start, pos)
    root = new_node(PASS, root_moves, len(start.colour))
    for _ in range(games):
        root = iterate(root, start, rng, cfg)
    return tuple(MoveStats(c.pos, c.wins, c.losses)
                 for c in root.children if c is not None)


def most_visited(stats: tuple[MoveStats, ...]) -> int:
    best, best_visits = PASS, -1
    for s in stats:                               # first maximum, in point order
        if s.wins + s.losses > best_visits:
            best, best_visits = s.pos, s.wins + s.losses
    return best


def merge_stats(parts: list[tuple[MoveStats, ...]]) -> tuple[MoveStats, ...]:
    total: dict[int, tuple[int, int]] = {}
    for part in parts:
        for s in part:
            w, l = total.get(s.pos, (0, 0))
            total[s.pos] = (w + s.wins, l + s.losses)
    return tuple(MoveStats(p, w, l) for p, (w, l) in sorted(total.items()))


def computer_move(history: tuple[int, ...], *, seed: int = 1, threads: int = 8,
                  mode: str = "exact", cfg: Config = Config(), size: int = 9
                  ) -> tuple[int, tuple[MoveStats, ...]]:
    """Choose a move for the position reached by `history`.

    Returns (move, root statistics).  mode="exact" reproduces the original
    (threads is ignored); mode="root-parallel" splits the games over threads.
    """
    geo = make_geometry(size)
    rng = random.Random(seed)
    # The game position, with its own Zobrist table, as in the original.
    game = new_position(Rules(geo, make_zobrist(geo, rng)))
    for pos in history:
        game = play(game, pos)
    move, game = random_move(game, rng)
    if move == PASS:
        return PASS, ()
    root_moves = useful_moves(game)
    search_rules = Rules(geo, make_zobrist(geo, rng))  # the original's search board table

    if mode == "exact":
        stats = search(search_rules, history, root_moves, cfg.games, cfg, rng)
        return most_visited(stats), stats
    if mode != "root-parallel":
        raise ValueError(mode)
    # Root parallelism: independent trees, games split as evenly as possible,
    # one RNG stream per worker seeded from (seed, worker).
    n = max(1, threads)
    shares = [cfg.games // n + (i < cfg.games % n) for i in range(n)]
    rngs = [random.Random(f"{seed}/{i}") for i in range(n)]

    def worker(i: int) -> tuple[MoveStats, ...]:
        return search(search_rules, history, root_moves, shares[i], cfg, rngs[i])

    if n == 1:
        parts = [worker(0)]
    else:
        with ThreadPoolExecutor(max_workers=n) as pool:
            parts = list(pool.map(worker, range(n)))
    stats = merge_stats(parts)
    return most_visited(stats), stats


def to_xy(pos: int, size: int = 9) -> tuple[int, int]:
    y, x = divmod(pos, size)
    return x, y


def main(*, threads: int = 8, mode: str = "exact") -> int:
    """The benchmark: Black's first move on an empty board, seed 1."""
    move, _ = computer_move((), seed=1, threads=threads, mode=mode)
    return move


if __name__ == "__main__":
    m = main()
    print("exact move", m, to_xy(m))
    m = main(mode="root-parallel")
    print("root-parallel (8 trees) move", m, to_xy(m))
