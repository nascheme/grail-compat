"""Single-threaded reference implementation for the build graph simulator."""

from __future__ import annotations

from collections import deque

from graph import BuildGraph, Target


def topological_sort(graph: BuildGraph) -> list[Target]:
    """Return targets in a valid build order."""
    in_degree = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)

    queue = deque(graph.targets[name] for name, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        target = queue.popleft()
        order.append(target)
        for dep in dependents[target.name]:
            in_degree[dep.name] -= 1
            if in_degree[dep.name] == 0:
                queue.append(dep)

    return order


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Reference implementation. Returns {target_name: result_hash}."""
    order = topological_sort(graph)
    results: dict[str, bytes] = {}
    for target in order:
        dep_results = {d.name: results[d.name] for d in target.deps}
        results[target.name] = target.build(dep_results)
    return results
