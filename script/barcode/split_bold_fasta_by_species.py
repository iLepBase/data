#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import OrderedDict
from pathlib import Path


INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]+')


class FileHandleCache:
    def __init__(self, max_open: int) -> None:
        self.max_open = max_open
        self._handles: OrderedDict[Path, object] = OrderedDict()

    def write_record(self, path: Path, header: str, sequence: str) -> None:
        handle = self._get_handle(path)
        handle.write(header)
        handle.write(sequence)

    def close(self) -> None:
        while self._handles:
            _, handle = self._handles.popitem(last=False)
            handle.close()

    def _get_handle(self, path: Path):
        handle = self._handles.pop(path, None)
        if handle is None:
            if len(self._handles) >= self.max_open:
                _, oldest = self._handles.popitem(last=False)
                oldest.close()
            handle = path.open("a", encoding="utf-8")
        self._handles[path] = handle
        return handle


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def normalize_species_name(name: str) -> str:
    return " ".join(name.strip().split())


def build_legacy_filename(species_name: str) -> str:
    safe_name = INVALID_FILENAME_CHARS.sub("_", species_name).strip()
    if not safe_name:
        raise ValueError("Species name becomes empty after filename normalization.")
    return f"{safe_name}.fasta"


def build_filename(species_name: str) -> str:
    return build_legacy_filename(species_name).replace(" ", "_")


def has_usable_sequence_length(value: str) -> bool:
    normalized = value.strip()
    return normalized != "" and normalized.lower() != "none"


def load_process_map(
    info_tsv: Path, filtered_info_tsv: Path, output_dir: Path
) -> tuple[dict[str, tuple[Path, str]], set[Path], dict[str, int]]:
    process_to_entry: dict[str, tuple[Path, str]] = {}
    filename_to_species: dict[str, str] = {}
    cleanup_paths: set[Path] = set()
    stats = {
        "rows": 0,
        "rows_written_to_filtered_tsv": 0,
        "rows_without_processid": 0,
        "rows_without_species": 0,
        "rows_without_sequence_length": 0,
        "duplicate_processid": 0,
        "species_files": 0,
    }

    with info_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"taxo_species", "bold_processid", "bold_nuc_basecount"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"Missing required columns in {info_tsv}: {', '.join(missing)}")

        with filtered_info_tsv.open("w", encoding="utf-8", newline="") as filtered_handle:
            writer = csv.DictWriter(filtered_handle, fieldnames=reader.fieldnames, delimiter="\t", lineterminator="\n")
            writer.writeheader()

            for row in reader:
                stats["rows"] += 1

                process_id = row["bold_processid"].strip()
                species_name = normalize_species_name(row["taxo_species"])
                sequence_length = row["bold_nuc_basecount"]

                if not has_usable_sequence_length(sequence_length):
                    stats["rows_without_sequence_length"] += 1
                    continue

                writer.writerow(row)
                stats["rows_written_to_filtered_tsv"] += 1

                if not process_id:
                    stats["rows_without_processid"] += 1
                    continue
                if not species_name:
                    stats["rows_without_species"] += 1
                    continue

                filename = build_filename(species_name)
                previous_species = filename_to_species.get(filename)
                if previous_species is None:
                    filename_to_species[filename] = species_name
                elif previous_species != species_name:
                    raise ValueError(
                        f"Filename collision: {species_name!r} and {previous_species!r} both map to {filename!r}"
                    )

                path = output_dir / filename
                cleanup_paths.add(path)
                cleanup_paths.add(output_dir / build_legacy_filename(species_name))
                entry = (path, species_name)
                previous_entry = process_to_entry.get(process_id)
                if previous_entry is None:
                    process_to_entry[process_id] = entry
                elif previous_entry != entry:
                    raise ValueError(
                        f"Conflicting taxo_species for processid {process_id}: "
                        f"{previous_entry[0].name!r} vs {path.name!r}"
                    )
                else:
                    stats["duplicate_processid"] += 1

    stats["species_files"] = len(filename_to_species)
    stats["mapped_processid"] = len(process_to_entry)
    return process_to_entry, cleanup_paths, stats


