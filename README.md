# Sequential and parallel processing in Java

## Run the experiment

Start Docker with Linux containers (cgroup v2) and open a terminal in the repository folder. Run the following command with Python 3.10 or later.

```sh
python3 run.py
```

On Windows, run `py run.py`. Use `py` in place of `python3` in the examples below.

The runner builds the image and detects the resources available to Docker. Processor counts increase from 1 to the detected maximum in steps of 1. The RAM limit increases in 2 GiB steps below Docker’s memory limit, with a smaller limit on low-memory machines.

## Research question

When does parallel processing save enough time over sequential processing, given the record count, available logical processors and RAM?

Both Java streams filter and sum the same records. Each combination of dataset size (100 to 100 million records), processor count and RAM limit is tested in five randomised blocks. Both streams use the same CPU IDs at each setting.

## Results

Each run creates a new folder under `results/`. Open `comparisons.csv` to see how much time parallel processing saved, its CPU and memory cost, and the gain from adding one more processor. `summary.csv` contains individual measurements and statuses. `environment.json` and `runs.json` record the setup and run order.

## Options and tests

- **Quick benchmark:** `python3 run.py smoke --build`
- **All options:** `python3 run.py --help`
- **Python unit tests:** `python3 -m unittest test_run`. The Docker build also runs the Java unit tests.
