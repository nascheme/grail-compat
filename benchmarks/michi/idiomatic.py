"""michi, Grail style: a Monte Carlo tree search Go engine.

What it computes
----------------
Michi (Petr Baudis, MIT; https://github.com/nascheme/michi) is the
Monte Carlo example of the free-threading guide
(https://py-free-threading.github.io/examples/monte-carlo/).  Its
`tsbenchmark` searches the empty 13x13 board with N_SIMS=1400 MCTS
iterations and prints the chosen move.  Each iteration descends the tree by
RAVE urgency (expanding a leaf with heuristic priors after 8 visits), plays
a heuristic Monte Carlo playout to the end of the game (capture and 3x3
pattern suggestions near the last two moves, self-atari rejection), scores
it by area, and backs the result up the path, with AMAF statistics for the
children of every node on it.  Leaf priors also use ~1M large-scale
patterns from the `patterns/` submodule.  `mcbenchmark` is the playouts
alone.

Structure
---------
* Position is a value record: the board is a string, as in the original
  (which already used a frozen dataclass; its `last` annotation said
  Position but held an int).  Moves, passes, scoring and move generation
  are plain functions of a Position.
* Patterns is a record of the shared, read-only tables: the 3x3 pattern
  set (frozenset), the spatial dictionary and the pattern probabilities
  (PMap).  Playouts only read the 3x3 set; the large patterns are used for
  priors when a descent expands a leaf.
* A playout takes a Position and the AMAF map so far (a tuple) and returns
  a Playout record (score, AMAF map, owner map); its working state is local.
* The search tree is a value: `Node` records whose children tuples are
  fixed once a leaf is expanded, so a node is addressed by its path of
  child indices from the root.  A descent returns a new root (its visit
  counts and any expansion, path-copied) and the path; a playout result is
  applied to whichever root is current by rebuilding that path, with AMAF
  updates on the children of each node on it.  Old roots stay valid.  The previous design (mutable
  `Node` structs, same algorithm) is kept as hybrid.py.
* No generic functions: nothing is polymorphic.  Move suggestions carry a
  kind constant and the capturing group's point instead of the original's
  "capture 123" strings that were parsed back with int().
* Randomness is an explicit random.Random, never a module global.

Parallelism
-----------
The original uses leaf parallelism: its search thread owns the tree,
descends (with "virtual loss" visits) and hands positions to a pool of
playout workers, polling them with short timed waits.  Here the tree is a
value, so it needs no owner: the current root is an Atom[Node], and each
of `threads` searcher tasks in a grail.Pool scope runs whole iterations.
A descent is computed from the current root and published with
compare_and_set, and redone from the newer root if another task got there
first; a playout's result is backed up with swap(tree_update, ...), which
applies it to whatever root is current.  Visit counts from a descent are
the virtual loss that steers concurrent descents apart, as in the
original.  There is no search thread to wait for results, and no result
is dropped: at an early stop the playouts already running still finish
and are backed up.

* threads=1 is the exact mode: descend, playout, update in strict order
  with one RNG.  It reproduces the original run the same way (serially,
  with RND seeded the same): identical best move and identical statistics
  in every tree node.
* threads=N gives playout i its own RNG seeded from (seed, i), so each
  playout's result does not depend on which thread ran it (descent i
  breaks urgency ties with another RNG of its own).  Which results
  have reached the tree at each descent still depends on timing, as in the
  original, so the search is not deterministic; the original's isn't
  either (its own process-pool mode included).

The original's random state with threads: `setup_random()` rebinds the
module global RND, and the thread pool's initializer calls it in every
worker.  So all workers and the main thread share whichever Random object
was bound last, and the main thread's stream is reset when the pool
starts.  It is not memory-unsafe (Random's methods are atomic in the
free-threaded build), but the "one generator per thread" the guide
describes does not happen: every call contends on one shared object, and
no seed makes the run repeatable.

Left out: the GTP and text UIs, `print_pos`/`dump_subtree` reporting, the
`disp` debug traces, and resignation.  The search keeps the original's
early-stop rule and owner map (averaged over the playouts run; the
original also counts those whose results it dropped at the early stop).
"""

from __future__ import annotations

import copy
import io
import math
import operator
import os
import random
import re
import shutil
import subprocess
import sys
from functools import reduce
from pathlib import Path

import grail

# Board: an (N+1)*(N+2) string, '.' empty, 'X' to play, 'x' the other
# player, whitespace off-board.  Coordinates are indices into it.
N = 13
W = N + 2
EMPTY_BOARD = "\n".join([(N + 1) * ' '] + N * [' ' + N * '.'] + [(N + 2) * ' '])
MAX_GAME_LEN = N * N * 3

