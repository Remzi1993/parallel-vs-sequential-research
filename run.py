#!/usr/bin/env python3
"""Run isolated, randomised Docker benchmarks using only the Python standard library."""

import argparse
import csv
import hashlib
from functools import partial
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
import uuid

from cpu_topology import discover_topology, parse_cpu_list

ROOT = Path(__file__).resolve().parent
IMAGE = "java-stream-experiment:local"
BENCHMARK = "info.remzi.experiment.StreamBenchmark."
MIB = 1024 * 1024
EXPANDED_SIZES = (1_000, 100_000, 1_000_000, 5_000_000, 10_000_000, 25_000_000)
CAMPAIGN_SECONDS = 2 * 60 * 60
CLEANUP_RESERVE_SECONDS = 30


class CampaignTimeout(RuntimeError):
    """The standard experiment used its wall-clock budget."""


def remaining_seconds(deadline, maximum=None):
    if deadline is None:
        return maximum
    left = deadline - time.monotonic()
    if left <= 0:
        raise CampaignTimeout("Two-hour experiment budget reached")
    return left if maximum is None else min(left, maximum)


def docker(*args, check=True, deadline=None, timeout=30, combined=False):
    try:
        result = subprocess.run(["docker", *args], cwd=ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT if combined else subprocess.PIPE,
                                timeout=remaining_seconds(deadline, timeout))
    except subprocess.TimeoutExpired as error:
        remaining_seconds(deadline)
        if check:
            raise RuntimeError(f"Docker did not respond within {timeout} seconds") from error
        return ""
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def block_orders(args):
    rng = random.Random(args.order_seed)
    streams = ("sequential", "parallel")
    if args.mode != "expanded":
        conditions = list(itertools.product(args.sizes, args.cpus, args.memory, streams))
        for _ in range(args.blocks):
            order = conditions.copy()
            rng.shuffle(order)
            yield order
        return

    # Compare RAM at the smallest size and at one million records when available.
    full_grid_sizes = sorted(args.sizes)[:2]
    if full_grid_sizes[0] < 1_000_000 and 1_000_000 in args.sizes:
        full_grid_sizes[-1] = 1_000_000
    settings = [(size, cpu, ram) for size in args.sizes for cpu in args.cpus
                for ram in (args.memory if size in full_grid_sizes else [max(args.memory)])]
    parallel_first = {setting: rng.randrange(2) for setting in settings}
    for block in range(args.blocks):
        order = settings.copy()
        rng.shuffle(order)
        if block == 0:
            # Finish one complete resource grid early, even on a slower machine.
            order.sort(key=lambda setting: setting[0] != full_grid_sizes[-1])
        pairs = []
        for setting in order:
            methods = streams[::-1] if (parallel_first[setting] + block) % 2 else streams
            pairs.extend((*setting, method) for method in methods)
        yield pairs


def plan(args, cpu_order):
    masks = {count: ",".join(str(cpu) for cpu in cpu_order[:count])
             for count in args.cpus}
    rows = []
    for block, order in enumerate(block_orders(args), start=1):
        for size, cpu, memory, implementation in order:
            row = dict(order=len(rows) + 1, block=block, records=size, logical_cpus=cpu,
                       cpuset_cpus=masks[cpu], memory_mib=memory, implementation=implementation, status="pending")
            heap_mib = memory * args.heap_percent // 100  # Matches entrypoint.sh
            field_bytes = size * 16  # Two long fields, excluding objects and list storage
            if field_bytes > heap_mib * MIB:
                row.update(status="infeasible", error=(
                    f"Record fields alone require {field_bytes / MIB:,.1f} MiB, "
                    f"above the {heap_mib:,} MiB worker heap."
                ))
            rows.append(row)
    return rows


def verify_limits(inspect, row):
    config = inspect["HostConfig"]
    memory = row["memory_mib"] * MIB
    if config["Memory"] != memory or config["MemorySwap"] != memory:
        raise ValueError("Docker did not apply the requested memory and no-swap limits")
    if parse_cpu_list(config["CpusetCpus"]) != parse_cpu_list(row["cpuset_cpus"]):
        raise ValueError("Docker did not apply the requested CPU IDs")
    if config["NanoCpus"] or config["CpuQuota"] > 0:
        raise ValueError("Docker applied a CPU time quota to the pinned container")


