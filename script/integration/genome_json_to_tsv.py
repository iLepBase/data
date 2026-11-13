#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_OUTPUT = Path("data/clean/genome/genome_summary_db.tsv")
DEFAULT_SPECIES_TSV = Path("genome_summary_db.tsv")


FIELDNAMES = [
    "family",
    "subfamily",
    "genus",
    "species",
    "genome_source",
    "assembly_accession",
    "assembly_level",
    "assembly_release_date",
    "number_of_scaffolds",
    "source_link",
    "download_link",
    "genome_size_bytes",
    "annotation_source",
    "busco_notation",
    "busco_number_of_scaffolds",
    "busco_number_of_contigs",
    "busco_total_length",
    "busco_percent_gaps",
    "busco_scaffold_n50",
    "busco_contigs_n50",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read data/json and export one genome record per TSV row."
    )
    parser.add_argument(
        "--json-dir",
        default=str(DEFAULT_JSON_DIR),
        help="JSON directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="TSV output path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--species-tsv",
        default=str(DEFAULT_SPECIES_TSV),
        help=(
            "Optional TSV used to limit which species JSON files are read. "
            "Reads the 'species' column and maps values like 'Abraxas grossulariata' "
            "to 'abraxas_grossulariata.json'."
        ),
    )
    return parser.parse_args()


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def flatten_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(str(item) for item in value if item is not None)
    return str(value)


def species_to_json_name(species: str) -> str:
    normalized = unicodedata.normalize("NFKD", species.strip())
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").lower()
    return f"{slug}.json"


def iter_target_json_paths(json_dir: Path, species_tsv: Path | None) -> list[Path]:
    if species_tsv is None:
        return sorted(json_dir.glob("*.json"))

    with species_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if "species" not in (reader.fieldnames or []):
            raise ValueError(f"'species' column not found in {species_tsv}")

        species_names = {
            row["species"].strip()
            for row in reader
            if row.get("species") and row["species"].strip()
        }

    return [
        json_dir / species_to_json_name(species)
        for species in sorted(species_names)
        if (json_dir / species_to_json_name(species)).exists()
    ]


def main() -> int:
    args = parse_args()
    json_dir = resolve_repo_path(args.json_dir)
    output_path = resolve_repo_path(args.output)
    species_tsv = resolve_repo_path(args.species_tsv) if args.species_tsv else None

    rows: list[dict[str, str]] = []
    json_paths = iter_target_json_paths(json_dir, species_tsv)
    for json_path in json_paths:
        document = json.loads(json_path.read_text(encoding="utf-8"))
        taxonomy = document.get("taxonomy") or {}
        genomes = document.get("genome") or []
        if not genomes:
            continue

        for genome in genomes:
            if genome.get("genome_size_bytes") in (None, ""):
                continue
            annotation = genome.get("annotation") or []
            if isinstance(annotation, dict):
                annotation = [annotation]
            busco = genome.get("busco") or {}
            busco_percentages = busco.get("percentages") or {}
            busco_assembly_stats = busco.get("assembly_statistics") or {}

            rows.append(
                {
                    "family": flatten_value(taxonomy.get("family")),
                    "subfamily": flatten_value(taxonomy.get("subfamily")),
                    "genus": flatten_value(taxonomy.get("genus")),
                    "species": flatten_value(document.get("species")),
                    "genome_source": flatten_value(genome.get("source")),
                    "assembly_accession": flatten_value(genome.get("assembly_accession")),
                    "assembly_level": flatten_value(genome.get("assembly_level")),
                    "assembly_release_date": flatten_value(genome.get("assembly_release_date")),
                    "number_of_scaffolds": flatten_value(genome.get("number_of_scaffolds")),
                    "source_link": flatten_value(genome.get("source_link")),
                    "download_link": flatten_value(genome.get("download_link")),
                    "genome_size_bytes": flatten_value(genome.get("genome_size_bytes")),
                    "annotation_source": flatten_value(
                        [item.get("source") for item in annotation if item.get("source")]
                    ),
                    "busco_notation": flatten_value(busco.get("notation")),
                    "busco_number_of_scaffolds": flatten_value(busco_assembly_stats.get("number_of_scaffolds")),
                    "busco_number_of_contigs": flatten_value(busco_assembly_stats.get("number_of_contigs")),
                    "busco_total_length": flatten_value(busco_assembly_stats.get("total_length")),
                    "busco_percent_gaps": flatten_value(busco_assembly_stats.get("percent_gaps")),
                    "busco_scaffold_n50": flatten_value(busco_assembly_stats.get("scaffold_n50")),
                    "busco_contigs_n50": flatten_value(busco_assembly_stats.get("contigs_n50")),
                }
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"root={ROOT}")
    print(f"json_dir={json_dir}")
    print(f"species_tsv={species_tsv}")
    print(f"json_files_read={len(json_paths)}")
    print(f"output={output_path}")
    print(f"rows_written={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
