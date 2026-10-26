"""Record-based probabilistic battle planning from pyperformance's bm_mdp.

Battle state and graph nodes are values. Generic successor expansion separates
choice, opponent and pending-action nodes. Graph construction uses a private
memo dictionary, then publishes read-only adjacency and a traversal tuple.
Value iteration retains local numeric tables and the upstream Gauss-Seidel
update order; it does not persistently copy the whole table per scalar update.
"""

from collections import defaultdict
from copy import replace
from fractions import Fraction
from types import MappingProxyType

import grail


@grail.record
class Stats:
    atk: int
    df: int
    speed: int
    spec: int


NOMODS = Stats(0, 0, 0, 0)


@grail.record
class FixedData:
    maxhp: int
    stats: Stats
    lvl: int
    badges: tuple[int, int, int, int]
    basespeed: int


@grail.record
class HalfState:
    fixed: FixedData
    hp: int
    status: int
    statmods: Stats
    stats: Stats


@grail.record
class State:
    me: HalfState
    them: HalfState
    extra: int


@grail.record
class Choice:
    state: State


@grail.record
class Opponent:
    state: State
    action: str


@grail.record
class Pending:
    state: State
    side: int
    action: str


@grail.record
class Outcome:
    won: bool


Node = Choice | Opponent | Pending | Outcome
WIN = Outcome(True)
LOSS = Outcome(False)


@grail.record
class Attack:
    power: int
    isspec: bool
    stab: bool
    te: int | float
    crit: bool


# Read-only configuration, with no retained mutable backing-map alias.
ATTACKS = MappingProxyType({
    "Ember": Attack(40, True, True, 0.5, False),
    "Dig": Attack(100, False, False, 1, False),
    "Slash": Attack(70, False, False, 1, True),
    "Water Gun": Attack(40, True, True, 2, False),
    "Bubblebeam": Attack(65, True, True, 2, False),
})


def stats_key(stats):
    return stats.atk, stats.df, stats.speed, stats.spec


def half_key(half):
    fixed = half.fixed
    return ((fixed.maxhp, stats_key(fixed.stats), fixed.lvl, fixed.badges,
             fixed.basespeed), half.hp, half.status, stats_key(half.statmods),
            stats_key(half.stats))


def state_key(state):
    return half_key(state.me), half_key(state.them), state.extra


def node_key(node):
    """Upstream tuple ordering, including its state tags and all tie-breaks.

    Native records intentionally have no ordering. Do not let hash order or
    variant names replace the original deterministic probability tie-break.
    """
    match node:
        case Choice(state):
            return 0, state_key(state)
        case Opponent(state, action):
            return 1, state_key(state), action
        case Pending(state, side, action):
            return 2, state_key(state), side, action
        case Outcome(won):
            return 4, won
    raise TypeError(type(node))


def topological_sort(roots, get_parents):
    """Iterative DFS postorder, retaining upstream order even on cyclic graphs."""
    results = []
    visited = set()
    stack = [(node, 0) for node in roots]
    while stack:
        current, state = stack.pop()
        if state == 0:
            if current not in visited:
                visited.add(current)
                stack.append((current, 1))
                stack.extend((parent, 0) for parent in get_parents(current))
        else:
            assert current in visited
            results.append(current)
    return results