def verify_fork_placement(properties, row):
    effective = parse_cpu_list(properties["cgroup.cpuset.cpus.effective"])
    if effective != parse_cpu_list(row["cpuset_cpus"]):
        raise ValueError("Benchmark observed a different effective CPU set")
    if int(properties["availableProcessors"]) != row["logical_cpus"]:
        raise ValueError("JVM available processor count differs from the requested CPU count")
    if properties["cgroup.cpu.max"].split()[0] != "max":
        raise ValueError("Benchmark observed a CPU time quota")


def read_result(path, row, args):
    results = json.loads(path.read_text(encoding="utf-8"))
    if len(results) != 1:
        raise ValueError("Expected exactly one JMH benchmark result")
    result = results[0]
    if result["benchmark"] != BENCHMARK + row["implementation"]:
        raise ValueError("Unexpected benchmark name")
    if result["params"] != {"records": str(row["records"]), "seed": str(args.seed)}:
        raise ValueError("Unexpected dataset parameters")
    raw = result["primaryMetric"]["rawData"]
    if len(raw) != 1 or len(raw[0]) != args.iterations:
        raise ValueError("Missing fork or measurement iterations")
    if result["mode"] != "avgt" or result["threads"] != 1 or result["forks"] != 1:
        raise ValueError("Unexpected JMH execution mode")
    if result["primaryMetric"]["scoreUnit"] != "ms/op":
        raise ValueError("Unexpected processing-time unit")
    if not all(math.isfinite(value) and value > 0 for value in raw[0]):
        raise ValueError("Non-finite or non-positive processing-time result")
    return statistics.mean(raw[0])


def resource_summary(directory, row, args):
    files = list(directory.glob("resources-*.csv"))
    if len(files) != 1:
        raise ValueError("Missing or ambiguous resource measurements")
    with files[0].open(encoding="utf-8", newline="") as stream:
        iterations = list(csv.DictReader(stream))
    if len(iterations) != args.iterations:
        raise ValueError("Incomplete resource iteration record")
    operations = sum(int(r["all_operations"]) for r in iterations)
    if operations <= 0:
        raise ValueError("No completed calculations in resource interval")
    cpu = None if any(r["cpu_nanos"] == "unavailable" for r in iterations) else (
        sum(int(r["cpu_nanos"]) for r in iterations) / operations / 1e6)
    samples = []
    for file in directory.glob("memory-samples-*.csv"):
        with file.open(encoding="utf-8", newline="") as stream:
            samples.extend(csv.DictReader(stream))
    values = [int(s["container_bytes"]) / MIB for s in samples if s["container_bytes"] != "unavailable"]
    if not samples:
        raise ValueError("No memory samples were recorded")
    metadata_files = list(directory.glob("metadata-*.properties"))
    if len(metadata_files) != 1:
        raise ValueError("Missing benchmark metadata")
    # These keys have simple numeric/string values; other Java properties remain raw.
    properties = dict(line.split("=", 1) for line in metadata_files[0].read_text(encoding="utf-8").splitlines()
                      if line and not line.startswith("#") and "=" in line)
    if properties.get("cgroup.memory.max") != str(row["memory_mib"] * MIB):
        raise ValueError("Benchmark did not observe the requested cgroup memory limit")
    if properties.get("cgroup.memory.swap.max") != "0":
        raise ValueError("Benchmark did not observe disabled container swap")
    verify_fork_placement(properties, row)
    return dict(jvm_available_processors=int(properties["availableProcessors"]),
                common_pool_parallelism=int(properties["commonPoolParallelism"]),
                cpu_ms_per_operation=cpu,
                container_mean_mib=statistics.mean(values) if values else None,
                container_max_observed_mib=max(values) if values else None,
                memory_samples=len(samples), dataset_checksum=properties["datasetChecksum"],
                expected_sum=properties["expectedSum"])


