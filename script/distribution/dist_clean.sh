#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
DATA_DIR="$REPO_ROOT/data/raw/distribution"

uv run "$REPO_ROOT/script/clean_species/clean_species.py" \
    --input-file "$DATA_DIR/lep_species_unique.txt" \
    --output-prefix "$DATA_DIR/lep_dist" \
    --cache-db "$REPO_ROOT/data/raw/hostplant/taxonomy_cache.sqlite" \
    --workers 8 \
    --progress-every 200