N_SIMS = 1400
RAVE_EQUIV = 3500
EXPAND_VISITS = 8
PRIOR_EVEN = 10             # should be even; 0.5 prior
PRIOR_SELFATARI = 10        # negative prior
PRIOR_CAPTURE_ONE = 15
PRIOR_CAPTURE_MANY = 30
PRIOR_PAT3 = 10
PRIOR_LARGEPATTERN = 100    # most moves have relatively small probability
PRIOR_CFG = (24, 22, 8)     # priors for moves in cfg distance 1, 2, 3
PRIOR_EMPTYAREA = 10
PROB_SSAREJECT = 0.9        # reject a suggested self-atari in playouts
PROB_RSAREJECT = 0.5        # reject a random self-atari (lower: allow nakade)
FASTPLAY20_THRES = 0.8      # at 20% of playouts winrate > this: stop
FASTPLAY5_THRES = 0.95      # at 5% of playouts winrate > this: stop

CAPTURE, PAT3, RANDOM = "capture", "pat3", "random"   # suggestion kinds

PAT3_SRC = (   # 3x3 playout patterns; X,O are colors, x,o their inverses
    ("XOX", "...", "???"),  # hane: enclosing hane
    ("XO.", "...", "?.?"),  # hane: non-cutting hane
    ("XO?", "X..", "x.?"),  # hane: magari
    (".O.", "X..", "..."),  # katatsuke or diagonal attachment
    ("XO?", "O.o", "?o?"),  # cut1: unprotected cut
    ("XO?", "O.X", "???"),  # cut1: peeped cut
    ("?X?", "O.O", "ooo"),  # cut2
    ("OX?", "o.O", "???"),  # cut keima
    ("X.?", "O.?", "   "),  # side: chase
    ("OX?", "X.O", "   "),  # side: block side cut
    ("?X?", "x.O", "   "),  # side: block side connection
    ("?XO", "x.x", "   "),  # side: sagari
    ("?OX", "X.O", "   "),  # side: cut
)


PAT_GRIDCULAR_SEQ = (   # (y, x) offsets of progressively wider gridcular
                        # diameters; d=1,2 are one shell
    ((0, 0), (0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, 1), (1, -1),
     (-1, -1)),
    ((0, 2), (0, -2), (2, 0), (-2, 0)),
    ((1, 2), (-1, 2), (1, -2), (-1, -2), (2, 1), (-2, 1), (2, -1), (-2, -1)),
    ((0, 3), (0, -3), (2, 2), (-2, 2), (2, -2), (-2, -2), (3, 0), (-3, 0)),
    ((1, 3), (-1, 3), (1, -3), (-1, -3), (3, 1), (-3, 1), (3, -1), (-3, -1)),
    ((0, 4), (0, -4), (2, 3), (-2, 3), (2, -3), (-2, -3), (3, 2), (-3, 2),
     (3, -2), (-3, -2), (4, 0), (-4, 0)),
    ((1, 4), (-1, 4), (1, -4), (-1, -4), (3, 3), (-3, 3), (3, -3), (-3, -3),
     (4, 1), (-4, 1), (4, -1), (-4, -1)),
    ((0, 5), (0, -5), (2, 4), (-2, 4), (2, -4), (-2, -4), (4, 2), (-4, 2),
     (4, -2), (-4, -2), (5, 0), (-5, 0)),
    ((1, 5), (-1, 5), (1, -5), (-1, -5), (3, 4), (-3, 4), (3, -4), (-3, -4),
     (4, 3), (-4, 3), (4, -3), (-4, -3), (5, 1), (-5, 1), (5, -1), (-5, -1)),
    ((0, 6), (0, -6), (2, 5), (-2, 5), (2, -5), (-2, -5), (4, 4), (-4, 4),
     (4, -4), (-4, -4), (5, 2), (-5, 2), (5, -2), (-5, -2), (6, 0), (-6, 0)),
    ((1, 6), (-1, 6), (1, -6), (-1, -6), (3, 5), (-3, 5), (3, -5), (-3, -5),
     (5, 3), (-5, 3), (5, -3), (-5, -3), (6, 1), (-6, 1), (6, -1), (-6, -1)),
    ((0, 7), (0, -7), (2, 6), (-2, 6), (2, -6), (-2, -6), (4, 5), (-4, 5),
     (4, -5), (-4, -5), (5, 4), (-5, 4), (5, -4), (-5, -4), (6, 2), (-6, 2),
     (6, -2), (-6, -2), (7, 0), (-7, 0)),
)


# ------------------------------------------------------------------ data

@grail.record
class Position:
    """A Go position under simple Chinese rules; n moves played so far."""
    board: str
    cap: tuple[int, int]     # stones captured by (to-play, other)
    n: int
    ko: int | None
    last: int | None
    last2: int | None
    komi: float


