import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(SCRIPT_DIR, "tools")


def getenv_int(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def getenv_float(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


ACC_FILE = os.environ.get(
    "NCBI_ACC_FILE",
    "/dataz/zhanglab-1/iLepbase/script/genome/accNum_20260311.txt",
)
OUTDIR = os.environ.get(
    "NCBI_OUTDIR",
    "/dataz/zhanglab-1/iLepbase/data/clean/genome",
)
LOG_FILE = os.path.join(OUTDIR, "download.log")

THREADS = max(1, getenv_int("NCBI_THREADS", 6))
RETRY = max(1, getenv_int("NCBI_RETRY", 4))
RETRY_BACKOFF_SECONDS = max(0.0, getenv_float("NCBI_RETRY_BACKOFF_SECONDS", 3.0))
RETRY_BACKOFF_FACTOR = max(1.0, getenv_float("NCBI_RETRY_BACKOFF_FACTOR", 2.0))
DATASETS_TIMEOUT_SECONDS = max(60, getenv_int("NCBI_DATASETS_TIMEOUT_SECONDS", 1800))

PROXY_HOST = os.environ.get("NCBI_PROXY_HOST", "127.0.0.1")
PROXY_PORT = getenv_int("NCBI_PROXY_PORT", 10808)
PROXY_SCHEME = os.environ.get("NCBI_PROXY_SCHEME", "http").strip() or "http"
PROXY_URL_OVERRIDE = os.environ.get("NCBI_PROXY_URL", "").strip()
PROXY_MODE = (os.environ.get("NCBI_PROXY_MODE", "proxy").strip() or "proxy").lower()
if PROXY_MODE not in {"auto", "proxy", "direct"}:
    PROXY_MODE = "proxy"

DATASETS_BIN_OVERRIDE = os.environ.get("NCBI_DATASETS_BIN", "").strip()
PROJECT_DATASETS_BIN = os.path.join(TOOLS_DIR, "datasets")
RESOLVED_DATASETS_BIN = PROJECT_DATASETS_BIN
API_KEYS_FILE = os.environ.get(
    "NCBI_API_KEYS_FILE",
    "/dataz/zhanglab-1/iLepbase/script/genome/ncbi_api_keys.txt",
)

OUTPUT_FILE_SUFFIXES = {
    "genome": (".fna",),
    "gff": (".gff",),
    "protein": (".faa",),
    "cds": (".cds.fna",),
    "rna": (".rna.fna",),
    "seq_report": (".seq_report.tsv", ".seq_report.jsonl"),
    "md5": (".md5sum.txt",),
}

DEFAULT_OUTPUT_SUFFIX = {
    "genome": ".fna",
    "gff": ".gff",
    "protein": ".faa",
    "cds": ".cds.fna",
    "rna": ".rna.fna",
    "seq_report": ".seq_report.tsv",
    "md5": ".md5sum.txt",
}

PACKAGE_FILE_SUFFIXES = {
    "genome": ("_genomic.fna",),
    "gff": ("_genomic.gff", "_genomic.gff3"),
    "protein": ("_protein.faa",),
    "cds": ("_cds_from_genomic.fna",),
    "rna": ("_rna_from_genomic.fna",),
    "seq_report": ("sequence_report.tsv", "sequence_report.jsonl"),
}

PROXY_ENV_KEYS = (
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
)

DATASETS_UPDATE_PREFIX = "New version of client ("
DATASETS_HELP_PREFIX = "Use datasets download genome accession "

RETRYABLE_DATASETS_PATTERNS = (
    "proxyconnect tcp",
    "proxy error",
    "proxy authentication required",
    "tls handshake timeout",
    "i/o timeout",
    "context deadline exceeded",
    "timeout awaiting response headers",
    "connection reset by peer",
    "connection refused",
    "no route to host",
    "temporary failure in name resolution",
    "server misbehaving",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "too many requests",
    "status code 429",
    "eof",
)

PERMANENT_DATASETS_PATTERNS = (
    "invalid accession",
    "is not a valid accession",
    "accession is invalid",
    "accession not found",
    "genome accession not found",
    "no genome data found",
    "no genome found",
)

os.makedirs(OUTDIR, exist_ok=True)

metadata = {}
metadata_lock = Lock()
log_lock = Lock()
key_lock = Lock()


def load_api_keys(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.startswith("#")]


api_keys = load_api_keys(API_KEYS_FILE)
api_key_index = 0


def next_api_key():
    global api_key_index
    if not api_keys:
        return None
    with key_lock:
        key = api_keys[api_key_index % len(api_keys)]
        api_key_index += 1
        return key


def log(message):
    with log_lock:
        print(message, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{message}\n")


def resolve_datasets_bin():
    if DATASETS_BIN_OVERRIDE:
        return DATASETS_BIN_OVERRIDE, "override"
    if os.path.isfile(PROJECT_DATASETS_BIN) and os.access(PROJECT_DATASETS_BIN, os.X_OK):
        return PROJECT_DATASETS_BIN, "project"

    path_bin = shutil.which("datasets")
    if path_bin:
        return path_bin, "path"

    return PROJECT_DATASETS_BIN, "missing"


def normalize_proxy_url(proxy_url):
    if not proxy_url:
        return ""
    return proxy_url if "://" in proxy_url else f"{PROXY_SCHEME}://{proxy_url}"


def resolve_proxy_settings():
    if PROXY_URL_OVERRIDE:
        proxy_url = normalize_proxy_url(PROXY_URL_OVERRIDE)
        source = "override"
        http_proxy = proxy_url
        https_proxy = proxy_url
        all_proxy = proxy_url
    else:
        http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
        https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
        all_proxy = os.environ.get("all_proxy") or os.environ.get("ALL_PROXY")
        source = "environment"

        if not any((http_proxy, https_proxy, all_proxy)):
            proxy_url = normalize_proxy_url(f"{PROXY_HOST}:{PROXY_PORT}")
            source = "default"
            http_proxy = proxy_url
            https_proxy = proxy_url
            all_proxy = proxy_url
        else:
            http_proxy = normalize_proxy_url(http_proxy or https_proxy or all_proxy)
            https_proxy = normalize_proxy_url(https_proxy or http_proxy or all_proxy)
            all_proxy = normalize_proxy_url(all_proxy or https_proxy or http_proxy)

    settings = {
        "http_proxy": http_proxy,
        "HTTP_PROXY": http_proxy,
        "https_proxy": https_proxy,
        "HTTPS_PROXY": https_proxy,
        "all_proxy": all_proxy,
        "ALL_PROXY": all_proxy,
    }
    summary = ", ".join(
        f"{key}={value}"
        for key, value in (
            ("http", http_proxy),
            ("https", https_proxy),
            ("all", all_proxy),
        )
        if value
    )
    return settings, summary or "disabled", source


def build_runtime_env(network_mode):
    env = os.environ.copy()
    if network_mode == "direct":
        for key in PROXY_ENV_KEYS:
            env.pop(key, None)
        return env, "direct"

    proxy_settings, proxy_summary, proxy_source = resolve_proxy_settings()
    env.update({key: value for key, value in proxy_settings.items() if value})
    return env, f"{proxy_source}:{proxy_summary}"


def choose_network_mode(attempt_index):
    if PROXY_MODE == "direct":
        return "direct"
    if PROXY_MODE == "proxy":
        return "proxy"
    return "proxy" if attempt_index % 2 == 0 else "direct"


def should_suppress_subprocess_line(line):
    return line.startswith(DATASETS_UPDATE_PREFIX) or line.startswith(DATASETS_HELP_PREFIX)


def decode_subprocess_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value


def collect_subprocess_lines(stdout, stderr):
    lines = []
    for text in (stdout, stderr):
        for raw_line in decode_subprocess_text(text).splitlines():
            line = raw_line.strip()
            if not line or should_suppress_subprocess_line(line):
                continue
            lines.append(line)
    return lines


@dataclass
class CommandOutcome:
    returncode: int
    lines: list[str]
    timed_out: bool
    duration_seconds: float
    env_label: str


def run_command(cmd, *, timeout_seconds, network_mode):
    env, env_label = build_runtime_env(network_mode)
    start_time = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
        lines = collect_subprocess_lines(result.stdout, result.stderr)
        return CommandOutcome(
            returncode=result.returncode,
            lines=lines,
            timed_out=False,
            duration_seconds=time.monotonic() - start_time,
            env_label=env_label,
        )
    except subprocess.TimeoutExpired as exc:
        lines = collect_subprocess_lines(exc.stdout, exc.stderr)
        return CommandOutcome(
            returncode=124,
            lines=lines,
            timed_out=True,
            duration_seconds=time.monotonic() - start_time,
            env_label=env_label,
        )


def collect_output_files(acc, output_dir=None):
    acc_dir = output_dir or os.path.join(OUTDIR, acc)
    files = {}

    for file_type, suffixes in OUTPUT_FILE_SUFFIXES.items():
        for suffix in suffixes:
            path = os.path.join(acc_dir, f"{acc}{suffix}")
            if os.path.exists(path):
                files[file_type] = {
                    "name": os.path.basename(path),
                    "size_bytes": os.path.getsize(path),
                }
                break

    return files


def compute_md5(path, chunk_size=1024 * 1024):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_md5_file(path):
    entries = {}
    if not os.path.exists(path):
        return entries

    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            md5_value, rel_path = parts
            entries[rel_path.lstrip("*")] = md5_value.lower()

    return entries


def matches_package_file(file_type, filename):
    normalized = filename.replace("\\", "/").lower()
    basename = os.path.basename(normalized)
    suffixes = PACKAGE_FILE_SUFFIXES[file_type]
    matched = any(basename.endswith(suffix.lower()) for suffix in suffixes)
    if file_type == "genome" and matched:
        return not basename.endswith("_from_genomic.fna")
    return matched


def find_matching_source_file(src_dir, file_type):
    for filename in sorted(os.listdir(src_dir)):
        if matches_package_file(file_type, filename):
            return os.path.join(src_dir, filename)
    return None


def resolve_output_path(acc, file_type, output_dir, source_path=None):
    if file_type == "seq_report" and source_path:
        if source_path.lower().endswith(".jsonl"):
            suffix = ".seq_report.jsonl"
        else:
            suffix = ".seq_report.tsv"
    else:
        suffix = DEFAULT_OUTPUT_SUFFIX[file_type]
    return os.path.join(output_dir, f"{acc}{suffix}")


def find_expected_md5(md5_entries, acc, file_type):
    for rel_path, md5_value in md5_entries.items():
        normalized = rel_path.replace("\\", "/")
        if f"/{acc}/" in normalized and matches_package_file(file_type, normalized):
            return md5_value, normalized

    return None, None


def build_md5_report(acc, files, output_dir=None):
    acc_dir = output_dir or os.path.join(OUTDIR, acc)
    md5_path = os.path.join(acc_dir, f"{acc}.md5sum.txt")
    report = {
        "status": "missing_md5",
        "checked_files": 0,
        "passed_files": 0,
        "failed_files": 0,
        "missing_expected_files": [],
        "details": {},
    }

    if not os.path.exists(md5_path):
        return report

    md5_entries = parse_md5_file(md5_path)
    data_file_types = [file_type for file_type in files if file_type != "md5"]

    if not data_file_types:
        report["status"] = "no_data_files"
        return report

    for file_type in data_file_types:
        file_info = files[file_type]
        local_path = os.path.join(acc_dir, file_info["name"])
        expected_md5, source_path = find_expected_md5(md5_entries, acc, file_type)

        if not expected_md5:
            report["missing_expected_files"].append(file_type)
            report["details"][file_type] = {
                "status": "missing_expected_hash",
                "file": file_info["name"],
            }
            continue

        actual_md5 = compute_md5(local_path)
        is_match = actual_md5 == expected_md5
        report["checked_files"] += 1
        report["details"][file_type] = {
            "status": "ok" if is_match else "md5_mismatch",
            "file": file_info["name"],
            "source_path": source_path,
            "expected_md5": expected_md5,
            "actual_md5": actual_md5,
        }

        if is_match:
            report["passed_files"] += 1
        else:
            report["failed_files"] += 1

    if report["failed_files"] > 0:
        report["status"] = "fail"
    elif report["passed_files"] == len(data_file_types):
        report["status"] = "pass"
    elif report["passed_files"] > 0:
        report["status"] = "partial"
    else:
        report["status"] = "no_expected_hashes"

    return report


def summarize_md5_failures(md5_check):
    failed_types = []
    missing_types = list(md5_check.get("missing_expected_files", []))

    for file_type, detail in md5_check.get("details", {}).items():
        if detail.get("status") == "md5_mismatch":
            failed_types.append(file_type)

    parts = []
    if failed_types:
        parts.append(f"mismatch={','.join(sorted(failed_types))}")
    if missing_types:
        parts.append(f"missing={','.join(sorted(missing_types))}")
    return " ".join(parts) if parts else "unknown_reason"


def set_metadata(acc, status, files, md5_check):
    with metadata_lock:
        metadata[acc] = {
            "status": status,
            "files": files,
            "md5_check": md5_check,
        }


def remove_path(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        os.remove(path)


def cleanup_download_artifacts(*paths):
    for path in paths:
        remove_path(path)


def classify_datasets_failure(outcome):
    if outcome.timed_out:
        return "timeout"

    text = " ".join(outcome.lines).lower()
    if any(pattern in text for pattern in PERMANENT_DATASETS_PATTERNS):
        return "permanent"
    if any(pattern in text for pattern in RETRYABLE_DATASETS_PATTERNS):
        return "retryable_network"
    return "retryable_unknown"


def compute_retry_delay(attempt_index, failure_kind):
    delay = RETRY_BACKOFF_SECONDS * (RETRY_BACKOFF_FACTOR ** attempt_index)
    if failure_kind == "retryable_network":
        delay = max(delay, 5.0)
    if failure_kind == "timeout":
        delay = max(delay, 10.0)
    return min(delay, 120.0)


def maybe_sleep_before_retry(acc, attempt_index, failure_kind):
    if attempt_index >= RETRY - 1:
        return
    delay = compute_retry_delay(attempt_index, failure_kind)
    if delay <= 0:
        return
    log(f"[BACKOFF] {acc} reason={failure_kind} sleep={delay:.1f}s")
    time.sleep(delay)


def extract_zip_archive(zip_path, extract_dir):
    if not zipfile.is_zipfile(zip_path):
        return False, [f"{zip_path} is not a valid zip archive"]

    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        return True, []
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
        return False, [str(exc)]


def finalize_output_dir(staging_dir, final_dir):
    if os.path.isdir(final_dir):
        shutil.rmtree(final_dir, ignore_errors=True)
    os.replace(staging_dir, final_dir)


def move_output_files(acc, data_dir, staging_dir):
    moved_files = []
    missing_in_package = []

    for file_type in ("genome", "gff", "protein", "cds", "rna", "seq_report"):
        source_path = find_matching_source_file(data_dir, file_type)
        if not source_path:
            missing_in_package.append(file_type)
            continue
        destination_path = resolve_output_path(acc, file_type, staging_dir, source_path)
        os.replace(source_path, destination_path)
        moved_files.append(file_type)

    return moved_files, missing_in_package


def download_one(acc):
    acc = acc.strip()
    if not acc:
        return

    final_outdir = os.path.join(OUTDIR, acc)
    genome_file = os.path.join(final_outdir, f"{acc}.fna")

    if os.path.exists(genome_file):
        files = collect_output_files(acc)
        set_metadata(acc, "skip", files, build_md5_report(acc, files))
        log(f"[ALREADY_EXISTS] {acc}")
        return

    zip_path = os.path.join(OUTDIR, f"{acc}.zip")
    extract_dir = os.path.join(OUTDIR, f".tmp_{acc}")
    staging_dir = os.path.join(OUTDIR, f".staging_{acc}")
    last_md5_check = {
        "status": "not_run",
        "checked_files": 0,
        "passed_files": 0,
        "failed_files": 0,
        "missing_expected_files": [],
        "details": {},
    }

    for attempt_index in range(RETRY):
        attempt_no = attempt_index + 1
        network_mode = choose_network_mode(attempt_index)
        cleanup_download_artifacts(zip_path, extract_dir, staging_dir)

        api_key = next_api_key()
        datasets_cmd = [
            RESOLVED_DATASETS_BIN,
            "download",
            "genome",
            "accession",
            acc,
            "--include",
            "genome,gff3,rna,cds,protein,seq-report",
            "--no-progressbar",
            "--filename",
            zip_path,
        ]
        if api_key:
            datasets_cmd.extend(["--api-key", api_key])

        log(f"[TRY {attempt_no}/{RETRY}] {acc} mode={network_mode}")
        datasets_outcome = run_command(
            datasets_cmd,
            timeout_seconds=DATASETS_TIMEOUT_SECONDS,
            network_mode=network_mode,
        )

        if datasets_outcome.returncode != 0:
            failure_kind = classify_datasets_failure(datasets_outcome)
            if datasets_outcome.lines:
                for line in datasets_outcome.lines:
                    log(f"[DATASETS_ERROR] {acc} {line}")
            else:
                log(
                    f"[DATASETS_ERROR] {acc} exit={datasets_outcome.returncode} "
                    f"timeout={datasets_outcome.timed_out}"
                )
            log(
                f"[RETRY_STATE] {acc} attempt={attempt_no} reason={failure_kind} "
                f"duration={datasets_outcome.duration_seconds:.1f}s env={datasets_outcome.env_label}"
            )
            if failure_kind == "permanent":
                break
            maybe_sleep_before_retry(acc, attempt_index, failure_kind)
            continue

        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            log(f"[ZIP_ERROR] {acc} attempt={attempt_no} zip missing_or_empty")
            maybe_sleep_before_retry(acc, attempt_index, "retryable_unknown")
            continue

        os.makedirs(extract_dir, exist_ok=True)
        unzip_ok, unzip_lines = extract_zip_archive(zip_path, extract_dir)
        if not unzip_ok:
            for line in unzip_lines:
                log(f"[UNZIP_ERROR] {acc} {line}")
            maybe_sleep_before_retry(acc, attempt_index, "retryable_unknown")
            continue

        data_dir = os.path.join(extract_dir, "ncbi_dataset", "data", acc)
        if not os.path.isdir(data_dir):
            log(f"[PACKAGE_ERROR] {acc} extracted data directory missing")
            maybe_sleep_before_retry(acc, attempt_index, "retryable_unknown")
            continue

        os.makedirs(staging_dir, exist_ok=True)
        moved_files, missing_in_package = move_output_files(acc, data_dir, staging_dir)
        if missing_in_package:
            log(f"[PACKAGE_MISSING] {acc} {','.join(missing_in_package)}")

        md5_source = os.path.join(extract_dir, "md5sum.txt")
        if not os.path.exists(md5_source):
            log(f"[MD5_ERROR] {acc} package md5sum.txt missing")
            maybe_sleep_before_retry(acc, attempt_index, "retryable_unknown")
            continue
        shutil.copy2(md5_source, resolve_output_path(acc, "md5", staging_dir))

        files = collect_output_files(acc, staging_dir)
        if "genome" not in files:
            log(f"[PACKAGE_ERROR] {acc} genome file missing after extraction")
            maybe_sleep_before_retry(acc, attempt_index, "retryable_unknown")
            continue

        last_md5_check = build_md5_report(acc, files, staging_dir)
        if last_md5_check["status"] == "fail":
            log(f"[MD5_FAIL] {acc} {summarize_md5_failures(last_md5_check)}")
            maybe_sleep_before_retry(acc, attempt_index, "retryable_unknown")
            continue
        if last_md5_check["status"] in {"missing_md5", "no_data_files"}:
            log(f"[MD5_ERROR] {acc} status={last_md5_check['status']}")
            maybe_sleep_before_retry(acc, attempt_index, "retryable_unknown")
            continue

        finalize_output_dir(staging_dir, final_outdir)
        cleanup_download_artifacts(zip_path, extract_dir)
        set_metadata(acc, "success", files, last_md5_check)
        log(
            f"[SUCCESS] {acc} moved={','.join(moved_files) or 'none'} "
            f"md5={last_md5_check['status']} "
            f"duration={datasets_outcome.duration_seconds:.1f}s env={datasets_outcome.env_label}"
        )
        return

    cleanup_download_artifacts(zip_path, extract_dir, staging_dir)
    files = collect_output_files(acc)
    if last_md5_check["status"] == "not_run":
        last_md5_check = build_md5_report(acc, files)
    set_metadata(acc, "error", files, last_md5_check)
    log(f"[ERROR] {acc}")


def build_stats(accs):
    status_counts = Counter()
    file_counts = Counter()
    md5_status_counts = Counter()
    accessions_by_status = {}
    total_output_bytes = 0
    accessions_with_files = 0
    md5_checked_files = 0
    md5_passed_files = 0
    md5_failed_files = 0

    for acc in accs:
        entry = metadata.get(
            acc,
            {
                "status": "missing",
                "files": {},
                "md5_check": {
                    "status": "missing_record",
                    "checked_files": 0,
                    "passed_files": 0,
                    "failed_files": 0,
                    "missing_expected_files": [],
                    "details": {},
                },
            },
        )
        status = entry["status"]
        files = entry["files"]
        md5_check = entry["md5_check"]

        status_counts[status] += 1
        md5_status_counts[md5_check["status"]] += 1
        accessions_by_status.setdefault(status, []).append(acc)
        md5_checked_files += md5_check.get("checked_files", 0)
        md5_passed_files += md5_check.get("passed_files", 0)
        md5_failed_files += md5_check.get("failed_files", 0)

        if files:
            accessions_with_files += 1

        for file_type, file_info in files.items():
            file_counts[file_type] += 1
            total_output_bytes += file_info.get("size_bytes", 0)

    available_count = status_counts["success"] + status_counts["skip"]
    file_coverage = {}
    for file_type in OUTPUT_FILE_SUFFIXES:
        count = file_counts[file_type]
        file_coverage[file_type] = {
            "count": count,
            "ratio": round(count / available_count, 4) if available_count else 0.0,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "acc_file": ACC_FILE,
        "outdir": OUTDIR,
        "requested_accessions": len(accs),
        "accessions_with_files": accessions_with_files,
        "status_counts": dict(sorted(status_counts.items())),
        "accessions_by_status": {
            status: sorted(values) for status, values in sorted(accessions_by_status.items())
        },
        "md5_status_counts": dict(sorted(md5_status_counts.items())),
        "md5_checked_files": md5_checked_files,
        "md5_passed_files": md5_passed_files,
        "md5_failed_files": md5_failed_files,
        "file_coverage": file_coverage,
        "total_output_files": sum(file_counts.values()),
        "total_output_bytes": total_output_bytes,
    }


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)


def load_accessions():
    with open(ACC_FILE, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def check_external_dependencies():
    datasets_path, datasets_source = resolve_datasets_bin()
    if datasets_source == "override":
        expanded_path = os.path.expanduser(datasets_path)
        if not os.path.isfile(expanded_path):
            raise RuntimeError(
                f"Required command not found: {expanded_path}. "
                "Set NCBI_DATASETS_BIN to a valid executable, or place the binary at "
                f"{PROJECT_DATASETS_BIN}."
            )
        if not os.access(expanded_path, os.X_OK):
            raise RuntimeError(
                f"Configured datasets binary is not executable: {expanded_path}. "
                f"Run `chmod +x {expanded_path}` or replace it with an executable file."
            )
        return expanded_path, datasets_source

    if datasets_source == "missing":
        raise RuntimeError(
            "Required command not found: datasets. "
            f"Place the NCBI datasets binary at {PROJECT_DATASETS_BIN}, "
            "or set NCBI_DATASETS_BIN to a valid executable."
        )
    return datasets_path, datasets_source


def bootstrap_existing_records(accs):
    existing_set = set()
    for acc in accs:
        if os.path.exists(os.path.join(OUTDIR, acc, f"{acc}.fna")):
            existing_set.add(acc)
            files = collect_output_files(acc)
            set_metadata(
                acc,
                "skip",
                files,
                {
                    "status": "skipped",
                    "checked_files": 0,
                    "passed_files": 0,
                    "failed_files": 0,
                    "missing_expected_files": [],
                    "details": {},
                },
            )
    return existing_set


def main():
    global RESOLVED_DATASETS_BIN

    with open(LOG_FILE, "w", encoding="utf-8") as handle:
        handle.write("")

    try:
        datasets_path, datasets_source = check_external_dependencies()
    except RuntimeError as exc:
        log(f"[FATAL] {exc}")
        return 1
    RESOLVED_DATASETS_BIN = datasets_path

    accs = load_accessions()
    existing_set = bootstrap_existing_records(accs)
    pending_accs = [acc for acc in accs if acc not in existing_set]

    _, proxy_summary, proxy_source = resolve_proxy_settings()
    log(
        f"[START] requested={len(accs)} need_download={len(pending_accs)} "
        f"already_exists={len(existing_set)} threads={THREADS} retry={RETRY} "
        f"api_keys={len(api_keys)} datasets={datasets_path} datasets_source={datasets_source} "
        f"proxy_mode={PROXY_MODE} "
        f"proxy_source={proxy_source} proxy={proxy_summary} timeout={DATASETS_TIMEOUT_SECONDS}s"
    )

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = {pool.submit(download_one, acc): acc for acc in pending_accs}
        for future in as_completed(futures):
            acc = futures[future]
            try:
                future.result()
            except Exception as exc:
                log(f"[EXCEPTION] {acc}: {exc}")
                set_metadata(
                    acc,
                    "error",
                    {},
                    {
                        "status": "exception",
                        "checked_files": 0,
                        "passed_files": 0,
                        "failed_files": 0,
                        "missing_expected_files": [],
                        "details": {},
                    },
                )

    metadata_json = os.path.join(OUTDIR, "genome_metadata.json")
    stats_json = os.path.join(OUTDIR, "genome_stats.json")

    write_json(metadata_json, metadata)
    stats_payload = build_stats(accs)
    write_json(stats_json, stats_payload)

    log(
        f"[DONE] requested={len(accs)} success={stats_payload['status_counts'].get('success', 0)} "
        f"skip={stats_payload['status_counts'].get('skip', 0)} "
        f"error={stats_payload['status_counts'].get('error', 0)}"
    )
    log(f"[OUTPUT] metadata={metadata_json}")
    log(f"[OUTPUT] stats={stats_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
