"""Go (9x9) Monte Carlo tree search, Grail style: hybrid control.

The earlier conversion, kept as a comparison control for idiomatic.py: value
records for shared inputs, but the board and search tree are mutable
thread-confined structs.  Same algorithm, RNG use and results as idiomatic.py.

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
    ints) and `Config` (games, komi, move cap).  They are built once and read
    by every playout, on any thread, without synchronisation.
  * The board is the textbook case for LOCAL mutable state: one game mutates
    it ~100 times and nothing outside the game ever sees it.  It is a
    `Board` holding plain lists (struct-of-arrays, indexed by point), owned
    by the one thread running the game and dropped afterwards.  `Board` is a
    plain slotted class used as a struct (no methods); all behaviour is in
    module functions.  (Grail has no "mutable local record"; see the notes.)
  * The search tree is likewise thread-confined working state: a `Node`
    struct with child slots.  Only its summary leaves the search, as a tuple of
    `MoveStats` value records.
  * No generic functions: there is no polymorphism anywhere in this program.
  * Randomness is an explicit random.Random passed in, not the global module.

Determinism and parallelism
  * mode="exact" (threads=1): one tree, one RNG seeded like the original and
    consumed in the same order (including the separate Zobrist table the
    original draws for its search board).  It returns the original's move,
    and the per-move visit counts match exactly.
  * mode="root-parallel" (threads=N): root parallelisation.  The GAMES
    iterations are split across N independent trees, worker i using its own
    RNG seeded from (seed, i); the root visit counts are summed in worker
    order and the most visited move wins.  This CHANGES THE RESULT relative to
    the original (different random streams, N smaller trees instead of one
    big one), but it is deterministic for a given (seed, N) and independent of
    thread scheduling.  Leaf parallelism (one tree, N playouts per leaf) also
    changes the result and needs the tree behind a lock or atom, so we do not
    use it.
"""

from __future__ import annotations

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


# --------------------------------------------------- thread-local game board

class Board:
    """Mutable state of one game.  Confined to the thread that made it.

    Stones are grouped with a union-find over points (`ref`), and the root of
    each group holds its pseudo-liberty count (`ledges`, liberties counted
    once per adjacent stone).  `stamp` is a counter used to mark visits
    within a single move or legality test.
    """
    __slots__ = ("geo", "zob", "colour", "ref", "ledges", "used", "seen",
                 "removed", "temp_ledges", "empties", "empty_pos", "hash",
                 "history_hashes", "to_move", "finished", "last_move",
                 "history", "stamp")


def new_board(geo: Geometry, zob: Zobrist) -> Board:
    n = geo.size * geo.size
    b = Board()
    b.geo, b.zob = geo, zob
    b.colour = [EMPTY] * n
    b.ref = list(range(n))
    b.ledges = [0] * n
    b.used = [False] * n
    b.seen = [0] * n            # stamp of the last legality test that counted this group
    b.removed = [0] * n         # stamp of the last (real or trial) capture of this point
    b.temp_ledges = [0] * n
    b.empties = list(range(n))  # unordered set of empty points, plus its index
    b.empty_pos = list(range(n))
    h = 0
    for pos in range(n):
        h ^= zob.keys[3 * pos + EMPTY]
    b.hash = h
    b.history_hashes = {h}
    b.to_move = BLACK
    b.finished = False
    b.last_move = -2
    b.history = []
    b.stamp = 0
    return b


def _find(b: Board, pos: int) -> int:
    """Root of pos's group, with path compression."""
    ref = b.ref
    root = pos
    while ref[root] != root:
        root = ref[root]
    while ref[pos] != root:
        ref[pos], pos = root, ref[pos]
    return root


def _set_hash(b: Board, pos: int, old: int, new: int) -> None:
    keys = b.zob.keys
    b.hash ^= keys[3 * pos + old] ^ keys[3 * pos + new]


