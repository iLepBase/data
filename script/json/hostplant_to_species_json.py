#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import OrderedDict
from pathlib import Path

from openpyxl import load_workbook

from log_utils import refresh_log_summary


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOSTPLANT_XLSX = Path("data/raw/hostplant/寄主植物_reviewed_20260419.xlsx")
DEFAULT_TAXONOMY_TSV = Path("data/clean/taxo/lep_taxo_20260415.tsv")
DEFAULT_SPECIES_DIR = Path("data/clean/hostplant/species")
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_LOG_OUTPUT = Path("script/json/stat.json")

TAXO_FIELD_MAP = {
    "origin Species": "origin_species",
    "subfamliy": "subfamily",
    "genuis": "genus",
    "source link": "source_link",
}
SOURCE_SPLIT_RE = re.compile(r"[;；]+")
CLEAN_RANK_FIELDS = (
    ("species", "Clean.P.species"),
    ("genus", "Clean.P.genus"),
    ("family", "Clean.P.family"),
    ("order", "Clean.P.order"),
    ("class", "Clean.P.class"),
    ("phylum", "Clean.P.phylum"),
)
SOURCE_TAXONOMY_FIELDS = (
    ("kingdom", "Source.P.kingdom"),
    ("order", "Source.P.Order"),
    ("family", "Source.P.Family"),
    ("genus", "Source.P.Genus"),
    ("name", "Source.P.Name"),
)
REQUIRED_HOSTPLANT_COLUMNS = (
    "I.Species",
    "Source.P.Order",
    "Source.P.Family",
    "Source.P.Genus",
    "Source.P.Name",
    "Source.P.kingdom",
    "Clean.P.taxid",
    "Clean.P.phylum",
    "Clean.P.class",
    "Clean.P.order",
    "Clean.P.family",
    "Clean.P.genus",
    "Clean.P.species",
    "分类数据来源",
    "source",
    "source label",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Write per-species hostplant JSON files and attach compact Hostplant "
            "summaries to species JSON files."
        )
    )
    parser.add_argument(
        "--hostplant-xlsx",
        default=str(DEFAULT_HOSTPLANT_XLSX),
        help="Reviewed hostplant Excel file, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--taxonomy-tsv",
        default=str(DEFAULT_TAXONOMY_TSV),
        help="Taxonomy TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--species-dir",
        default=str(DEFAULT_SPECIES_DIR),
        help="Per-species hostplant JSON directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--json-dir",
        default=str(DEFAULT_JSON_DIR),
        help="Species JSON directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--log-output",
        default=str(DEFAULT_LOG_OUTPUT),
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


def clean_value(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def normalize_taxo_row(row: dict[str, str]) -> dict[str, str | None]:
    normalized: dict[str, str | None] = {}
    for key, value in row.items():
        mapped_key = TAXO_FIELD_MAP.get(key, key.strip().lower().replace(" ", "_"))
        normalized[mapped_key] = clean_value(value)
    return normalized


def resolve_species_name(row: dict[str, str | None]) -> str | None:
    return row.get("species") or row.get("origin_species")


def slugify_species_name(name: str, max_length: int = 150) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").lower()
    if not slug:
        slug = "species"
    if len(slug) <= max_length:
        return slug
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    keep = max_length - len(digest) - 2
    truncated = slug[:keep].rstrip("_")
    return f"{truncated}__{digest}"


def make_unique_stem(name: str, used_stems: dict[str, str]) -> str:
    base_stem = slugify_species_name(name)
    existing_name = used_stems.get(base_stem)
    if existing_name is None:
        used_stems[base_stem] = name
        return base_stem
    if existing_name == name:
        return base_stem

    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    candidate = f"{base_stem}__{digest}"
    if len(candidate) > 180:
        candidate = candidate[:169].rstrip("_") + f"__{digest}"
    used_stems[candidate] = name
    return candidate


def split_sources(source_value: str | None) -> list[str]:
    cleaned = clean_value(source_value)
    if cleaned is None:
        return []

    values: list[str] = []
    seen: set[str] = set()
    for part in SOURCE_SPLIT_RE.split(cleaned):
        item = clean_value(part)
        if item is None or item in seen:
            continue
        seen.add(item)
        values.append(item)
    if values:
        return values
    return [cleaned]


def build_clean_taxonomy(row: dict[str, str | None]) -> OrderedDict[str, object] | None:
    taxonomy_values = OrderedDict(
        [
            ("phylum", clean_value(row.get("Clean.P.phylum"))),
            ("class", clean_value(row.get("Clean.P.class"))),
            ("order", clean_value(row.get("Clean.P.order"))),
            ("family", clean_value(row.get("Clean.P.family"))),
            ("genus", clean_value(row.get("Clean.P.genus"))),
            ("species", clean_value(row.get("Clean.P.species"))),
        ]
    )

    rank = None
    name = None
    for candidate_rank, field_name in CLEAN_RANK_FIELDS:
        value = clean_value(row.get(field_name))
        if value is not None:
            rank = candidate_rank
            name = value
            break

    if rank is None or name is None:
        return None

    return OrderedDict(
        [
            ("rank", rank),
            ("name", name),
            ("classification_source", clean_value(row.get("分类数据来源"))),
            ("taxid", clean_value(row.get("Clean.P.taxid"))),
            *taxonomy_values.items(),
        ]
    )


def build_source_taxonomy(row: dict[str, str | None]) -> OrderedDict[str, object] | None:
    source_taxonomy = OrderedDict(
        (field_name, clean_value(row.get(column_name)))
        for field_name, column_name in SOURCE_TAXONOMY_FIELDS
    )
    if not any(value is not None for value in source_taxonomy.values()):
        return None
    return source_taxonomy


def build_hostplant_record(row: dict[str, str | None]) -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("clean_taxonomy", build_clean_taxonomy(row)),
            ("source_taxonomy", build_source_taxonomy(row)),
            ("source_label", clean_value(row.get("source label"))),
            ("sources", split_sources(row.get("source"))),
        ]
    )


