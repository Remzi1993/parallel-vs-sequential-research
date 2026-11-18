package info.remzi.experiment;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;

public final class Workload {
    public static final long START_INCLUSIVE = 1_735_689_600L;
    public static final long END_EXCLUSIVE = START_INCLUSIVE + 86_400L;

    private Workload() {
    }

    /** Half the records match; one quarter precedes the interval and one quarter follows it. */
    public static ArrayList<EventRecord> syntheticRecords(int recordCount, long seed) {
        if (recordCount < 0 || recordCount % 4 != 0) {
            throw new IllegalArgumentException("Record count must be non-negative and divisible by four");
        }
        Random random = new Random(seed);
        ArrayList<EventRecord> records = new ArrayList<>(recordCount);
        for (int i = 0; i < recordCount; i++) {
            long intervalStart;
            if (i < recordCount / 2) {
                intervalStart = START_INCLUSIVE;
            } else if (i < recordCount / 2 + recordCount / 4) {
                intervalStart = START_INCLUSIVE - 86_400L;
            } else {
                intervalStart = END_EXCLUSIVE;
            }
            long timestamp = intervalStart + random.nextInt(86_400);
            long count = 1L + random.nextInt(100);
            records.add(new EventRecord(timestamp, count));
        }
        Collections.shuffle(records, random);
        return records;
    }

    public static long sequential(List<EventRecord> records) {
        return records.stream()
                .filter(record -> record.timestampSeconds() >= START_INCLUSIVE
                        && record.timestampSeconds() < END_EXCLUSIVE)
                .mapToLong(EventRecord::eventCount)
                .sum();
    }

    public static long parallel(List<EventRecord> records) {
        return records.parallelStream()
                .filter(record -> record.timestampSeconds() >= START_INCLUSIVE
                        && record.timestampSeconds() < END_EXCLUSIVE)
                .mapToLong(EventRecord::eventCount)
                .sum();
    }

    /** Independent correctness oracle, never a third timed implementation. */
    public static long reference(List<EventRecord> records) {
        long sum = 0;
        for (EventRecord record : records) {
            if (record.timestampSeconds() >= START_INCLUSIVE
                    && record.timestampSeconds() < END_EXCLUSIVE) {
                sum += record.eventCount();
            }
        }
        return sum;
    }

    /** Order-sensitive 64-bit fingerprint; a reproducibility check, not a cryptographic hash. */
    public static String checksum(List<EventRecord> records) {
        long hash = 0xcbf29ce484222325L;
        for (EventRecord record : records) {
            hash = (hash ^ record.timestampSeconds()) * 0x100000001b3L;
            hash = (hash ^ record.eventCount()) * 0x100000001b3L;
        }
        return Long.toUnsignedString(hash, 16);
    }
}
