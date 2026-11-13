#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_DIR = Path("data/raw/genome/NCBI_unzip")
DEFAULT_OUT_BASE = Path("data/clean/genome/anno")


@dataclass(frozen=True)
class FileSpec:
    key: str
    suffixes: tuple[str, ...]
    out_subdir: str
    out_suffix: str


FILE_SPECS = (
    FileSpec(
        key="cds",
        suffixes=("_cds_from_genomic.fna", "cds_from_genomic.fna"),
        out_subdir="cds",
        out_suffix=".cds.fa",
    ),
    FileSpec(
        key="gff3",
        suffixes=("_genomic.gff3", "_genomic.gff", "genomic.gff3", "genomic.gff"),
        out_subdir="gff3",
        out_suffix=".gff3",
    ),
    FileSpec(
        key="protein",
        suffixes=("_protein.faa", "protein.faa"),
        out_subdir="protein",
        out_suffix=".anno.pep.fa",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy NCBI annotation files from data/raw/genome/NCBI_unzip into "
            "data/clean/genome/anno with normalized accession-based names."
        )
    )
    parser.add_argument(
        "--source-dir",
        default=str(DEFAULT_SOURCE_DIR),
        help="Source directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--out-base",
        default=str(DEFAULT_OUT_BASE),
        help="Output base directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing destination files.",
    )
    return parser.parse_args()


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def collect_annotation_files(
    source_dir: Path,
) -> dict[str, dict[str, Path]]:
    grouped: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        assembly_name = path.parent.name
        for spec in FILE_SPECS:
            if path.name.endswith(spec.suffixes) and spec.key not in grouped[assembly_name]:
                grouped[assembly_name][spec.key] = path
    return grouped


def ensure_output_dirs(out_base: Path) -> None:
    for spec in FILE_SPECS:
        (out_base / spec.out_subdir).mkdir(parents=True, exist_ok=True)


def main() -> int:
    args = parse_args()
    source_dir = resolve_repo_path(args.source_dir)
    out_base = resolve_repo_path(args.out_base)

    if not source_dir.exists():
        raise SystemExit(f"source directory not found: {source_dir}")

    ensure_output_dirs(out_base)

    copied = {spec.key: 0 for spec in FILE_SPECS}
    missing = {spec.key: 0 for spec in FILE_SPECS}
    skipped = {spec.key: 0 for spec in FILE_SPECS}

    assembly_dirs = sorted(path for path in source_dir.iterdir() if path.is_dir())
    annotation_files = collect_annotation_files(source_dir)

    for assembly_dir in assembly_dirs:
        accession = assembly_dir.name
        assembly_files = annotation_files.get(accession, {})
        for spec in FILE_SPECS:
            source_file = assembly_files.get(spec.key)
            if source_file is None:
                missing[spec.key] += 1
                continue

            dest_file = out_base / spec.out_subdir / f"{accession}{spec.out_suffix}"
            if dest_file.exists() and not args.overwrite:
                skipped[spec.key] += 1
                continue

            shutil.copy2(source_file, dest_file)
            copied[spec.key] += 1

    print(f"root={ROOT}")
    print(f"source_dir={source_dir}")
    print(f"out_base={out_base}")
    print(f"assemblies_scanned={len(assembly_dirs)}")
    for spec in FILE_SPECS:
        print(f"{spec.key}_copied={copied[spec.key]}")
        print(f"{spec.key}_missing={missing[spec.key]}")
        print(f"{spec.key}_skipped={skipped[spec.key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
