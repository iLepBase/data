#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TAXO_TSV = Path("data/clean/taxo/lep_taxo_20260415.tsv")
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_LOG_DIR = Path("script/integration/logs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the JSON build/update pipeline in script/json, using the specified "
            "taxonomy TSV."
        )
    )
    parser.add_argument(
        "--taxonomy-tsv",
        default=str(DEFAULT_TAXO_TSV),
        help="Taxonomy TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--json-dir",
        default=str(DEFAULT_JSON_DIR),
        help="Species JSON directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        help="Directory for pipeline log files, relative to the iLepbase root by default.",
    )
    return parser.parse_args()


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def to_repo_relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def log_message(handle, message: str) -> None:
    handle.write(message.rstrip("\n") + "\n")
    handle.flush()


NOISY_PREFIXES = (
    "root=",
    "input=",
    "output=",
    "taxonomy_tsv=",
    "json_dir=",
    "genome_input=",
    "ibg_dir=",
    "ibg_xlsx=",
    "ngdc_dir=",
    "ngdc_xlsx=",
    "mapping_tsv=",
    "clean_genome_dir=",
    "clean_anno_dir=",
    "log_output=",
    "stat_output=",
    "unmatched_output=",
    "ncbi_unzip_dir=",
    "busco_dir=",
    "hostplant_xlsx=",
    "hostplant_species_dir=",
    "info_tsv=",
    "coi_dir=",
    "transcriptome_csv=",
    "taxo_tsv=",
    "excluded=",
)


def filter_step_output(stdout: str) -> list[str]:
    kept: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[progress]"):
            continue
        if line.startswith("[info]"):
            continue
        if line.startswith("[cmd]"):
            continue
        if any(line.startswith(prefix) for prefix in NOISY_PREFIXES):
            continue
        kept.append(line)
    return kept


def run_step(step_name: str, command: list[str], log_handle) -> None:
    print(f"[run] {step_name}", flush=True)
    log_message(log_handle, f"[run] {step_name}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        log_message(log_handle, "[stdout]")
        if result.stdout:
            log_handle.write(result.stdout)
            if not result.stdout.endswith("\n"):
                log_handle.write("\n")
        log_message(log_handle, "[stderr]")
        if result.stderr:
            log_handle.write(result.stderr)
            if not result.stderr.endswith("\n"):
                log_handle.write("\n")
        log_handle.flush()
        raise subprocess.CalledProcessError(result.returncode, command)
    filtered_lines = filter_step_output(result.stdout)
    for line in filtered_lines:
        log_message(log_handle, line)


def main() -> int:
    args = parse_args()
    taxonomy_tsv = resolve_repo_path(args.taxonomy_tsv)
    json_dir = resolve_repo_path(args.json_dir)
    log_dir = resolve_repo_path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"json_pipeline_{timestamp}.log"

    steps = [
        (
            "lep_taxo_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/lep_taxo_to_species_json.py"),
                "--input",
                str(taxonomy_tsv),
                "--output",
                str(json_dir),
            ],
        ),
        (
            "genomes_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/genomes_to_species_json.py"),
                "--json-dir",
                str(json_dir),
            ],
        ),
        (
            "busco_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/busco_to_species_json.py"),
                "--json-dir",
                str(json_dir),
            ],
        ),
        (
            "chromosome_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/chromosome_to_species_json.py"),
                "--json-dir",
                str(json_dir),
            ],
        ),
        (
            "COI_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/COI_to_species_json.py"),
                "--json-dir",
                str(json_dir),
            ],
        ),
        (
            "dist_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/dist_to_species_json.py"),
                "--json-dir",
                str(json_dir),
            ],
        ),
        (
            "hostplant_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/hostplant_to_species_json.py"),
                "--taxonomy-tsv",
                str(taxonomy_tsv),
                "--json-dir",
                str(json_dir),
            ],
        ),
        (
            "transcriptome_to_species_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/transcriptome_to_species_json.py"),
                "--json-dir",
                str(json_dir),
            ],
        ),
        (
            "rebuild_log_json.py",
            [
                sys.executable,
                str(ROOT / "script/integration/rebuild_log_json.py"),
                "--json-dir",
                str(json_dir),
            ],
        ),
    ]

    print(f"[start] log={to_repo_relative_path(log_path)}", flush=True)
    print(f"[info] taxonomy_tsv={to_repo_relative_path(taxonomy_tsv)}", flush=True)
    print(f"[info] json_dir={to_repo_relative_path(json_dir)}", flush=True)

    with log_path.open("w", encoding="utf-8", newline="\n") as log_handle:
        log_message(log_handle, f"[start] {timestamp}")
        log_message(log_handle, f"log={to_repo_relative_path(log_path)}")

        for step_name, command in steps:
            run_step(step_name, command, log_handle)

        log_message(log_handle, "[done] json pipeline completed")

    print("[done] json pipeline completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