@grail.record
class Probs:
    """Probabilities of taking each heuristic's suggestions."""
    capture: float
    pat3: float


PLAYOUT_PROBS = Probs(capture=0.9, pat3=0.95)
ALWAYS = Probs(capture=1.0, pat3=1.0)


@grail.record
class Patterns:
    """Read-only pattern tables, shared by every thread."""
    pat3: frozenset            # 3x3 neighbourhood strings
    spat: grail.PMap[int, int]    # hash(gridcular neighbourhood) -> spatial id
    large: grail.PMap[int, float]  # spatial id -> probability


@grail.record
class Playout:
    score: float               # for the player to move at the start
    amaf: tuple[int, ...]      # who played first at each point (+1 black)
    owner: tuple[int, ...]     # final owner of each point (+1 black)


@grail.record
class Node:
    """MCTS tree node.

    v/w are visits/wins for the player who just moved; pv/pw are priors;
    av/aw are AMAF (RAVE) statistics; children is None for a leaf.
    """
    pos: Position
    v: int
    w: int
    pv: int | float
    pw: float
    av: int
    aw: int
    children: tuple | None


def new_node(pos: Position, pv: int | float = PRIOR_EVEN,
             pw: float = PRIOR_EVEN / 2) -> Node:
    return Node(pos, 0, 0, pv, pw, 0, 0, None)


def empty_position() -> Position:
    return Position(board=EMPTY_BOARD, cap=(0, 0), n=0, ko=None,
                    last=None, last2=None, komi=6.5)


# ------------------------------------------------------- board routines

def neighbors(c):
    return [c - 1, c + 1, c - W, c + W]


def diag_neighbors(c):
    return [c - W - 1, c - W + 1, c + W - 1, c + W + 1]


def board_put(board, c, p):
    return board[:c] + p + board[c + 1:]


def floodfill(board, c):
    """Replace the same-colour area containing c with '#'."""
    byteboard = bytearray(board, 'ascii')
    p = byteboard[c]
    byteboard[c] = ord('#')
    fringe = [c]
    while fringe:
        c = fringe.pop()
        for d in neighbors(c):
            if byteboard[d] == p:
                byteboard[d] = ord('#')
                fringe.append(d)
    return byteboard.decode('ascii')


def _contact_re(p):
    rp = '\\.' if p == '.' else p
    return re.compile('|'.join([
        '#' + rp,                       # p at right
        rp + '#',                       # p at left
        '#' + '.' * (W - 1) + rp,       # p below
        rp + '.' * (W - 1) + '#',       # p above
    ]), flags=re.DOTALL)


CONTACT_RES = {p: _contact_re(p) for p in ('.', 'x', 'X')}


def contact(board, p):
    """A point of colour p adjacent to a '#' point, or None."""
    m = CONTACT_RES[p].search(board)
    if not m:
        return None
    return m.start() if m.group(0)[0] == p else m.end() - 1


def is_eyeish(board, c):
    """The colour of the single-colour diamond around c, or None."""
    eyecolor = othercolor = None
    for d in neighbors(c):
        if board[d].isspace():
            continue
        if board[d] == '.':
            return None
        if eyecolor is None:
            eyecolor = board[d]
            othercolor = eyecolor.swapcase()
        elif board[d] == othercolor:
            return None
    return eyecolor


def is_eye(board, c):
    """The colour of a (not falsified) eye at c, or None."""
    eyecolor = is_eyeish(board, c)
    if eyecolor is None:
        return None
    falsecolor = eyecolor.swapcase()
    false_count = 0
    at_edge = False
    for d in diag_neighbors(c):
        if board[d].isspace():
            at_edge = True
        elif board[d] == falsecolor:
            false_count += 1
    if at_edge:
        false_count += 1
    if false_count >= 2:
        return None
    return eyecolor


# ------------------------------------------------------------ positions

def play(pos: Position, c: int) -> Position | None:
    """Play X at c; the new position, or None if the move is illegal."""
    if c == pos.ko:
        return None
    in_enemy_eye = is_eyeish(pos.board, c) == 'x'

    board = board_put(pos.board, c, 'X')
    cap_x = pos.cap[0]
    singlecaps = []
    for d in neighbors(c):
        if board[d] != 'x':
            continue
        fboard = floodfill(board, d)      # the adjacent group as '#'
        if contact(fboard, '.') is not None:
            continue                      # it has liberties
        capcount = fboard.count('#')
        if capcount == 1:
            singlecaps.append(d)
        cap_x += capcount
        board = fboard.replace('#', '.')  # capture it
    ko = singlecaps[0] if in_enemy_eye and len(singlecaps) == 1 else None
    if contact(floodfill(board, c), '.') is None:
        return None                       # suicide
    return Position(board=board.swapcase(), cap=(pos.cap[1], cap_x),
                    n=pos.n + 1, ko=ko, last=c, last2=pos.last, komi=pos.komi)


