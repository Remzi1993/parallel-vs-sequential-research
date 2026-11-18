package info.remzi.experiment;

import java.io.IOException;
import java.io.Writer;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;
import java.util.concurrent.ForkJoinPool;

final class RunMetadata {
    private RunMetadata() {
    }

    static Path outputDirectory() throws IOException {
        Path directory = Path.of(System.getProperty("experiment.outputDir", "results"));
        Files.createDirectories(directory);
        return directory;
    }

    static String cgroupValue(String file) {
        try {
            return Files.readString(Path.of("/sys/fs/cgroup", file)).strip();
        } catch (IOException exception) {
            return "unavailable";
        }
    }

    static void write(int records, long seed, long expected, String checksum) throws IOException {
        Properties metadata = new Properties();
        metadata.setProperty("pid", Long.toString(ProcessHandle.current().pid()));
        String cpuModel = "unavailable";
        try {
            cpuModel = Files.readString(Path.of("/proc/cpuinfo")).lines()
                    .filter(line -> line.startsWith("model name"))
                    .map(line -> line.substring(line.indexOf(':') + 1).strip()).findFirst().orElse(cpuModel);
        } catch (IOException exception) {
            // Local non-Linux development runs do not expose /proc/cpuinfo.
        }
        metadata.setProperty("cpuModel", cpuModel);
        for (String property : new String[]{"java.version", "java.runtime.version", "java.vendor",
                "java.vm.name", "java.vm.version", "os.name", "os.version", "os.arch"}) {
            metadata.setProperty(property, System.getProperty(property, "unavailable"));
        }
        metadata.setProperty("records", Integer.toString(records));
        metadata.setProperty("seed", Long.toString(seed));
        metadata.setProperty("expectedSum", Long.toString(expected));
        metadata.setProperty("datasetChecksum", checksum);
        metadata.setProperty("datasetRepresentation", "ArrayList<EventRecord(long timestampSeconds,long eventCount)>");
        metadata.setProperty("intervalStartInclusive", Long.toString(Workload.START_INCLUSIVE));
        metadata.setProperty("intervalEndExclusive", Long.toString(Workload.END_EXCLUSIVE));
        metadata.setProperty("availableProcessors", Integer.toString(Runtime.getRuntime().availableProcessors()));
        metadata.setProperty("commonPoolParallelism", Integer.toString(ForkJoinPool.getCommonPoolParallelism()));
        metadata.setProperty("commonPoolWorkersAtSetup", Long.toString(Thread.getAllStackTraces().keySet().stream()
                .filter(thread -> thread.getName().startsWith("ForkJoinPool.commonPool-worker-")).count()));
        metadata.setProperty("heapMaxBytes", Long.toString(Runtime.getRuntime().maxMemory()));
        metadata.setProperty("heapCommittedAtSetupBytes", Long.toString(Runtime.getRuntime().totalMemory()));
        metadata.setProperty("jvmArguments", String.join(" ", ManagementFactory.getRuntimeMXBean().getInputArguments()));
        metadata.setProperty("garbageCollectors", ManagementFactory.getGarbageCollectorMXBeans().stream()
                .map(bean -> bean.getName()).reduce((left, right) -> left + ", " + right).orElse("unavailable"));
        for (String file : new String[]{"cpu.max", "memory.max", "memory.swap.max", "cpu.stat"}) {
            metadata.setProperty("cgroup." + file, cgroupValue(file));
        }
        Path target = outputDirectory().resolve("metadata-" + ProcessHandle.current().pid() + ".properties");
        try (Writer writer = Files.newBufferedWriter(target, StandardCharsets.UTF_8)) {
            metadata.store(writer, "Benchmark fork metadata; generated before warm-up");
        }
    }
}