def _remove_group(b: Board, pos: int, root: int, really: bool) -> None:
    """Take the group `root` off the board, starting at stone `pos`.

    really=False is the trial capture used by is_useful: it changes only the
    hash, so the superko test sees the position after the capture.
    """
    _set_hash(b, pos, b.colour[pos], EMPTY)
    b.removed[pos] = b.stamp
    if really:
        b.colour[pos] = EMPTY
        b.empty_pos[pos] = len(b.empties)
        b.empties.append(pos)
    for nb in b.geo.neighbours[pos]:
        if b.colour[nb] != EMPTY and b.removed[nb] != b.stamp:
            nb_root = _find(b, nb)
            if nb_root == root:
                _remove_group(b, nb, root, really)
            elif really:
                b.ledges[nb_root] += 1      # a neighbouring group gains a liberty


def _place(b: Board, pos: int, colour: int) -> None:
    b.stamp += 1
    _set_hash(b, pos, EMPTY, colour)
    b.colour[pos] = colour
    b.ref[pos] = pos
    b.used[pos] = True
    ledges = 0
    for nb in b.geo.neighbours[pos]:
        nb_colour = b.colour[nb]
        if nb_colour == EMPTY:
            ledges += 1
            continue
        nb_root = _find(b, nb)
        if nb_colour == colour:
            if nb_root != pos:              # merge a friendly group into ours
                ledges += b.ledges[nb_root]
                b.ref[nb_root] = pos
            ledges -= 1
        else:
            b.ledges[nb_root] -= 1
            if b.ledges[nb_root] == 0:
                b.ledges[pos] = ledges      # visible to the capture's liberty updates
                _remove_group(b, nb, nb_root, True)
                ledges = b.ledges[pos]
    b.ledges[pos] = ledges
    b.history_hashes.add(b.hash)


def play(b: Board, pos: int) -> None:
    """Play `pos` (or PASS) for the side to move."""
    if pos != PASS:
        _place(b, pos, b.to_move)
        last = b.empties.pop()              # remove pos from the empty set
        if last != pos:
            i = b.empty_pos[pos]
            b.empties[i] = last
            b.empty_pos[last] = i
    elif b.last_move == PASS:
        b.finished = True
    b.to_move = WHITE if b.to_move == BLACK else BLACK
    b.last_move = pos
    b.history.append(pos)


def is_useful(b: Board, pos: int) -> bool:
    """Whether pos is worth playing: not an own eye, not suicide, no superko."""
    b.stamp += 1
    nbs = b.geo.neighbours[pos]
    colour = b.colour
    if not b.used[pos]:
        for nb in nbs:
            if colour[nb] == EMPTY:
                return True
    me = b.to_move
    old_hash = b.hash
    _set_hash(b, pos, EMPTY, me)
    empties = opps = weak_opps = neighs = weak_neighs = 0
    for nb in nbs:
        nb_colour = colour[nb]
        if nb_colour == EMPTY:
            empties += 1
            continue
        root = _find(b, nb)
        if b.seen[root] != b.stamp:         # first time this group is touched
            if nb_colour == me:
                neighs += 1
            else:
                opps += 1
            b.seen[root] = b.stamp
            b.temp_ledges[root] = b.ledges[root]
        b.temp_ledges[root] -= 1
        if b.temp_ledges[root] == 0:        # the move takes its last liberty
            if nb_colour == me:
                weak_neighs += 1
            else:
                weak_opps += 1
                _remove_group(b, root, root, False)
    repeat = b.hash in b.history_hashes
    b.hash = old_hash
    strong_neighs = neighs - weak_neighs
    strong_opps = opps - weak_opps
    return not repeat and bool(
        empties or weak_opps or (strong_neighs and (strong_opps or weak_neighs)))


def useful_moves(b: Board) -> list[int]:
    return [pos for pos in b.empties if is_useful(b, pos)]


def random_move(b: Board, rng: random.Random) -> int:
    """A random useful move, or PASS.  Rejected points are moved to the end of
    the empty list (as in the original, so the RNG stream is reproduced)."""
    empties, empty_pos = b.empties, b.empty_pos
    choices = len(empties)
    while choices:
        i = int(rng.random() * choices)
        pos = empties[i]
        if is_useful(b, pos):
            return pos
        choices -= 1
        other = empties[choices]
        empties[i], empty_pos[other] = other, i
        empties[choices], empty_pos[pos] = pos, choices
    return PASS


