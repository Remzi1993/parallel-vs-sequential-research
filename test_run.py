"""Check the measurement plan and rejection of incomplete/mismatched benchmark data."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run


class RunnerTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_DOCKER_SMOKE") == "1", "requires the built Docker image")
    def test_built_image_runs_both_benchmarks_with_resource_measurements(self):
        results = run.ROOT / "results"
        results.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=results) as directory:
            output = Path(directory) / "smoke"
            result = subprocess.run(
                [sys.executable, "run.py", "smoke", "--output", str(output)],
                cwd=run.ROOT, capture_output=True, text=True, timeout=90,
            )
            logs = "\n".join(path.read_text(encoding="utf-8") for path in output.glob("*/console.log"))
            self.assertEqual(0, result.returncode, result.stdout + result.stderr + logs)
            rows = json.loads((output / "runs.json").read_text(encoding="utf-8"))
            self.assertEqual({"sequential", "parallel"}, {row["implementation"] for row in rows})
            self.assertEqual(1, len({row["dataset_checksum"] for row in rows}))
            for row in rows:
                self.assertIsNotNone(row["cpu_ms_per_operation"])
                self.assertIsNotNone(row["container_mean_mib"])

    def test_matrix_covers_every_condition_in_each_randomised_block(self):
        args = SimpleNamespace(mode="matrix", sizes=[100000, 1000000, 100000000], cpus=[1, 4],
                               memory=[2048, 6144], blocks=5, order_seed=42, heap_percent=60)
        rows = run.plan(args)
        self.assertEqual(120, len(rows))
        self.assertEqual(rows, run.plan(args))
        for block in range(1, 6):
            combinations = {(r["records"], r["cpu"], r["memory_mib"], r["implementation"])
                            for r in rows if r["block"] == block}
            self.assertEqual(24, len(combinations))
        self.assertNotEqual([(r["records"], r["implementation"]) for r in rows[:24]],
                            [(r["records"], r["implementation"]) for r in rows[24:48]])

    def test_expanded_pairs_both_streams_at_every_size_cpu_count_and_ram_limit(self):
        with patch.object(sys, "argv", ["run.py", "expanded", "--sizes", "1000", "2000",
                                       "--cpus", "1", "2", "3", "--memory", "512", "1024",
                                       "--blocks", "1"]):
            rows = run.plan(run.arguments())
        expected = {(n, cpu, ram, impl) for n in (1000, 2000) for cpu in (1, 2, 3)
                    for ram in (512, 1024) for impl in ("sequential", "parallel")}
        self.assertEqual(len(expected), len(rows))
        self.assertEqual(expected, {(row["records"], row["logical_cpus"], row["memory_mib"],
                                     row["implementation"]) for row in rows})

    def test_expanded_design_covers_the_size_sweep_and_crossed_resource_grid(self):
        with patch.object(sys, "argv", ["run.py", "expanded"]):
            rows = run.plan(run.arguments())

        self.assertEqual(370, len(rows))
        for block in range(1, 6):
            block_rows = [row for row in rows if row["block"] == block]
            self.assertEqual(74, len(block_rows))
            self.assertEqual({"pending"}, {row["status"] for row in block_rows})
            for implementation in ("sequential", "parallel"):
                subset = [row for row in block_rows if row["implementation"] == implementation]
                grid = {(row["cpu"], row["memory_mib"]) for row in subset if row["records"] == 10_000_000}
                self.assertEqual({(cpu, memory) for cpu in (1, 2, 4, 6, 8, 10, 12, 14, 16)
                                  for memory in (2048, 4096, 6144)}, grid)
                sizes = {row["records"] for row in subset if row["cpu"] == 4 and row["memory_mib"] == 6144}
                self.assertEqual({100_000, 250_000, 500_000, 1_000_000, 2_500_000, 5_000_000,
                                  10_000_000, 25_000_000, 50_000_000, 75_000_000, 100_000_000}, sizes)

    def test_plan_marks_heap_lower_bound_infeasible_without_timing_values(self):
        args = SimpleNamespace(mode="matrix", sizes=[100_000_000, 500_000_000], cpus=[4],
                               memory=[2048, 6144], blocks=1, order_seed=42, heap_percent=60)
        rows = run.plan(args)
        accepted = [row for row in rows if row["status"] == "pending"]
        self.assertEqual(2, len(accepted))
        self.assertTrue(all(row["records"] == 100_000_000 and row["memory_mib"] == 6144 for row in accepted))
        skipped = [row for row in rows if row["status"] == "infeasible"]
        self.assertEqual(6, len(skipped))
        for row in skipped:
            self.assertIn("Record fields alone", row["error"])
            self.assertNotIn("processing_ms", row)

    def test_engine_limit_errors_identify_unsupported_levels(self):
        engine = {"NCPU": 16, "MemTotal": 7_958_884_352}
        run.verify_engine_limits(engine, SimpleNamespace(cpus=[1, 16], memory=[2048, 4096, 6144]))
        with self.assertRaisesRegex(RuntimeError, r"7\.41 GiB.*8192, 10240"):
            run.verify_engine_limits(engine, SimpleNamespace(cpus=[1, 16], memory=[6144, 8192, 10240]))
        with self.assertRaisesRegex(RuntimeError, "16 logical CPUs.*18"):
            run.verify_engine_limits(engine, SimpleNamespace(cpus=[1, 18], memory=[2048]))

    def test_rejects_unapplied_cpu_memory_or_swap_limits(self):
        row = dict(cpu=2, memory_mib=512)
        config = dict(HostConfig=dict(Memory=512 * run.MIB, MemorySwap=512 * run.MIB, NanoCpus=2_000_000_000))
        run.verify_limits(config, row)
        for key in ["Memory", "MemorySwap", "NanoCpus"]:
            bad = copy.deepcopy(config)
            bad["HostConfig"][key] = 0
            with self.assertRaises(ValueError):
                run.verify_limits(bad, row)

    def test_requires_complete_matching_single_fork_results(self):
        row = dict(records=100000, implementation="sequential")
        args = SimpleNamespace(seed=42, iterations=2)
        result = dict(benchmark=run.BENCHMARK + "sequential", params={"records": "100000", "seed": "42"},
                      mode="avgt", threads=1, forks=1,
                      primaryMetric=dict(rawData=[[1.0, 3.0]], scoreUnit="ms/op"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jmh.json"
            path.write_text(json.dumps([result]), encoding="utf-8")
            self.assertEqual(2.0, run.read_result(path, row, args))
            for raw in [[], [[1.0]], [[1.0, 2.0], [1.0, 2.0]], [[1.0, float("nan")]]]:
                bad = copy.deepcopy(result)
                bad["primaryMetric"]["rawData"] = raw
                path.write_text(json.dumps([bad]), encoding="utf-8")
                with self.assertRaises(ValueError):
                    run.read_result(path, row, args)
            bad = copy.deepcopy(result)
            bad["params"]["seed"] = "43"
            path.write_text(json.dumps([bad]), encoding="utf-8")
            with self.assertRaises(ValueError):
                run.read_result(path, row, args)


if __name__ == "__main__":
    unittest.main()
