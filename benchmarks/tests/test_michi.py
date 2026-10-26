"""Michi playouts and pattern tables against the vendored upstream.

The large pattern tables are not vendored: run michi/fetch_patterns.py (or
set MICHI to a michi checkout with its patterns/ submodule) to enable the
pattern checks; they skip otherwise.  Upstream reads them with the zstd tool.
"""

import os
from pathlib import Path
import shutil
import unittest

from bench_support import BENCHMARKS, load_benchmark, load_upstream

g = load_benchmark("michi")
PATTERNS = (Path(os.environ["MICHI"]) / "patterns" if "MICHI" in os.environ
            else BENCHMARKS / "michi" / "patterns")


class MichiUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = load_upstream("michi")

    def test_playouts(self):
        for seed in (0, 1):
            with self.subTest(seed=seed):
                self.up.setup_random(seed)
                self.assertEqual(g.mcbenchmark(20, seed=seed), self.up.mcbenchmark(20))

    @unittest.skipUnless((PATTERNS / "spat.zst").exists() and (PATTERNS / "prob.zst").exists(),
                         f"no pattern files in {PATTERNS}; run michi/fetch_patterns.py")
    @unittest.skipUnless(shutil.which("zstd"), "upstream needs the zstd tool")
    def test_large_pattern_tables(self):
        up = self.up
        try:
            pats = g.load_patterns(PATTERNS)
        except FileNotFoundError as e:          # before 3.14 without zstd
            self.skipTest(str(e))
        with up.CompressedFile(str(PATTERNS / "spat.zst")) as f:
            up.load_spat_patterndict(f)
        with up.CompressedFile(str(PATTERNS / "prob.zst")) as f:
            up.load_large_patterns(f)
        self.assertEqual(dict(pats.spat), up.spat_patterndict)
        self.assertEqual(dict(pats.large), up.large_patterns)


if __name__ == "__main__":
    unittest.main()
