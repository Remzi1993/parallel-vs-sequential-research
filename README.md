# Controlled Java stream experiment

This experiment compares **`stream()` and `parallelStream()`** on the same in-memory filtering and summation task. It measures elapsed time, JVM CPU time and observed container memory under controlled CPU and RAM limits.

Follow [Run the experiment](#run-the-experiment) to set up Docker, check the setup, run a pilot and collect formal measurements. The main results file is [`summary.csv`](#find-and-read-the-results) in the output directory for each run.

## Run the experiment

### 1. Set up Docker and Python

- On Windows, start Docker Desktop with Linux containers. On Linux, start Docker Engine with the Compose plugin. The runner requires Linux containers with cgroup v2.
- Install Python 3.10 or later. The runner uses only the standard library. Docker supplies Java 25 and Maven 3.9.16, so the experiment commands only need Docker and Python on the host.
- Start in the project root. The following commands work in **PowerShell 7+ and Bash**.

```text
cd experiment
docker version
docker compose version
docker compose build
```

Run the remaining commands from the `experiment` folder unless stated otherwise. If you prefer an isolated Python environment, see [Optional Python virtual environment](#optional-python-virtual-environment).

### 2. Check the setup with a short run

This runs both implementations on 100,000 records with 1 CPU equivalent and 512 MiB of RAM. Open `results/smoke-01/summary.csv` and check that both rows show `status=ok`.

**PowerShell 7+**

```powershell
py -3 run.py smoke --output results/smoke-01
```

**Linux/Bash**

```bash
python3 run.py smoke --output results/smoke-01
```

### 3. Run a pilot

This uses the 11 dataset sizes at 4 CPU equivalents and adds the 10-million-record comparison across 2, 4 and 6 GiB. It runs one block of 26 conditions using one warm-up and two measurement iterations.

**PowerShell 7+**

```powershell
py -3 run.py expanded --cpus 4 --blocks 1 --warmup 1 --iterations 2 --output results/pilot-expanded-01
```

**Linux/Bash**

```bash
python3 run.py expanded --cpus 4 --blocks 1 --warmup 1 --iterations 2 --output results/pilot-expanded-01
```

Open `results/pilot-expanded-01/summary.csv` and check the `status` column. For the largest datasets, use the per-condition `jmh.json` and `resources-*.csv` to review iteration times and completed calculations. If long calculations finish too few operations in an iteration or timing is still changing during warm-up, adjust `--seconds` or `--warmup` before setting the formal protocol. Use a fixed power profile and limit unrelated activity during the formal run.

### 4. Run the expanded experiment

You can preview the full plan with the first command in each block. The **`--dry-run`** command prints the plan without starting Docker or writing files. The second command starts the measurement campaign and writes `results/formal-expanded-01/summary.csv`.

**PowerShell 7+**

```powershell
py -3 run.py expanded --dry-run
py -3 run.py expanded --output results/formal-expanded-01
```

**Linux/Bash**

```bash
python3 run.py expanded --dry-run
python3 run.py expanded --output results/formal-expanded-01
```

The default design schedules **370 conditions in five blocks**. The five warm-up and ten measurement iterations of one second each total about **93 minutes** of nominal iteration time. Container startup, dataset generation, validation and long calculations add time. Use the pilot to set durations and repetition counts. Add `--build` if you have changed the Java or Docker build files since building the image.

Choose a new name for each `--output` directory. For example, use `results/formal-expanded-02` for the next formal run. The runner rejects existing directories. Use [`matrix` mode](#run-a-custom-full-matrix) for a full combination of all selected sizes, CPU quotas and RAM limits.

## Find and read the results

Open **`experiment/results/formal-expanded-01/summary.csv`** from the project root after running the named command above. For a pilot or smoke run, replace `formal-expanded-01` with the name you passed to `--output`. The terminal prints the full path while the run starts and again on completion.

A relative `--output` path starts from your terminal's working directory. If you omit it, the runner creates a folder under `experiment/results/` named after the mode, a UTC timestamp and a short random suffix. For example, an expanded run gets an `expanded-...` directory. The `results` folder is ignored by Git and contains the measurements on the machine where you ran the experiment.

```text
experiment/results/formal-expanded-01/
├── summary.csv              Main table for Excel, one row per condition and block
├── runs.json                The same plan and progress as structured data
├── environment.json         Selected settings, software versions and Docker resources
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
| `records`, `cpu`, `memory_mib` | Dataset size and configured CPU and RAM limits. 2,048 MiB is 2 GiB. |
| `implementation`, `block` | Sequential or parallel processing and the independent repetition number. |
| `processing_ms` | Mean elapsed milliseconds for one complete filter-and-sum calculation in that fork. Lower means it finished sooner. |
| `cpu_ms_per_operation` | CPU milliseconds used by all threads in the benchmark JVM per calculation. Parallel CPU time can exceed elapsed time. |
| `container_mean_mib` | Mean of the available samples of charged container memory. It includes both JVMs. |
| `container_max_observed_mib` | Highest observed container memory sample. Sampling may miss brief peaks. |
| `memory_samples`, `dataset_checksum`, `expected_sum` | Sample count, a fingerprint of the generated input and the expected calculation result. |
| `status`, `directory`, `error` | Outcome, folder for the attempted condition and any preflight or validation error. |

Resource measurements cover the measurement iterations and short harness transitions. They exclude dataset setup and warm-up. CPU time per operation uses the `all_operations` count for those intervals. The profiler reads cgroup v2 `memory.current` about every 100 ms. That value includes the JVM heaps, other memory used by both JVMs and any other charges to the container. The separate sampled heap column in `memory-samples-*.csv` is already part of container memory.

For Excel, import `summary.csv` via **Data → From Text/CSV** with the comma delimiter. Set the timing and memory metrics to decimal numbers using **English (United States)** as the import locale to interpret the decimal points.

Filter to `status=ok` for timing comparisons. Compare sequential and parallel at the same `records`, `cpu`, `memory_mib` and `block`. The dataset checksum and expected sum should agree. Divide sequential `processing_ms` by parallel `processing_ms` for speedup. A value above 1 favours parallel.

Use the independent block results to calculate the report's 95% confidence intervals. The 99.9% interval and average-time percentiles in `jmh.json` summarise iteration scores within one fork. For the implementation and setup effort comparison, keep a dated log of active minutes for shared setup, each implementation and correctness checks. Record AI assistance and previous experience in that log.

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

**100 million was the largest size verified on this setup.** Both implementations completed at 4 CPU equivalents with a 6 GiB container limit. The 60% heap policy capped the worker JVM at **3,686 MiB (about 3.60 GiB)**. Both runs had an average sampled heap usage of **3.38 GiB** during measurement. At a 2 GiB container limit, the earlier 100-million-record verification ran out of memory. The current runner skips that setting before allocation because the fields alone exceed the smaller heap.

The benchmark creates the whole dataset in an `ArrayList` before measuring. Each record has two Java `long` fields, which require **16 bytes per record** before Java adds object headers and the list's references. The figures below are lower bounds. [Java integral types](https://docs.oracle.com/javase/specs/jls/se25/html/jls-4.html#jls-4.2.1) and [ArrayList storage](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/util/ArrayList.html).

| Records | Memory for the two fields alone | What this means on this host |
| --- | ---: | --- |
| 100 million | 1.49 GiB | Both implementations completed under the 6 GiB container limit. |
| 500 million | 7.45 GiB | The fields alone exceed the Docker VM's 7.41 GiB total RAM. |
| 1 billion | 14.90 GiB | The fields alone need over four times the 3.60 GiB worker heap. |
| 50 billion | 745.06 GiB | This also exceeds the integer record-count limit of the current single-list implementation. |

The exact limit above 100 million depends on object overhead and available heap, so larger intermediate sizes would need their own feasibility checks. A billion records is already outside this machine's capacity on the current workload. Processing them in batches or from disk would change the storage and access pattern, so those measurements would describe a different experiment.

## Experiment design

| Comparison | Settings that vary | Settings held fixed |
| --- | --- | --- |
| Dataset size | 100,000, 250,000, 500,000, 1 million, 2.5 million, 5 million, 10 million, 25 million, 50 million, 75 million and 100 million records | 4 CPU equivalents, 6 GiB RAM |
| CPU, RAM and their combined effect | Every combination of CPU allowances 1, 2, 4, 6, 8, 10, 12, 14 and 16 and RAM limits 2, 4 and 6 GiB | 10 million records |

Both parts run both implementations. They share the condition at 10 million records, 4 CPU equivalents and 6 GiB. Each of the five default blocks runs the 74 distinct conditions once in a reproducibly randomised order, one container and fresh benchmark JVM at a time.

Within the resource grid, vary CPU at one RAM level to measure the CPU effect. Vary RAM at one CPU level to measure the RAM effect. Compare the CPU curves across RAM levels to see if they change differently. These resource comparisons cover the reference size of 10 million records.

`--sizes`, `--cpus` and `--memory` override the levels. If an override omits 10 million, the grid uses the smallest selected dataset. If it omits 4 CPU equivalents, the size sweep uses the lowest selected CPU allowance. The size sweep uses the highest selected RAM allowance. The default dataset seed is 42 and the default order seed is 20261116. Both are saved alongside overrides in `environment.json`.

## Hardware and resource controls

The development machine is a Windows 11 laptop with an AMD Ryzen 7 5825U, 8 physical cores, 16 logical processors and 16 GiB of installed RAM. Docker Desktop on WSL 2 exposed 16 logical processors and **7.41 GiB RAM** during verification. CPU allowance measures an aggregate CPU-time quota on the shared host. [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)

The **2, 4 and 6 GiB** memory settings fit the Docker allocation. **8 and 10 GiB** exceed it. More memory in the Docker VM requires enough headroom for Windows and WSL. Record the Docker allocation for each campaign. [WSL VM settings](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)

The runner checks the applied CPU and RAM limits and zero container swap. The benchmark fork uses G1 and sets its initial and maximum heap to `--heap-percent` percent of the container limit, rounded down to whole MiB. The default is 60%. The separate JMH controller uses a 128 MiB maximum heap. Increasing the RAM allowance also increases the heap available to the worker, so the experiment measures the effect of that memory policy. Keep the policy fixed during formal comparisons.

Short verification runs completed all nine CPU quotas at 100,000 records and 2 GiB RAM, and both implementations also ran at 4 and 6 GiB with 4 CPU equivalents. The expanded check at 4 CPU equivalents also completed all 11 sizes at 6 GiB and the 10-million-record cases at 2 and 4 GiB. See `results/verification-expanded-*` if those local checks are present.

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

Use `matrix` when you want to combine **every** supplied dataset size, CPU quota and RAM limit. This example compares both implementations at 1 million records using four CPU quotas and two RAM limits.

**PowerShell 7+**

```powershell
py -3 run.py matrix --sizes 1000000 --cpus 1 2 4 8 --memory 2048 4096 --blocks 1 --output results/matrix-example-01
```

**Linux/Bash**

```bash
python3 run.py matrix --sizes 1000000 --cpus 1 2 4 8 --memory 2048 4096 --blocks 1 --output results/matrix-example-01
```

Without overrides, `matrix` plans 24 conditions per block across 100,000, 1 million and 100 million records, 1 and 4 CPU equivalents, and 2 and 6 GiB of RAM. The 100-million-record conditions at 2 GiB are recorded as infeasible under the default heap policy.

## Optional Python virtual environment

From the project root, create the environment once and activate it when opening a new terminal.

**PowerShell 7+**

```powershell
py -3 -m venv .\experiment\.venv
.\experiment\.venv\Scripts\Activate.ps1
```

**Linux/Bash**

```bash
python3 -m venv --without-pip ./experiment/.venv
source ./experiment/.venv/bin/activate
```

If you are already in `experiment`, activate it with `.\.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` in Bash. Once activated, use `python` in place of `py -3` or `python3` in the run commands.

## Docker-only single comparison

For a manual comparison without Python, build the image with `docker compose build` from `experiment`. Then select the resource limits and a new output directory.

**PowerShell 7+**

```powershell
$env:CPU_ALLOWANCE = '2'
$env:MEMORY_MIB = '2048'
$env:HEAP_PERCENT = '60'
$env:RESULTS_DIR = './results/manual-01'
```

**Linux/Bash**

```bash
export CPU_ALLOWANCE=2
export MEMORY_MIB=2048
export HEAP_PERCENT=60
export RESULTS_DIR=./results/manual-01
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
Remove-Item Env:CPU_ALLOWANCE,Env:MEMORY_MIB,Env:HEAP_PERCENT,Env:RESULTS_DIR -ErrorAction SilentlyContinue
```

**Linux/Bash**

```bash
unset CPU_ALLOWANCE MEMORY_MIB HEAP_PERCENT RESULTS_DIR
```

## Tests and local development

The JUnit Jupiter 6.1.3 tests cover workload correctness, including empty data, intervals, matching sequential and parallel results and totals larger than a 32-bit integer. They also check that sequential runs start without parallel workers. The Docker build runs the Java tests automatically.

Run the Python tests for planning, feasibility checks and result validation from `experiment`.

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

## Files and their purpose

- `pom.xml`: pinned Java dependencies, JMH annotation processing, tests and executable JAR packaging.
- `src/main/java/.../Workload.java` and `EventRecord.java`: synthetic input and equivalent calculations.
- `StreamBenchmark.java`: JMH entry points, setup and correctness checks.
- `ResourceProfiler.java` and `RunMetadata.java`: measurement-only resource collection and reproducibility metadata.
- `Dockerfile`, `compose.yaml` and `entrypoint.sh`: repeatable build, resource allowances and separate controller/worker heaps.
- `run.py`: Docker inspection, randomised blocks, result validation, failure preservation and CSV summary.
- `src/test/java/.../WorkloadTest.java`, `BenchmarkIsolationTest.java` and `test_run.py`: workload correctness, fresh-JVM isolation and runner-validation tests.
- `.dockerignore` and `.gitignore`: keep generated measurements, build output and local settings out of image contexts and source commits.
- `.gitattributes`: preserve LF line endings for scripts and source.

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
