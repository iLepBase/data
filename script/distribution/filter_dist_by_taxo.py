#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TAXO_TSV = Path("data/clean/taxo/lep_taxo_20260415.tsv")
DEFAULT_DIST_TSV = Path("data/raw/distribution/lepidoptera_distribution.tsv")

TAXO_OUTPUT_FIELDS = [
    "family",
    "subfamily",
    "genus",
    "species",
]
PROGRESS_BAR_WIDTH = 30


def default_output_path() -> Path:
    return Path(f"data/clean/distribution/lep_dist_clean_{date.today():%Y%m%d}.tsv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Filter lepidoptera distribution rows by species listed in lep_taxo TSV, "
            "matching taxo 'origin Species' to distribution 'species'."
        )
    )
    parser.add_argument(
        "--taxo-tsv",
        default=str(DEFAULT_TAXO_TSV),
        help="Taxonomy TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--dist-tsv",
        default=str(DEFAULT_DIST_TSV),
        help="Distribution TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--output",
        default=str(default_output_path()),
        help="Output TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1_000_000,
        help="Print progress every N distribution rows.",
    )
    return parser.parse_args()


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


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


def split_tsv_line(raw_line: str) -> list[str]:
    return raw_line.rstrip("\r\n").split("\t")


def rename_dist_field(field: str) -> str:
    if field == "family":
        return "origin_family"
    if field == "genus":
        return "origin_genus"
    if field == "species":
        return "origin_species"
    return field


def clamp_progress_ratio(processed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, processed / total))


def build_progress_bar(processed: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    ratio = clamp_progress_ratio(processed, total)
    filled = min(width, int(ratio * width))
    return f"[{'#' * filled}{'.' * (width - filled)}]"


def format_progress_percent(processed: int, total: int) -> str:
    return f"{clamp_progress_ratio(processed, total) * 100:5.1f}%"


def load_taxo_species_map(
    taxo_tsv: Path,
) -> tuple[dict[str, OrderedDict[str, str]], dict[str, int]]:
    species_map: dict[str, OrderedDict[str, str]] = {}
    stats = {
        "taxo_rows_total": 0,
        "taxo_rows_without_origin_species": 0,
        "taxo_species_loaded": 0,
    }

    with taxo_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        header = split_tsv_line(handle.readline())
        index_map = {name: idx for idx, name in enumerate(header)}
        required = {"origin Species", "family", "subfamliy", "genuis", "species"}
        if not required.issubset(index_map):
            missing = sorted(required - set(index_map))
            raise ValueError(f"Missing required columns in {taxo_tsv}: {', '.join(missing)}")

        for raw_line in handle:
            stats["taxo_rows_total"] += 1
            row = split_tsv_line(raw_line)
            origin_species = normalize_species_name(
                row[index_map["origin Species"]] if index_map["origin Species"] < len(row) else ""
            )
            if origin_species is None:
                stats["taxo_rows_without_origin_species"] += 1
                continue

            if origin_species in species_map:
                raise ValueError(f"Duplicate origin Species found in {taxo_tsv}: {origin_species}")

            species_map[origin_species] = OrderedDict(
                [
                    ("family", clean_value(row[index_map["family"]] if index_map["family"] < len(row) else "") or ""),
                    (
                        "subfamily",
                        clean_value(row[index_map["subfamliy"]] if index_map["subfamliy"] < len(row) else "") or "",
                    ),
                    ("genus", clean_value(row[index_map["genuis"]] if index_map["genuis"] < len(row) else "") or ""),
                    (
                        "species",
                        normalize_species_name(row[index_map["species"]] if index_map["species"] < len(row) else "")
                        or origin_species,
                    ),
                ]
            )
            stats["taxo_species_loaded"] += 1

    return species_map, stats


def filter_distribution(
    taxo_species_map: dict[str, OrderedDict[str, str]],
    dist_tsv: Path,
    output_tsv: Path,
    progress_every: int,
) -> dict[str, int]:
    stats = {
        "dist_rows_total": 0,
        "dist_rows_without_species": 0,
        "dist_rows_matched": 0,
        "dist_rows_written": 0,
    }

    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    temp_output_tsv = output_tsv.with_name(f"{output_tsv.name}.tmp")
    total_bytes = dist_tsv.stat().st_size

    with dist_tsv.open("r", encoding="utf-8-sig", newline="") as input_handle, temp_output_tsv.open(
        "w", encoding="utf-8", newline=""
    ) as output_handle:
        header_line = input_handle.readline()
        if not header_line:
            output_handle.write("\t".join(TAXO_OUTPUT_FIELDS) + "\n")
            temp_output_tsv.replace(output_tsv)
            return stats

        dist_fields = split_tsv_line(header_line)
        index_map = {name: idx for idx, name in enumerate(dist_fields)}
        required = {"species"}
        if not required.issubset(index_map):
            missing = sorted(required - set(index_map))
            raise ValueError(f"Missing required columns in {dist_tsv}: {', '.join(missing)}")

        output_fields = TAXO_OUTPUT_FIELDS + [rename_dist_field(field) for field in dist_fields]
        output_handle.write("\t".join(output_fields) + "\n")

        species_idx = index_map["species"]

        while True:
            raw_line = input_handle.readline()
            if not raw_line:
                break

            stats["dist_rows_total"] += 1
            if progress_every > 0 and stats["dist_rows_total"] % progress_every == 0:
                processed_bytes = input_handle.tell()
                print(
                    f"[progress] {format_progress_percent(processed_bytes, total_bytes)} "
                    f"{build_progress_bar(processed_bytes, total_bytes)} "
                    f"dist_rows_total={stats['dist_rows_total']:,} "
                    f"dist_rows_matched={stats['dist_rows_matched']:,} "
                    f"dist_rows_written={stats['dist_rows_written']:,}",
                    flush=True,
                )

            row = split_tsv_line(raw_line)
            dist_species = normalize_species_name(row[species_idx] if species_idx < len(row) else "")
            if dist_species is None:
                stats["dist_rows_without_species"] += 1
                continue

            taxo_row = taxo_species_map.get(dist_species)
            if taxo_row is None:
                continue

            stats["dist_rows_matched"] += 1
            if len(row) < len(dist_fields):
                row.extend([""] * (len(dist_fields) - len(row)))

            output_row = [taxo_row[field] for field in TAXO_OUTPUT_FIELDS]
            output_row.extend(row[: len(dist_fields)])
            output_handle.write("\t".join(output_row) + "\n")
            stats["dist_rows_written"] += 1

    temp_output_tsv.replace(output_tsv)
    return stats


def main() -> int:
    args = parse_args()
    taxo_tsv = resolve_repo_path(args.taxo_tsv)
    dist_tsv = resolve_repo_path(args.dist_tsv)
    output_tsv = resolve_repo_path(args.output)

    taxo_species_map, taxo_stats = load_taxo_species_map(taxo_tsv)
    dist_stats = filter_distribution(
        taxo_species_map=taxo_species_map,
        dist_tsv=dist_tsv,
        output_tsv=output_tsv,
        progress_every=args.progress_every,
    )

    print(
        "[done] "
        f"taxo_rows_total={taxo_stats['taxo_rows_total']:,} "
        f"taxo_species_loaded={taxo_stats['taxo_species_loaded']:,} "
        f"dist_rows_total={dist_stats['dist_rows_total']:,} "
        f"dist_rows_matched={dist_stats['dist_rows_matched']:,} "
        f"dist_rows_written={dist_stats['dist_rows_written']:,} "
        f"output={display_path(output_tsv)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.exit(141)
