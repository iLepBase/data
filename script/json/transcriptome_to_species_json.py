#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSCRIPTOME_CSV = Path("data/clean/SRA/transcriptome_data_20260323.csv")
DEFAULT_TAXO_TSV = Path("data/clean/taxo/lep_taxo_20260415.tsv")
DEFAULT_JSON_DIR = Path("data/clean/json")
BIOSAMPLE_URL = "https://www.ncbi.nlm.nih.gov/biosample/{accession}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write SRA transcriptome metadata into species JSON files under the Transcriptome field."
    )
    parser.add_argument(
        "--transcriptome-csv",
        default=str(DEFAULT_TRANSCRIPTOME_CSV),
        help="Transcriptome CSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--taxo-tsv",
        default=str(DEFAULT_TAXO_TSV),
        help="Taxonomy TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--json-dir",
        default=str(DEFAULT_JSON_DIR),
        help="Species JSON directory, relative to the iLepbase root by default.",
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
        default=100000,
        help="Print progress every N transcriptome rows.",
    )
    return parser.parse_args()


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


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


def normalize_run_id(value: str | None) -> str | None:
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    if cleaned.lower() in {"n/a", "na", "unknown", "none", "null"}:
        return None
    return cleaned


def normalize_transcriptome_value(value: str | None) -> str:
    cleaned = clean_value(value)
    if cleaned is None:
        return "unknown"
    if cleaned.lower() in {"unknown", "none", "null"}:
        return "unknown"
    return cleaned


def normalize_transcriptome_field(field: str, value: str | None) -> str | None:
    if field.endswith(".Corr"):
        cleaned = clean_value(value)
        if cleaned is None:
            return ""
        if cleaned.lower() in {"unknown", "none", "null"}:
            return "unknown"
        return cleaned
    return clean_value(value)


def build_biosample_link(sample_accession: str | None) -> str:
    cleaned = clean_value(sample_accession)
    if cleaned is None:
        return ""
    return BIOSAMPLE_URL.format(accession=cleaned)


def load_taxo_species_map(taxo_tsv: Path) -> dict[str, str]:
    species_map: dict[str, str] = {}
    ambiguous_names: set[str] = set()

    with taxo_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"origin Species", "species"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"Missing required columns in {taxo_tsv}: {', '.join(missing)}")

        for row in reader:
            origin_species = normalize_species_name(row.get("origin Species"))
            canonical_species = normalize_species_name(row.get("species")) or origin_species
            if canonical_species is None:
                continue
            if origin_species is not None:
                if origin_species in ambiguous_names:
                    continue
                previous = species_map.get(origin_species)
                if previous is not None and previous != canonical_species:
                    species_map.pop(origin_species, None)
                    ambiguous_names.add(origin_species)
                    continue
                species_map[origin_species] = canonical_species
            species_map.setdefault(canonical_species, canonical_species)

    return species_map


def load_species_json_paths(json_dir: Path) -> dict[str, Path]:
    species_to_path: dict[str, Path] = {}
    for json_path in sorted(json_dir.glob("*.json")):
        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        species_name = normalize_species_name(document.get("species"))
        if species_name is not None:
            species_to_path[species_name] = json_path
    return species_to_path


def resolve_taxo_species(organism_name: str | None, taxo_species_map: dict[str, str]) -> str | None:
    normalized = normalize_species_name(organism_name)
    if normalized is None:
        return None
    exact = taxo_species_map.get(normalized)
    if exact is not None:
        return exact
    parts = normalized.split()
    if len(parts) >= 3:
        return taxo_species_map.get(" ".join(parts[:2]))
    return None


def build_transcriptome_record(
    row: dict[str, str | None],
    fieldnames: list[str],
) -> OrderedDict[str, object]:
    record = OrderedDict(
        (field, normalize_transcriptome_field(field, row.get(field)))
        for field in fieldnames
        if field != "Organism Name"
    )
    insert_after = "Sample Accession"
    enriched = OrderedDict()
    for field, value in record.items():
        enriched[field] = value
        if field == insert_after:
            enriched["Biosample Link"] = build_biosample_link(str(value) if value is not None else None)
    if "Biosample Link" not in enriched:
        enriched["Biosample Link"] = build_biosample_link(row.get("Sample Accession"))
    return enriched


def build_transcriptome_payload(
    records_by_run: OrderedDict[str, OrderedDict[str, object]],
) -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("count", len(records_by_run)),
            ("records", records_by_run),
        ]
    )


