# Sequential and parallel processing in Java

## Run the experiment

Start Docker with Linux containers (cgroup v2) and open a terminal in the repository folder. Run the following command with Python 3.10 or later.

```sh
python3 run.py
```

On Windows, run `py run.py`. Use `py` in place of `python3` in the examples below.

The runner builds the image and tests every logical processor count from 1 to Docker’s maximum. It tests RAM in 2 GiB steps (2, 4, 6 GiB and so on), always below Docker’s memory limit. If Docker has 2 GiB or less, the runner uses one smaller limit. The default run has a two-hour budget including the build. If time runs out, it saves completed comparisons and marks unfinished runs in the summary.

## Research question

When does parallel processing save enough time over sequential processing, given the record count, available logical processors and RAM?

Both streams filter and sum the same records.

| Records | Logical processor counts | RAM limits |
| --- | --- | --- |
| 1,000 and 1 million | Every count | Every limit |
| 100,000, 5 million, 10 million and 25 million | Every count | Highest selected limit |
| 50 million, 75 million and 100 million | Lowest, middle and highest selected counts | 6 GiB, or the closest selected limit |

With 16 logical processors, the three largest datasets run at 1, 8 and 16 processors. The runner uses the lower middle count when there are two middle counts.

Each setting gets two independent repetitions. The streams run back-to-back in separate JVMs on the same CPU IDs, in reverse order on the second repetition. Each JVM warms up for two half-second iterations, then measures the calculation for three more. JVM startup and data generation add to the total time you wait. The reported processing time covers only the filter and sum. With 16 logical processors and RAM limits of 2, 4 and 6 GiB, the runner schedules 676 runs, including 36 for the three largest datasets.

## Results

Each run creates a new folder under `results/`. Open `comparisons.csv` to see how much time parallel processing saved, its CPU and memory cost, and the gain from adding one more processor. `summary.csv` contains individual measurements and statuses. `environment.json` and `runs.json` record the setup and run order.

To see what going from 8 to 16 processors gained, compare `parallel_ms` for those two rows in `comparisons.csv` at the same record count, RAM limit and repetition (`block`). Lower is faster.

## Options and tests

- **Quick benchmark:** `python3 run.py smoke --build`
- **All options:** `python3 run.py --help`
- **Python unit tests:** `python3 -m unittest test_run`. The Docker build also runs the Java unit tests.
