#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUSCO_DIR = Path("data/raw/genome/busco")
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_GENOME_SUMMARY_TSV = Path("data/clean/genome/genome_summary_db.tsv")
SUMMARY_GLOB_PATTERNS = (
    "results_*/busco/*/*.short_summary.txt",
    "busco/*/*.short_summary.txt",
    "*/*.short_summary.txt",
)

SUMMARY_RE = re.compile(
    r"C:(?P<complete>[\d.]+)%"
    r"\[S:(?P<single_copy>[\d.]+)%,D:(?P<duplicated>[\d.]+)%\],"
    r"F:(?P<fragmented>[\d.]+)%,"
    r"M:(?P<missing>[\d.]+)%,"
    r"n:(?P<total>\d+),"
    r"E:(?P<error>[\d.]+)%"
)

COUNT_PATTERNS = (
    (
        re.compile(
            r"^(?P<count>\d+)\s+Complete BUSCOs \(C\)(?:\s+\(of which (?P<internal>\d+) contain internal stop codons\))?$"
        ),
        "complete_buscos",
    ),
    (
        re.compile(r"^(?P<count>\d+)\s+Complete and single-copy BUSCOs \(S\)$"),
        "complete_single_copy_buscos",
    ),
    (
        re.compile(r"^(?P<count>\d+)\s+Complete and duplicated BUSCOs \(D\)$"),
        "complete_duplicated_buscos",
    ),
    (
        re.compile(r"^(?P<count>\d+)\s+Fragmented BUSCOs \(F\)$"),
        "fragmented_buscos",
    ),
    (
        re.compile(r"^(?P<count>\d+)\s+Missing BUSCOs \(M\)$"),
        "missing_buscos",
    ),
    (
        re.compile(r"^(?P<count>\d+)\s+Total BUSCO groups searched$"),
        "total_busco_groups",
    ),
)

