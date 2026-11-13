#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
DATA_DIR="$REPO_ROOT/data/raw/distribution"

INPUT_FILE="${1:-$DATA_DIR/0021218-260226173443078.csv}"
OUTPUT_FILE="${2:-$DATA_DIR/distribution_gbif.csv}"
PROGRESS_EVERY="${PROGRESS_EVERY:-1000000}"

mkdir -p "$(dirname "$OUTPUT_FILE")"

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >&2
}

log "Input file: $INPUT_FILE"
log "Output file: $OUTPUT_FILE"
log "Phase 1/1: filtering Lepidoptera records directly to output"
log "Filter progress interval: every $PROGRESS_EVERY input rows"

if command -v pv >/dev/null 2>&1; then
    log "pv detected: byte-level progress will be shown while reading the input file"
    INPUT_CMD=(pv "$INPUT_FILE")
else
    log "pv not found: using row-count progress logs only"
    INPUT_CMD=(cat "$INPUT_FILE")
fi

"${INPUT_CMD[@]}" | awk -F'\t' -v OFS='\t' -v progress_every="$PROGRESS_EVERY" '
BEGIN {
    processed = 0
    matched = 0
}
{
    processed++
    if (progress_every > 0 && processed % progress_every == 0) {
        printf("[filter] processed_rows=%d matched_rows=%d\n", processed, matched) > "/dev/stderr"
        fflush("/dev/stderr")
    }
    if ($7 == "Lepidoptera" && $10 != "" && $22 != "" && $23 != "") {
        print $1, $8, $9, $10, $16, $22, $23, "GBIF"
        matched++
    }
}
END {
    printf("[filter] processed_rows=%d matched_rows=%d\n", processed, matched) > "/dev/stderr"
    fflush("/dev/stderr")
}
' > "$OUTPUT_FILE"

log "Phase 1/1 complete: filtered output written to $OUTPUT_FILE"

echo "Filtered data has been saved to: $OUTPUT_FILE"
