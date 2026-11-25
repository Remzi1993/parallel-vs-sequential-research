# Controlled Java stream experiment

This experiment compares Java's sequential `stream()` and `parallelStream()` on the same in-memory filter and sum. It measures how long one calculation takes, how much processor time it consumes across Java threads and how much container memory it uses. Docker restricts each run to a specific set of logical processors and a RAM limit.

## Research questions

**Main question.** For this Java calculation, when does parallel processing save enough time over sequential processing to justify the extra processor capacity, and how does that depend on the number of records and available RAM?

1. With access to one logical processor and the same RAM limit, how does processing time change for each stream as the number of records increases?
2. For each dataset size, how does giving each stream access to one more logical processor affect processing time when the RAM limit stays the same?
3. How does increasing the RAM limit affect each stream at a given dataset size and processor count? Does that effect change as more processors are made available?
4. For each dataset size and RAM limit, when does parallel processing become worthwhile, and how much further time does each extra processor save compared with the CPU time and memory used?

Use the [full experiment](#4-run-the-full-experiment) for all four questions. The [results guide](#compare-the-two-streams-and-the-value-of-one-more-processor) explains how to make the comparisons.

## Run the experiment

### 1. Set up Docker and Python

- On Windows, start Docker Desktop with Linux containers. On Linux, start Docker Engine with the Compose plugin. The runner requires Linux containers with cgroup v2.
- Install Python 3.10 or later. The runner uses only the standard library. Docker supplies Java 25 and Maven 3.9.16, so the experiment commands only need Docker and Python on the host.
- Open a terminal in the cloned repository, in the directory that contains `run.py`, `compose.yaml` and `pom.xml`. Run all README commands from that directory. The following commands work in **PowerShell 7+ and Bash**.

```text
docker version
docker compose version
docker compose build
```

If you prefer an isolated Python environment, see [Optional Python virtual environment](#optional-python-virtual-environment).

### 2. Check the setup with a short run

This optional smoke run measures both streams at 100,000 records, one logical processor and 512 MiB of RAM. Open `results/smoke-cpuset-01/summary.csv` and check that both rows have `status=ok`.

**PowerShell 7+**

```powershell
py -3 run.py smoke --output results/smoke-cpuset-01
```

**Linux/Bash**

```bash
python3 run.py smoke --output results/smoke-cpuset-01
```

### 3. Run a pilot

This example checks three dataset sizes and two processor counts at one RAM limit. It runs 12 conditions with one warm-up and two measurement iterations each.

**PowerShell 7+**

```powershell
py -3 run.py expanded --sizes 100 100000 10000000 --cpu-counts 1 2 --memory 2048 --blocks 1 --warmup 1 --iterations 2 --output results/pilot-cpuset-01
```

**Linux/Bash**

```bash
python3 run.py expanded --sizes 100 100000 10000000 --cpu-counts 1 2 --memory 2048 --blocks 1 --warmup 1 --iterations 2 --output results/pilot-cpuset-01
```

Check `summary.csv` for failures. Before a long campaign, pilot the largest size and lowest RAM limit you intend to use. The per-condition `jmh.json` and `resources-*.csv` show iteration times and completed calculations. If warm-up is still changing the timings or long calculations finish too few operations, adjust `--warmup` or `--seconds` before collecting the final data. Keep the power profile and unrelated machine activity consistent.

### 4. Run the full experiment

The first command previews every planned combination. With automatic levels it uses the built image and queries Docker to discover eligible processor IDs and memory capacity. Its short-lived topology probe starts a shell only. The second command starts the measurements and writes the results.

**PowerShell 7+**

```powershell
py -3 run.py expanded --dry-run
py -3 run.py expanded --output results/full-cpuset-01
```

**Linux/Bash**

```bash
python3 run.py expanded --dry-run
python3 run.py expanded --output results/full-cpuset-01
```

The default plan crosses all 14 dataset sizes with each processor count, each supported RAM limit, both streams and five repeated blocks. On a Docker engine exposing 16 eligible processors and 7.41 GiB of RAM this schedules **6,720 rows** (14 × 16 × 3 × 2 × 5), including settings that exceed the available Java heap. If all 6,720 rows completed, their scheduled warm-up and measurement time alone would total 28 hours. Each completed row uses five warm-up and ten measurement iterations of one second each, plus container startup, dataset setup and validation. Use `--sizes`, `--cpu-counts` and `--memory` to narrow a campaign. RAM values are in MiB.

To preview a smaller plan offline, supply both the processor counts and RAM levels, as shown in [Run a custom full matrix](#run-a-custom-full-matrix). A real run always discovers the eligible IDs and validates the settings. Use a new output directory for each run. Rebuild the image after a Java or Docker change with `docker compose build`, or use `--build` with the measurement command.

## Find and read the results

After the full run, open **`results/full-cpuset-01/summary.csv`** relative to the repository root. For another run, open `summary.csv` in the directory passed to `--output`. The terminal prints the full path while the run starts and again on completion.

A relative `--output` path starts from your terminal's working directory. If you omit it, the runner creates a folder under `results/` in the repository root, named after the mode, a UTC timestamp and a short random suffix. For example, an expanded run gets an `expanded-...` directory. The `results` folder is ignored by Git and contains the measurements on the machine where you ran the experiment.

```text
results/full-cpuset-01/
├── summary.csv              Measurements for each condition and block
├── comparisons.csv          Paired timings, resource differences and gains from adding one CPU
├── runs.json                The same plan and progress as structured data
├── environment.json         Settings, eligible CPU IDs and topology, software versions and Docker resources
└── <condition-directory>/   Files for one attempted condition
    ├── jmh.json             JMH 1.37 timing scores and measurement iterations
    ├── resources-*.csv      CPU time, completed operations and throttling per iteration
    ├── memory-samples-*.csv Sampled container and JVM heap usage
    ├── console.log          Benchmark output and error details
    ├── metadata-*.properties  Heap limit, dataset checksum, Java version and cgroup limits
    ├── condition.json       Condition settings and Docker command
    ├── container-before.json / container-after.json  Limits and container state
    └── status.json         Outcome of this condition
```

The runner updates `summary.csv` and `runs.json` as conditions finish and continues after individual failures. Ctrl+C stops the active experiment container and preserves completed results. The per-condition deadline defaults to 600 seconds and includes JVM startup and dataset setup. Use `--timeout` to set a different deadline if the pilot calls for it.

### Which columns to use

| Column | Meaning |
| --- | --- |
| `records`, `memory_mib` | Number of records and container RAM limit. 2,048 MiB is 2 GiB. |
| `logical_cpus`, `cpuset_cpus` | Number of logical processors available to the container and the exact Linux CPU IDs selected. |
| `jvm_available_processors`, `common_pool_parallelism` | Processor count reported by the benchmark JVM and its common pool's target parallelism. The target is a configuration value, not a measured count of busy threads. |
| `implementation`, `block` | Sequential or parallel processing and the independent repetition number. |
| `processing_ms` | Mean elapsed milliseconds for one complete filter-and-sum calculation in that fork. Lower means it finished sooner. |
| `cpu_ms_per_operation` | CPU milliseconds used by all threads in the benchmark JVM per calculation. Parallel CPU time can exceed elapsed time. |
| `container_mean_mib` | Mean of the available samples of charged container memory. It includes both JVMs. |
| `container_max_observed_mib` | Highest observed container memory sample. Sampling may miss brief peaks. |
| `memory_samples`, `dataset_checksum`, `expected_sum` | Sample count, a fingerprint of the generated input and the expected calculation result. |
| `status`, `directory`, `error` | Outcome, folder for the attempted condition and any preflight or validation error. |

Resource measurements cover the measurement iterations and short harness transitions. They exclude dataset setup and warm-up. CPU time per operation uses the `all_operations` count for those intervals. The profiler reads cgroup v2 `memory.current` about every 100 ms. That value includes the JVM heaps, other memory used by both JVMs and any other charges to the container. The separate sampled heap column in `memory-samples-*.csv` is already part of container memory.

For Excel, import `summary.csv` via **Data → From Text/CSV** with the comma delimiter. Set the timing and memory metrics to decimal numbers using **English (United States)** as the import locale to interpret the decimal points.

### Compare the two streams and the value of one more processor

Open `comparisons.csv` for the results per matched block. The runner writes this file when the campaign finishes or is stopped with Ctrl+C. It includes settings where both streams finished with matching inputs and CPU placement. `speedup` divides `sequential_ms` by `parallel_ms` for that pair. `time_saved_ms` is the elapsed time saved by the parallel stream. `extra_cpu_ms` and `extra_memory_mib` subtract sequential resource use from parallel resource use. A positive difference means parallel used more of that resource.

At fixed records, RAM and block, `marginal_time_saved_ms` measures how much sooner the parallel stream finished with one additional logical processor. `marginal_cpu_ms` measures the change in CPU milliseconds used for that step. A blank comparison means the required measurement was unavailable.

Use only matched `status=ok` rows for elapsed-time comparisons. Pair rows within the same block, dataset size and RAM limit. For a stream comparison, also match the CPU set. Check that the dataset checksums and expected sums agree.

| What to compare | How to read it |
| --- | --- |
| Sequential and parallel at the same setting | Subtract parallel elapsed time from sequential elapsed time in each block. A positive value is the time saved by parallel processing. For speedup, divide mean sequential time by mean parallel time. A ratio above 1 favours parallel. |
| The next logical processor | For each stream and block, subtract the time at `k + 1` processors from the time at `k` with the same records and RAM. Compare the milliseconds saved with the change in CPU milliseconds per calculation and average container MiB. |
| Increasing RAM, alone and together with CPU count | Compare two RAM levels at the same processor count and dataset size. Then check how the time saved by adding one processor changes between those RAM levels. |

Decide before analysis what time saving would matter for the intended use, in milliseconds or percent, and how much processor capacity can be spared. For example, a gain that meets a response-time target can matter even if it uses more CPU time. At each dataset size and RAM limit, report the first tested setting where the gain is worthwhile and where further gains become small or uncertain. State the range of counts if the measurements do not distinguish adjacent settings. A difference close to zero in a single run cannot establish that the streams perform equally well.

Summarise paired differences across independent blocks with their mean and a 95% confidence interval. With five complete pairs, use the mean difference ± 2.776 × the sample standard deviation of the differences / √5. An interval spanning zero leaves the direction uncertain. A claim that two settings perform practically alike requires an interval within the preselected tolerance. Report missing pairs and interpret the many comparisons as exploratory. JMH iteration scores from the same JVM are repeated measurements within one block.

Here, resource cost means processor capacity made available, CPU time consumed per calculation and observed memory use. A financial estimate would require an actual pricing model. Assess coding and maintenance effort separately using a dated log of implementation and validation work, with shared setup and AI assistance identified.

### What the statuses mean

| Status | Meaning |
| --- | --- |
| `ok` | The benchmark completed and its timings and resources passed validation. |
| `infeasible` | The record fields alone exceed the configured JVM heap. The row records the reason in `error` and the runner skips launching a container. |
| `insufficient_memory` | The JVM reported an out-of-memory error or Docker recorded an out-of-memory kill. |
| `timeout` | The container exceeded its deadline. |
| `failed` | Another execution or output validation error occurred. Check `error` and the available condition logs. |
| `interrupted` or `pending` | The active condition was stopped or a scheduled condition has yet to start. |

Report memory failures as feasibility outcomes. Blank timing or resource cells represent missing measurements. The runner returns a nonzero exit code when the campaign has incomplete or unsuccessful conditions.

## Why the size sweep stops at 100 million records

**100 million was the largest size verified in the earlier CPU-quota experiment.** Both implementations completed with a quota of 4 and a 6 GiB container limit. The 60% heap policy capped the worker JVM at **3,686 MiB (about 3.60 GiB)**. Both runs had an average sampled heap usage of **3.38 GiB** during measurement. At a 2 GiB container limit, the earlier 100-million-record verification ran out of memory. The current runner skips that setting before allocation because the fields alone exceed the smaller heap.

The benchmark creates the whole dataset in an `ArrayList` before measuring. Each record has two Java `long` fields, which require **16 bytes per record** before Java adds object headers and the list's references. The figures below are lower bounds. [Java integral types](https://docs.oracle.com/javase/specs/jls/se25/html/jls-4.html#jls-4.2.1) and [ArrayList storage](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/util/ArrayList.html).

| Records | Memory for the two fields alone | What this means on this host |
| --- | ---: | --- |
| 100 million | 1.49 GiB | Both implementations completed under the 6 GiB container limit. |
| 500 million | 7.45 GiB | The fields alone exceed the Docker VM's 7.41 GiB total RAM. |
| 1 billion | 14.90 GiB | The fields alone need over four times the 3.60 GiB worker heap. |
| 50 billion | 745.06 GiB | This also exceeds the integer record-count limit of the current single-list implementation. |

The exact limit above 100 million depends on object overhead and available heap, so larger intermediate sizes would need their own feasibility checks. A billion records is already outside this machine's capacity on the current workload. Processing them in batches or from disk would change the storage and access pattern, so those measurements would describe a different experiment.

## Experiment design

Every independent block runs both implementations once for **every combination** of selected records, logical processor counts and RAM limits, in a reproducibly randomised order. Each condition gets a fresh container and benchmark JVM. Only one condition runs at a time.

| Factor | Default in `expanded` |
| --- | --- |
| Records | 100, 1,000, 10,000, 100,000, 250,000, 500,000, 1 million, 2.5 million, 5 million, 10 million, 25 million, 50 million, 75 million and 100 million |
| Logical processors | 1, 2, 3 and so on through the number of eligible IDs discovered inside Docker |
| Container RAM | 2, 4, 6, 8 and 10 GiB, using the levels strictly below Docker's reported capacity |
| Repetitions | Five independent blocks. The dataset seed is 42 and the processing order seed is 20261116. |

The single-processor comparison is part of this same plan. The sequential stream handles records on one calling thread. The parallel stream can split the records among Java threads even when they share one logical processor. Allowing more logical processors lets those threads execute simultaneously when the scheduler can place them there. The benchmark keeps its single caller and uses Java's default parallel stream behaviour throughout.

## Hardware and resource controls

The development laptop has an AMD Ryzen 7 5825U with 8 physical cores, 16 hardware threads and 16 GiB of installed RAM. A *logical processor* is one CPU ID available to the operating system. Two hardware threads on one physical core share execution resources. On Docker Desktop with WSL 2, the experiment sees the Linux VM's processor IDs and topology. The host scheduler controls their placement on the laptop's physical cores.

For automated runs, the runner discovers the IDs a fresh container may use. Where the Linux guest reports a consistent sibling topology, it selects one eligible ID from each reported core before adding sibling threads. Otherwise it uses ascending eligible IDs. Each larger CPU set adds one ID to the previous set. Both streams use the same set at a given count. `environment.json` records the discovered order and sibling information. A guest-reported group describes what Docker can see. To study specific physical cores and their SMT siblings directly, run the same protocol on native Linux with verified hardware topology. [Linux CPU topology](https://docs.kernel.org/admin-guide/cputopology.html)

Docker's `--cpuset-cpus` restricts which logical CPUs may schedule the container. Other processes can still use these CPUs. Docker's `--cpus` sets a processor-time budget and was used in the earlier experiment. Keep those historical measurements separate from the new CPU-set runs. The runner verifies Docker's configured set and the benchmark JVM's observed processor count, effective CPU set, unlimited container CPU quota, memory limit and disabled swap. [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)

Docker previously exposed 7.41 GiB RAM on this laptop. With that capacity the automatic levels are 2, 4 and 6 GiB. Docker's memory allocation can change between campaigns. Each condition sets the worker JVM's initial and maximum heap to 60% of the container RAM limit by default, rounded down to whole MiB. The separate JMH controller has a 128 MiB maximum heap. Changing the RAM limit therefore also changes the worker heap. Keep `--heap-percent` fixed when comparing RAM levels. [WSL VM settings](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)

## What the calculation does

`EventRecord` is an immutable Java record containing two `long` fields: a timestamp in seconds and an event count. `Workload.syntheticRecords` creates an `ArrayList` using `java.util.Random` with seed 42 by default.

- The selected interval is **1 January 2025 00:00:00 UTC inclusive to 2 January 2025 00:00:00 UTC exclusive**.
- Exactly half the records fall within that day, one quarter in the preceding day and one quarter in the following day.
- Timestamps are generated within those intervals and event counts range from 1 to 100.
- The list is shuffled with the same seeded generator. Each implementation receives the same values and ordering for a given size and seed.
- The generator accepts non-negative multiples of four. The runner requires a positive multiple of four.

Both timed methods filter by the same interval, map to the event count and return a `long` sum. The difference is the initial call to `stream()` or `parallelStream()`. There is no I/O, logging, dataset generation or artificial delay inside the timed calculation.

Before warm-up, every benchmark fork generates the dataset and validates its selected implementation against an independent reference loop. A mismatch fails the run. Keeping parallel workers out of sequential forks preserves the sequential memory baseline. Metadata records the expected sum and an order-sensitive 64-bit fingerprint to detect accidental dataset differences.

## Run a custom full matrix

Both `matrix` and `expanded` cross all selected levels. This example previews two dataset sizes, three processor counts, two RAM limits and both streams in one block. Supplying the counts and RAM values makes the preview independent of Docker. The exact CPU IDs are assigned when a real run probes the engine.

**PowerShell 7+**

```powershell
py -3 run.py matrix --sizes 1000 1000000 --cpu-counts 1 2 3 --memory 2048 4096 --blocks 1 --dry-run
```

**Linux/Bash**

```bash
python3 run.py matrix --sizes 1000 1000000 --cpu-counts 1 2 3 --memory 2048 4096 --blocks 1 --dry-run
```

To measure those 24 conditions, replace `--dry-run` with `--output results/matrix-cpuset-01`. The runner's `--cpu-counts` (also accepted as `--cpus`) takes whole counts, such as `1 2 3`. The underlying Docker command receives a list of actual CPU IDs.

## Optional Python virtual environment

From the repository root, create `.venv/` once. In each new terminal, run the activation command for your shell.

**PowerShell 7+**

```powershell
py -3 -m venv .\.venv
.\.venv\Scripts\Activate.ps1
```

**Linux/Bash**

```bash
python3 -m venv --without-pip ./.venv
source ./.venv/bin/activate
```

Once activated, use `python` in place of `py -3` or `python3` in the run commands.

## Docker-only single comparison

For a manual comparison without Python, build the image with `docker compose build` from the repository root. Then set a CPU ID or list of IDs eligible on your Docker engine, a memory limit and a new output directory. This example assumes CPU ID 0 is available. For automated discovery and validation use the Python runner.

**PowerShell 7+**

```powershell
$env:CPU_SET = '0'
$env:MEMORY_MIB = '2048'
$env:HEAP_PERCENT = '60'
$env:RESULTS_DIR = './results/manual-cpuset-01'
```

**Linux/Bash**

```bash
export CPU_SET=0
export MEMORY_MIB=2048
export HEAP_PERCENT=60
export RESULTS_DIR=./results/manual-cpuset-01
```

Run the comparison. This command works in **PowerShell 7+ and Bash**:

```text
docker compose run --rm benchmark -p records=1000000 -p seed=42
```

This runs both benchmark methods in one resource configuration, each in a fresh fork, using the annotation defaults of five warm-up and ten measurement iterations. For a shorter setup check, the shared command is:

```text
docker compose run --rm benchmark -p records=100000 -wi 1 -i 2 -w 200ms -r 200ms
```

Choose another `RESULTS_DIR` before running again. The entrypoint refuses to reuse an output directory containing its `.benchmark-started` marker. Manual runs contain raw JMH and resource outputs but do not create the automated summary or randomised block plan. Prefer `run.py` for the controlled matrix and its before/after limit verification.

Clear overrides when returning to default Compose settings.

**PowerShell 7+**

```powershell
Remove-Item Env:CPU_SET,Env:MEMORY_MIB,Env:HEAP_PERCENT,Env:RESULTS_DIR -ErrorAction SilentlyContinue
```

**Linux/Bash**

```bash
unset CPU_SET MEMORY_MIB HEAP_PERCENT RESULTS_DIR
```

## Tests and local development

The JUnit Jupiter 6.1.3 tests cover workload correctness, including empty data, intervals, matching sequential and parallel results and totals larger than a 32-bit integer. They also check that sequential runs start without parallel workers. The Docker build runs the Java tests automatically.

Run the Python tests for planning, feasibility checks and result validation from the repository root.

**PowerShell 7+**

```powershell
py -3 -m unittest -v test_run.py
```

**Linux/Bash**

```bash
python3 -m unittest -v test_run.py
```

To test that the built image runs both benchmarks and records resource measurements, build it first with `docker compose build`, then enable the Docker smoke test.

**PowerShell 7+**

```powershell
$env:RUN_DOCKER_SMOKE = '1'
py -3 -m unittest -v test_run.py
Remove-Item Env:RUN_DOCKER_SMOKE
```

**Linux/Bash**

```bash
RUN_DOCKER_SMOKE=1 python3 -m unittest -v test_run.py
```

With a local JDK 25+ and Maven installed, these commands work in **PowerShell 7+ and Bash**:

```text
mvn verify
java -jar target/benchmarks.jar -l
```

Use local execution for development checks. The Docker runs provide the specified resource limits and cgroup measurements. Rebuild the Docker image after changing Java sources or `entrypoint.sh`. Source hashes in `environment.json` describe the working files at invocation time. The recorded image identity describes the binary actually executed. Keep the source snapshot and image together for reproducibility, and use `--build` after edits.

## Repository layout

```text
parallel-vs-sequential-research/
├── README.md
├── run.py             Plan and run benchmarks, validate results and write the CSV summary
├── cpu_topology.py    Discover eligible Docker CPU IDs and the order for increasing CPU access
├── test_run.py        Tests for the Python runner
├── requirements.txt   Optional virtualenv tooling
├── pom.xml            Maven build, Java dependencies and JMH packaging
├── Dockerfile         Build and test the Java code, then package the runtime image
├── compose.yaml        Build settings and resource limits for manual Docker runs
├── entrypoint.sh      Configure JVM heaps and start the benchmark
├── src/
│   ├── main/java/info/remzi/experiment/  Java benchmark, workload and resource measurements
│   └── test/java/info/remzi/experiment/  Java correctness and isolation tests
├── results/           Local benchmark outputs, created when you run the experiment
├── target/            Local Maven output, created when you build with Maven on the host
└── .venv/             Optional local Python virtual environment
```

`results/`, `target/` and `.venv/` are ignored by Git.

## Sources

The research report's two scientific papers remain the literature foundation:

- [Rosales et al. (2023), Profiling and Optimizing Java Streams](https://doi.org/10.22152/programming-journal.org/2023/7/10).
- [Georges et al. (2007), Statistically Rigorous Java Performance Evaluation](https://doi.org/10.1145/1297027.1297033).

Implementation references:

- [OpenJDK JMH](https://github.com/openjdk/jmh) and [JMH 1.37 profiler implementation](https://github.com/openjdk/jmh/blob/1.37/jmh-core/src/main/java/org/openjdk/jmh/profile/GCProfiler.java).
- [Java SE 25 streams](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/util/stream/package-summary.html).
- [Java process CPU-time API](https://docs.oracle.com/en/java/javase/25/docs/api/jdk.management/com/sun/management/OperatingSystemMXBean.html).
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/).
- [Docker Compose services](https://docs.docker.com/reference/compose-file/services/).
- [Linux cgroup v2 accounting](https://docs.kernel.org/admin-guide/cgroup-v2.html).
