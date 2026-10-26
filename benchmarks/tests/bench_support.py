"""Load benchmark modules by file path.

Every benchmark directory has an idiomatic.py (and most an upstream.py), so they
are never put on sys.path; each module gets a distinct name instead.
"""

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

BENCHMARKS = Path(__file__).resolve().parents[1]


def load_benchmark(name, file="idiomatic.py"):
    """<name>/<file> as module <name>_<stem>, registered in sys.modules."""
    module_name = f"{name}_{Path(file).stem}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, BENCHMARKS / name / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_upstream(name):
    """The vendored original, <name>/upstream.py, with a stub pyperf module."""
    spec = importlib.util.spec_from_file_location(
        f"_original_{name}", BENCHMARKS / name / "upstream.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {spec.name: module,
                                  "pyperf": types.ModuleType("pyperf")}):
        spec.loader.exec_module(module)
    return module
