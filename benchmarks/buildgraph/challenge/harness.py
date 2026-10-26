"""Test harness for the build graph simulator challenge.

Instruments contestant submissions, validates correctness, and measures performance.

Usage:
    python harness.py submission.py graph.json
    python harness.py submission.py graphs/
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from graph import BuildGraph, Target
from reference import build_all


@dataclass
class InstrumentedResults:
    dups: set[str] = field(default_factory=set)
    violations: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)


def _load_submission(path: str):
    """Dynamically load a contestant's submission module."""
    spec = importlib.util.spec_from_file_location("submission", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load submission from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "build_all"):
        raise RuntimeError(f"Submission {path} must define a build_all() function")
    return module


def _instrument_build(instrumented: InstrumentedResults, graph: BuildGraph):
    """Monkey-patch Target.build to record build events."""
    original_build = Target.build

    for tgt in graph.targets.values():
        tgt._result = None
        tgt._built = threading.Event()

    def instrumented_build(self: Target, dep_results: dict[str, bytes]) -> bytes:
        # Check that all deps are finished before we start
        for dep in self.deps:
            if not dep._built.is_set():
                msg = (
                    f"Order violation: {self.name!r} started "
                    f"before dep {dep!r} finished"
                )
                with instrumented._lock:
                    instrumented.violations.append(msg)

        self._result = original_build(self, dep_results)
        already_built = self._built.is_set()
        self._built.set()

        if already_built:
            with instrumented._lock:
                instrumented.dups.add(self.name)

        return self._result

    Target.build = instrumented_build
    return original_build


@dataclass
class ValidationResult:
    passed: bool
    errors: list[str]


def validate(
    graph: BuildGraph,
    instrumented: InstrumentedResults,
    reference_results: dict[str, bytes],
) -> ValidationResult:
    """Validate a contestant's build against the reference."""
    errors: list[str] = []

    # 1. Completeness — all targets were built
    built_names = set()
    for target in graph.targets.values():
        if target._built.is_set():
            built_names.add(target.name)
    missing = set(graph.targets.keys()) - built_names
    if missing:
        examples = sorted(missing)[:5]
        errors.append(f"Missing {len(missing)} targets (e.g., {', '.join(examples)})")

    # 2. No duplicates
    if instrumented.dups:
        examples = sorted(instrumented.dups)[:5]
        errors.append(
            f"{len(instrumented.dups)} duplicate build(s) (e.g., {', '.join(examples)})"
        )

    # 3. Dependency order — violations recorded during execution
    errors.extend(instrumented.violations)

    # 4. Result correctness — outputs match reference
    for name, expected in reference_results.items():
        target = graph.targets[name]
        if not target._built.is_set():
            continue
        actual = target._result
        if actual != expected:
            errors.append(f"Wrong result for {name!r}")

    return ValidationResult(passed=len(errors) == 0, errors=errors)


def run_one(submission_module, graph: BuildGraph, graph_label: str) -> dict:
    """Run a submission against a single graph. Returns a results dict."""
    # Compute reference results
    ref_start = time.perf_counter()
    reference_results = build_all(graph)
    ref_time = time.perf_counter() - ref_start

    # Instrument and run contestant's code
    instrumented = InstrumentedResults()
    original_build = _instrument_build(instrumented, graph)
    try:
        sub_start = time.perf_counter()
        contestant_results = submission_module.build_all(graph)
        sub_time = time.perf_counter() - sub_start
    finally:
        # Restore original build
        Target.build = original_build

    # Validate
    validation = validate(graph, instrumented, reference_results)

    return {
        "graph": graph_label,
        "num_targets": len(graph),
        "reference_time": ref_time,
        "submission_time": sub_time,
        "speedup": ref_time / sub_time if sub_time > 0 else float("inf"),
        "passed": validation.passed,
        "errors": validation.errors,
    }


def print_result(result: dict) -> None:
    """Pretty-print a single run result."""
    status = "PASS" if result["passed"] else "FAIL"
    print(f"\n{'=' * 60}")
    print(f"Graph:      {result['graph']} ({result['num_targets']} targets)")
    print(f"Reference:  {result['reference_time']:.3f}s")
    print(f"Submission: {result['submission_time']:.3f}s")
    print(f"Speedup:    {result['speedup']:.2f}x")
    print(f"Correctness: {status}")
    if result["errors"]:
        for err in result["errors"][:10]:
            print(f"  ERROR: {err}")
        if len(result["errors"]) > 10:
            print(f"  ... and {len(result['errors']) - 10} more errors")
    print(f"{'=' * 60}")