def damages(level, attack, defense, power, stab, effectiveness):
    x = (2 * level) // 5
    x = ((x + 2) * attack * power) // (defense * 50) + 2
    if stab:
        x += x // 2
    x = int(x * effectiveness)
    return [(x * z) // 255 for z in range(217, 256)]


def critical_distribution(level, probability, attack, base_attack, defense,
                          base_defense, power, stab, effectiveness):
    probability = min(probability, Fraction(1))
    normal = damages(level, attack, defense, power, stab, effectiveness)
    critical = damages(level * 2, base_attack, base_defense, power, stab, effectiveness)
    dist = defaultdict(Fraction)
    for mult, values in zip([1 - probability, probability], [normal, critical]):
        mult /= len(values)
        for value in values:
            dist[value] += mult
    return dist


def plus12(value):
    return value + value // 8


def badge_boosts(badges, stats):
    return Stats(*[(plus12(value) if badge else value)
                   for value, badge in zip(stats_key(stats), badges)])


def change_hp(half, change):
    hp = min(half.fixed.maxhp, max(0, half.hp + change))
    return replace(half, hp=hp)


def _action_side_one(state, action):
    me, them, extra = state.me, state.them, state.extra
    if action == "Super Potion":
        return {State(change_hp(me, 50), them, extra): Fraction(1)}
    move = ATTACKS[action]
    pdiv = 64 if move.crit else 512
    dist = critical_distribution(
        me.fixed.lvl, Fraction(me.fixed.basespeed, pdiv),
        me.stats.spec if move.isspec else me.stats.atk,
        me.fixed.stats.spec if move.isspec else me.fixed.stats.atk,
        them.stats.spec if move.isspec else them.stats.df,
        them.fixed.stats.spec if move.isspec else them.fixed.stats.df,
        move.power, move.stab, move.te)
    result = defaultdict(Fraction)
    for damage, probability in dist.items():
        result[State(me, change_hp(them, -damage), extra)] += probability
    return result


def apply_action(state, side, action):
    if side == 0:
        return _action_side_one(state, action)
    dist = _action_side_one(State(state.them, state.me, state.extra), action)
    return {State(k.them, k.me, k.extra): v for k, v in dist.items()}


def _after_action(state, continuation):
    # Preserve loss precedence if both HPs are zero.
    if state.me.hp == 0:
        return LOSS
    if state.them.hp == 0:
        return WIN
    return continuation(state)


def _action_pair(state, side1, action1, side2, action2, dist, multiplier):
    for newstate, probability in apply_action(state, side1, action1).items():
        node = _after_action(newstate, lambda s: Pending(s, side2, action2))
        dist[node] += probability * multiplier


def _ordered_distribution(dist):
    # Accumulate exactly as Fractions, convert only at the original boundary.
    values = {node: float(p) for node, p in dist.items() if p > 0}
    return tuple(sorted(values.items(), key=lambda item: (-item[1], node_key(item[0]))))


@grail.generic
def successors(node):
    raise TypeError(type(node))


@successors.impl
def _choice_successors(node: Choice):
    return tuple(Opponent(node.state, action) for action in ("Dig", "Super Potion"))


@successors.impl
def _opponent_successors(node: Opponent):
    state, action = node.state, node.action
    dist = defaultdict(Fraction)
    for enemy_action, probability in [("Water Gun", Fraction(64, 130)),
                                       ("Bubblebeam", Fraction(66, 130))]:
        priority1 = state.me.stats.speed + 10000 * (action == "Super Potion")
        # The original tests the player's action here, not enemy_action.
        priority2 = state.them.stats.speed + 10000 * (action == "X Defend")
        if priority1 > priority2:
            _action_pair(state, 0, action, 1, enemy_action, dist, probability)
        elif priority1 < priority2:
            _action_pair(state, 1, enemy_action, 0, action, dist, probability)
        else:
            _action_pair(state, 0, action, 1, enemy_action, dist, probability / 2)
            _action_pair(state, 1, enemy_action, 0, action, dist, probability / 2)
    return _ordered_distribution(dist)


@successors.impl
def _pending_successors(node: Pending):
    dist = defaultdict(Fraction)
    for state, probability in apply_action(node.state, node.side, node.action).items():
        dist[_after_action(state, Choice)] += probability
    return _ordered_distribution(dist)


@successors.impl
def _outcome_successors(node: Outcome):
    return ()


@grail.record
class Graph:
    initial: Node
    nodes: tuple[Node, ...]
    # A read-only view over a privately built dict of immutable tuple edges.
    # MappingProxyType is an ordinary field, not a native value plan.
    edges: MappingProxyType


def explore(initial):
    memo = {}

    def parents(node):
        if type(node) is Outcome:
            return ()
        try:
            edges = memo[node]
        except KeyError:
            edges = successors(node)
            memo[node] = edges
        if type(node) is Choice:
            return edges
        return tuple(child for child, probability in edges)

    nodes = topological_sort([initial], parents)
    return Graph(initial, tuple(nodes), MappingProxyType(memo))


def initial_node():
    badges = (1, 0, 0, 0)
    star_stats = Stats(40, 44, 56, 50)
    star_fixed = FixedData(59, star_stats, 11, (0, 0, 0, 0), 115)
    star_half = HalfState(star_fixed, 59, 0, NOMODS, Stats(40, 44, 56, 50))
    char_stats = Stats(39, 34, 46, 38)
    char_fixed = FixedData(63, char_stats, 26, badges, 65)
    char_half = HalfState(char_fixed, 63, 0, NOMODS, badge_boosts(badges, char_stats))
    return Choice(State(char_half, star_half, 0))


def _iterate_bounds(graph, tolerance):
    """Private thread-confined numeric scratch, not a published domain snapshot."""
    lower = defaultdict(float)
    upper = defaultdict(lambda: 1.0)
    upper[LOSS] = 0.0
    lower[WIN] = 1.0
    frozen = {WIN, LOSS}
    iterations = 0
    while upper[graph.initial] - lower[graph.initial] > tolerance:
        iterations += 1
        for node in graph.nodes:
            if node in frozen:
                continue
            if type(node) is Choice:
                lower[node] = max(lower[child] for child in graph.edges[node])
                upper[node] = max(upper[child] for child in graph.edges[node])
            else:
                lower[node] = sum(lower[child] * probability
                                  for child, probability in graph.edges[node])
                upper[node] = sum(upper[child] * probability
                                  for child, probability in graph.edges[node])
            if lower[node] >= upper[node]:
                upper[node] = lower[node] = (lower[node] + upper[node]) / 2
                frozen.add(node)
    return lower, upper, frozen, iterations


@grail.record
class Evaluation:
    probability: float
    lower: float
    upper: float
    iterations: int
    nodes: int


def evaluate(tolerance=0.192):
    graph = explore(initial_node())
    lower, upper, frozen, iterations = _iterate_bounds(graph, tolerance)
    low, high = lower[graph.initial], upper[graph.initial]
    return Evaluation((high + low) / 2, low, high, iterations, len(graph.nodes))


def main(tolerance=0.192):
    return evaluate(tolerance).probability


if __name__ == "__main__":
    print(evaluate())
