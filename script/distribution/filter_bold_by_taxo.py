#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import tarfile
from pathlib import Path
from typing import Iterator, TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOLD = Path("data/raw/distribution/BOLD_Public_27-FEB-2026.tsv")
DEFAULT_TAXO = Path("data/clean/taxo/lep_taxo_20260414.tsv")
DEFAULT_OUTPUT = Path("data/clean/distribution/bold_info.tsv")

BOLD_INDEX = {
    "processid": 0,
    "family": 17,
    "subfamily": 18,
    "genus": 20,
    "species": 21,
    "coord": 54,
    "country_ocean": 47,
    "country_iso": 48,
    "nuc_basecount": 67,
}

OUTPUT_FIELDS = [
    "taxo_species",
    "taxo_family",
    "taxo_subfamily",
    "taxo_genus",
    "bold_processid",
    "bold_family",
    "bold_subfamily",
    "bold_genus",
    "bold_species",
    "latitude",
    "longitude",
    "bold_country_ocean",
    "bold_country_iso",
    "bold_nuc_basecount",
]


def set_csv_field_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).strip())


def resolve_from_root(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def split_coord(text: str | None) -> tuple[str, str]:
    coord = normalize_space(text)
    if not coord or coord == "None":
        return "", ""

    coord = coord.strip("[]")
    parts = [part.strip() for part in coord.split(",", 1)]
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def load_taxonomy(path: Path) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError(f"Taxonomy TSV has no header: {path}")

        for row in reader:
            origin_species = normalize_space(row.get("origin Species", ""))
            if not origin_species:
                continue
            taxo_species = normalize_space(row.get("species", "")) or origin_species
            records[origin_species] = {
                "taxo_species": taxo_species,
                "taxo_family": normalize_space(row.get("family", "")),
                "taxo_subfamily": normalize_space(row.get("subfamliy", "")),
                "taxo_genus": normalize_space(row.get("genuis", "")),
            }
    return records


def open_bold_text(path: Path) -> TextIO:
    if tarfile.is_tarfile(path):
        archive = tarfile.open(path, "r")
        members = [member for member in archive.getmembers() if member.isfile()]
        tsv_members = [
            member
            for member in members
            if member.name.lower().endswith(".tsv")
            and not member.name.lower().endswith(".datapackage.tsv")
        ]
        if not tsv_members:
            archive.close()
            raise FileNotFoundError(f"No TSV member found inside archive: {path}")

        extracted = archive.extractfile(tsv_members[0])
        if extracted is None:
            archive.close()
            raise FileNotFoundError(f"Could not extract TSV member from archive: {path}")

        text = io.TextIOWrapper(extracted, encoding="utf-8-sig", newline="")
        setattr(text, "_archive_handle", archive)
        return text

    return path.open("r", encoding="utf-8-sig", newline="")


def iter_bold_rows(path: Path) -> Iterator[list[str]]:
    handle = open_bold_text(path)
    try:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if header is None:
            raise ValueError(f"BOLD input has no header: {path}")

        for row in reader:
            yield row
    finally:
        archive = getattr(handle, "_archive_handle", None)
        handle.close()
        if archive is not None:
            archive.close()


def build_output_row(
    taxo_row: dict[str, str],
    bold_row: list[str],
) -> dict[str, str]:
    latitude, longitude = split_coord(bold_row[BOLD_INDEX["coord"]])
    return {
        "taxo_species": taxo_row["taxo_species"],
        "taxo_family": taxo_row["taxo_family"],
        "taxo_subfamily": taxo_row["taxo_subfamily"],
        "taxo_genus": taxo_row["taxo_genus"],
        "bold_processid": normalize_space(bold_row[BOLD_INDEX["processid"]]),
        "bold_family": normalize_space(bold_row[BOLD_INDEX["family"]]),
        "bold_subfamily": normalize_space(bold_row[BOLD_INDEX["subfamily"]]),
        "bold_genus": normalize_space(bold_row[BOLD_INDEX["genus"]]),
        "bold_species": normalize_space(bold_row[BOLD_INDEX["species"]]),
        "latitude": latitude,
        "longitude": longitude,
        "bold_country_ocean": normalize_space(bold_row[BOLD_INDEX["country_ocean"]]),
        "bold_country_iso": normalize_space(bold_row[BOLD_INDEX["country_iso"]]),
        "bold_nuc_basecount": normalize_space(bold_row[BOLD_INDEX["nuc_basecount"]]),
    }


def write_filtered_bold(
    bold_path: Path,
    taxo_by_species: dict[str, dict[str, str]],
    output_path: Path,
) -> tuple[int, int]:
    matched_rows = 0
    scanned_rows = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, delimiter="\t")
        writer.writeheader()

        for row in iter_bold_rows(bold_path):
            scanned_rows += 1
            if len(row) <= BOLD_INDEX["nuc_basecount"]:
                continue

            species = normalize_space(row[BOLD_INDEX["species"]])
            if not species:
                continue

            taxo_row = taxo_by_species.get(species)
            if taxo_row is None:
                continue

            writer.writerow(build_output_row(taxo_row, row))
            matched_rows += 1

    return scanned_rows, matched_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Filter BOLD distribution rows by taxonomy species and output selected "
            "taxonomy and BOLD fields."
        )
    )
    parser.add_argument(
        "--bold",
        type=Path,
        default=DEFAULT_BOLD,
        help=f"BOLD input path relative to project root (default: {DEFAULT_BOLD})",
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=DEFAULT_TAXO,
        help=f"Taxonomy input path relative to project root (default: {DEFAULT_TAXO})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output TSV path relative to project root (default: {DEFAULT_OUTPUT})",
    )
    return parser


def main() -> int:
    set_csv_field_limit()
    args = build_parser().parse_args()

    bold_path = resolve_from_root(args.bold)
    taxo_path = resolve_from_root(args.taxonomy)
    output_path = resolve_from_root(args.output)

    taxo_by_species = load_taxonomy(taxo_path)
    scanned_rows, matched_rows = write_filtered_bold(
        bold_path=bold_path,
        taxo_by_species=taxo_by_species,
        output_path=output_path,
    )

    print(f"BOLD input: {Path(args.bold)}")
    print(f"Taxonomy input: {Path(args.taxonomy)}")
    print(f"Taxonomy species loaded: {len(taxo_by_species)}")
    print(f"BOLD rows scanned: {scanned_rows}")
    print(f"BOLD rows matched: {matched_rows}")
    print(f"Output written to: {Path(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
