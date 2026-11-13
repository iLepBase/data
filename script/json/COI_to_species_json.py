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
DEFAULT_INFO_TSV = Path("data/raw/COI/bold_info.tsv")
DEFAULT_COI_DIR = Path("data/clean/COI/species")
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_LOG_OUTPUT = Path("script/json/stat.json")

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]+')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write BOLD COI records into species JSON files."
    )
    parser.add_argument(
        "--info-tsv",
        default=str(DEFAULT_INFO_TSV),
        help="COI metadata TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--coi-dir",
        default=str(DEFAULT_COI_DIR),
        help="Per-species COI FASTA directory, relative to the iLepbase root by default.",
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
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100000,
        help="Print progress every N TSV rows.",
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


def has_usable_sequence_length(value: str | None) -> bool:
    cleaned = clean_value(value)
    return cleaned is not None and cleaned.lower() != "none"


def parse_nuc_length(value: str | None) -> int | None:
    cleaned = clean_value(value)
    if cleaned is None or cleaned.lower() == "none":
        return None
    if cleaned.isdigit():
        return int(cleaned)
    return None


def build_legacy_filename(species_name: str) -> str:
    safe_name = INVALID_FILENAME_CHARS.sub("_", species_name).strip()
    if not safe_name:
        raise ValueError("Species name becomes empty after filename normalization.")
    return f"{safe_name}.fasta"


def build_coi_filename(species_name: str) -> str:
    return build_legacy_filename(species_name).replace(" ", "_")


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


def add_unique_value(container: OrderedDict[str, None], value: str | None) -> None:
    cleaned = clean_value(value)
    if cleaned is not None and cleaned not in container:
        container[cleaned] = None


def collapse_unique_values(container: OrderedDict[str, None]) -> str | list[str] | None:
    values = list(container.keys())
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def resolve_species_json_path(species_name: str, json_dir: Path) -> Path | None:
    base_stem = slugify_species_name(species_name)
    direct_path = json_dir / f"{base_stem}.json"
    return direct_path if direct_path.exists() else None


def blank_species_group(coi_path: str) -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("taxonomy", OrderedDict(
                [
                    ("family", OrderedDict()),
                    ("subfamily", OrderedDict()),
                    ("genus", OrderedDict()),
                    ("species", OrderedDict()),
                ]
            )),
            ("records", OrderedDict()),
            ("coi_path", coi_path),
        ]
    )


def load_coi_groups(
    info_tsv: Path,
    coi_dir: Path,
    progress_every: int,
) -> tuple[OrderedDict[str, OrderedDict[str, object]], dict[str, int]]:
    species_groups: OrderedDict[str, OrderedDict[str, object]] = OrderedDict()
    stats = {
        "rows_total": 0,
        "rows_without_species": 0,
        "rows_without_processid": 0,
        "rows_without_sequence_length": 0,
        "rows_with_missing_coi_path": 0,
        "duplicate_records": 0,
    }

    with info_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "taxo_species",
            "bold_processid",
            "bold_family",
            "bold_subfamily",
            "bold_genus",
            "bold_species",
            "bold_country_iso",
            "bold_nuc_basecount",
        }
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"Missing required columns in {info_tsv}: {', '.join(missing)}")

        for row in reader:
            stats["rows_total"] += 1
            if progress_every > 0 and stats["rows_total"] % progress_every == 0:
                print(
                    f"[progress] rows_total={stats['rows_total']:,} species_with_coi={len(species_groups):,}",
                    flush=True,
                )

            species_name = normalize_species_name(row["taxo_species"])
            process_id = clean_value(row["bold_processid"])

            if species_name is None:
                stats["rows_without_species"] += 1
                continue
            if process_id is None:
                stats["rows_without_processid"] += 1
                continue
            if not has_usable_sequence_length(row["bold_nuc_basecount"]):
                stats["rows_without_sequence_length"] += 1
                continue

            coi_path = coi_dir / build_coi_filename(species_name)
            if not coi_path.exists():
                stats["rows_with_missing_coi_path"] += 1

            group = species_groups.setdefault(
                species_name,
                blank_species_group(to_repo_relative_path(coi_path)),
            )

            taxonomy = group["taxonomy"]
            add_unique_value(taxonomy["family"], row["bold_family"])
            add_unique_value(taxonomy["subfamily"], row["bold_subfamily"])
            add_unique_value(taxonomy["genus"], row["bold_genus"])
            add_unique_value(taxonomy["species"], row["bold_species"])

            records = group["records"]
            if process_id in records:
                stats["duplicate_records"] += 1
                continue

            records[process_id] = OrderedDict(
                [
                    ("id", process_id),
                    ("country_iso", clean_value(row["bold_country_iso"])),
                    ("nuc_length", parse_nuc_length(row["bold_nuc_basecount"])),
                    ("path", group["coi_path"]),
                ]
            )

    return species_groups, stats