def run_condition(row, args, output, image_id, deadline=None):
    directory = output / f'{row["order"]:03d}-block{row["block"]}-{row["implementation"]}-n{row["records"]}-c{row["logical_cpus"]}-m{row["memory_mib"]}'
    directory.mkdir()
    row["directory"] = directory.name
    container_name = "java-stream-" + uuid.uuid4().hex[:12]
    command = ["create", "--name", container_name, "--network", "none",
               "--cpuset-cpus", row["cpuset_cpus"], "--memory", str(row["memory_mib"] * MIB),
               "--memory-swap", str(row["memory_mib"] * MIB),
               "--env", f'MEMORY_MIB={row["memory_mib"]}',
               "--env", f"HEAP_PERCENT={args.heap_percent}",
               "--mount", f"type=bind,source={directory},target=/out", image_id,
               "^" + BENCHMARK.replace(".", r"\.") + row["implementation"] + "$",
               "-p", f'records={row["records"]}', "-p", f"seed={args.seed}",
               "-wi", str(args.warmup), "-i", str(args.iterations),
               "-w", f"{round(args.seconds * 1000)}ms", "-r", f"{round(args.seconds * 1000)}ms"]
    write_json(directory / "condition.json", dict(row, command=["docker", *command]))
    container = None
    command_docker = partial(docker, deadline=deadline)
    finished = False
    try:
        container = command_docker(*command)
        before = json.loads(command_docker("inspect", container))[0]
        write_json(directory / "container-before.json", before)
        verify_limits(before, row)
        command_docker("start", container)
        condition_deadline = time.monotonic() + args.timeout
        while True:
            state = json.loads(command_docker("inspect", "--format", "{{json .State}}", container))
            if not state["Running"]:
                break
            if time.monotonic() >= condition_deadline:
                row["status"] = "timeout"
                command_docker("stop", "--time", "1", container)
                break
            time.sleep(0.5)
        state = json.loads(command_docker("inspect", "--format", "{{json .State}}", container))
        row["exit_code"] = state["ExitCode"]
        finished = not state["Running"]
        logs = command_docker("logs", container, combined=True)
        (directory / "console.log").write_text(logs, encoding="utf-8")
        if row["status"] == "timeout":
            return
        if state["OOMKilled"] or "java.lang.OutOfMemoryError" in logs:
            row["status"] = "insufficient_memory"
        elif state["ExitCode"] != 0:
            row["status"] = "failed"
        else:
            processing_ms = read_result(directory / "jmh.json", row, args)
            resources = resource_summary(directory, row, args)
            row.update(processing_ms=processing_ms, **resources)
            row["status"] = "ok"
    except CampaignTimeout:
        row["status"] = "budget_exhausted"
        # A cancelled docker create may have created the named container before returning its ID.
        container = container or container_name
        raise
    except KeyboardInterrupt:
        row["status"] = "interrupted"
        container = container or container_name
        raise
    except (RuntimeError, ValueError, OSError, KeyError, IndexError) as error:
        row["status"] = "failed"
        row["error"] = str(error)
    finally:
        if container:
            # These calls use the reserved cleanup time even after the run deadline.
            if not finished:
                docker("stop", "--time", "1", container, check=False, timeout=5)
            after = docker("inspect", container, check=False, timeout=5)
            if after:
                (directory / "container-after.json").write_text(after + "\n", encoding="utf-8")
            if not (directory / "console.log").exists():
                logs = docker("logs", container, check=False, timeout=5, combined=True)
                (directory / "console.log").write_text(logs, encoding="utf-8")
            docker("rm", "--force", container, check=False, timeout=5)
        write_json(directory / "status.json", row)