def write_json_atomic(path: Path, document: OrderedDict[str, object], indent: int) -> None:
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")
    temp_path.replace(path)


def load_taxonomy_mappings(
    taxonomy_tsv: Path,
) -> tuple[OrderedDict[str, str], OrderedDict[str, str]]:
    origin_to_target: OrderedDict[str, str] = OrderedDict()
    target_species_names: OrderedDict[str, None] = OrderedDict()

    with taxonomy_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            normalized = normalize_taxo_row(row)
            origin_species = normalized.get("origin_species")
            target_species = resolve_species_name(normalized)
            if origin_species is None or target_species is None:
                continue

            existing_target = origin_to_target.get(origin_species)
            if existing_target is not None and existing_target != target_species:
                raise ValueError(
                    f"Conflicting target species for origin species {origin_species!r}: "
                    f"{existing_target!r} vs {target_species!r}"
                )

            origin_to_target[origin_species] = target_species
            target_species_names.setdefault(target_species, None)

    target_to_stem: OrderedDict[str, str] = OrderedDict()
    used_stems: dict[str, str] = {}
    for target_species in target_species_names:
        target_to_stem[target_species] = make_unique_stem(target_species, used_stems)

    return origin_to_target, target_to_stem


def load_hostplant_groups(
    hostplant_xlsx: Path,
    origin_to_target: OrderedDict[str, str],
) -> tuple[OrderedDict[str, list[OrderedDict[str, object]]], dict[str, int]]:
    workbook = load_workbook(hostplant_xlsx, read_only=True, data_only=True)
    if "Sheet 1" not in workbook.sheetnames:
        raise ValueError(f"Expected sheet 'Sheet 1' in {hostplant_xlsx}")

    worksheet = workbook["Sheet 1"]
    row_iter = worksheet.iter_rows(values_only=True)
    header = next(row_iter)
    if header is None:
        raise ValueError(f"Hostplant workbook is empty: {hostplant_xlsx}")

    column_index = {name: index for index, name in enumerate(header) if name is not None}
    missing_columns = [name for name in REQUIRED_HOSTPLANT_COLUMNS if name not in column_index]
    if missing_columns:
        raise ValueError(
            f"Missing required columns in {hostplant_xlsx}: {', '.join(missing_columns)}"
        )

    species_groups: OrderedDict[str, list[OrderedDict[str, object]]] = OrderedDict()
    stats = {
        "rows_total": 0,
        "rows_without_input_species": 0,
        "rows_without_taxonomy_match": 0,
        "matched_rows": 0,
        "rows_without_clean_taxonomy": 0,
    }

    for row in row_iter:
        stats["rows_total"] += 1
        input_species = clean_value(row[column_index["I.Species"]])
        if input_species is None:
            stats["rows_without_input_species"] += 1
            continue

        target_species = origin_to_target.get(input_species)
        if target_species is None:
            stats["rows_without_taxonomy_match"] += 1
            continue

        normalized_row = {
            column_name: clean_value(row[column_index[column_name]])
            for column_name in REQUIRED_HOSTPLANT_COLUMNS
        }
        record = build_hostplant_record(normalized_row)
        if record["clean_taxonomy"] is None:
            stats["rows_without_clean_taxonomy"] += 1

        species_groups.setdefault(target_species, []).append(record)
        stats["matched_rows"] += 1

    return species_groups, stats


def build_hostplant_document(
    species_name: str,
    records: list[OrderedDict[str, object]],
) -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("species", species_name),
            ("record_count", len(records)),
            ("records", records),
        ]
    )


