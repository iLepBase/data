#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NCBI_UNZIP_DIR = Path("data/raw/genome/NCBI_unzip")
DEFAULT_JSON_DIR = Path("data/clean/json")

CHROMOSOME_FIELD_MAP = OrderedDict(
    [
        ("chrName", "Chromosome"),
        ("gcPercent", "GC content (%)"),
        ("genbankAccession", "GenBank"),
        ("length", "Size (bp)"),
        ("refseqAccession", "RefSeq"),
        ("unlocalizedCount", "unlocalizedCount"),
    ]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write chromosome records from sequence_report.jsonl into species genome JSON."
    )
    parser.add_argument(
        "--ncbi-unzip-dir",
        default=str(DEFAULT_NCBI_UNZIP_DIR),
        help="NCBI unzip directory, relative to the iLepbase root by default.",
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
    return parser.parse_args()


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def coerce_numeric(field: str, value):
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    if field in {"gcCount", "length", "unlocalizedCount"}:
        return int(cleaned)
    if field == "gcPercent":
        return float(cleaned)
    return cleaned


def load_chromosome_map(ncbi_unzip_dir: Path) -> dict[str, list[OrderedDict[str, object]]]:
    chromosome_map: dict[str, list[OrderedDict[str, object]]] = {}
    for report_path in sorted(ncbi_unzip_dir.glob("*/sequence_report.jsonl")):
        accession = report_path.parent.name
        chromosomes: list[OrderedDict[str, object]] = []
        with report_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                if clean_value(payload.get("assignedMoleculeLocationType")) != "Chromosome":
                    continue
                chromosome = OrderedDict()
                for source_field, output_field in CHROMOSOME_FIELD_MAP.items():
                    chromosome[output_field] = coerce_numeric(
                        source_field, payload.get(source_field)
                    )
                if clean_value(chromosome.get("Chromosome")) == "Un":
                    continue
                chromosomes.append(chromosome)
        if chromosomes:
            chromosome_map[accession] = chromosomes
    return chromosome_map


def update_species_jsons(
    json_dir: Path,
    chromosome_map: dict[str, list[OrderedDict[str, object]]],
    indent: int,
) -> tuple[int, int, int]:
    json_files_updated = 0
    genome_records_with_chromosomes = 0
    species_with_chromosomes = 0

    for json_path in sorted(json_dir.glob("*.json")):
        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)

        species_matched = False
        for genome_record in document.get("genome") or []:
            if "chromosomes" in genome_record:
                del genome_record["chromosomes"]
            accession = clean_value(genome_record.get("assembly_accession"))
            if accession and accession in chromosome_map:
                genome_record["chromosomes"] = chromosome_map[accession]
                genome_records_with_chromosomes += 1
                species_matched = True

        if species_matched:
            species_with_chromosomes += 1

        with json_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
        json_files_updated += 1

    return json_files_updated, genome_records_with_chromosomes, species_with_chromosomes


def main() -> int:
    args = parse_args()
    ncbi_unzip_dir = resolve_repo_path(args.ncbi_unzip_dir)
    json_dir = resolve_repo_path(args.json_dir)

    chromosome_map = load_chromosome_map(ncbi_unzip_dir)
    json_files_updated, genome_records_with_chromosomes, species_with_chromosomes = update_species_jsons(
        json_dir,
        chromosome_map,
        args.indent,
    )

    print(f"root={ROOT}")
    print(f"ncbi_unzip_dir={ncbi_unzip_dir}")
    print(f"json_dir={json_dir}")
    print(f"sequence_reports_with_chromosomes={len(chromosome_map)}")
    print(f"json_files_updated={json_files_updated}")
    print(f"genome_records_with_chromosomes={genome_records_with_chromosomes}")
    print(f"species_with_chromosomes={species_with_chromosomes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
