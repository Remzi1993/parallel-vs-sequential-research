"""Check the measurement plan and rejection of incomplete/mismatched benchmark data."""
import copy
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cpu_topology
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
            combinations = {(r["records"], r["logical_cpus"], r["memory_mib"], r["implementation"])
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

    def test_expanded_defaults_use_nested_cpuset_masks_and_supported_memory(self):
        with patch.object(sys, "argv", ["run.py", "expanded", "--sizes", "1000", "--blocks", "1"]):
            args = run.arguments()
        cpu_order = [2, 6, 4, 8]
        run.resolve_levels(args, cpu_order, 7_958_884_352)
        rows = run.plan(args, cpu_order)
        self.assertEqual([1, 2, 3, 4], list(args.cpus))
        self.assertEqual([2048, 4096, 6144], list(args.memory))
        self.assertEqual(24, len(rows))
        for row in rows:
            expected = set(cpu_order[:row["logical_cpus"]])
            self.assertEqual(expected, cpu_topology.parse_cpu_list(row["cpuset_cpus"]))

    def test_probe_orders_one_logical_cpu_per_reported_core_before_siblings(self):
        lines = ["allowed\t2,4,6,8", "quota\tmax 100000", "siblings\t2\t2,4",
                 "siblings\t4\t2,4", "siblings\t6\t6,8", "siblings\t8\t6,8"]
        probe = Mock(return_value="\n".join(lines))
        topology = cpu_topology.discover_topology(probe, "fake-image")
        self.assertEqual([2, 6, 4, 8], topology["cpu_order"])
        self.assertEqual([[2, 4], [6, 8]], topology["sibling_groups"])
        probe.return_value = "\n".join(lines[:-1])
        topology = cpu_topology.discover_topology(probe, "fake-image")
        self.assertEqual([2, 4, 6, 8], topology["cpu_order"])
        self.assertIsNone(topology["sibling_groups"])

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

    def test_rejects_unavailable_cpu_counts_and_ram_limits(self):
        for flags in [["--cpus", "3"], ["--memory", "8192"]]:
            with self.subTest(flags=flags), patch.object(sys, "argv", ["run.py", "expanded", *flags]):
                args = run.arguments()
                with self.assertRaises(RuntimeError):
                    run.resolve_levels(args, [2, 6], 7_958_884_352)

    def test_container_creation_passes_exact_cpu_ids_without_a_bandwidth_limit(self):
        args = SimpleNamespace(seed=42, heap_percent=60, warmup=1, iterations=1, seconds=1)
        row = dict(order=1, block=1, records=1000, implementation="parallel", logical_cpus=2,
                   cpuset_cpus="2,6", memory_mib=512, status="pending")
        with tempfile.TemporaryDirectory() as directory, patch.object(
                run, "docker", side_effect=RuntimeError("simulated create failure")) as docker:
            run.run_condition(row, args, Path(directory), "fake-image")
        command = docker.call_args.args
        self.assertEqual("create", command[0])
        self.assertEqual("2,6", command[command.index("--cpuset-cpus") + 1])
        self.assertNotIn("--cpus", command)
        self.assertNotIn("--cpu-quota", command)

    def test_rejects_unapplied_cpu_memory_or_swap_limits(self):
        row = dict(logical_cpus=2, cpuset_cpus="4,5", memory_mib=512)
        config = dict(HostConfig=dict(Memory=512 * run.MIB, MemorySwap=512 * run.MIB,
                                      CpusetCpus="4-5", NanoCpus=0, CpuQuota=0))
        run.verify_limits(config, row)
        for key, wrong in [("Memory", 0), ("MemorySwap", 0), ("CpusetCpus", "3-4"),
                           ("NanoCpus", 2_000_000_000), ("CpuQuota", 200000)]:
            with self.subTest(key=key):
                bad = copy.deepcopy(config)
                bad["HostConfig"][key] = wrong
                with self.assertRaises(ValueError):
                    run.verify_limits(bad, row)

    def test_rejects_mismatched_fork_affinity_processor_count_and_cpu_quota(self):
        row = dict(logical_cpus=2, cpuset_cpus="4,5")
        metadata = {"cgroup.cpuset.cpus.effective": "4-5", "cgroup.cpu.max": "max 100000",
                    "availableProcessors": "2"}
        run.verify_fork_placement(metadata, row)
        for key, wrong in [("cgroup.cpuset.cpus.effective", "0-1"), ("availableProcessors", "1"),
                           ("cgroup.cpu.max", "200000 100000")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                run.verify_fork_placement({**metadata, key: wrong}, row)

    def test_comparisons_pair_identical_inputs_and_measure_adjacent_cpu_gains(self):
        rows = []
        for count, parallel_ms, parallel_cpu in [(1, 12, 12), (2, 6, 15), (4, 5, 18), (5, 4, 20)]:
            for method, time_ms, cpu_ms, memory in [
                    ("sequential", 10, 10, 100), ("parallel", parallel_ms, parallel_cpu, 108)]:
                rows.append(dict(block=1, records=1000, logical_cpus=count, memory_mib=512,
                                 cpuset_cpus=",".join(str(i) for i in range(count)), implementation=method,
                                 status="ok", processing_ms=time_ms, cpu_ms_per_operation=cpu_ms,
                                 container_mean_mib=memory, dataset_checksum="abc", expected_sum="42"))
        rows[-1]["dataset_checksum"] = "different-input"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run.write_comparisons(output, rows)
            with (output / "comparisons.csv").open(encoding="utf-8", newline="") as stream:
                results = {int(r["logical_cpus"]): r for r in csv.DictReader(stream)}
        pair = results[2]
        self.assertAlmostEqual(10 / 6, float(pair["speedup"]))
        self.assertEqual(4, float(pair["time_saved_ms"]))
        self.assertEqual(5, float(pair["extra_cpu_ms"]))
        self.assertEqual(8, float(pair["extra_memory_mib"]))
        self.assertEqual(6, float(pair["marginal_time_saved_ms"]))
        self.assertEqual(3, float(pair["marginal_cpu_ms"]))
        self.assertEqual("", results[4]["marginal_time_saved_ms"])
        self.assertNotIn(5, results)

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