def pass_move(pos: Position) -> Position:
    return Position(board=pos.board.swapcase(), cap=(pos.cap[1], pos.cap[0]),
                    n=pos.n + 1, ko=None, last=None, last2=pos.last,
                    komi=pos.komi)


def moves(pos: Position, i0: int):
    """Candidate moves starting at index i0 and wrapping around; includes
    suicides, excludes filling the mover's own true eyes."""
    board = pos.board
    i = i0 - 1
    passes = 0
    while True:
        i = board.find('.', i + 1)
        if passes > 0 and (i == -1 or i >= i0):
            break
        elif i == -1:
            i = 0
            passes += 1
            continue
        if is_eye(board, i) == 'X':
            continue
        yield i


def last_moves_neighbors(pos: Position, rng: random.Random) -> list[int]:
    """The last two moves and their neighbours, shuffled, last move first."""
    clist = []
    for c in (pos.last, pos.last2):
        if c is None:
            continue
        dlist = [c] + neighbors(c) + diag_neighbors(c)
        rng.shuffle(dlist)
        clist += [d for d in dlist if d not in clist]
    return clist


def score(pos: Position) -> tuple[float, tuple[int, ...]]:
    """Area score for the player to move, assuming dead stones are gone;
    and the owner of each point (+1 black, -1 white)."""
    board = pos.board
    i = 0
    while True:
        i = pos.board.find('.', i + 1)
        if i == -1:
            break
        fboard = floodfill(board, i)
        touches_X = contact(fboard, 'X') is not None
        touches_x = contact(fboard, 'x') is not None
        if touches_X and not touches_x:
            board = fboard.replace('#', 'X')
        elif touches_x and not touches_X:
            board = fboard.replace('#', 'x')
        else:
            board = fboard.replace('#', ':')       # seki, rare
    komi = pos.komi if pos.n % 2 == 1 else -pos.komi
    sign = 1 if pos.n % 2 == 0 else -1
    owner = tuple((1 if p == 'X' else -1 if p == 'x' else 0) * sign
                  for p in board[:W * W])
    return board.count('X') - board.count('x') + komi, owner


# ------------------------------------------------------------ heuristics

def line_height(c):
    """The line number above the nearest board edge (0-based)."""
    row, col = divmod(c - (W + 1), W)
    return min(row, col, N - 1 - row, N - 1 - col)


def fix_atari(pos, c, singlept_ok=False, twolib_test=True,
              twolib_edgeonly=False):
    """Atari analysis of the group at c: (in_atari, moves), where moves
    escape or capture it ([] if impossible).  (False, [...]) means a ladder
    capture is available.  See the original for the full contract; this
    is a straight port."""

    def read_ladder_attack(pos, c, l1, l2):
        for l in (l1, l2):
            pos_l = play(pos, l)
            if pos_l is None:
                continue
            is_atari, atari_escape = fix_atari(pos_l, c, twolib_test=False)
            if is_atari and not atari_escape:
                return l
        return None

    fboard = floodfill(pos.board, c)
    group_size = fboard.count('#')
    if singlept_ok and group_size == 1:
        return (False, [])
    l = contact(fboard, '.')
    fboard = board_put(fboard, l, 'L')
    l2 = contact(fboard, '.')
    if l2 is not None:
        if (twolib_test and group_size > 1
                and (not twolib_edgeonly
                     or line_height(l) == 0 and line_height(l2) == 0)
                and contact(board_put(fboard, l2, 'L'), '.') is None):
            ladder_attack = read_ladder_attack(pos, c, l, l2)
            if ladder_attack:
                return (False, [ladder_attack])
        return (False, [])

    if pos.board[c] == 'x':
        return (True, [l])

    solutions = []
    ccboard = fboard
    while True:                           # counter-capture a neighbour?
        othergroup = contact(ccboard, 'x')
        if othergroup is None:
            break
        a, ccls = fix_atari(pos, othergroup, twolib_test=False)
        if a and ccls:
            solutions += ccls
        ccboard = board_put(ccboard, othergroup, '%')

    escpos = play(pos, l)                 # escape by extending?
    if escpos is None:
        return (True, solutions)
    fboard = floodfill(escpos.board, l)
    l_new = contact(fboard, '.')
    fboard = board_put(fboard, l_new, 'L')
    l_new_2 = contact(fboard, '.')
    if l_new_2 is not None:
        if solutions or not (
                contact(board_put(fboard, l_new_2, 'L'), '.') is None
                and read_ladder_attack(escpos, l, l_new, l_new_2) is not None):
            solutions.append(l)
    return (True, solutions)


