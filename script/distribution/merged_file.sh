#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
DATA_DIR="$REPO_ROOT/data/raw/distribution"

FILE1="${1:-$DATA_DIR/distribution_gbif.csv}"
FILE2="${2:-$DATA_DIR/distribution_bold.csv}"
OUTPUT_FILE="${3:-$DATA_DIR/lepidoptera_distribution.tsv}"

COLUMN_NAMES=$'id\tfamily\tgenus\tspecies\tcountryCode\tlat\tlon\tsource'

mkdir -p "$(dirname "$OUTPUT_FILE")"

{
    printf '%s\n' "$COLUMN_NAMES"
    cat "$FILE1"
    cat "$FILE2"
} > "$OUTPUT_FILE"

echo "Files have been merged and saved to: $OUTPUT_FILE"
