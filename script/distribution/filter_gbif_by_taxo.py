#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TAXO_TSV = Path("data/clean/taxo/lep_taxo_20260414.tsv")
DEFAULT_GBIF_TSV = Path("data/raw/distribution/distribution_gbif.csv")
DEFAULT_OUTPUT = Path("data/raw/distribution/dist_gbif_clean.tsv")

OUTPUT_FIELDS = [
    "taxo_family",
    "taxo_subfamily",
    "taxo_genus",
    "taxo_species",
    "gbifID",
    "gbif_family",
    "gbif_genus",
    "gbif_species",
    "countryCode",
    "latitude",
    "longitude",
    "source",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter GBIF distribution rows by species listed in lep_taxo TSV."
    )
    parser.add_argument(
        "--taxo-tsv",
        default=str(DEFAULT_TAXO_TSV),
        help="Taxonomy TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--gbif-tsv",
        default=str(DEFAULT_GBIF_TSV),
        help="Raw GBIF TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1_000_000,
        help="Print progress every N GBIF rows.",
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


def normalize_species_name(value: str | None) -> str | None:
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    return " ".join(cleaned.split())


def load_taxo_species_map(taxo_tsv: Path) -> tuple[dict[str, OrderedDict[str, str]], dict[str, int]]:
    species_map: dict[str, OrderedDict[str, str]] = {}
    stats = {
        "taxo_rows_total": 0,
        "taxo_rows_without_origin_species": 0,
        "taxo_duplicate_origin_species": 0,
    }

    with taxo_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        index_map = {name: idx for idx, name in enumerate(header)}
        required = {"origin Species", "family", "subfamliy", "genuis", "species"}
        if not required.issubset(index_map):
            missing = sorted(required - set(index_map))
            raise ValueError(f"Missing required columns in {taxo_tsv}: {', '.join(missing)}")

        for raw_line in handle:
            stats["taxo_rows_total"] += 1
            row = raw_line.rstrip("\n").split("\t")
            origin_species = normalize_species_name(row[index_map["origin Species"]] if index_map["origin Species"] < len(row) else "")
            if origin_species is None:
                stats["taxo_rows_without_origin_species"] += 1
                continue

            taxo_record = OrderedDict(
                [
                    ("taxo_family", clean_value(row[index_map["family"]] if index_map["family"] < len(row) else "") or ""),
                    ("taxo_subfamily", clean_value(row[index_map["subfamliy"]] if index_map["subfamliy"] < len(row) else "") or ""),
                    ("taxo_genus", clean_value(row[index_map["genuis"]] if index_map["genuis"] < len(row) else "") or ""),
                    ("taxo_species", normalize_species_name(row[index_map["species"]] if index_map["species"] < len(row) else "") or origin_species),
                ]
            )

            previous = species_map.get(origin_species)
            if previous is None:
                species_map[origin_species] = taxo_record
            else:
                stats["taxo_duplicate_origin_species"] += 1
                if previous != taxo_record:
                    raise ValueError(
                        f"Conflicting taxonomy rows for origin Species {origin_species!r}"
                    )

    return species_map, stats


def filter_gbif(
    taxo_species_map: dict[str, OrderedDict[str, str]],
    gbif_tsv: Path,
    output_tsv: Path,
    progress_every: int,
) -> dict[str, int]:
    stats = {
        "gbif_rows_total": 0,
        "gbif_rows_without_species": 0,
        "gbif_rows_matched": 0,
        "gbif_rows_written": 0,
    }

    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    temp_output_tsv = output_tsv.with_name(f"{output_tsv.name}.tmp")

    with gbif_tsv.open("r", encoding="utf-8-sig", newline="") as input_handle, temp_output_tsv.open(
        "w", encoding="utf-8", newline=""
    ) as output_handle:
        output_handle.write("\t".join(OUTPUT_FIELDS) + "\n")

        first_line = input_handle.readline()
        if not first_line:
            temp_output_tsv.replace(output_tsv)
            return stats

        first_row = first_line.rstrip("\n").split("\t")
        raw_header_index_map = {name: idx for idx, name in enumerate(first_row)}
        raw_required = {
            "gbifID",
            "family",
            "genus",
            "species",
            "countryCode",
            "decimalLatitude",
            "decimalLongitude",
        }
        is_raw_gbif = raw_required.issubset(raw_header_index_map)

        if is_raw_gbif:
            def iter_rows():
                for line in input_handle:
                    yield line.rstrip("\n").split("\t")

            def build_output_row(row: list[str], gbif_species: str) -> OrderedDict[str, str]:
                output_row = OrderedDict(taxo_species_map[gbif_species])
                output_row.update(
                    OrderedDict(
                        [
                            ("gbifID", clean_value(row[raw_header_index_map["gbifID"]] if raw_header_index_map["gbifID"] < len(row) else "") or ""),
                            ("gbif_family", clean_value(row[raw_header_index_map["family"]] if raw_header_index_map["family"] < len(row) else "") or ""),
                            ("gbif_genus", clean_value(row[raw_header_index_map["genus"]] if raw_header_index_map["genus"] < len(row) else "") or ""),
                            ("gbif_species", gbif_species),
                            ("countryCode", clean_value(row[raw_header_index_map["countryCode"]] if raw_header_index_map["countryCode"] < len(row) else "") or ""),
                            ("latitude", clean_value(row[raw_header_index_map["decimalLatitude"]] if raw_header_index_map["decimalLatitude"] < len(row) else "") or ""),
                            ("longitude", clean_value(row[raw_header_index_map["decimalLongitude"]] if raw_header_index_map["decimalLongitude"] < len(row) else "") or ""),
                            ("source", "GBIF"),
                        ]
                    )
                )
                return output_row

            def extract_species(row: list[str]) -> str | None:
                return normalize_species_name(
                    row[raw_header_index_map["species"]] if raw_header_index_map["species"] < len(row) else ""
                )
        else:
            if len(first_row) < 8:
                raise ValueError(
                    f"Unsupported GBIF input format in {gbif_tsv}: expected raw header row or 8-column extracted rows."
                )

            def iter_rows():
                yield first_row
                for line in input_handle:
                    yield line.rstrip("\n").split("\t")

            def build_output_row(row: list[str], gbif_species: str) -> OrderedDict[str, str]:
                output_row = OrderedDict(taxo_species_map[gbif_species])
                output_row.update(
                    OrderedDict(
                        [
                            ("gbifID", clean_value(row[0] if 0 < len(row) else "") or ""),
                            ("gbif_family", clean_value(row[1] if 1 < len(row) else "") or ""),
                            ("gbif_genus", clean_value(row[2] if 2 < len(row) else "") or ""),
                            ("gbif_species", gbif_species),
                            ("countryCode", clean_value(row[4] if 4 < len(row) else "") or ""),
                            ("latitude", clean_value(row[5] if 5 < len(row) else "") or ""),
                            ("longitude", clean_value(row[6] if 6 < len(row) else "") or ""),
                            ("source", clean_value(row[7] if 7 < len(row) else "") or "GBIF"),
                        ]
                    )
                )
                return output_row

            def extract_species(row: list[str]) -> str | None:
                return normalize_species_name(row[3] if 3 < len(row) else "")

        for row in iter_rows():
            stats["gbif_rows_total"] += 1
            if progress_every > 0 and stats["gbif_rows_total"] % progress_every == 0:
                print(
                    f"[progress] gbif_rows_total={stats['gbif_rows_total']:,} "
                    f"gbif_rows_matched={stats['gbif_rows_matched']:,} "
                    f"gbif_rows_written={stats['gbif_rows_written']:,}",
                    flush=True,
                )

            gbif_species = extract_species(row)
            if gbif_species is None:
                stats["gbif_rows_without_species"] += 1
                continue

            if gbif_species not in taxo_species_map:
                continue

            stats["gbif_rows_matched"] += 1
            output_row = build_output_row(row, gbif_species)
            output_handle.write("\t".join(output_row[field] for field in OUTPUT_FIELDS) + "\n")
            stats["gbif_rows_written"] += 1

    temp_output_tsv.replace(output_tsv)
    return stats


def main() -> int:
    args = parse_args()
    taxo_tsv = resolve_repo_path(args.taxo_tsv)
    gbif_tsv = resolve_repo_path(args.gbif_tsv)
    output_tsv = resolve_repo_path(args.output)

    taxo_species_map, taxo_stats = load_taxo_species_map(taxo_tsv)
    gbif_stats = filter_gbif(
        taxo_species_map=taxo_species_map,
        gbif_tsv=gbif_tsv,
        output_tsv=output_tsv,
        progress_every=args.progress_every,
    )

    print(f"root={ROOT}")
    print(f"taxo_tsv={taxo_tsv}")
    print(f"gbif_tsv={gbif_tsv}")
    print(f"output_tsv={output_tsv}")
    print(f"taxo_species_total={len(taxo_species_map)}")
    for key, value in taxo_stats.items():
        print(f"{key}={value}")
    for key, value in gbif_stats.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
