#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ZIP_DIR="/data-z/zhanglab-1/iLepbase/data/clean/genome/NCBI"
DEFAULT_OUT_DIR="/data-z/zhanglab-1/iLepbase/data/clean/genome/NCBI_unzip"

ZIP_DIR="$DEFAULT_ZIP_DIR"
OUT_DIR="$DEFAULT_OUT_DIR"
THREADS=1
OVERWRITE=0
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$SCRIPT_DIR/unzip_ncbi_archives_${TIMESTAMP}.log"
LOG_LOCK=""


usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --zip-dir PATH     Directory containing .zip archives.
  --out-dir PATH     Directory for extracted accession folders.
  --threads N        Number of archives to extract in parallel. Default: 1
  --overwrite        Replace an existing output directory with a fresh extraction.
  --log-file PATH    Log file path. Default: $LOG_FILE
  -h, --help         Show this help message.
EOF
}


require_value() {
    local flag="$1"
    local value="${2:-}"
    if [[ -z "$value" ]]; then
        echo "[ERROR] missing value for $flag" >&2
        exit 1
    fi
}


while [[ $# -gt 0 ]]; do
    case "$1" in
        --zip-dir)
            require_value "$1" "${2:-}"
            ZIP_DIR="$2"
            shift 2
            ;;
        --out-dir)
            require_value "$1" "${2:-}"
            OUT_DIR="$2"
            shift 2
            ;;
        --threads)
            require_value "$1" "${2:-}"
            THREADS="$2"
            shift 2
            ;;
        --overwrite)
            OVERWRITE=1
            shift
            ;;
        --log-file)
            require_value "$1" "${2:-}"
            LOG_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[ERROR] unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done


if ! [[ "$THREADS" =~ ^[0-9]+$ ]] || [[ "$THREADS" -lt 1 ]]; then
    echo "[ERROR] --threads must be an integer >= 1" >&2
    exit 1
fi

if [[ ! -d "$ZIP_DIR" ]]; then
    echo "[ERROR] zip directory not found: $ZIP_DIR" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
mkdir -p "$(dirname "$LOG_FILE")"
: > "$LOG_FILE"
LOG_LOCK="${LOG_FILE}.lock"
: > "$LOG_LOCK"


log_msg() {
    local level="$1"
    shift
    local timestamp
    timestamp="$(date '+%F %T')"
    {
        flock 200
        printf '[%s] [%s] %s\n' "$timestamp" "$level" "$*" | tee -a "$LOG_FILE"
    } 200>>"$LOG_LOCK"
}


remove_path() {
    local target="$1"
    if [[ -d "$target" && ! -L "$target" ]]; then
        rm -rf "$target"
    elif [[ -e "$target" || -L "$target" ]]; then
        rm -f "$target"
    fi
}


cleanup_stale_tmp() {
    local stale_count=0
    local path
    while IFS= read -r -d '' path; do
        remove_path "$path"
        stale_count=$((stale_count + 1))
    done < <(find "$OUT_DIR" -mindepth 1 -maxdepth 1 -type d \
        \( -name '.tmp_extract_*' -o -name '.tmp_stage_*' \) -print0)

    log_msg CLEANUP "removed_stale_tmp=$stale_count"
}


process_zip() {
    local zip_path="$1"
    local zip_name accession final_dir extract_tmp stage_tmp stage_dir
    local file_count duplicate_names

    zip_name="$(basename "$zip_path")"
    accession="${zip_name%.zip}"
    final_dir="$OUT_DIR/$accession"
    extract_tmp=""
    stage_tmp=""

    cleanup_tmp() {
        [[ -n "$extract_tmp" && -d "$extract_tmp" ]] && rm -rf "$extract_tmp"
        [[ -n "$stage_tmp" && -d "$stage_tmp" ]] && rm -rf "$stage_tmp"
    }

    trap cleanup_tmp EXIT

    if [[ -e "$final_dir" ]]; then
        if [[ "$OVERWRITE" -eq 0 ]]; then
            log_msg SKIP "$accession already exists: $final_dir"
            trap - EXIT
            cleanup_tmp
            return 0
        fi
        remove_path "$final_dir"
    fi

    log_msg START "$accession zip=$zip_path"

    extract_tmp="$(mktemp -d "$OUT_DIR/.tmp_extract_${accession}_XXXXXX")"
    stage_tmp="$(mktemp -d "$OUT_DIR/.tmp_stage_${accession}_XXXXXX")"
    stage_dir="$stage_tmp/$accession"
    mkdir -p "$stage_dir"

    unzip -q "$zip_path" -d "$extract_tmp"

    file_count="$(find "$extract_tmp" -type f | wc -l | tr -d ' ')"
    if [[ "$file_count" -eq 0 ]]; then
        log_msg ERROR "$accession archive contained no files"
        return 1
    fi

    duplicate_names="$(
        find "$extract_tmp" -type f -printf '%f\n' | sort | uniq -d || true
    )"
    if [[ -n "$duplicate_names" ]]; then
        duplicate_names="$(printf '%s' "$duplicate_names" | tr '\n' ',' | sed 's/,$//')"
        log_msg ERROR "$accession duplicate filenames after flatten: $duplicate_names"
        return 1
    fi

    log_msg UNZIP "$accession files=$file_count"

    while IFS= read -r -d '' src; do
        mv "$src" "$stage_dir/$(basename "$src")"
    done < <(find "$extract_tmp" -type f -print0)

    mv "$stage_dir" "$final_dir"

    trap - EXIT
    cleanup_tmp
    log_msg DONE "$accession -> $final_dir files=$file_count"
}


export ZIP_DIR OUT_DIR OVERWRITE LOG_FILE LOG_LOCK
export -f log_msg remove_path process_zip

cleanup_stale_tmp

TOTAL_ARCHIVES="$(find "$ZIP_DIR" -maxdepth 1 -type f -name '*.zip' | wc -l | tr -d ' ')"
if [[ "$TOTAL_ARCHIVES" -eq 0 ]]; then
    log_msg ERROR "no .zip archives found in $ZIP_DIR"
    exit 1
fi

log_msg STARTUP "zip_dir=$ZIP_DIR out_dir=$OUT_DIR threads=$THREADS overwrite=$OVERWRITE total=$TOTAL_ARCHIVES"

set +e
find "$ZIP_DIR" -maxdepth 1 -type f -name '*.zip' -print0 \
    | sort -z \
    | xargs -0 -n 1 -P "$THREADS" bash -c 'set -euo pipefail; process_zip "$1"' _
XARGS_STATUS=$?
set -e

SUCCESS_COUNT="$(grep -c '\[DONE\]' "$LOG_FILE" || true)"
SKIP_COUNT="$(grep -c '\[SKIP\]' "$LOG_FILE" || true)"
ERROR_COUNT="$(grep -c '\[ERROR\]' "$LOG_FILE" || true)"

log_msg SUMMARY "total=$TOTAL_ARCHIVES success=$SUCCESS_COUNT skipped=$SKIP_COUNT failed=$ERROR_COUNT"

if [[ "$ERROR_COUNT" -gt 0 || "$XARGS_STATUS" -ne 0 ]]; then
    exit 1
fi