def save_progress(output, rows):
    write_json(output / "runs.json", rows)
    fields = ["order", "block", "implementation", "records", "logical_cpus", "cpuset_cpus",
              "jvm_available_processors", "common_pool_parallelism", "memory_mib", "status",
              "processing_ms", "cpu_ms_per_operation", "container_mean_mib",
              "container_max_observed_mib", "memory_samples", "dataset_checksum", "expected_sum",
              "exit_code", "directory", "error"]
    with (output / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_comparisons(output, rows):
    """Compare successful runs within the same randomised block."""
    groups = {}
    for row in rows:
        if row["status"] == "ok":
            key = (row["records"], row["memory_mib"], row["block"], row["logical_cpus"])
            groups.setdefault(key, {})[row["implementation"]] = row

    def same_input(first, second):
        return (first["dataset_checksum"], first["expected_sum"]) == (
            second["dataset_checksum"], second["expected_sum"])

    def difference(first, second, metric):
        if first is None or second is None or first.get(metric) is None or second.get(metric) is None:
            return None
        return first[metric] - second[metric]

    fields = ["records", "memory_mib", "block", "logical_cpus", "cpuset_cpus", "sequential_ms",
              "parallel_ms", "speedup", "time_saved_ms", "extra_cpu_ms", "extra_memory_mib",
              "marginal_time_saved_ms", "marginal_cpu_ms"]
    with (output / "comparisons.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for key, pair in sorted(groups.items()):
            if "sequential" not in pair or "parallel" not in pair:
                continue
            seq, par = pair["sequential"], pair["parallel"]
            if not same_input(seq, par) or seq["cpuset_cpus"] != par["cpuset_cpus"]:
                print(f"Skipping unmatched inputs or CPU placement at {key}", file=sys.stderr)
                continue
            records, ram, block, count = key
            previous = groups.get((records, ram, block, count - 1), {}).get("parallel")
            if previous and not same_input(previous, par):
                previous = None
            writer.writerow(dict(
                records=records, memory_mib=ram, block=block, logical_cpus=count,
                cpuset_cpus=par["cpuset_cpus"], sequential_ms=seq["processing_ms"],
                parallel_ms=par["processing_ms"], speedup=seq["processing_ms"] / par["processing_ms"],
                time_saved_ms=seq["processing_ms"] - par["processing_ms"],
                extra_cpu_ms=difference(par, seq, "cpu_ms_per_operation"),
                extra_memory_mib=difference(par, seq, "container_mean_mib"),
                marginal_time_saved_ms=difference(previous, par, "processing_ms"),
                marginal_cpu_ms=difference(par, previous, "cpu_ms_per_operation")))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "The default pairs both streams across all CPU and RAM levels at two dataset sizes. "
        "Use matrix for a full crossing at every selected size."
    ))
    parser.add_argument("mode", nargs="?", default="expanded", choices=["smoke", "matrix", "expanded"],
                        help="Run the two-hour experiment (default), a smoke check or a custom matrix")
    parser.add_argument("--sizes", nargs="+", type=int)
    parser.add_argument("--cpu-counts", "--cpus", dest="cpus", nargs="+", type=int,
                        help="Counts of allowed logical CPUs, using nested CPU sets")
    parser.add_argument("--memory", nargs="+", type=int, help="Container limits in MiB")
    parser.add_argument("--blocks", type=int)
    parser.add_argument("--warmup", type=int)
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--heap-percent", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--order-seed", type=int, default=20261116)
    parser.add_argument("--timeout", type=float, default=600, help="Seconds per container, including setup")
    parser.add_argument("--output", type=Path, help="New directory; existing directories are rejected")
    parser.add_argument("--build", action="store_true", help="Build image and run correctness tests first")
    args = parser.parse_args()
    smoke = args.mode == "smoke"
    defaults = dict(sizes=[100000] if smoke else [100000, 1000000, 100000000],
                    cpus=[1] if smoke else None, memory=[512] if smoke else None,
                    blocks=1 if smoke else 5, warmup=1 if smoke else 5,
                    iterations=2 if smoke else 10, seconds=0.2 if smoke else 1.0)
    if args.mode == "expanded":
        defaults.update(sizes=list(EXPANDED_SIZES), blocks=2, warmup=2, iterations=3, seconds=0.5)
    for key, value in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    if any(n <= 0 or n % 4 or n > 2147483647 for n in args.sizes):
        parser.error("Dataset sizes must be positive multiples of four and at most 2,147,483,647 for the Java ArrayList workload")
    if any(c < 1 for c in (args.cpus or [])) or any(m < 256 for m in (args.memory or [])):
        parser.error("CPU counts must be positive integers and memory limits at least 256 MiB")
    if not 10 <= args.heap_percent <= 80 or min(args.blocks, args.warmup, args.iterations) < 1:
        parser.error("Heap percentage must be 10..80; blocks and iteration counts must be positive")
    if not math.isfinite(args.seconds) or args.seconds < 0.001 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Durations must be finite and positive; iterations must last at least 0.001 seconds")
    if not -(2**63) <= args.seed < 2**63:
        parser.error("Dataset seed must fit a Java long")
    if any(len(values) != len(set(values)) for values in (args.sizes, args.cpus or [], args.memory or [])):
        parser.error("Do not repeat factor levels")
    return args


