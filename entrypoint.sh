#!/bin/sh
set -eu

case ${MEMORY_MIB:-} in ''|*[!0-9]*) echo 'MEMORY_MIB must be a positive integer.' >&2; exit 64;; esac
case ${HEAP_PERCENT:-60} in ''|*[!0-9]*) echo 'HEAP_PERCENT must be an integer.' >&2; exit 64;; esac
HEAP_PERCENT=${HEAP_PERCENT:-60}
if [ "$MEMORY_MIB" -lt 256 ] || [ "$HEAP_PERCENT" -lt 10 ] || [ "$HEAP_PERCENT" -gt 80 ]; then
    echo 'Use at least 256 MiB and a heap percentage from 10 to 80.' >&2
    exit 64
fi
HEAP_MIB=$((MEMORY_MIB * HEAP_PERCENT / 100))
mkdir -p /out
if [ -e /out/.benchmark-started ]; then
    echo 'This output directory has already been used. Select a new RESULTS_DIR.' >&2
    exit 64
fi
touch /out/.benchmark-started

# Bound the controller separately. Only its fork receives the experimental heap.
exec java -Xms32m -Xmx128m -XX:+UseG1GC -jar /app/benchmarks.jar \
    -f 1 -wf 0 -t 1 -bm avgt -tu ms -foe true \
    -rf json -rff /out/jmh.json \
    -prof info.remzi.experiment.ResourceProfiler \
    -jvmArgs "-Xms${HEAP_MIB}m -Xmx${HEAP_MIB}m -XX:+UseG1GC -Dexperiment.outputDir=/out" \
    "$@"