def extract_process_id(header: str) -> str:
    return header[1:].split("|", 1)[0].strip()


def iter_fasta_records(fasta_file: Path):
    header: str | None = None
    sequence_lines: list[str] = []

    with fasta_file.open("r", encoding="utf-8", newline="") as handle:
        for line in handle:
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(sequence_lines)
                header = line
                sequence_lines = []
            elif header is not None:
                sequence_lines.append(line)

    if header is not None:
        yield header, "".join(sequence_lines)


def remove_existing_outputs(paths: set[Path]) -> int:
    removed = 0
    for path in paths:
        if path.exists():
            path.unlink()
            removed += 1
    return removed


def parse_args() -> argparse.Namespace:
    root = repo_root()
    parser = argparse.ArgumentParser(
        description="Split BOLD Lepidoptera COI FASTA into one FASTA file per taxo_species."
    )
    parser.add_argument(
        "--info-tsv",
        type=Path,
        default=root / "data/raw/COI/bold_info.tsv",
        help="Path to bold_info.tsv",
    )
    parser.add_argument(
        "--input-fasta",
        type=Path,
        default=root / "data/raw/COI/lepidoptera_COI_BOLD_27-FEB-2026.fasta",
        help="Path to the filtered Lepidoptera FASTA file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "data/clean/COI",
        help="Directory for per-species FASTA files",
    )
    parser.add_argument(
        "--filtered-info-tsv",
        type=Path,
        default=root / "data/clean/COI/bold_info.filtered.tsv",
        help="Filtered bold_info.tsv with rows having usable bold_nuc_basecount",
    )
    parser.add_argument(
        "--max-open-files",
        type=int,
        default=256,
        help="Maximum number of FASTA output handles kept open at once",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100000,
        help="Print progress every N FASTA records",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.filtered_info_tsv.parent.mkdir(parents=True, exist_ok=True)

    process_to_entry, cleanup_paths, mapping_stats = load_process_map(
        args.info_tsv, args.filtered_info_tsv, args.output_dir
    )
    removed_files = remove_existing_outputs(cleanup_paths)

    cache = FileHandleCache(max_open=args.max_open_files)
    total_records = 0
    matched_records = 0

    try:
        for header, sequence in iter_fasta_records(args.input_fasta):
            total_records += 1
            process_id = extract_process_id(header)
            entry = process_to_entry.get(process_id)

            if entry is not None:
                output_path, _ = entry
                cache.write_record(output_path, header, sequence)
                matched_records += 1

            if args.progress_every > 0 and total_records % args.progress_every == 0:
                print(
                    f"[progress] records={total_records:,} matched={matched_records:,}",
                    file=sys.stderr,
                )
    finally:
        cache.close()

    print(f"Input TSV: {args.info_tsv}", file=sys.stderr)
    print(f"Filtered TSV: {args.filtered_info_tsv}", file=sys.stderr)
    print(f"Input FASTA: {args.input_fasta}", file=sys.stderr)
    print(f"Output dir: {args.output_dir}", file=sys.stderr)
    print(
        (
            f"[mapping] rows={mapping_stats['rows']:,} "
            f"rows_written_to_filtered_tsv={mapping_stats['rows_written_to_filtered_tsv']:,} "
            f"mapped_processid={mapping_stats['mapped_processid']:,} "
            f"species_files={mapping_stats['species_files']:,} "
            f"duplicate_processid={mapping_stats['duplicate_processid']:,} "
            f"rows_without_processid={mapping_stats['rows_without_processid']:,} "
            f"rows_without_species={mapping_stats['rows_without_species']:,} "
            f"rows_without_sequence_length={mapping_stats['rows_without_sequence_length']:,}"
        ),
        file=sys.stderr,
    )
    print(f"[output] removed_existing_files={removed_files:,}", file=sys.stderr)
    print(f"[fasta] total_records={total_records:,} matched={matched_records:,}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
