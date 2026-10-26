"""Generate build graphs with various shapes for the challenge."""

from __future__ import annotations

import argparse
import math
import random

from graph import BuildGraph, Target


def _random_work(rng: random.Random, low: int = 5_000, high: int = 50_000) -> int:
    return rng.randint(low, high)


def generate_wide(num_targets: int, seed: int) -> BuildGraph:
    """Many independent targets with a thin final link layer.

    90% of targets are independent leaf targets. The remaining 10%
    each depend on a random subset of the leaves.
    """
    rng = random.Random(seed)
    targets: dict[str, Target] = {}

    num_leaves = max(1, int(num_targets * 0.9))
    num_aggregators = num_targets - num_leaves

    for i in range(num_leaves):
        name = f"leaf_{i}"
        targets[name] = Target(name=name, deps=[], work=_random_work(rng), seed=seed)

    leaf_names = list(targets.keys())
    for i in range(num_aggregators):
        name = f"agg_{i}"
        num_deps = rng.randint(2, min(10, num_leaves))
        dep_names = rng.sample(leaf_names, num_deps)
        deps = [targets[d] for d in dep_names]
        targets[name] = Target(
            name=name, deps=deps, work=_random_work(rng, 1_000, 5_000), seed=seed
        )

    return BuildGraph(seed=seed, targets=targets)


def generate_chain(num_targets: int, seed: int) -> BuildGraph:
    """Linear dependency chain. No parallelism possible."""
    rng = random.Random(seed)
    targets: dict[str, Target] = {}

    for i in range(num_targets):
        name = f"step_{i}"
        deps = [targets[f"step_{i - 1}"]] if i > 0 else []
        targets[name] = Target(name=name, deps=deps, work=_random_work(rng), seed=seed)

    return BuildGraph(seed=seed, targets=targets)


def generate_diamond(num_targets: int, seed: int) -> BuildGraph:
    """Repeated fan-out / fan-in pattern.

    Structure: source -> [fan_0_0..fan_0_K] -> merge_0 -> [fan_1_0..fan_1_K] -> merge_1 -> ...
    """
    rng = random.Random(seed)
    targets: dict[str, Target] = {}
    fan_width = max(2, int(math.sqrt(num_targets)))

    idx = 0
    prev_name = f"source_{idx}"
    targets[prev_name] = Target(
        name=prev_name, deps=[], work=_random_work(rng), seed=seed
    )
    idx += 1

    while idx < num_targets:
        # Fan-out
        fan_names = []
        for j in range(fan_width):
            if idx >= num_targets:
                break
            name = f"fan_{idx}"
            targets[name] = Target(
                name=name, deps=[targets[prev_name]], work=_random_work(rng), seed=seed
            )
            fan_names.append(name)
            idx += 1

        if idx >= num_targets:
            break

        # Merge
        name = f"merge_{idx}"
        targets[name] = Target(
            name=name,
            deps=[targets[n] for n in fan_names],
            work=_random_work(rng),
            seed=seed,
        )
        prev_name = name
        idx += 1

    return BuildGraph(seed=seed, targets=targets)


def generate_tree(num_targets: int, seed: int) -> BuildGraph:
    """K-ary tree of targets. Leaves are independent, parents depend on children."""
    rng = random.Random(seed)
    targets: dict[str, Target] = {}
    k = 4  # branching factor

    # Build level by level, bottom-up
    names = [f"leaf_{i}" for i in range(num_targets)]
    for name in names:
        targets[name] = Target(name=name, deps=[], work=_random_work(rng), seed=seed)

    # Build parent levels until we have one root or hit target count
    level = 0
    current_level = list(names)
    total = len(targets)

    while len(current_level) > 1 and total < num_targets * 2:
        next_level = []
        for i in range(0, len(current_level), k):
            children = current_level[i : i + k]
            parent_name = f"parent_L{level}_{i // k}"
            targets[parent_name] = Target(
                name=parent_name,
                deps=[targets[c] for c in children],
                work=_random_work(rng, 1_000, 10_000),
                seed=seed,
            )
            next_level.append(parent_name)
            total += 1
        current_level = next_level
        level += 1

    return BuildGraph(seed=seed, targets=targets)


def generate_realistic(num_targets: int, seed: int) -> BuildGraph:
    """Layered DAG with power-law degree distribution.

    Mimics a real build graph: many small leaf libraries, some mid-level
    libraries, a few large binaries that depend on many things.
    """
    rng = random.Random(seed)
    targets: dict[str, Target] = {}

    # Create targets in layers; each target can only depend on earlier layers
    num_layers = max(3, int(math.log2(num_targets)))
    layer_sizes = []
    remaining = num_targets
    for i in range(num_layers):
        if i == num_layers - 1:
            layer_sizes.append(remaining)
        else:
            # Earlier layers are larger (more leaf targets)
            size = max(1, int(remaining * 0.4))
            layer_sizes.append(size)
            remaining -= size

    all_previous: list[str] = []
    for layer_idx, layer_size in enumerate(layer_sizes):
        for j in range(layer_size):
            name = f"target_L{layer_idx}_{j}"

            if not all_previous:
                deps = []
            else:
                # Power-law: most targets have few deps, some have many
                max_deps = min(len(all_previous), 20)
                # Draw from exponential distribution, clamp to [1, max_deps]
                num_deps = min(max_deps, max(1, int(rng.expovariate(0.5))))
                dep_names = rng.sample(all_previous, num_deps)
                deps = [targets[d] for d in dep_names]

            # Earlier layers tend to be more expensive (core libraries)
            if layer_idx < num_layers // 3:
                work = _random_work(rng, 10_000, 50_000)
            elif layer_idx < 2 * num_layers // 3:
                work = _random_work(rng, 3_000, 15_000)
            else:
                work = _random_work(rng, 1_000, 5_000)

            targets[name] = Target(name=name, deps=deps, work=work, seed=seed)
            all_previous.append(name)

    return BuildGraph(seed=seed, targets=targets)


GENERATORS = {
    "wide": generate_wide,
    "chain": generate_chain,
    "diamond": generate_diamond,
    "tree": generate_tree,
    "realistic": generate_realistic,
}


def main():
    parser = argparse.ArgumentParser(
        description="Generate build graphs for the challenge"
    )
    parser.add_argument(
        "--shape",
        choices=GENERATORS.keys(),
        required=True,
        help="Shape of the dependency graph",
    )
    parser.add_argument(
        "--num-targets",
        type=int,
        required=True,
        help="Approximate number of targets to generate",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("-o", "--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    generator = GENERATORS[args.shape]
    graph = generator(args.num_targets, args.seed)
    graph.save(args.output)
    print(f"Generated {args.shape} graph with {len(graph)} targets -> {args.output}")


if __name__ == "__main__":
    main()
