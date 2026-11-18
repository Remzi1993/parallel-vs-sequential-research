package info.remzi.experiment;

/** One immutable synthetic event. Timestamps are UTC epoch seconds. */
public record EventRecord(long timestampSeconds, long eventCount) {
}
