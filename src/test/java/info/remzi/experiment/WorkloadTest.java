package info.remzi.experiment;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class WorkloadTest {
    @Test
    void selectsLowerBoundaryButNotUpperBoundary() {
        var records = List.of(new EventRecord(Workload.START_INCLUSIVE - 1, 100),
                new EventRecord(Workload.START_INCLUSIVE, 7),
                new EventRecord(Workload.END_EXCLUSIVE - 1, 11),
                new EventRecord(Workload.END_EXCLUSIVE, 1000));
        assertAllImplementations(18, records);
    }

    @Test
    void handlesEmptyAndNonmatchingData() {
        assertAllImplementations(0, List.of());
        assertAllImplementations(0, List.of(new EventRecord(Workload.END_EXCLUSIVE, 99)));
    }

    @Test
    void sumsAllMatchesWithoutIntegerOverflow() {
        assertAllImplementations(4_294_967_294L,
                List.of(new EventRecord(Workload.START_INCLUSIVE, Integer.MAX_VALUE),
                        new EventRecord(Workload.START_INCLUSIVE + 1, Integer.MAX_VALUE)));
        assertAllImplementations(42, List.of(new EventRecord(Workload.START_INCLUSIVE, 42)));
    }

    @ParameterizedTest
    @ValueSource(ints = {0, 4, 100, 100_000})
    void generatedDatasetsHaveExactDistributionAndEquivalentResults(int count) {
        var data = Workload.syntheticRecords(count, 42);
        assertEquals(count, data.size());
        assertEquals(count / 2, data.stream().filter(r -> r.timestampSeconds() >= Workload.START_INCLUSIVE
                && r.timestampSeconds() < Workload.END_EXCLUSIVE).count());
        assertEquals(count / 4, data.stream().filter(r -> r.timestampSeconds() < Workload.START_INCLUSIVE).count());
        assertEquals(count / 4, data.stream().filter(r -> r.timestampSeconds() >= Workload.END_EXCLUSIVE).count());
        assertTrue(data.stream().allMatch(r -> r.eventCount() >= 1 && r.eventCount() <= 100));
        var original = List.copyOf(data);
        assertAllImplementations(Workload.reference(data), data);
        assertEquals(original, data, "Processing must not change the source dataset");
    }

    @Test
    void seedReproducesDataAndOrdering() {
        var first = Workload.syntheticRecords(1000, 42);
        var same = Workload.syntheticRecords(1000, 42);
        var different = Workload.syntheticRecords(1000, 43);
        assertEquals(first, same);
        assertEquals(Workload.checksum(first), Workload.checksum(same));
        assertNotEquals(first, different);
        assertNotEquals(Workload.checksum(first), Workload.checksum(different));
        long matchesInFirstHalf = first.subList(0, 500).stream()
                .filter(r -> r.timestampSeconds() >= Workload.START_INCLUSIVE
                        && r.timestampSeconds() < Workload.END_EXCLUSIVE).count();
        assertTrue(matchesInFirstHalf > 0 && matchesInFirstHalf < 500,
                "Matching records should be shuffled across the dataset");
    }

    @ParameterizedTest
    @ValueSource(ints = {-4, -1, 1, 3, 101})
    void rejectsSizesThatCannotHaveTheSpecifiedDistribution(int count) {
        assertThrows(IllegalArgumentException.class, () -> Workload.syntheticRecords(count, 42));
    }

    private static void assertAllImplementations(long expected, List<EventRecord> records) {
        assertEquals(expected, Workload.reference(records));
        assertEquals(expected, Workload.sequential(records));
        assertEquals(expected, Workload.parallel(records));
    }
}