def cfg_distances(board, c):
    """Common-fate-graph distance of every point from c (-1: not reached)."""
    cfg_map = W * W * [-1]
    cfg_map[c] = 0
    fringe = [c]
    while fringe:
        c = fringe.pop()
        for d in neighbors(c):
            if board[d].isspace() or 0 <= cfg_map[d] <= cfg_map[c]:
                continue
            cfg_before = cfg_map[d]
            if board[d] != '.' and board[d] == board[c]:
                cfg_map[d] = cfg_map[c]
            else:
                cfg_map[d] = cfg_map[c] + 1
            if cfg_before < 0 or cfg_before > cfg_map[d]:
                fringe.append(d)
    return cfg_map


def empty_area(board, c, dist=3):
    """True if there is no stone within Manhattan distance dist."""
    for d in neighbors(c):
        if board[d] in 'Xx':
            return False
        elif board[d] == '.' and dist > 1 and not empty_area(board, d, dist - 1):
            return False
    return True


# -------------------------------------------------------------- patterns

def pat3_expand(pat):
    """Every neighbourhood string matching a 3x3 pattern, in all
    rotations, reflections and colour swaps."""

    def rot90(p):
        return [p[2][0] + p[1][0] + p[0][0],
                p[2][1] + p[1][1] + p[0][1],
                p[2][2] + p[1][2] + p[0][2]]

    def vertflip(p):
        return [p[2], p[1], p[0]]

    def horizflip(p):
        return [l[::-1] for l in p]

    def swapcolors(p):
        return [l.replace('X', 'Z').replace('x', 'z').replace('O', 'X')
                .replace('o', 'x').replace('Z', 'O').replace('z', 'o')
                for l in p]

    def wildexp(p, c, to):
        i = p.find(c)
        if i == -1:
            return [p]
        return reduce(lambda a, b: a + b,
                      [wildexp(p[:i] + t + p[i + 1:], c, to) for t in to])

    def wildcards(pat):
        return [p
                for p in wildexp(pat, '?', list('.XO '))
                for p in wildexp(p, 'x', list('.O '))
                for p in wildexp(p, 'o', list('.X '))]

    pat = list(pat)
    return [p
            for p in [pat, rot90(pat)]
            for p in [p, vertflip(p)]
            for p in [p, horizflip(p)]
            for p in [p, swapcolors(p)]
            for p in wildcards(''.join(p))]


def neighborhood_33(board, c):
    return (board[c - W - 1:c - W + 2] + board[c - 1:c + 2]
            + board[c + W - 1:c + W + 2]).replace('\n', ' ')


def _read_zst(path: Path):
    """Lines of a zstd-compressed text file."""
    try:
        from compression import zstd          # 3.14+
    except ImportError:
        if not shutil.which('zstd'):
            raise FileNotFoundError("need Python 3.14 or the zstd tool")
        data = subprocess.run(['zstd', '-dc', str(path)], check=True,
                              capture_output=True).stdout
        return io.StringIO(data.decode())
    return io.TextIOWrapper(zstd.open(path), encoding='utf-8')


def load_patterns(directory: str | os.PathLike | None) -> Patterns:
    """The pattern tables; without a directory, only the 3x3 patterns."""
    pat3 = frozenset(p.replace('O', 'x') for src in PAT3_SRC
                     for p in pat3_expand(src))
    spat, large = {}, {}
    if directory is not None:
        directory = Path(directory)
        with _read_zst(directory / 'spat.zst') as f:
            for line in f:
                # 71 6 ..X.X..OO.O..........#X...... 33408f5e ...
                if line.startswith('#'):
                    continue
                fields = line.split()
                hood = fields[2].replace('#', ' ').replace('O', 'x')
                # Keyed by the string's hash, as in the original; a
                # collision maps two neighbourhoods to one id there too.
                spat[hash(hood)] = int(fields[0])
        with _read_zst(directory / 'prob.zst') as f:
            for line in f:
                # 0.004 14 3842 (capture:17 border:0 s:784)
                m = re.search(r's:(\d+)', line)
                if m is not None:
                    large[int(m.groups()[0])] = float(line.split()[0])
    return Patterns(pat3=pat3, spat=grail.PMap(spat), large=grail.PMap(large))


ROTATIONS = (((0, 1), (1, 1)), ((0, 1), (-1, 1)),
             ((0, 1), (1, -1)), ((0, 1), (-1, -1)),
             ((1, 0), (1, 1)), ((1, 0), (-1, 1)),
             ((1, 0), (1, -1)), ((1, 0), (-1, -1)))


