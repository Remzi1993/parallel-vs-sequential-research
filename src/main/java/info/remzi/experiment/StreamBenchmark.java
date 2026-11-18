package info.remzi.experiment;

import org.openjdk.jmh.annotations.Benchmark;
import org.openjdk.jmh.annotations.BenchmarkMode;
import org.openjdk.jmh.annotations.Fork;
import org.openjdk.jmh.annotations.Level;
import org.openjdk.jmh.annotations.Measurement;
import org.openjdk.jmh.annotations.Mode;
import org.openjdk.jmh.annotations.OutputTimeUnit;
import org.openjdk.jmh.annotations.Param;
import org.openjdk.jmh.annotations.Scope;
import org.openjdk.jmh.annotations.Setup;
import org.openjdk.jmh.annotations.State;
import org.openjdk.jmh.annotations.Threads;
import org.openjdk.jmh.annotations.Warmup;
import org.openjdk.jmh.infra.BenchmarkParams;

import java.io.IOException;
import java.util.ArrayList;
import java.util.concurrent.TimeUnit;

@State(Scope.Benchmark)
@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@Threads(1)
@Fork(5)
@Warmup(iterations = 5, time = 1)
@Measurement(iterations = 10, time = 1)
public class StreamBenchmark {
    @Param({"100000"})
    public int records;

    @Param({"42"})
    public long seed;

    private ArrayList<EventRecord> dataset;

    @Setup(Level.Trial)
    public void setup(BenchmarkParams benchmark) throws IOException {
        dataset = Workload.syntheticRecords(records, seed);
        long expected = Workload.reference(dataset);
        // Do not start parallel workers in a sequential fork: their memory would bias its baseline.
        long actual = benchmark.getBenchmark().endsWith(".parallel")
                ? Workload.parallel(dataset) : Workload.sequential(dataset);
        if (actual != expected) {
            throw new IllegalStateException("Calculation failed correctness validation: expected="
                    + expected + ", actual=" + actual);
        }
        String checksum = Workload.checksum(dataset);
        RunMetadata.write(records, seed, expected, checksum);
        System.out.printf("Validated %,d records: sum=%d, checksum=%s%n", records, expected, checksum);
    }

    @Benchmark
    public long sequential() {
        return Workload.sequential(dataset);
    }

    @Benchmark
    public long parallel() {
        return Workload.parallel(dataset);
    }
}
