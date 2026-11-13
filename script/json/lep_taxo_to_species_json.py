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


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = Path("data/clean/taxo/lep_taxo_20260414.tsv")
DEFAULT_OUTPUT = Path("data/clean/json")

FIELD_MAP = {
    "origin Species": "origin_species",
    "subfamliy": "subfamily",
    "genuis": "genus",
    "source link": "source_link",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert the lep_taxo TSV into one JSON file per species."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Input TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level.",
    )
    parser.add_argument(
        "--keep-orphans",
        action="store_true",
        help="Keep existing JSON files whose species are not present in the current taxonomy TSV.",
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


def normalize_row(row: dict[str, str]) -> dict[str, str | None]:
    normalized: dict[str, str | None] = {}
    for key, value in row.items():
        mapped_key = FIELD_MAP.get(key, key.strip().lower().replace(" ", "_"))
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


def unique_values(records: list[dict[str, str | None]], key: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for record in records:
        value = record.get(key)
        if value is None or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def collapse_values(values: list[str]) -> str | list[str] | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def build_species_document(
    species_name: str,
    records: list[dict[str, str | None]],
) -> dict[str, object]:
    taxonomy = OrderedDict(
        [
            ("family", collapse_values(unique_values(records, "family"))),
            ("family_id", collapse_values(unique_values(records, "fid"))),
            ("subfamily", collapse_values(unique_values(records, "subfamily"))),
            ("subfamily_id", collapse_values(unique_values(records, "sfid"))),
            ("genus", collapse_values(unique_values(records, "genus"))),
            ("genus_id", collapse_values(unique_values(records, "gid"))),
            ("species_id", collapse_values(unique_values(records, "sid"))),
        ]
    )

    return OrderedDict(
        [
            ("species", species_name),
            ("record_count", len(records)),
            ("taxonomy", taxonomy),
            ("authority", collapse_values(unique_values(records, "authority"))),
            ("basionym", collapse_values(unique_values(records, "basionym"))),
            ("sources", unique_values(records, "source")),
            ("source_links", unique_values(records, "source_link")),
        ]
    )


def load_species_groups(input_path: Path) -> tuple[OrderedDict[str, list[dict[str, str | None]]], int]:
    species_groups: OrderedDict[str, list[dict[str, str | None]]] = OrderedDict()
    skipped_rows = 0

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            normalized = normalize_row(row)
            species_name = resolve_species_name(normalized)
            if species_name is None:
                skipped_rows += 1
                continue
            species_groups.setdefault(species_name, []).append(normalized)

    return species_groups, skipped_rows


def write_species_json(
    species_groups: OrderedDict[str, list[dict[str, str | None]]],
    output_dir: Path,
    indent: int,
    remove_orphans: bool,
) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    used_stems: dict[str, str] = {}
    written = 0
    expected_names: set[str] = set()

    for species_name, records in species_groups.items():
        file_stem = make_unique_stem(species_name, used_stems)
        output_path = output_dir / f"{file_stem}.json"
        expected_names.add(output_path.name)
        document = build_species_document(species_name, records)
        with output_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
        written += 1

    removed = 0
    if remove_orphans:
        for path in output_dir.glob("*.json"):
            if path.name not in expected_names:
                path.unlink()
                removed += 1

    return written, removed


def main() -> int:
    args = parse_args()
    input_path = resolve_repo_path(args.input)
    output_dir = resolve_repo_path(args.output)

    species_groups, skipped_rows = load_species_groups(input_path)
    written_files, removed_files = write_species_json(
        species_groups,
        output_dir,
        args.indent,
        remove_orphans=not args.keep_orphans,
    )

    print(f"root={ROOT}")
    print(f"input={input_path}")
    print(f"output={output_dir}")
    print(f"species_files_written={written_files}")
    print(f"orphan_json_files_removed={removed_files}")
    print(f"skipped_rows_without_species={skipped_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
