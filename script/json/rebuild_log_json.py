#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from log_utils import refresh_log_summary


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_STAT_OUTPUT = Path("script/json/stat.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild script/json/stat.json from the current per-species JSON files."
    )
    parser.add_argument(
        "--json-dir",
        default=str(DEFAULT_JSON_DIR),
        help="Species JSON directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--stat-output",
        "--log-output",
        dest="stat_output",
        default=str(DEFAULT_STAT_OUTPUT),
        help="Stat JSON path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level.",
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


def main() -> int:
    args = parse_args()
    json_dir = resolve_repo_path(args.json_dir)
    stat_output = resolve_repo_path(args.stat_output)

    summary = refresh_log_summary(json_dir=json_dir, log_output=stat_output, indent=args.indent)

    print(f"root={ROOT}")
    print(f"json_dir={to_repo_relative_path(json_dir)}")
    print(f"stat_output={to_repo_relative_path(stat_output)}")
    print(f"species_with_taxo={summary['taxo']['species_total']}")
    print(f"species_with_genome={summary['genome']['species_total']}")
    print(f"species_with_annotation={summary['genome']['annotation']['species_total']}")
    print(f"species_with_publications={summary['genome']['publications']['species_total']}")
    print(f"species_with_busco={summary['genome']['busco']['species_total']}")
    print(f"species_with_chromosomes={summary['genome']['chromosomes']['species_total']}")
    print(f"species_with_hostplant={summary['Hostplant']['species_total']}")
    print(f"species_with_distribution={summary['distribution']['species_total']}")
    print(f"species_with_coi={summary['COI']['species_total']}")
    print(f"species_with_pdb={summary['PDB']['species_total']}")
    print(f"species_with_transcriptome={summary['Transcriptome']['species_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
