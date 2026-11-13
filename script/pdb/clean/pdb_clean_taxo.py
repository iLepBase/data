#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def detect_delimiter(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return ","
    if suffix in {".tsv", ".txt"}:
        return "\t"

    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        return ","


def load_summary_mapping(
    summary_file: Path,
    summary_key_column: str,
    pdb_id_column: str,
    keep_summary_rows: bool,
) -> dict[str, list[str]]:
    delimiter = detect_delimiter(summary_file)
    mapping: dict[str, list[str]] = defaultdict(list)
    seen_by_key: dict[str, set[str]] = defaultdict(set)

    with summary_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError(f"CSV/TSV has no header: {summary_file}")
        for column in (summary_key_column, pdb_id_column):
            if column not in reader.fieldnames:
                raise ValueError(f"Missing column '{column}' in {summary_file}")

        for row in reader:
            match_value = (row.get(summary_key_column) or "").strip()
            pdb_id = (row.get(pdb_id_column) or "").strip()
            if not match_value or not pdb_id:
                continue
            if keep_summary_rows:
                mapping[match_value].append(pdb_id)
                continue
            if pdb_id in seen_by_key[match_value]:
                continue
            seen_by_key[match_value].add(pdb_id)
            mapping[match_value].append(pdb_id)

    for key in mapping:
        mapping[key].sort()
    return mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Match clean taxonomy rows back to a species_summary file and prepend pdb_id. "
            "Unmatched clean rows are skipped."
        )
    )
    parser.add_argument(
        "--clean-file",
        type=Path,
        required=True,
        help="Input clean CSV/TSV, e.g. pdb_org.clean.sp.csv",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=Path("/home/iLepbase/script/pdb_bank/species_summary.tsv"),
        help="species_summary file containing pdb_id and organism columns",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        required=True,
        help="Output CSV path",
    )
    parser.add_argument(
        "--clean-key-column",
        default="origin Species",
        help="Column in the clean file used for matching",
    )
    parser.add_argument(
        "--summary-key-column",
        default="source_organism",
        help="Column in species_summary used for matching",
    )
    parser.add_argument(
        "--pdb-id-column",
        default="pdb_id",
        help="PDB id column name in species_summary",
    )
    parser.add_argument(
        "--keep-summary-rows",
        action="store_true",
        help="Keep duplicate summary rows instead of collapsing to unique pdb_id per key",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not args.clean_file.exists():
        raise FileNotFoundError(f"Clean file not found: {args.clean_file}")
    if not args.summary_file.exists():
        raise FileNotFoundError(f"Summary file not found: {args.summary_file}")

    mapping = load_summary_mapping(
        summary_file=args.summary_file,
        summary_key_column=args.summary_key_column,
        pdb_id_column=args.pdb_id_column,
        keep_summary_rows=args.keep_summary_rows,
    )

    clean_delimiter = detect_delimiter(args.clean_file)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    clean_rows = 0
    matched_clean_rows = 0
    unmatched_clean_rows = 0
    written_rows = 0

    with args.clean_file.open("r", encoding="utf-8-sig", newline="") as in_handle:
        reader = csv.DictReader(in_handle, delimiter=clean_delimiter)
        if not reader.fieldnames:
            raise ValueError(f"CSV/TSV has no header: {args.clean_file}")
        if args.clean_key_column not in reader.fieldnames:
            raise ValueError(
                f"Missing column '{args.clean_key_column}' in {args.clean_file}"
            )

        output_fieldnames = [args.pdb_id_column] + [
            field for field in reader.fieldnames if field != args.pdb_id_column
        ]

        with args.output_file.open("w", encoding="utf-8", newline="") as out_handle:
            writer = csv.DictWriter(out_handle, fieldnames=output_fieldnames)
            writer.writeheader()

            for row in reader:
                clean_rows += 1
                match_value = (row.get(args.clean_key_column) or "").strip()
                pdb_ids = mapping.get(match_value, [])
                if not pdb_ids:
                    unmatched_clean_rows += 1
                    continue
                matched_clean_rows += 1

                base_row = {field: row.get(field, "") for field in reader.fieldnames}
                for pdb_id in pdb_ids:
                    out_row = dict(base_row)
                    out_row[args.pdb_id_column] = pdb_id
                    writer.writerow(out_row)
                    written_rows += 1

    print(f"[out] {args.output_file}")
    print(f"[clean_rows] {clean_rows}")
    print(f"[matched_clean_rows] {matched_clean_rows}")
    print(f"[unmatched_clean_rows] {unmatched_clean_rows}")
    print(f"[written_rows] {written_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
