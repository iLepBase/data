#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import OrderedDict
from pathlib import Path

from log_utils import refresh_log_summary


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDB_TSV = Path("data/clean/PDB/lep_pdb_20260416.tsv")
DEFAULT_PDB_DIR = Path("data/clean/PDB")
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_STAT_OUTPUT = Path("script/json/stat.json")

REQUIRED_FIELDS = {"pdb_id", "species"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write per-species PDB records into species JSON files."
    )
    parser.add_argument(
        "--pdb-tsv",
        default=str(DEFAULT_PDB_TSV),
        help="PDB metadata TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--pdb-dir",
        default=str(DEFAULT_PDB_DIR),
        help="PDB data directory containing entities/ metadata/ structures.",
    )
    parser.add_argument(
        "--json-dir",
        default=str(DEFAULT_JSON_DIR),
        help="Species JSON directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--stat-output",
        default=str(DEFAULT_STAT_OUTPUT),
        help="Stat JSON path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10000,
        help="Print progress every N PDB rows while loading the TSV.",
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


def clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def normalize_species_name(name: str | None) -> str | None:
    cleaned = clean_value(name)
    if cleaned is None:
        return None
    return " ".join(cleaned.split())


def load_species_json_paths(json_dir: Path) -> dict[str, Path]:
    species_to_path: dict[str, Path] = {}
    for json_path in sorted(json_dir.glob("*.json")):
        try:
            document = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        species_name = normalize_species_name(document.get("species"))
        if species_name is None or species_name in species_to_path:
            continue
        species_to_path[species_name] = json_path
    return species_to_path


def validate_header(fieldnames: list[str] | None, pdb_tsv: Path) -> None:
    if fieldnames is None:
        raise ValueError(f"PDB TSV has no header: {pdb_tsv}")
    missing = sorted(REQUIRED_FIELDS - set(fieldnames))
    if missing:
        raise ValueError(f"Missing required columns in {pdb_tsv}: {', '.join(missing)}")


def collect_relative_paths(paths: list[Path]) -> list[str]:
    return [to_repo_relative_path(path) for path in sorted(paths)]


def build_pdb_record(pdb_id: str, pdb_dir: Path) -> OrderedDict[str, object]:
    entities = collect_relative_paths(list((pdb_dir / "entities").glob(f"{pdb_id}_*.json")))

    metadata_path = pdb_dir / "metadata" / f"{pdb_id}.json"
    structure_path = pdb_dir / "structures" / f"{pdb_id}.cif"

    return OrderedDict(
        [
            ("pdb_id", pdb_id),
            ("entities", entities),
            ("metadata", to_repo_relative_path(metadata_path) if metadata_path.exists() else None),
            ("structures", to_repo_relative_path(structure_path) if structure_path.exists() else None),
        ]
    )


def load_pdb_groups(
    pdb_tsv: Path,
    pdb_dir: Path,
    progress_every: int,
) -> tuple[OrderedDict[str, OrderedDict[str, OrderedDict[str, object]]], dict[str, int]]:
    species_groups: OrderedDict[str, OrderedDict[str, OrderedDict[str, object]]] = OrderedDict()
    stats = {
        "rows_total": 0,
        "rows_without_species": 0,
        "rows_without_pdb_id": 0,
        "duplicate_pdb_rows": 0,
        "pdb_records_total": 0,
        "pdb_records_without_entities": 0,
        "pdb_records_without_metadata": 0,
        "pdb_records_without_structures": 0,
    }

    with pdb_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        validate_header(reader.fieldnames, pdb_tsv)

        for row in reader:
            stats["rows_total"] += 1
            if progress_every > 0 and stats["rows_total"] % progress_every == 0:
                print(
                    f"[progress] rows_total={stats['rows_total']:,} species_with_pdb={len(species_groups):,}",
                    flush=True,
                )

            species_name = normalize_species_name(row.get("species"))
            pdb_id = clean_value(row.get("pdb_id"))

            if species_name is None:
                stats["rows_without_species"] += 1
                continue
            if pdb_id is None:
                stats["rows_without_pdb_id"] += 1
                continue

            group = species_groups.setdefault(species_name, OrderedDict())
            if pdb_id in group:
                stats["duplicate_pdb_rows"] += 1
                continue

            record = build_pdb_record(pdb_id, pdb_dir)
            if not record["entities"]:
                stats["pdb_records_without_entities"] += 1
            if record["metadata"] is None:
                stats["pdb_records_without_metadata"] += 1
            if record["structures"] is None:
                stats["pdb_records_without_structures"] += 1

            group[pdb_id] = record
            stats["pdb_records_total"] += 1

    return species_groups, stats


def build_pdb_payload(records: OrderedDict[str, OrderedDict[str, object]]) -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("count", len(records)),
            ("records", list(records.values())),
        ]
    )


def update_species_jsons(
    species_groups: OrderedDict[str, OrderedDict[str, OrderedDict[str, object]]],
    species_to_path: dict[str, Path],
    indent: int,
) -> tuple[int, int, int]:
    files_updated = 0
    files_unchanged = 0
    species_missing_json = 0

    for species_name, records in species_groups.items():
        json_path = species_to_path.get(species_name)
        if json_path is None:
            species_missing_json += 1
            continue

        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)

        pdb_payload = build_pdb_payload(records)
        if document.get("PDB") == pdb_payload:
            files_unchanged += 1
            continue

        document["PDB"] = pdb_payload
        with json_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
        files_updated += 1

    return files_updated, files_unchanged, species_missing_json


def main() -> int:
    args = parse_args()
    pdb_tsv = resolve_repo_path(args.pdb_tsv)
    pdb_dir = resolve_repo_path(args.pdb_dir)
    json_dir = resolve_repo_path(args.json_dir)
    stat_output = resolve_repo_path(args.stat_output)
    species_to_path = load_species_json_paths(json_dir)

    species_groups, stats = load_pdb_groups(
        pdb_tsv=pdb_tsv,
        pdb_dir=pdb_dir,
        progress_every=args.progress_every,
    )
    files_updated, files_unchanged, species_missing_json = update_species_jsons(
        species_groups=species_groups,
        species_to_path=species_to_path,
        indent=args.indent,
    )

    summary = refresh_log_summary(json_dir=json_dir, log_output=stat_output, indent=args.indent)

    print(f"pdb_tsv={to_repo_relative_path(pdb_tsv)}")
    print(f"pdb_dir={to_repo_relative_path(pdb_dir)}")
    print(f"json_dir={to_repo_relative_path(json_dir)}")
    print(f"stat_output={to_repo_relative_path(stat_output)}")
    print(f"species_with_pdb={len(species_groups)}")
    print(f"pdb_records_total={stats['pdb_records_total']}")
    print(f"json_files_updated={files_updated}")
    print(f"json_files_unchanged={files_unchanged}")
    print(f"species_missing_json={species_missing_json}")
    print(f"pdb_records_without_entities={stats['pdb_records_without_entities']}")
    print(f"pdb_records_without_metadata={stats['pdb_records_without_metadata']}")
    print(f"pdb_records_without_structures={stats['pdb_records_without_structures']}")
    print(f"stat_species_with_pdb={summary['PDB']['species_total']}")
    print(f"stat_pdb_records_total={summary['PDB']['records_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
