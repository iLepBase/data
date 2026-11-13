#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_TSV = Path("data/clean/genome/genome_summary_db.tsv")
DEFAULT_OUTPUT_TSV = Path("data/raw/gene_tree/best_busco_genome_per_species.tsv")

BUSCO_RE = re.compile(
    r"C:(?P<complete>[\d.]+)%"
    r"\[S:(?P<single_copy>[\d.]+)%,D:(?P<duplicated>[\d.]+)%\],"
    r"F:(?P<fragmented>[\d.]+)%,"
    r"M:(?P<missing>[\d.]+)%,"
    r"n:(?P<total>\d+),"
    r"E:(?P<error>[\d.]+)%"
)

ASSEMBLY_LEVEL_RANK = {
    "chromosome": 3,
    "complete genome": 2,
    "scaffold": 1,
    "contig": 0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select the best BUSCO-scoring genome per species from genome_summary_db.tsv."
        )
    )
    parser.add_argument(
        "--input-tsv",
        default=str(DEFAULT_INPUT_TSV),
        help="Genome summary TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--output-tsv",
        default=str(DEFAULT_OUTPUT_TSV),
        help="Output TSV path, relative to the iLepbase root by default.",
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


def parse_float(value: str | None) -> float | None:
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    cleaned = cleaned.replace("%", "")
    if cleaned.endswith(" Mbp"):
        cleaned = cleaned[:-4]
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_busco_notation(busco_notation: str | None) -> dict[str, float | int | None]:
    cleaned = clean_value(busco_notation)
    if cleaned is None:
        return {
            "complete_percent": None,
            "single_copy_percent": None,
            "duplicated_percent": None,
            "fragmented_percent": None,
            "missing_percent": None,
            "error_percent": None,
            "total_busco_groups": None,
        }

    match = BUSCO_RE.search(cleaned)
    if not match:
        raise ValueError(f"Unrecognized BUSCO notation: {cleaned}")

    return {
        "complete_percent": float(match.group("complete")),
        "single_copy_percent": float(match.group("single_copy")),
        "duplicated_percent": float(match.group("duplicated")),
        "fragmented_percent": float(match.group("fragmented")),
        "missing_percent": float(match.group("missing")),
        "error_percent": float(match.group("error")),
        "total_busco_groups": int(match.group("total")),
    }


def assembly_level_rank(value: str | None) -> int:
    cleaned = clean_value(value)
    if cleaned is None:
        return -1
    return ASSEMBLY_LEVEL_RANK.get(cleaned.lower(), -1)


def release_date_key(value: str | None) -> str:
    cleaned = clean_value(value)
    return cleaned or ""


def comparison_key(row: dict[str, str], busco: dict[str, float | int | None]) -> tuple[object, ...]:
    complete = busco["complete_percent"]
    single_copy = busco["single_copy_percent"]
    missing = busco["missing_percent"]
    fragmented = busco["fragmented_percent"]
    duplicated = busco["duplicated_percent"]
    error = busco["error_percent"]

    return (
        complete if complete is not None else -1.0,
        single_copy if single_copy is not None else -1.0,
        -(missing if missing is not None else 10**9),
        -(fragmented if fragmented is not None else 10**9),
        -(duplicated if duplicated is not None else 10**9),
        -(error if error is not None else 10**9),
        assembly_level_rank(row.get("assembly_level")),
        parse_float(row.get("busco_scaffold_n50")) or 0.0,
        parse_float(row.get("busco_contigs_n50")) or 0.0,
        -(parse_float(row.get("busco_percent_gaps")) or 10**9),
        -(parse_int(row.get("busco_number_of_scaffolds")) or 10**9),
        -(parse_int(row.get("busco_number_of_contigs")) or 10**9),
        release_date_key(row.get("assembly_release_date")),
        clean_value(row.get("assembly_accession")) or "",
    )


def load_best_rows(
    input_tsv: Path,
) -> tuple[list[OrderedDict[str, str]], dict[str, int]]:
    best_by_species: OrderedDict[str, tuple[dict[str, str], dict[str, float | int | None]]] = OrderedDict()
    stats = {
        "rows_total": 0,
        "species_total": 0,
        "species_with_multiple_genomes": 0,
        "rows_without_species": 0,
    }

    species_counts: OrderedDict[str, int] = OrderedDict()

    with input_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Missing header in TSV: {input_tsv}")

        for raw_row in reader:
            stats["rows_total"] += 1
            row = {key: value for key, value in raw_row.items()}
            species = clean_value(row.get("species"))
            if species is None:
                stats["rows_without_species"] += 1
                continue

            species_counts[species] = species_counts.get(species, 0) + 1
            busco = parse_busco_notation(row.get("busco_notation"))

            existing = best_by_species.get(species)
            if existing is None:
                best_by_species[species] = (row, busco)
                continue

            current_row, current_busco = existing
            if comparison_key(row, busco) > comparison_key(current_row, current_busco):
                best_by_species[species] = (row, busco)

    output_rows: list[OrderedDict[str, str]] = []
    for species in sorted(best_by_species):
        row, busco = best_by_species[species]
        output_row = OrderedDict(row)
        output_row["selected_by"] = "best_busco"
        output_row["busco_complete_percent"] = (
            "" if busco["complete_percent"] is None else str(busco["complete_percent"])
        )
        output_row["busco_single_copy_percent"] = (
            "" if busco["single_copy_percent"] is None else str(busco["single_copy_percent"])
        )
        output_row["busco_duplicated_percent"] = (
            "" if busco["duplicated_percent"] is None else str(busco["duplicated_percent"])
        )
        output_row["busco_fragmented_percent"] = (
            "" if busco["fragmented_percent"] is None else str(busco["fragmented_percent"])
        )
        output_row["busco_missing_percent"] = (
            "" if busco["missing_percent"] is None else str(busco["missing_percent"])
        )
        output_row["busco_error_percent"] = (
            "" if busco["error_percent"] is None else str(busco["error_percent"])
        )
        output_row["busco_total_groups"] = (
            "" if busco["total_busco_groups"] is None else str(busco["total_busco_groups"])
        )
        output_rows.append(output_row)

    stats["species_total"] = len(best_by_species)
    stats["species_with_multiple_genomes"] = sum(1 for count in species_counts.values() if count > 1)
    return output_rows, stats


def write_output_tsv(output_tsv: Path, rows: list[OrderedDict[str, str]]) -> None:
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("No rows selected for output.")

    fieldnames = list(rows[0].keys())
    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    input_tsv = resolve_repo_path(args.input_tsv)
    output_tsv = resolve_repo_path(args.output_tsv)

    output_rows, stats = load_best_rows(input_tsv)
    write_output_tsv(output_tsv, output_rows)

    print(f"root={ROOT}")
    print(f"input_tsv={input_tsv}")
    print(f"output_tsv={output_tsv}")
    print(f"rows_total={stats['rows_total']}")
    print(f"species_total={stats['species_total']}")
    print(f"species_with_multiple_genomes={stats['species_with_multiple_genomes']}")
    print(f"rows_without_species={stats['rows_without_species']}")
    print(f"selected_rows={len(output_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