def resolve_levels(args, cpu_order, memory_bytes):
    capacity = len(cpu_order)
    if args.cpus is None:
        args.cpus = (list(range(1, capacity + 1)) if args.mode == "expanded"
                     else [n for n in (1, 4) if n <= capacity])
    if any(count > capacity for count in args.cpus):
        raise RuntimeError(f"Docker exposes {capacity} eligible logical CPUs; requested {args.cpus}")
    if args.memory is None:
        # Keep each RAM limit strictly below Docker's reported capacity.
        max_mib = (memory_bytes - 1) // MIB
        args.memory = list(range(2048, max_mib + 1, 2048))
        if not args.memory and max_mib >= 256:
            args.memory = [max_mib // 256 * 256]
    unsupported = [m for m in args.memory if m * MIB >= memory_bytes]
    if unsupported or not args.memory:
        raise RuntimeError(f"Docker reports {memory_bytes / (1024 * MIB):.2f} GiB RAM; "
                           f"choose --memory limits below that capacity (MiB). Unsupported: {unsupported}")


def main():
    started = time.monotonic()
    args = arguments()
    deadline = started + CAMPAIGN_SECONDS - CLEANUP_RESERVE_SECONDS if args.mode == "expanded" else None
    try:
        return run_campaign(args, deadline)
    except CampaignTimeout as error:
        print(f"{error} during setup; no measurements started.", file=sys.stderr)
        return 1


def run_campaign(args, deadline):
    setup_docker = partial(docker, deadline=deadline)
    engine = json.loads(setup_docker("info", "--format", "{{json .}}"))
    if engine["OSType"] != "linux" or str(engine["CgroupVersion"]) != "2":
        raise RuntimeError("Use Linux containers with cgroup v2 for the recorded resource controls")
    if args.mode == "expanded" or args.build:
        try:
            subprocess.run(["docker", "compose", "build"], cwd=ROOT, check=True,
                           timeout=remaining_seconds(deadline))
        except subprocess.TimeoutExpired as error:
            raise CampaignTimeout("Two-hour experiment budget reached") from error
    image = json.loads(setup_docker("image", "inspect", IMAGE))[0]
    host_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(engine["Architecture"], engine["Architecture"])
    if image["Architecture"] != host_arch:
        raise RuntimeError("Image architecture differs from Docker host; avoid emulation for measurements")
    topology = discover_topology(setup_docker, image["Id"])
    resolve_levels(args, topology["cpu_order"], engine["MemTotal"])
    rows = plan(args, topology["cpu_order"])
    output = (args.output or ROOT / "results" / (args.mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                                + "-" + uuid.uuid4().hex[:6])).resolve()
    if "," in str(output):
        raise RuntimeError("Choose an output path without commas; Docker's mount syntax uses commas as separators")
    docker_version = json.loads(setup_docker("version", "--format", "{{json .}}"))
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(p.relative_to(ROOT)).replace(os.sep, "/"): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [ROOT / "pom.xml", ROOT / "Dockerfile", ROOT / "entrypoint.sh", ROOT / "compose.yaml",
                        ROOT / "run.py", ROOT / "cpu_topology.py", *sorted((ROOT / "src").rglob("*.java"))]}
    write_json(output / "environment.json", dict(
        arguments={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        captured_utc=datetime.now(timezone.utc).isoformat(), python=sys.version,
        cpu_control="cpuset", cpu_topology=topology,
        host_platform=platform.platform(), docker_version=docker_version,
        campaign_budget_seconds=CAMPAIGN_SECONDS if deadline is not None else None,
        docker_resources={k: engine[k] for k in ["NCPU", "MemTotal", "Architecture", "KernelVersion", "CgroupVersion"]},
        image=image, source_sha256=hashes))
    save_progress(output, rows)
    runnable = sum(row["status"] == "pending" for row in rows)
    print(f"Running {runnable} of {len(rows)} planned conditions in fresh JVMs. Results: {output}", flush=True)
    try:
        for row in rows:
            remaining_seconds(deadline)
            print(f'[{row["order"]}/{len(rows)}] block {row["block"]}: {row["implementation"]}, '
                  f'{row["records"]:,} records, {row["logical_cpus"]} logical CPUs, {row["memory_mib"]} MiB', flush=True)
            if row["status"] == "infeasible":
                print(f'  infeasible: {row["error"]}', flush=True)
                continue
            run_condition(row, args, output, image["Id"], deadline=deadline)
            save_progress(output, rows)
            print(f'  {row["status"]}', flush=True)
    except CampaignTimeout:
        for row in rows:
            if row["status"] == "pending":
                row["status"] = "not_run_budget"
        save_progress(output, rows)
        write_comparisons(output, rows)
        completed = sum(row["status"] == "ok" for row in rows)
        print(f"Time budget reached. {completed}/{len(rows)} valid measurements. "
              f"Partial results: {output}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        save_progress(output, rows)
        write_comparisons(output, rows)
        print("Interrupted; active container stopped and partial results preserved.", file=sys.stderr)
        return 130
    write_comparisons(output, rows)
    completed = sum(row["status"] == "ok" for row in rows)
    print(f"Finished with {completed}/{len(rows)} valid measurements. "
          f"Read {output / 'summary.csv'} and {output / 'comparisons.csv'}", flush=True)
    return 0 if completed == len(rows) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