def neighborhood_gridcular(board, c):
    """Progressively wider gridcular neighbourhoods of c, in all eight
    rotations."""
    hoods = ['' for _ in ROTATIONS]
    wboard = board.replace('\n', ' ')
    for dseq in PAT_GRIDCULAR_SEQ:
        for ri, r in enumerate(ROTATIONS):
            for o in dseq:
                y, x = divmod(c - (W + 1), W)
                y += o[r[0][0]] * r[1][0]
                x += o[r[0][1]] * r[1][1]
                if 0 <= y < N and 0 <= x < N:
                    hoods[ri] += wboard[(y + 1) * W + x + 1]
                else:
                    hoods[ri] += ' '
            yield hoods[ri]


def large_pattern_probability(pats: Patterns, board, c):
    """Probability of the widest large pattern matching at c, or None."""
    probability = None
    matched_len = 0
    non_matched_len = 0
    for hood in neighborhood_gridcular(board, c):
        sp_i = pats.spat.get(hash(hood))
        prob = pats.large.get(sp_i) if sp_i is not None else None
        if prob is not None:
            probability = prob
            matched_len = len(hood)
        elif matched_len < non_matched_len < len(hood):
            break           # no match at this diameter; wider won't either
        else:
            non_matched_len = len(hood)
    return probability


# ------------------------------------------------------- playout policy

def gen_playout_moves(pos, heuristic_set, rng, pats, probs=ALWAYS,
                      expensive_ok=False):
    """Yield (move, kind, group) in order of preference.  group is the
    point of the group a capture suggestion is about, else None.

    A generator, consumed lazily: the caller's own random draws interleave
    with these, and exact mode depends on that order."""
    board = pos.board
    if rng.random() <= probs.capture:
        suggested = set()
        for c in heuristic_set:
            if board[c] in 'Xx':
                in_atari, ds = fix_atari(pos, c, twolib_edgeonly=not expensive_ok)
                rng.shuffle(ds)
                for d in ds:
                    if d not in suggested:
                        yield (d, CAPTURE, c)
                        suggested.add(d)

    if rng.random() <= probs.pat3:
        suggested = set()
        for c in heuristic_set:
            if (board[c] == '.' and c not in suggested
                    and neighborhood_33(board, c) in pats.pat3):
                yield (c, PAT3, None)
                suggested.add(c)

    x, y = rng.randint(1, N), rng.randint(1, N)
    for c in moves(pos, y * W + x):
        yield (c, RANDOM, None)


def mcplayout(pos: Position, amaf: tuple[int, ...], rng: random.Random,
              pats: Patterns) -> Playout:
    """Play a heuristic random game to the end from pos."""
    amaf_map = list(amaf)
    start_n = pos.n
    passes = 0
    while passes < 2 and pos.n < MAX_GAME_LEN:
        pos2 = None
        for c, kind, _ in gen_playout_moves(
                pos, last_moves_neighbors(pos, rng), rng, pats, PLAYOUT_PROBS):
            pos2 = play(pos, c)
            if pos2 is None:
                continue
            # Reject (usually) a move that turned out to be a self-atari.
            if rng.random() <= (PROB_RSAREJECT if kind == RANDOM
                                else PROB_SSAREJECT):
                in_atari, ds = fix_atari(pos2, c, singlept_ok=True,
                                         twolib_edgeonly=True)
                if ds:
                    pos2 = None
                    continue
            if amaf_map[c] == 0:
                amaf_map[c] = 1 if pos.n % 2 == 0 else -1
            break
        if pos2 is None:
            pos = pass_move(pos)
            passes += 1
            continue
        passes = 0
        pos = pos2

    s, owner = score(pos)
    if start_n % 2 != pos.n % 2:
        s = -s
    return Playout(score=s, amaf=tuple(amaf_map), owner=owner)


def mcbenchmark(n: int, *, seed: int = 0, threads: int = 1) -> float:
    """Average score of n playouts from the empty position.

    threads=1 uses one RNG for all playouts, like the original; otherwise
    playout i uses its own RNG, so the result is the same for any N > 1."""
    pats = PATTERNS_3X3
    start = empty_position()
    blank = W * W * (0,)
    if threads == 1:
        rng = random.Random(seed)
        total = sum(mcplayout(start, blank, rng, pats).score for _ in range(n))
    else:
        with grail.Pool(threads) as pool:
            total = sum(grail.pmap(
                lambda i: mcplayout(start, blank, job_rng(seed, i), pats).score,
                range(n), chunk=1, pool=pool))
    return float(total) / n


def job_rng(seed: int, i: int) -> random.Random:
    return random.Random((seed << 32) | i)


# ------------------------------------------------------------ tree search

