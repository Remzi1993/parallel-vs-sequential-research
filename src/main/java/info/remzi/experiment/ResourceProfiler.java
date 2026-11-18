package info.remzi.experiment;

import com.sun.management.OperatingSystemMXBean;
import org.openjdk.jmh.infra.BenchmarkParams;
import org.openjdk.jmh.infra.IterationParams;
import org.openjdk.jmh.profile.InternalProfiler;
import org.openjdk.jmh.results.AggregationPolicy;
import org.openjdk.jmh.results.IterationResult;
import org.openjdk.jmh.results.Result;
import org.openjdk.jmh.results.ScalarResult;
import org.openjdk.jmh.runner.IterationType;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Locale;

/** Measurements surround JMH iterations, including harness transitions and this sampler's overhead. */
public class ResourceProfiler implements InternalProfiler {
    private static final long SAMPLE_INTERVAL_MILLIS = 100;
    private static final double BYTES_PER_MIB = 1024.0 * 1024.0;
    private final OperatingSystemMXBean os = ManagementFactory.getOperatingSystemMXBean()
            instanceof OperatingSystemMXBean bean ? bean : null;
    private final MemoryMXBean memory = ManagementFactory.getMemoryMXBean();
    private final List<MemorySample> samples = new ArrayList<>();
    private volatile boolean sampling;
    private volatile Throwable samplingFailure;
    private Thread sampler;
    private long startNanos;
    private long startCpuNanos;
    private long startThrottledMicros;
    private int measurementIteration;

    @Override
    public String getDescription() {
        return "Measurement-only JVM CPU time and 100 ms cgroup/heap memory samples";
    }

    @Override
    public void beforeIteration(BenchmarkParams benchmark, IterationParams iteration) {
        if (iteration.getType() != IterationType.MEASUREMENT) {
            return;
        }
        if (benchmark.getWarmup().getCount() < 1) {
            throw new IllegalArgumentException("Resource profiling requires at least one warm-up iteration");
        }
        samples.clear();
        samplingFailure = null;
        startThrottledMicros = throttledMicros();
        startNanos = System.nanoTime();
        startCpuNanos = processCpuNanos();
        sampling = true;
        sampler = new Thread(() -> {
            while (sampling) {
                sampleMemory();
                try {
                    Thread.sleep(SAMPLE_INTERVAL_MILLIS);
                } catch (InterruptedException exception) {
                    return;
                }
            }
        }, "experiment-memory-sampler");
        sampler.setDaemon(true);
        sampler.setUncaughtExceptionHandler((thread, failure) -> samplingFailure = failure);
        sampler.start();
    }

    @Override
    public Collection<? extends Result> afterIteration(BenchmarkParams benchmark, IterationParams iteration,
                                                       IterationResult result) {
        if (iteration.getType() != IterationType.MEASUREMENT) {
            return List.of();
        }
        long endCpuNanos = processCpuNanos();
        long elapsedNanos = System.nanoTime() - startNanos;
        long endThrottledMicros = throttledMicros();
        sampling = false;
        sampler.interrupt();
        try {
            sampler.join();
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while stopping resource sampling", exception);
        }
        if (samplingFailure != null) {
            throw new IllegalStateException("Resource sampler failed; measurements are invalid", samplingFailure);
        }
        samples.removeIf(sample -> sample.elapsedNanos() > elapsedNanos);
        measurementIteration++;
        long operations = result.getMetadata().getAllOps();
        long cpuNanos = difference(startCpuNanos, endCpuNanos);
        long throttledMicros = difference(startThrottledMicros, endThrottledMicros);
        List<Result> results = new ArrayList<>();
        double cpuPerOperation = cpuNanos >= 0 && operations > 0
                ? cpuNanos / 1_000_000.0 / operations : Double.NaN;
        results.add(new ScalarResult("cpu.msPerOperation", cpuPerOperation, "ms/op", AggregationPolicy.AVG));
        addMemoryResults(results, "container", samples.stream().mapToLong(MemorySample::containerBytes)
                .filter(value -> value >= 0).toArray());
        addMemoryResults(results, "heap", samples.stream().mapToLong(MemorySample::heapBytes)
                .filter(value -> value >= 0).toArray());
        results.add(new ScalarResult("memory.sampleCount", samples.size(), "samples", AggregationPolicy.SUM));
        writeRawData(benchmark, operations, cpuNanos, elapsedNanos, throttledMicros);
        return results;
    }