def print_summary(results: list[dict]) -> None:
    """Print an aggregate summary table."""
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Graph':<30} {'Time':>8} {'Speedup':>8} {'Status':>8}")
    print(f"{'-' * 30} {'-' * 8} {'-' * 8} {'-' * 8}")

    total_ref = 0.0
    total_sub = 0.0
    all_passed = True

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(
            f"{r['graph']:<30} {r['submission_time']:>7.3f}s {r['speedup']:>7.2f}x {status:>8}"
        )
        total_ref += r["reference_time"]
        total_sub += r["submission_time"]
        if not r["passed"]:
            all_passed = False

    overall_speedup = total_ref / total_sub if total_sub > 0 else float("inf")
    overall_status = "PASS" if all_passed else "FAIL"
    print(f"{'-' * 30} {'-' * 8} {'-' * 8} {'-' * 8}")
    print(
        f"{'TOTAL':<30} {total_sub:>7.3f}s {overall_speedup:>7.2f}x {overall_status:>8}"
    )
    print(f"{'=' * 60}")


def _median_result(trial_results: list[dict]) -> dict:
    """Pick the median trial by submission_time. Average two middle for even counts."""
    sorted_trials = sorted(trial_results, key=lambda r: r["submission_time"])
    n = len(sorted_trials)
    all_passed = all(r["passed"] for r in sorted_trials)
    all_errors = []
    for r in sorted_trials:
        all_errors.extend(r["errors"])
    if n % 2 == 1:
        result = dict(sorted_trials[n // 2])
    else:
        lo = sorted_trials[n // 2 - 1]
        hi = sorted_trials[n // 2]
        result = {
            "graph": lo["graph"],
            "num_targets": lo["num_targets"],
            "reference_time": (lo["reference_time"] + hi["reference_time"]) / 2,
            "submission_time": (lo["submission_time"] + hi["submission_time"]) / 2,
            "speedup": (lo["speedup"] + hi["speedup"]) / 2,
        }
    result["passed"] = all_passed
    result["errors"] = all_errors
    return result


def format_json_results(results: list[dict]) -> dict:
    """Build a JSON-serializable output dict from a list of run results."""
    if len(results) == 1:
        return results[0]
    total_ref = sum(r["reference_time"] for r in results)
    total_sub = sum(r["submission_time"] for r in results)
    return {
        "results": results,
        "summary": {
            "total_reference_time": total_ref,
            "total_submission_time": total_sub,
            "overall_speedup": total_ref / total_sub if total_sub > 0 else float("inf"),
            "overall_passed": all(r["passed"] for r in results),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run and score a build graph simulator submission"
    )
    parser.add_argument("submission", help="Path to the submission .py file")
    parser.add_argument(
        "graph_path", help="Path to a graph JSON file or directory of graph files"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    parser.add_argument(
        "--num-trials",
        type=int,
        default=1,
        help="Run each graph this many times and use the median result",
    )
    args = parser.parse_args()

    # Load submission
    submission_module = _load_submission(args.submission)

    # Discover graph files
    graph_path = Path(args.graph_path)
    if graph_path.is_dir():
        graph_files = sorted(graph_path.glob("*.json"))
        if not graph_files:
            print(f"No .json files found in {graph_path}")
            sys.exit(1)
    else:
        graph_files = [graph_path]

    # Run
    results = []
    for gf in graph_files:
        trial_results = []
        for _ in range(args.num_trials):
            graph = BuildGraph.load(str(gf))
            trial_results.append(run_one(submission_module, graph, gf.name))
        if args.num_trials > 1:
            result = _median_result(trial_results)
        else:
            result = trial_results[0]
        if not args.json:
            print_result(result)
        results.append(result)

    if args.json:
        json.dump(format_json_results(results), sys.stdout, indent=2)
        print()
    else:
        if len(results) > 1:
            print_summary(results)

    # Exit code
    if not all(r["passed"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
