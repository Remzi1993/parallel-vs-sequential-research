#!/usr/bin/env python3
"""Run isolated, randomised Docker benchmarks using only the Python standard library."""

import argparse
import csv
import hashlib
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
EXPANDED_SIZES = (100, 1_000, 10_000, 100_000, 250_000, 500_000, 1_000_000, 2_500_000, 5_000_000,
                  10_000_000, 25_000_000, 50_000_000, 75_000_000, 100_000_000)
EXPANDED_MEMORY_MIB = (2048, 4096, 6144, 8192, 10240)


def docker(*args, check=True):
    try:
        result = subprocess.run(["docker", *args], cwd=ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except subprocess.TimeoutExpired as error:
        if check:
            raise RuntimeError("Docker did not respond within 30 seconds") from error
        return ""
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def plan(args, cpu_order=None):
    # Offline previews have no CPU IDs. Every measured condition uses the probed order.
    masks = {count: ",".join(str(cpu) for cpu in cpu_order[:count]) if cpu_order else None
             for count in args.cpus}
    conditions = list(itertools.product(args.sizes, args.cpus, args.memory, ("sequential", "parallel")))
    rng = random.Random(args.order_seed)
    rows = []
    for block in range(1, args.blocks + 1):
        order = conditions.copy()
        rng.shuffle(order)
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


def run_condition(row, args, output, image_id):
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
    interrupted = False
    try:
        container = docker(*command)
        before = json.loads(docker("inspect", container))[0]
        write_json(directory / "container-before.json", before)
        verify_limits(before, row)
        docker("start", container)
        deadline = time.monotonic() + args.timeout
        while True:
            state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
            if not state["Running"]:
                break
            if time.monotonic() >= deadline:
                row["status"] = "timeout"
                docker("stop", "--time", "1", container)
                break
            time.sleep(0.5)
        state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
        row["exit_code"] = state["ExitCode"]
        logs = subprocess.run(["docker", "logs", container], text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT).stdout
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
    except KeyboardInterrupt:
        row["status"] = "interrupted"
        interrupted = True
    except (RuntimeError, ValueError, OSError, KeyError, IndexError) as error:
        row["status"] = "failed"
        row["error"] = str(error)
    finally:
        if container:
            # Only this invocation's uniquely named container is affected.
            docker("stop", "--time", "1", container, check=False)
            after = docker("inspect", container, check=False)
            if after:
                (directory / "container-after.json").write_text(after + "\n", encoding="utf-8")
            if not (directory / "console.log").exists():
                logs = subprocess.run(["docker", "logs", container], text=True, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT).stdout
                (directory / "console.log").write_text(logs, encoding="utf-8")
            docker("rm", container, check=False)
        write_json(directory / "status.json", row)
    if interrupted:
        raise KeyboardInterrupt


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
        "Both streams run at every combination of dataset size, allowed logical CPU count and RAM limit."
    ))
    parser.add_argument("mode", choices=["smoke", "matrix", "expanded"])
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
    parser.add_argument("--dry-run", action="store_true", help="Preview only. Supply CPU counts and RAM limits for an offline plan.")
    args = parser.parse_args()
    smoke = args.mode == "smoke"
    defaults = dict(sizes=[100000] if smoke else [100000, 1000000, 100000000],
                    cpus=[1] if smoke else None, memory=[512] if smoke else None,
                    blocks=1 if smoke else 5, warmup=1 if smoke else 5,
                    iterations=2 if smoke else 10, seconds=0.2 if smoke else 1.0)
    if args.mode == "expanded":
        defaults["sizes"] = list(EXPANDED_SIZES)
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
        defaults = EXPANDED_MEMORY_MIB if args.mode == "expanded" else (2048, 6144)
        args.memory = [m for m in defaults if m * MIB < memory_bytes]
    unsupported = [m for m in args.memory if m * MIB >= memory_bytes]
    if unsupported or not args.memory:
        raise RuntimeError(f"Docker reports {memory_bytes / (1024 * MIB):.2f} GiB RAM; "
                           f"choose --memory limits below that capacity (MiB). Unsupported: {unsupported}")


def main():
    args = arguments()
    if args.dry_run and args.cpus is not None and args.memory is not None:
        print(json.dumps(plan(args), indent=2))
        return 0
    engine = json.loads(docker("info", "--format", "{{json .}}"))
    if engine["OSType"] != "linux" or str(engine["CgroupVersion"]) != "2":
        raise RuntimeError("Use Linux containers with cgroup v2 for the recorded resource controls")
    if args.build and not args.dry_run:
        subprocess.run(["docker", "compose", "build"], cwd=ROOT, check=True)
    image = json.loads(docker("image", "inspect", IMAGE))[0]
    host_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(engine["Architecture"], engine["Architecture"])
    if image["Architecture"] != host_arch:
        raise RuntimeError("Image architecture differs from Docker host; avoid emulation for measurements")
    topology = discover_topology(docker, image["Id"])
    resolve_levels(args, topology["cpu_order"], engine["MemTotal"])
    rows = plan(args, topology["cpu_order"])
    if args.dry_run:
        print(json.dumps(rows, indent=2))
        return 0
    output = (args.output or ROOT / "results" / (args.mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                                + "-" + uuid.uuid4().hex[:6])).resolve()
    if "," in str(output):
        raise RuntimeError("Choose an output path without commas; Docker's mount syntax uses commas as separators")
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(p.relative_to(ROOT)).replace(os.sep, "/"): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [ROOT / "pom.xml", ROOT / "Dockerfile", ROOT / "entrypoint.sh", ROOT / "compose.yaml",
                        ROOT / "run.py", ROOT / "cpu_topology.py", *sorted((ROOT / "src").rglob("*.java"))]}
    write_json(output / "environment.json", dict(
        arguments={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        captured_utc=datetime.now(timezone.utc).isoformat(), python=sys.version,
        cpu_control="cpuset", cpu_topology=topology,
        host_platform=platform.platform(), docker_version=json.loads(docker("version", "--format", "{{json .}}")),
        docker_resources={k: engine[k] for k in ["NCPU", "MemTotal", "Architecture", "KernelVersion", "CgroupVersion"]},
        image=image, source_sha256=hashes))
    save_progress(output, rows)
    runnable = sum(row["status"] == "pending" for row in rows)
    print(f"Running {runnable} of {len(rows)} planned conditions in fresh JVMs. Results: {output}", flush=True)
    try:
        for row in rows:
            print(f'[{row["order"]}/{len(rows)}] block {row["block"]}: {row["implementation"]}, '
                  f'{row["records"]:,} records, {row["logical_cpus"]} logical CPUs, {row["memory_mib"]} MiB', flush=True)
            if row["status"] == "infeasible":
                print(f'  infeasible: {row["error"]}', flush=True)
                continue
            run_condition(row, args, output, image["Id"])
            save_progress(output, rows)
            print(f'  {row["status"]}', flush=True)
    except KeyboardInterrupt:
        save_progress(output, rows)
        write_comparisons(output, rows)
        print("Interrupted; active container stopped and partial results preserved.", file=sys.stderr)
        return 130
    write_comparisons(output, rows)
    print(f"Finished. Read {output / 'summary.csv'} and {output / 'comparisons.csv'}", flush=True)
    return 0 if all(row["status"] == "ok" for row in rows) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
