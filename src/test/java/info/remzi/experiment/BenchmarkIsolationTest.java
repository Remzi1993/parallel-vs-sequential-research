package info.remzi.experiment;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.openjdk.jmh.runner.Runner;
import org.openjdk.jmh.runner.options.OptionsBuilder;
import org.openjdk.jmh.runner.options.TimeValue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;

class BenchmarkIsolationTest {
    @TempDir
    Path output;

    @Test
    void sequentialForkDoesNotStartParallelWorkersDuringSetup() throws Exception {
        new Runner(new OptionsBuilder()
                .include("^" + StreamBenchmark.class.getName().replace(".", "\\.") + "\\.sequential$")
                .param("records", "1000")
                .forks(1).warmupIterations(1).measurementIterations(1)
                .warmupTime(TimeValue.milliseconds(50)).measurementTime(TimeValue.milliseconds(50))
                .jvmArgsAppend("-Dexperiment.outputDir=" + output)
                .output(output.resolve("jmh.log").toString())
                .shouldFailOnError(true).build()).run();
        try (var paths = Files.list(output)) {
            var metadataFile = paths.filter(path -> path.getFileName().toString().startsWith("metadata-")).findFirst().orElseThrow();
            Properties metadata = new Properties();
            try (var reader = Files.newBufferedReader(metadataFile)) {
                metadata.load(reader);
            }
            assertEquals("0", metadata.getProperty("commonPoolWorkersAtSetup"),
                    "A sequential fork must not pre-start parallel workers and inflate its memory baseline");
        }
    }
}