def expand(node: Node, rng: random.Random, pats: Patterns) -> Node:
    """node with children added, with heuristic priors."""
    pos = node.pos
    cfg_map = cfg_distances(pos.board, pos.last) if pos.last is not None else None
    moves = []                                # child positions, and their
    pv, pw = [], []                           # priors, built up locally
    index = {}
    for c, kind, group in gen_playout_moves(pos, range(N, (N + 1) * W), rng,
                                            pats, expensive_ok=True):
        pos2 = play(pos, c)
        if pos2 is None:
            continue
        i = index.get(pos2.last)              # suggestions repeat
        if i is None:
            i = index[pos2.last] = len(moves)
            moves.append(pos2)
            pv.append(PRIOR_EVEN)
            pw.append(PRIOR_EVEN / 2)
        if kind == CAPTURE:
            if floodfill(pos.board, group).count('#') > 1:
                pv[i] += PRIOR_CAPTURE_MANY
                pw[i] += PRIOR_CAPTURE_MANY
            else:
                pv[i] += PRIOR_CAPTURE_ONE
                pw[i] += PRIOR_CAPTURE_ONE
        elif kind == PAT3:
            pv[i] += PRIOR_PAT3
            pw[i] += PRIOR_PAT3

    for i, pos2 in enumerate(moves):          # once per move now
        c = pos2.last
        if cfg_map is not None and cfg_map[c] - 1 < len(PRIOR_CFG):
            pv[i] += PRIOR_CFG[cfg_map[c] - 1]
            pw[i] += PRIOR_CFG[cfg_map[c] - 1]
        height = line_height(c)
        if height <= 2 and empty_area(pos.board, c):
            # Nothing near: discourage the 1st and 2nd line, favour the 3rd.
            if height <= 1:
                pv[i] += PRIOR_EMPTYAREA
                pw[i] += 0
            if height == 2:
                pv[i] += PRIOR_EMPTYAREA
                pw[i] += PRIOR_EMPTYAREA
        in_atari, ds = fix_atari(pos2, c, singlept_ok=True)
        if ds:
            pv[i] += PRIOR_SELFATARI
            pw[i] += 0
        patternprob = large_pattern_probability(pats, pos.board, c)
        if patternprob is not None and patternprob > 0.001:
            pattern_prior = math.sqrt(patternprob)
            pv[i] += pattern_prior * PRIOR_LARGEPATTERN
            pw[i] += pattern_prior * PRIOR_LARGEPATTERN

    children = tuple(map(new_node, moves, pv, pw))
    if not children:
        children = (new_node(pass_move(pos)),)
    return copy.replace(node, children=children)


def rave_urgency(node: Node) -> float:
    v = node.v + node.pv
    expectation = float(node.w + node.pw) / v
    if node.av == 0:
        return expectation
    rave_expectation = float(node.aw) / node.av
    beta = node.av / (node.av + v + float(v) * node.av / RAVE_EQUIV)
    return beta * rave_expectation + (1 - beta) * expectation


def winrate(node: Node) -> float:
    return float(node.w) / node.v if node.v > 0 else float('nan')


def best_move(node: Node) -> Node | None:
    """The most simulated child."""
    if node.children is None:
        return None
    return max(node.children, key=lambda child: child.v)


def tree_descend(tree: Node, amaf_map: list[int], rng: random.Random,
                 pats: Patterns) -> tuple[Node, tuple[int, ...]]:
    """Walk from the root to a leaf by urgency.

    Returns the new root, with visits counted along the path ("virtual
    loss" for parallel search) and the leaf expanded if due, and the path
    as child indices."""
    nodes = [copy.replace(tree, v=tree.v + 1)]
    path = []
    passes = 0
    while nodes[-1].children is not None and passes < 2:
        parent = nodes[-1]
        children = parent.children
        order = list(range(len(children)))
        rng.shuffle(order)                    # random among equal urgency
        i = max(order, key=lambda j: rave_urgency(children[j]))
        node = children[i]
        path.append(i)
        if node.pos.last is None:
            passes += 1
        else:
            passes = 0
            if amaf_map[node.pos.last] == 0:
                amaf_map[node.pos.last] = 1 if parent.pos.n % 2 == 0 else -1
        node = copy.replace(node, v=node.v + 1)
        if node.children is None and node.v >= EXPAND_VISITS:
            node = expand(node, rng, pats)
        nodes.append(node)
    node = nodes[-1]
    for parent, i in zip(reversed(nodes[:-1]), reversed(path)):
        children = parent.children
        node = copy.replace(parent, children=children[:i] + (node,) + children[i + 1:])
    return node, tuple(path)


def leaf(tree: Node, path: tuple[int, ...]) -> Node:
    for i in path:
        tree = tree.children[i]
    return tree


