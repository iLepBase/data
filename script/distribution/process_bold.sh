#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
DATA_DIR="$REPO_ROOT/data/raw/distribution"

INPUT_FILE="${1:-$DATA_DIR/BOLD_Public_27-FEB-2026.tsv}"
OUTPUT_FILE="${2:-$DATA_DIR/distribution_bold.csv}"

mkdir -p "$(dirname "$OUTPUT_FILE")"

if tar -tf "$INPUT_FILE" >/dev/null 2>&1; then
    INNER_FILE=$(tar -tf "$INPUT_FILE" | awk '/\.tsv$/ { print; exit }')
    if [[ -z "$INNER_FILE" ]]; then
        echo "No TSV file found inside archive: $INPUT_FILE" >&2
        exit 1
    fi
    INPUT_CMD=(tar -xOf "$INPUT_FILE" "$INNER_FILE")
else
    INPUT_CMD=(cat "$INPUT_FILE")
fi

"${INPUT_CMD[@]}" | awk -F'\t' '
BEGIN { OFS = "\t" }

# Keep Lepidoptera records with usable coordinates.
$17 == "Lepidoptera" && $55 != "" && $55 != "None" {
    coord = $55
    gsub(/\[|\]/, "", coord)
    split(coord, coords, ",")

    lat = coords[1]
    lon = coords[2]
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", lat)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", lon)

    if (lat != "" && lon != "") {
        print $1, $18, $21, $22, $49, lat, lon, "BOLD"
    }
}
' | LC_ALL=C sort -u -S 8G > "$OUTPUT_FILE"

echo "Filtered and deduplicated data has been saved to: $OUTPUT_FILE"