    private void sampleMemory() {
        long container = parseNonnegative(RunMetadata.cgroupValue("memory.current"));
        long heap = memory.getHeapMemoryUsage().getUsed();
        long elapsed = System.nanoTime() - startNanos;
        samples.add(new MemorySample(elapsed, container, heap));
    }

    private long processCpuNanos() {
        return os == null ? -1 : os.getProcessCpuTime();
    }

    private static long throttledMicros() {
        for (String line : RunMetadata.cgroupValue("cpu.stat").split("\\R")) {
            if (line.startsWith("throttled_usec ")) {
                return parseNonnegative(line.substring("throttled_usec ".length()));
            }
        }
        return -1;
    }

    private static long parseNonnegative(String value) {
        try {
            long parsed = Long.parseLong(value.strip());
            return parsed >= 0 ? parsed : -1;
        } catch (NumberFormatException exception) {
            return -1;
        }
    }

    private static long difference(long start, long end) {
        return start >= 0 && end >= start ? end - start : -1;
    }

    private static void addMemoryResults(List<Result> results, String prefix, long[] values) {
        if (values.length == 0) {
            // JMH can fill absent results with zero. NaN preserves unavailable measurements instead.
            results.add(new ScalarResult(prefix + ".meanMiB", Double.NaN, "MiB", AggregationPolicy.AVG));
            results.add(new ScalarResult(prefix + ".maxObservedMiB", Double.NaN, "MiB", AggregationPolicy.MAX));
            return;
        }
        double sum = 0;
        long max = 0;
        for (long value : values) {
            sum += value;
            max = Math.max(max, value);
        }
        results.add(new ScalarResult(prefix + ".meanMiB", sum / values.length / BYTES_PER_MIB,
                "MiB", AggregationPolicy.AVG));
        results.add(new ScalarResult(prefix + ".maxObservedMiB", max / BYTES_PER_MIB,
                "MiB", AggregationPolicy.MAX));
    }

    private void writeRawData(BenchmarkParams benchmark, long operations, long cpuNanos, long elapsedNanos,
                              long throttledMicros) {
        try {
            Path directory = RunMetadata.outputDirectory();
            String pid = Long.toString(ProcessHandle.current().pid());
            Path iterations = directory.resolve("resources-" + pid + ".csv");
            boolean newIterations = !Files.exists(iterations);
            try (BufferedWriter writer = Files.newBufferedWriter(iterations, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
                if (newIterations) {
                    writer.write("benchmark,records,seed,measurement_iteration,all_operations,cpu_nanos,"
                            + "interval_nanos,throttled_micros,sample_count\n");
                }
                writer.write(String.format(Locale.ROOT, "%s,%s,%s,%d,%d,%s,%d,%s,%d%n",
                        benchmark.getBenchmark(), benchmark.getParam("records"), benchmark.getParam("seed"),
                        measurementIteration, operations, available(cpuNanos), elapsedNanos,
                        available(throttledMicros), samples.size()));
            }
            Path memorySamples = directory.resolve("memory-samples-" + pid + ".csv");
            boolean newSamples = !Files.exists(memorySamples);
            try (BufferedWriter writer = Files.newBufferedWriter(memorySamples, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
                if (newSamples) {
                    writer.write("benchmark,measurement_iteration,elapsed_nanos,container_bytes,heap_bytes\n");
                }
                for (MemorySample sample : samples) {
                    writer.write(String.format(Locale.ROOT, "%s,%d,%d,%s,%s%n", benchmark.getBenchmark(),
                            measurementIteration, sample.elapsedNanos(), available(sample.containerBytes()),
                            available(sample.heapBytes())));
                }
            }
        } catch (IOException exception) {
            throw new UncheckedIOException("Could not preserve resource measurements", exception);
        }
    }

    private static String available(long value) {
        return value < 0 ? "unavailable" : Long.toString(value);
    }

    private record MemorySample(long elapsedNanos, long containerBytes, long heapBytes) {
    }
}