def sync_hostplant_species_files(
    species_groups: OrderedDict[str, list[OrderedDict[str, object]]],
    species_dir: Path,
    target_to_stem: OrderedDict[str, str],
    indent: int,
) -> tuple[dict[str, int], set[str], set[str]]:
    species_dir.mkdir(parents=True, exist_ok=True)
    expected_stems: set[str] = set()
    removed_stems: set[str] = set()
    stats = {
        "species_files_written": 0,
        "species_files_unchanged": 0,
        "orphan_species_files_removed": 0,
    }

    for target_species, records in species_groups.items():
        stem = target_to_stem[target_species]
        expected_stems.add(stem)
        output_path = species_dir / f"{stem}.json"
        document = build_hostplant_document(target_species, records)

        if output_path.exists():
            with output_path.open("r", encoding="utf-8") as handle:
                existing = json.load(handle, object_pairs_hook=OrderedDict)
            if existing == document:
                stats["species_files_unchanged"] += 1
                continue

        write_json_atomic(output_path, document, indent)
        stats["species_files_written"] += 1

    for existing_path in sorted(species_dir.glob("*.json")):
        if existing_path.stem in expected_stems:
            continue
        removed_stems.add(existing_path.stem)
        existing_path.unlink()
        stats["orphan_species_files_removed"] += 1

    return stats, expected_stems, removed_stems


def sync_species_json_summaries(
    species_groups: OrderedDict[str, list[OrderedDict[str, object]]],
    species_dir: Path,
    json_dir: Path,
    target_to_stem: OrderedDict[str, str],
    removed_stems: set[str],
    indent: int,
) -> dict[str, int]:
    stats = {
        "json_files_updated": 0,
        "json_hostplant_removed": 0,
        "missing_json_files": 0,
    }

    for target_species, records in species_groups.items():
        stem = target_to_stem[target_species]
        json_path = json_dir / f"{stem}.json"
        if not json_path.exists():
            stats["missing_json_files"] += 1
            continue

        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)

        summary = OrderedDict(
            [
                ("count", len(records)),
                ("path", to_repo_relative_path(species_dir / f"{stem}.json")),
            ]
        )
        if document.get("Hostplant") == summary:
            continue

        document["Hostplant"] = summary
        write_json_atomic(json_path, document, indent)
        stats["json_files_updated"] += 1

    for stem in sorted(removed_stems):
        json_path = json_dir / f"{stem}.json"
        if not json_path.exists():
            continue
        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)
        if "Hostplant" not in document:
            continue
        document.pop("Hostplant", None)
        write_json_atomic(json_path, document, indent)
        stats["json_hostplant_removed"] += 1

    return stats


def main() -> int:
    args = parse_args()
    hostplant_xlsx = resolve_repo_path(args.hostplant_xlsx)
    taxonomy_tsv = resolve_repo_path(args.taxonomy_tsv)
    species_dir = resolve_repo_path(args.species_dir)
    json_dir = resolve_repo_path(args.json_dir)
    log_output = resolve_repo_path(args.log_output)

    origin_to_target, target_to_stem = load_taxonomy_mappings(taxonomy_tsv)
    species_groups, load_stats = load_hostplant_groups(hostplant_xlsx, origin_to_target)
    file_stats, expected_stems, removed_stems = sync_hostplant_species_files(
        species_groups=species_groups,
        species_dir=species_dir,
        target_to_stem=target_to_stem,
        indent=args.indent,
    )
    json_stats = sync_species_json_summaries(
        species_groups=species_groups,
        species_dir=species_dir,
        json_dir=json_dir,
        target_to_stem=target_to_stem,
        removed_stems=removed_stems,
        indent=args.indent,
    )
    summary = refresh_log_summary(json_dir=json_dir, log_output=log_output, indent=args.indent)

    print(f"root={ROOT}")
    print(f"hostplant_xlsx={hostplant_xlsx}")
    print(f"taxonomy_tsv={taxonomy_tsv}")
    print(f"hostplant_species_dir={species_dir}")
    print(f"json_dir={json_dir}")
    print(f"stat_output={log_output}")
    print(f"rows_total={load_stats['rows_total']}")
    print(f"rows_without_input_species={load_stats['rows_without_input_species']}")
    print(f"rows_without_taxonomy_match={load_stats['rows_without_taxonomy_match']}")
    print(f"matched_rows={load_stats['matched_rows']}")
    print(f"rows_without_clean_taxonomy={load_stats['rows_without_clean_taxonomy']}")
    print(f"matched_species_total={len(species_groups)}")
    print(f"species_files_written={file_stats['species_files_written']}")
    print(f"species_files_unchanged={file_stats['species_files_unchanged']}")
    print(f"orphan_species_files_removed={file_stats['orphan_species_files_removed']}")
    print(f"json_files_updated={json_stats['json_files_updated']}")
    print(f"json_hostplant_removed={json_stats['json_hostplant_removed']}")
    print(f"missing_json_files={json_stats['missing_json_files']}")
    print(f"stat_species_with_hostplant={summary['Hostplant']['species_total']}")
    print(f"stat_hostplant_records_total={summary['Hostplant']['records_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
