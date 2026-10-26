"""Build graph data structures and I/O."""

from __future__ import annotations

import hashlib
import json
import random
from collections import deque
from dataclasses import dataclass


@dataclass(eq=False)
class Target:
    name: str
    deps: list[Target]
    work: int
    seed: int

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Target):
            return NotImplemented
        return (
            self.name == other.name
            and self.work == other.work
            and self.seed == other.seed
            and [d.name for d in self.deps] == [d.name for d in other.deps]
        )

    def _make_source_data(self) -> bytes:
        """Generate deterministic source data for this target.

        The data is derived from the target name and graph seed, ensuring
        reproducible results across runs.
        """
        rng = random.Random(hash((self.name, self.seed)))
        return rng.randbytes(self.work)

    def build(self, dep_results: dict[str, bytes]) -> bytes:
        """Execute a build for this target.

        Produces a deterministic hash from the target's source data and
        its dependencies' outputs. Contestants must call this method
        for every target -- do not skip or replace it.
        """
        h = hashlib.sha256()
        h.update(self.name.encode())
        source = self._make_source_data()
        h.update(source)
        acc = 0x811C9DC5
        for i in range(0, len(source), 8):
            acc = ((acc ^ source[i]) * 0x01000193) & 0xFFFFFFFF
        h.update(acc.to_bytes(4, "little"))
        for dep_name in sorted(dep_results):
            h.update(dep_results[dep_name])
        return h.digest()


class BuildGraph:
    def __init__(self, seed: int, targets: dict[str, Target]):
        self.seed = seed
        self.targets = targets

    @staticmethod
    def load(path: str) -> BuildGraph:
        """Load a build graph from a JSON file."""
        with open(path) as f:
            data = json.load(f)

        targets = {}
        for name, info in data["targets"].items():
            targets[name] = Target(
                name=name, deps=[], work=info["work"], seed=data["seed"]
            )
        # Resolve dep names to Target instances
        for name, info in data["targets"].items():
            for dep in info["deps"]:
                if dep not in targets:
                    raise ValueError(
                        f"Target {name!r} depends on {dep!r}, which does not exist"
                    )
            targets[name].deps = [targets[dep] for dep in info["deps"]]

        graph = BuildGraph(seed=data["seed"], targets=targets)
        graph._validate()
        return graph

    def save(self, path: str) -> None:
        """Write the build graph to a JSON file."""
        data = {
            "seed": self.seed,
            "targets": {
                name: {"deps": [d.name for d in t.deps], "work": t.work}
                for name, t in self.targets.items()
            },
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _validate(self) -> None:
        """Check that all deps exist and the graph is acyclic."""
        for name, target in self.targets.items():
            for dep in target.deps:
                if dep.name not in self.targets:
                    raise ValueError(
                        f"Target {name!r} depends on {dep.name!r}, which does not exist"
                    )

        # Cycle detection via topological sort (Kahn's algorithm)
        in_degree = {name: len(t.deps) for name, t in self.targets.items()}
        dependents: dict[str, list[str]] = {name: [] for name in self.targets}
        for name, target in self.targets.items():
            for dep in target.deps:
                dependents[dep.name].append(name)

        queue = deque(name for name, deg in in_degree.items() if deg == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for dep in dependents[node]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

        if visited != len(self.targets):
            raise ValueError("Build graph contains a cycle")

    def __len__(self) -> int:
        return len(self.targets)
