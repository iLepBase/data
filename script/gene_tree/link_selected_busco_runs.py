#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SELECTED_TSV = Path("data/raw/gene_tree/best_busco_genome_per_species.tsv")
DEFAULT_BUSCO_ROOT = Path("data/raw/genome/busco")
DEFAULT_OUTPUT_DIR = Path("data/raw/gene_tree/select_busco")
DEFAULT_MANIFEST = Path("data/raw/gene_tree/select_busco_manifest.tsv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Symlink selected BUSCO result directories into one directory for downstream phylogenomics."
    )
    parser.add_argument(
        "--selected-tsv",
        default=str(DEFAULT_SELECTED_TSV),
        help="Per-species selected BUSCO genome TSV, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--busco-root",
        default=str(DEFAULT_BUSCO_ROOT),
        help="Root BUSCO results directory, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for symlinked BUSCO result directories.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Output manifest TSV path.",
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


def clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def slugify_species_name(name: str, max_length: int = 150) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").lower()
    if not slug:
        slug = "species"
    if len(slug) <= max_length:
        return slug
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    keep = max_length - len(digest) - 2
    truncated = slug[:keep].rstrip("_")
    return f"{truncated}__{digest}"


def make_unique_stem(name: str, used_stems: dict[str, str]) -> str:
    base_stem = slugify_species_name(name)
    existing_name = used_stems.get(base_stem)
    if existing_name is None:
        used_stems[base_stem] = name
        return base_stem
    if existing_name == name:
        return base_stem

    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    candidate = f"{base_stem}__{digest}"
    if len(candidate) > 180:
        candidate = candidate[:169].rstrip("_") + f"__{digest}"
    used_stems[candidate] = name
    return candidate


def load_selected_rows(selected_tsv: Path) -> list[dict[str, str]]:
    with selected_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [row for row in reader]


def build_target_dir(busco_root: Path, row: dict[str, str]) -> Path:
    genome_source = clean_value(row.get("genome_source"))
    assembly_accession = clean_value(row.get("assembly_accession"))
    if genome_source is None or assembly_accession is None:
        raise ValueError(f"Missing genome_source or assembly_accession in selected row: {row}")
    return busco_root / f"results_{genome_source}" / "busco" / assembly_accession / assembly_accession


def write_manifest(manifest_path: Path, rows: list[dict[str, str]]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("No manifest rows to write.")
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    selected_tsv = resolve_repo_path(args.selected_tsv)
    busco_root = resolve_repo_path(args.busco_root)
    output_dir = resolve_repo_path(args.output_dir)
    manifest_path = resolve_repo_path(args.manifest)

    selected_rows = load_selected_rows(selected_tsv)
    output_dir.mkdir(parents=True, exist_ok=True)

    used_stems: dict[str, str] = {}
    expected_names: set[str] = set()
    manifest_rows: list[dict[str, str]] = []
    links_created = 0
    links_updated = 0
    links_unchanged = 0

    for row in selected_rows:
        species = clean_value(row.get("species"))
        if species is None:
            continue

        stem = make_unique_stem(species, used_stems)
        link_path = output_dir / stem
        target_dir = build_target_dir(busco_root, row)
        expected_names.add(stem)

        if not target_dir.exists():
            raise FileNotFoundError(f"Selected BUSCO directory does not exist: {target_dir}")

        if link_path.exists() or link_path.is_symlink():
            if link_path.is_symlink() and link_path.resolve() == target_dir.resolve():
                links_unchanged += 1
            else:
                link_path.unlink()
                link_path.symlink_to(target_dir)
                links_updated += 1
        else:
            link_path.symlink_to(target_dir)
            links_created += 1

        manifest_rows.append(
            {
                "species": species,
                "sample_id": stem,
                "genome_source": row["genome_source"],
                "assembly_accession": row["assembly_accession"],
                "busco_link": to_repo_relative_path(link_path),
                "busco_target": to_repo_relative_path(target_dir),
            }
        )

    stale_removed = 0
    for existing in output_dir.iterdir():
        if existing.name in expected_names:
            continue
        if existing.is_symlink() or existing.is_file():
            existing.unlink()
        elif existing.is_dir():
            raise RuntimeError(f"Unexpected real directory inside output dir: {existing}")
        stale_removed += 1

    write_manifest(manifest_path, manifest_rows)

    print(f"root={ROOT}")
    print(f"selected_tsv={selected_tsv}")
    print(f"busco_root={busco_root}")
    print(f"output_dir={output_dir}")
    print(f"manifest={manifest_path}")
    print(f"selected_rows={len(selected_rows)}")
    print(f"links_created={links_created}")
    print(f"links_updated={links_updated}")
    print(f"links_unchanged={links_unchanged}")
    print(f"stale_links_removed={stale_removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