def build_coi_payload(group: OrderedDict[str, object]) -> OrderedDict[str, object]:
    # BOLD taxonomy here is built from bold_info.tsv, not copied from the existing species JSON.
    taxonomy = group["taxonomy"]
    records = list(group["records"].values())
    return OrderedDict(
        [
            ("source", "bold"),
            ("count", len(records)),
            (
                "bold_taxonomy",
                OrderedDict(
                    [
                        ("family", collapse_unique_values(taxonomy["family"])),
                        ("subfamily", collapse_unique_values(taxonomy["subfamily"])),
                        ("genus", collapse_unique_values(taxonomy["genus"])),
                        ("species", collapse_unique_values(taxonomy["species"])),
                    ]
                ),
            ),
            ("records", records),
        ]
    )


def build_coi_log_entry(
    species_with_coi: int,
    coi_records_written: int,
) -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("species_total", species_with_coi),
            ("records_total", coi_records_written),
        ]
    )


def update_log_json(log_output: Path, coi_log_entry: OrderedDict[str, object], indent: int) -> None:
    if log_output.exists():
        with log_output.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)
    else:
        document = OrderedDict()

    document["COI"] = coi_log_entry

    with log_output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")


def update_species_jsons(
    species_groups: OrderedDict[str, OrderedDict[str, object]],
    json_dir: Path,
    indent: int,
) -> tuple[int, int, int]:
    files_updated = 0
    species_missing_json = 0
    files_unchanged = 0

    for index, (species_name, group) in enumerate(species_groups.items(), start=1):
        if index % 1000 == 0:
            print(
                f"[write] species_processed={index:,} json_files_updated={files_updated:,}",
                flush=True,
            )

        json_path = resolve_species_json_path(species_name, json_dir)
        if json_path is None:
            species_missing_json += 1
            continue

        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)

        coi_payload = build_coi_payload(group)
        legacy_has_lowercase = "coi" in document
        if legacy_has_lowercase:
            del document["coi"]
        if not legacy_has_lowercase and document.get("COI") == coi_payload:
            files_unchanged += 1
            continue
        document["COI"] = coi_payload

        with json_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
        files_updated += 1

    return files_updated, species_missing_json, files_unchanged


def main() -> int:
    args = parse_args()
    info_tsv = resolve_repo_path(args.info_tsv)
    coi_dir = resolve_repo_path(args.coi_dir)
    json_dir = resolve_repo_path(args.json_dir)
    log_output = resolve_repo_path(args.log_output)

    species_groups, stats = load_coi_groups(
        info_tsv=info_tsv,
        coi_dir=coi_dir,
        progress_every=args.progress_every,
    )
    files_updated, species_missing_json, files_unchanged = update_species_jsons(
        species_groups=species_groups,
        json_dir=json_dir,
        indent=args.indent,
    )

    total_records = sum(len(group["records"]) for group in species_groups.values())
    coi_log_entry = build_coi_log_entry(
        species_with_coi=len(species_groups),
        coi_records_written=total_records,
    )
    update_log_json(log_output, coi_log_entry, args.indent)

    print(f"root={ROOT}")
    print(f"info_tsv={info_tsv}")
    print(f"coi_dir={coi_dir}")
    print(f"json_dir={json_dir}")
    print(f"log_output={log_output}")
    print(f"species_with_coi={len(species_groups)}")
    print(f"coi_records_written={total_records}")
    print(f"json_files_updated={files_updated}")
    print(f"json_files_unchanged={files_unchanged}")
    print(f"species_missing_json={species_missing_json}")
    for key, value in stats.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