def load_transcriptome_groups(
    transcriptome_csv: Path,
    taxo_species_map: dict[str, str],
    species_to_path: dict[str, Path],
    progress_every: int,
) -> tuple[OrderedDict[str, OrderedDict[str, OrderedDict[str, object]]], dict[str, int]]:
    groups: OrderedDict[str, OrderedDict[str, OrderedDict[str, object]]] = OrderedDict()
    stats = {
        "rows_total": 0,
        "rows_without_run_id": 0,
        "rows_without_organism_name": 0,
        "rows_unmatched_taxo": 0,
        "rows_unmatched_json": 0,
        "duplicate_run_ids": 0,
    }

    with transcriptome_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"Organism Name", "Run ID"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"Missing required columns in {transcriptome_csv}: {', '.join(missing)}")
        transcriptome_fields = list(reader.fieldnames)

        for row in reader:
            stats["rows_total"] += 1
            if progress_every > 0 and stats["rows_total"] % progress_every == 0:
                print(
                    f"[progress] rows_total={stats['rows_total']:,} "
                    f"species_matched={len(groups):,}",
                    flush=True,
                )

            run_id = normalize_run_id(row.get("Run ID"))
            if run_id is None:
                stats["rows_without_run_id"] += 1
                continue

            organism_name = normalize_species_name(row.get("Organism Name"))
            if organism_name is None:
                stats["rows_without_organism_name"] += 1
                continue

            matched_species = resolve_taxo_species(organism_name, taxo_species_map)
            if matched_species is None:
                stats["rows_unmatched_taxo"] += 1
                continue
            if matched_species not in species_to_path:
                stats["rows_unmatched_json"] += 1
                continue

            species_records = groups.setdefault(matched_species, OrderedDict())
            record = build_transcriptome_record(row, transcriptome_fields)
            existing = species_records.get(run_id)
            if existing is not None:
                if existing != record:
                    raise ValueError(
                        f"Conflicting transcriptome rows for Run ID {run_id} in species {matched_species}"
                    )
                stats["duplicate_run_ids"] += 1
                continue
            species_records[run_id] = record

    return groups, stats


def write_json_atomic(json_path: Path, document: OrderedDict[str, object], indent: int) -> None:
    temp_path = json_path.with_name(f"{json_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")
    temp_path.replace(json_path)


def update_species_jsons(
    species_to_path: dict[str, Path],
    transcriptome_groups: OrderedDict[str, OrderedDict[str, OrderedDict[str, object]]],
    indent: int,
) -> tuple[int, int]:
    json_files_updated = 0
    json_files_unchanged = 0

    for species_name, records_by_run in transcriptome_groups.items():
        json_path = species_to_path[species_name]
        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)

        payload = build_transcriptome_payload(records_by_run)
        if document.get("Transcriptome") == payload:
            json_files_unchanged += 1
            continue
        if "transcriptome" in document:
            del document["transcriptome"]
        document["Transcriptome"] = payload
        write_json_atomic(json_path, document, indent)
        json_files_updated += 1

    return json_files_updated, json_files_unchanged


def main() -> int:
    args = parse_args()
    transcriptome_csv = resolve_repo_path(args.transcriptome_csv)
    taxo_tsv = resolve_repo_path(args.taxo_tsv)
    json_dir = resolve_repo_path(args.json_dir)

    taxo_species_map = load_taxo_species_map(taxo_tsv)
    species_to_path = load_species_json_paths(json_dir)
    transcriptome_groups, stats = load_transcriptome_groups(
        transcriptome_csv=transcriptome_csv,
        taxo_species_map=taxo_species_map,
        species_to_path=species_to_path,
        progress_every=args.progress_every,
    )
    json_files_updated, json_files_unchanged = update_species_jsons(
        species_to_path=species_to_path,
        transcriptome_groups=transcriptome_groups,
        indent=args.indent,
    )

    total_runs_written = sum(len(records) for records in transcriptome_groups.values())
    print(f"root={ROOT}")
    print(f"transcriptome_csv={transcriptome_csv}")
    print(f"taxo_tsv={taxo_tsv}")
    print(f"json_dir={json_dir}")
    print(f"species_with_transcriptome={len(transcriptome_groups)}")
    print(f"transcriptome_runs_written={total_runs_written}")
    print(f"json_files_updated={json_files_updated}")
    print(f"json_files_unchanged={json_files_unchanged}")
    for key, value in stats.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