ASSEMBLY_STAT_KEYS = {
    "Number of scaffolds": "number_of_scaffolds",
    "Number of contigs": "number_of_contigs",
    "Total length": "total_length",
    "Percent gaps": "percent_gaps",
    "Scaffold N50": "scaffold_n50",
    "Contigs N50": "contigs_n50",
}
BUSCO_TSV_FIELD_MAP = {
    "busco_notation": ("notation",),
    "busco_number_of_scaffolds": ("assembly_statistics", "number_of_scaffolds"),
    "busco_number_of_contigs": ("assembly_statistics", "number_of_contigs"),
    "busco_total_length": ("assembly_statistics", "total_length"),
    "busco_percent_gaps": ("assembly_statistics", "percent_gaps"),
    "busco_scaffold_n50": ("assembly_statistics", "scaffold_n50"),
    "busco_contigs_n50": ("assembly_statistics", "contigs_n50"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write BUSCO short_summary results into species genome JSON records."
    )
    parser.add_argument(
        "--busco-dir",
        default=str(DEFAULT_BUSCO_DIR),
        help="BUSCO results directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--json-dir",
        default=str(DEFAULT_JSON_DIR),
        help="Species JSON directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--genome-summary-tsv",
        default=str(DEFAULT_GENOME_SUMMARY_TSV),
        help="Genome summary table used to identify species JSON files with genome records.",
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


def clean_value(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def parse_numeric_or_text(value: str) -> int | float | str:
    text = value.strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    if re.fullmatch(r"\d+\.\d+", text):
        return float(text)
    return text


def parse_busco_summary(summary_path: Path) -> OrderedDict[str, object]:
    notation = None
    percentages: OrderedDict[str, object] = OrderedDict()
    assembly_stats: OrderedDict[str, object] = OrderedDict()
    in_assembly_stats = False

    for raw_line in summary_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        summary_match = SUMMARY_RE.search(line)
        if summary_match:
            notation = line
            percentages = OrderedDict(
                [
                    ("complete_percent", float(summary_match.group("complete"))),
                    ("single_copy_percent", float(summary_match.group("single_copy"))),
                    ("duplicated_percent", float(summary_match.group("duplicated"))),
                    ("fragmented_percent", float(summary_match.group("fragmented"))),
                    ("missing_percent", float(summary_match.group("missing"))),
                    ("error_percent", float(summary_match.group("error"))),
                    ("total_busco_groups", int(summary_match.group("total"))),
                ]
            )
            continue

        if line == "Assembly Statistics:":
            in_assembly_stats = True
            continue

        if line == "Dependencies and versions:":
            in_assembly_stats = False
            continue

        matched_count = False
        for pattern, key in COUNT_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            matched_count = True
            break
        if matched_count:
            continue

        if in_assembly_stats:
            parts = [part for part in raw_line.split("\t") if part.strip()]
            if len(parts) >= 2:
                value = clean_value(parts[0])
                label = clean_value(parts[1])
                if value and label:
                    assembly_stats[ASSEMBLY_STAT_KEYS.get(label, label.lower().replace(" ", "_"))] = parse_numeric_or_text(value)

    busco_payload = OrderedDict(
        [
            ("notation", notation),
            ("percentages", percentages),
            ("assembly_statistics", assembly_stats),
        ]
    )
    return busco_payload


def get_nested_value(payload: OrderedDict[str, object], *keys: str) -> object | None:
    current: object | None = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def iter_summary_paths(busco_dir: Path) -> list[Path]:
    summary_paths: list[Path] = []
    seen: set[Path] = set()

    for pattern in SUMMARY_GLOB_PATTERNS:
        for summary_path in sorted(busco_dir.glob(pattern)):
            if summary_path in seen:
                continue
            seen.add(summary_path)
            summary_paths.append(summary_path)

    if summary_paths:
        return summary_paths
    return sorted(busco_dir.rglob("*.short_summary.txt"))


def load_busco_results(busco_dir: Path) -> dict[str, OrderedDict[str, object]]:
    if not busco_dir.exists():
        raise FileNotFoundError(f"BUSCO directory does not exist: {busco_dir}")

    busco_results: dict[str, OrderedDict[str, object]] = {}
    for summary_path in iter_summary_paths(busco_dir):
        accession = summary_path.parent.name
        busco_results[accession] = parse_busco_summary(summary_path)
    if not busco_results:
        raise FileNotFoundError(
            f"No BUSCO short summary files found under: {busco_dir}"
        )
    return busco_results


def format_tsv_value(value: object | None) -> str:
    if value is None:
        return ""
    return str(value)


def build_busco_tsv_values(
    busco_payload: OrderedDict[str, object],
) -> dict[str, str]:
    return {
        field_name: format_tsv_value(get_nested_value(busco_payload, *path))
        for field_name, path in BUSCO_TSV_FIELD_MAP.items()
    }


def species_to_json_stem(species_name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", species_name.lower()).strip("_")


def iter_candidate_json_paths(json_dir: Path, genome_summary_tsv: Path) -> list[Path]:
    if not genome_summary_tsv.exists():
        raise FileNotFoundError(
            f"Genome summary table does not exist: {genome_summary_tsv}"
        )

    candidate_json_paths: list[Path] = []
    seen: set[Path] = set()
    with genome_summary_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            species_name = clean_value(row.get("species"))
            if not species_name:
                continue
            json_path = json_dir / f"{species_to_json_stem(species_name)}.json"
            if json_path in seen or not json_path.exists():
                continue
            seen.add(json_path)
            candidate_json_paths.append(json_path)

    return sorted(candidate_json_paths)


def update_species_jsons(
    json_dir: Path,
    genome_summary_tsv: Path,
    busco_results: dict[str, OrderedDict[str, object]],
    indent: int,
) -> tuple[int, int, int, int]:
    if not json_dir.exists():
        raise FileNotFoundError(f"Species JSON directory does not exist: {json_dir}")

    files_updated = 0
    genome_records_with_busco = 0
    species_with_busco = 0
    candidate_json_paths = iter_candidate_json_paths(json_dir, genome_summary_tsv)

    for json_path in candidate_json_paths:
        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)

        document_changed = False
        species_matched = False
        genome_records = document.get("genome") or []
        for genome_record in genome_records:
            accession = clean_value(genome_record.get("assembly_accession"))
            if accession and accession in busco_results:
                target_busco = busco_results[accession]
                if genome_record.get("busco") != target_busco:
                    genome_record["busco"] = target_busco
                    document_changed = True
                genome_records_with_busco += 1
                species_matched = True

        if species_matched:
            species_with_busco += 1

        if not document_changed:
            continue

        with json_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
        files_updated += 1

    return (
        len(candidate_json_paths),
        files_updated,
        genome_records_with_busco,
        species_with_busco,
    )


def update_genome_summary_tsv(
    genome_summary_tsv: Path,
    busco_results: dict[str, OrderedDict[str, object]],
) -> tuple[int, int]:
    if not genome_summary_tsv.exists():
        raise FileNotFoundError(
            f"Genome summary table does not exist: {genome_summary_tsv}"
        )

    rows: list[dict[str, str]] = []
    rows_updated = 0
    rows_with_busco = 0

    with genome_summary_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise ValueError(f"Genome summary table has no header: {genome_summary_tsv}")

        for row in reader:
            accession = clean_value(row.get("assembly_accession"))
            if accession and accession in busco_results:
                rows_with_busco += 1
                if not clean_value(row.get("busco_notation")):
                    busco_tsv_values = build_busco_tsv_values(busco_results[accession])
                    row_changed = False
                    for field_name, value in busco_tsv_values.items():
                        if row.get(field_name, "") != value:
                            row[field_name] = value
                            row_changed = True
                    if row_changed:
                        rows_updated += 1
            rows.append(row)

    if rows_updated:
        with genome_summary_tsv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                delimiter="\t",
                lineterminator="\r\n",
            )
            writer.writeheader()
            writer.writerows(rows)

    return rows_updated, rows_with_busco


def main() -> int:
    args = parse_args()
    busco_dir = resolve_repo_path(args.busco_dir)
    json_dir = resolve_repo_path(args.json_dir)
    genome_summary_tsv = resolve_repo_path(args.genome_summary_tsv)

    busco_results = load_busco_results(busco_dir)
    json_files_scanned, files_updated, genome_records_with_busco, species_with_busco = update_species_jsons(
        json_dir,
        genome_summary_tsv,
        busco_results,
        args.indent,
    )
    tsv_rows_updated, tsv_rows_with_busco = update_genome_summary_tsv(
        genome_summary_tsv,
        busco_results,
    )

    print(f"root={ROOT}")
    print(f"busco_dir={busco_dir}")
    print(f"json_dir={json_dir}")
    print(f"genome_summary_tsv={genome_summary_tsv}")
    print(f"busco_result_files={len(busco_results)}")
    print(f"json_files_scanned={json_files_scanned}")
    print(f"json_files_updated={files_updated}")
    print(f"genome_records_with_busco={genome_records_with_busco}")
    print(f"species_with_busco={species_with_busco}")
    print(f"tsv_rows_with_busco={tsv_rows_with_busco}")
    print(f"tsv_rows_updated={tsv_rows_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
