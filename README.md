# Sequential and parallel processing in Java

## Run the experiment

Start Docker with Linux containers (cgroup v2) and open a terminal in the repository folder. Run the following command with Python 3.10 or later.

```sh
python3 run.py
```

On Windows, run `py run.py`. Use `py` in place of `python3` in the examples below.

The runner builds the image and detects the resources available to Docker. Processor counts increase from 1 to the detected maximum in steps of 1. RAM limits start at 1 GiB and increase by 1 GiB while staying below Docker’s memory limit, with a smaller limit on low-memory machines. The default run has a two-hour budget including the Docker build. If time runs out, it saves completed comparisons and marks unfinished runs in the summary.

## Research question

When does parallel processing save enough time over sequential processing, given the record count, available logical processors and RAM?

Both streams filter and sum the same records. At 1,000 and 1 million records, the experiment tests every processor count at every RAM limit. At 5 million records it tests every processor count at the highest RAM limit.

Each setting gets two independent repetitions. The streams run back-to-back in separate JVMs on the same CPU IDs, in reverse order on the second repetition. Each JVM warms up for two half-second iterations, then measures the calculation for three more. JVM startup and data generation add to the total time you wait. The reported processing time covers only the filter and sum. With 16 logical processors and seven RAM levels, that makes 960 runs.

## Results

Each run creates a new folder under `results/`. Open `comparisons.csv` to see how much time parallel processing saved, its CPU and memory cost, and the gain from adding one more processor. `summary.csv` contains individual measurements and statuses. `environment.json` and `runs.json` record the setup and run order.

## Options and tests

- **Quick benchmark:** `python3 run.py smoke --build`
- **All options:** `python3 run.py --help`
- **Python unit tests:** `python3 -m unittest test_run`. The Docker build also runs the Java unit tests.