def area_score(b: Board, colour: int, komi: float) -> float:
    count = komi if colour == WHITE else 0
    for pos, c in enumerate(b.colour):
        if c == colour:
            count += 1
        elif c == EMPTY:
            nbs = b.geo.neighbours[pos]
            if all(b.colour[nb] == colour for nb in nbs):
                count += 1
    return count


# ------------------------------------------------- thread-local search tree

class Node:
    """One UCT tree node.  Confined to the thread running its search."""
    __slots__ = ("pos", "wins", "losses", "parent", "children", "best",
                 "unexplored")


def new_node(pos: int, parent: Node | None, unexplored: list[int], npoints: int) -> Node:
    node = Node()
    node.pos, node.parent, node.unexplored = pos, parent, unexplored
    node.wins = node.losses = 0
    node.children = [None] * npoints
    node.best = None
    return node


def ucb(node: Node) -> float:
    winrate = node.wins / float(node.wins + node.losses)
    parent_visits = node.parent.wins + node.parent.losses
    if not parent_visits:
        return winrate
    visits = node.wins + node.losses
    return winrate + math.sqrt(math.log(parent_visits) / (5 * visits))


def best_child(node: Node) -> Node | None:
    best, best_score = None, -1
    for child in node.children:
        if child is not None:
            s = ucb(child)
            if s > best_score:
                best, best_score = child, s
    return best


def select(node: Node, rng: random.Random) -> int:
    """Next move from node: a random unexplored one, else the UCB best."""
    if node.unexplored:
        u = node.unexplored
        i = rng.randrange(len(u))
        pos = u[i]
        u[i] = u[-1]
        u.pop()
        return pos
    if node.best is not None:
        return node.best.pos
    return PASS


def iterate(root: Node, b: Board, rng: random.Random, cfg: Config) -> None:
    """One MCTS iteration on board b (already at the root position)."""
    npoints = len(b.colour)
    root_colour = b.to_move
    node, path = root, [root]
    while True:                                   # selection + expansion
        pos = select(node, rng)
        if pos == PASS:
            break
        play(b, pos)
        child = node.children[pos]
        if child is None:
            child = node.children[pos] = new_node(pos, node, useful_moves(b), npoints)
            path.append(child)
            break
        path.append(child)
        node = child
    for _ in range(cfg.max_moves):                # random playout
        if b.finished:
            break
        play(b, random_move(b, rng))
    black_wins = area_score(b, BLACK, cfg.komi) >= area_score(b, WHITE, cfg.komi)
    colour = root_colour                          # back-propagation
    for node in path:
        colour = WHITE if colour == BLACK else BLACK   # the side that moved into node
        if black_wins == (colour == BLACK):
            node.wins += 1
        else:
            node.losses += 1
        if node.parent is not None:
            node.parent.best = best_child(node.parent)


def search(geo: Geometry, zob: Zobrist, history: tuple[int, ...],
           root_moves: tuple[int, ...], games: int, cfg: Config,
           rng: random.Random) -> tuple[MoveStats, ...]:
    """Run `games` iterations on a private tree; return root child stats."""
    root = new_node(PASS, None, list(root_moves), geo.size * geo.size)
    for _ in range(games):
        b = new_board(geo, zob)
        for pos in history:
            play(b, pos)
        iterate(root, b, rng, cfg)
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
    game = new_board(geo, make_zobrist(geo, rng))
    for pos in history:
        play(game, pos)
    if random_move(game, rng) == PASS:
        return PASS, ()
    root_moves = tuple(useful_moves(game))
    search_zob = make_zobrist(geo, rng)           # the original's search board table

    if mode == "exact":
        stats = search(geo, search_zob, history, root_moves, cfg.games, cfg, rng)
        return most_visited(stats), stats
    if mode != "root-parallel":
        raise ValueError(mode)
    # Root parallelism: independent trees, games split as evenly as possible,
    # one RNG stream per worker seeded from (seed, worker).
    n = max(1, threads)
    shares = [cfg.games // n + (i < cfg.games % n) for i in range(n)]
    rngs = [random.Random(f"{seed}/{i}") for i in range(n)]

    def worker(i: int) -> tuple[MoveStats, ...]:
        return search(geo, search_zob, history, root_moves, shares[i], cfg, rngs[i])

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