def tree_update(tree: Node, path: tuple[int, ...], amaf: tuple[int, ...],
                score: float) -> Node:
    """Back up one playout's result along the path; returns the new root."""
    nodes = [tree]
    for i in path:
        nodes.append(nodes[-1].children[i])
    if len(path) % 2:
        score = -score                        # score is for the leaf's player
    for d, node in enumerate(nodes):          # score for each node, root first
        nodes[d] = (node, score)
        score = -score
    new = None
    for d in range(len(path), -1, -1):
        node, score = nodes[d]
        children = node.children
        if children is not None:
            if new is not None:
                i = path[d]
                children = children[:i] + (new,) + children[i + 1:]
            amaf_value = 1 if node.pos.n % 2 == 0 else -1
            won = score > 0                   # reversed perspective
            children = tuple(
                Node(c.pos, c.v, c.w, c.pv, c.pw, c.av + 1, c.aw + won, c.children)
                if c.pos.last is not None and amaf[c.pos.last] == amaf_value
                else c
                for c in children)
        # score is for to-play; w for just-played
        new = Node(node.pos, node.v, node.w + (score < 0), node.pv, node.pw,
                   node.av, node.aw, children)
    return new


def _early_stop(tree: Node, i: int, n: int) -> bool:
    best_wr = winrate(best_move(tree))
    return (i > n * 0.05 and best_wr > FASTPLAY5_THRES
            or i > n * 0.2 and best_wr > FASTPLAY20_THRES)


def tree_search(tree: Node, n: int, pats: Patterns, *, seed: int = 0,
                threads: int = 1) -> tuple[Node, Node, tuple[float, ...]]:
    """Run up to n MCTS iterations from tree; return the final tree, its best
    child and the average owner map.  See the module docstring for threads=1
    vs N."""
    rng = random.Random(seed)
    if tree.children is None:
        tree = expand(tree, rng, pats)
    if threads == 1:
        owner_sum = [0] * (W * W)
        i = 0
        while i < n:
            i += 1
            amaf = W * W * [0]
            tree, path = tree_descend(tree, amaf, rng, pats)
            r = mcplayout(leaf(tree, path).pos, tuple(amaf), rng, pats)
            tree = tree_update(tree, path, r.amaf, r.score)
            for c in range(W * W):
                owner_sum[c] += r.owner[c]
            if _early_stop(tree, i, n):
                break
        return tree, best_move(tree), tuple(float(o) / i for o in owner_sum)

    root = grail.Atom(tree)
    issued = grail.Atom(0)                     # iterations started
    stop = grail.Atom(False)
    finished = grail.Atom(0)                   # playouts backed up
    owner_sums = grail.Atom((0,) * (W * W))    # summed over those playouts

    def search() -> None:
        while not stop.value:
            i = issued.add(1)
            if i > n:
                return
            ties = random.Random(f"{seed}/{i}/descent")
            while True:
                old = root.value
                amaf = W * W * [0]
                new, path = tree_descend(old, amaf, ties, pats)
                if root.compare_and_set(old, new):
                    break
            r = mcplayout(leaf(new, path).pos, tuple(amaf), job_rng(seed, i),
                          pats)
            tree = root.swap(tree_update, path, r.amaf, r.score)
            owner_sums.swap(lambda s: tuple(map(operator.add, s, r.owner)))
            finished.add(1)
            if _early_stop(tree, i, n):
                stop.reset(True)

    with grail.Pool(threads) as pool, pool.scope() as s:
        for _ in range(threads):
            s.spawn(search)
    tree = root.value
    done = finished.value
    return tree, best_move(tree), tuple(float(o) / done for o in owner_sums.value)


PATTERNS_3X3 = load_patterns(None)


def main(n: int = N_SIMS, *, patterns_dir=None, seed: int = 0,
         threads: int = 1) -> int | None:
    """tsbenchmark: the move chosen on the empty board (None for pass)."""
    pats = load_patterns(patterns_dir) if patterns_dir else PATTERNS_3X3
    _, best, _ = tree_search(new_node(empty_position()), n, pats, seed=seed,
                             threads=threads)
    return best.pos.last


def str_coord(c: int | None) -> str:
    if c is None:
        return 'pass'
    row, col = divmod(c - (W + 1), W)
    return '%c%d' % ('ABCDEFGHJKLMNOPQRST'[col], N - row)


if __name__ == "__main__":
    import time
    patterns = sys.argv[1] if len(sys.argv) > 1 else None
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    threads = (int(sys.argv[2]) if len(sys.argv) > 2
               else 1 if gil else min(os.cpu_count() or 1, 8))
    t0 = time.perf_counter()
    move = main(patterns_dir=patterns, threads=threads)
    print(f"best move {str_coord(move)}  threads={threads}  "
          f"{time.perf_counter() - t0:.2f}s")
