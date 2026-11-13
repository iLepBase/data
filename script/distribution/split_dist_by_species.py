#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pycountry


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIST_TSV = Path(f"data/clean/distribution/lep_dist_clean_{date.today():%Y%m%d}.tsv")
DEFAULT_TAXO_TSV = Path("data/clean/taxo/lep_taxo_20260415.tsv")
DEFAULT_OUTPUT_DIR = Path("data/clean/distribution/species")
DEFAULT_BUCKET_COUNT = 256
DEFAULT_WORKERS = 8
DEFAULT_FINALIZE_PROGRESS_EVERY = 8
PROGRESS_BAR_WIDTH = 30

REQUIRED_FIELDS = (
    "species",
    "id",
    "origin_family",
    "origin_genus",
    "origin_species",
    "countryCode",
    "lat",
    "lon",
    "source",
)

COUNTRY_NAME_OVERRIDES = {
    "XK": "Kosovo",
}

WORKER_SPECIES_TO_OUTPUT: dict[str, str] | None = None
WORKER_INDENT = 2
WORKER_COUNTRY_CACHE: dict[str, str | None] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split the cleaned distribution TSV into one JSON file per species."
    )
    parser.add_argument(
        "--dist-tsv",
        default=str(DEFAULT_DIST_TSV),
        help="Cleaned distribution TSV path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--taxonomy-tsv",
        default=str(DEFAULT_TAXO_TSV),
        help="Taxonomy TSV path used to rebuild stable per-species file names.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for per-species distribution JSON files.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1_000_000,
        help="Print split progress every N distribution rows.",
    )
    parser.add_argument(
        "--finalize-progress-every",
        type=int,
        default=DEFAULT_FINALIZE_PROGRESS_EVERY,
        help="Print finalize progress every N completed buckets.",
    )
    parser.add_argument(
        "--bucket-count",
        type=int,
        default=DEFAULT_BUCKET_COUNT,
        help="Number of temporary bucket files used during the split phase.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Number of worker processes used during JSON finalize.",
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


def normalize_species_name(name: str | None) -> str | None:
    cleaned = clean_value(name)
    if cleaned is None:
        return None
    return " ".join(cleaned.split())


def split_tsv_line(raw_line: str) -> list[str]:
    return raw_line.rstrip("\r\n").split("\t")


def parse_float(value: str | None) -> float | None:
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    return float(cleaned)


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


def country_name_from_code(country_code: str | None) -> str | None:
    cleaned = clean_value(country_code)
    if cleaned is None:
        return None
    code = cleaned.upper()
    if code in WORKER_COUNTRY_CACHE:
        return WORKER_COUNTRY_CACHE[code]
    if code in COUNTRY_NAME_OVERRIDES:
        WORKER_COUNTRY_CACHE[code] = COUNTRY_NAME_OVERRIDES[code]
        return WORKER_COUNTRY_CACHE[code]
    country = pycountry.countries.get(alpha_2=code)
    WORKER_COUNTRY_CACHE[code] = None if country is None else country.name
    return WORKER_COUNTRY_CACHE[code]


def add_unique_value(container: OrderedDict[str, None], value: str | None) -> None:
    if value is not None and value not in container:
        container[value] = None


def collapse_unique_values(container: OrderedDict[str, None]) -> str | list[str] | None:
    values = list(container.keys())
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def build_source_taxonomy_payload(
    taxonomy_values: OrderedDict[str, OrderedDict[str, None]],
) -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("family", collapse_unique_values(taxonomy_values["family"])),
            ("genus", collapse_unique_values(taxonomy_values["genus"])),
            ("species", collapse_unique_values(taxonomy_values["species"])),
        ]
    )


def build_species_output_paths(taxonomy_tsv: Path, output_dir: Path) -> tuple[dict[str, Path], dict[str, int]]:
    species_to_output_path: dict[str, Path] = {}
    used_stems: dict[str, str] = {}
    stats = {
        "taxonomy_rows_total": 0,
        "taxonomy_rows_without_species": 0,
        "species_paths_built": 0,
    }

    with taxonomy_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            stats["taxonomy_rows_total"] += 1
            species_name = normalize_species_name(row.get("species")) or normalize_species_name(row.get("origin Species"))
            if species_name is None:
                stats["taxonomy_rows_without_species"] += 1
                continue
            if species_name in species_to_output_path:
                continue
            species_to_output_path[species_name] = output_dir / f"{make_unique_stem(species_name, used_stems)}.json"
            stats["species_paths_built"] += 1

    return species_to_output_path, stats


def validate_dist_header(header_fields: list[str], dist_tsv: Path) -> dict[str, int]:
    index_map = {name: idx for idx, name in enumerate(header_fields)}
    missing = [field for field in REQUIRED_FIELDS if field not in index_map]
    if missing:
        raise ValueError(f"Missing required columns in {dist_tsv}: {', '.join(missing)}")
    return index_map


def clear_existing_output(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.json", "*.json.tmp", "*.rows.tmp", "bucket_*.tsv.tmp"):
        for path in output_dir.glob(pattern):
            path.unlink()


def build_bucket_paths(output_dir: Path, bucket_count: int) -> list[Path]:
    width = max(3, len(str(bucket_count - 1)))
    return [output_dir / f"bucket_{index:0{width}d}.tsv.tmp" for index in range(bucket_count)]


def bucket_index_for_species(species_name: str, bucket_count: int) -> int:
    digest = hashlib.sha1(species_name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % bucket_count


def write_json_atomic(json_path: Path, document: OrderedDict[str, object], indent: int) -> None:
    temp_path = json_path.with_name(f"{json_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")
    temp_path.replace(json_path)


def split_distribution_to_buckets(
    dist_tsv: Path,
    species_to_output_path: dict[str, Path],
    output_dir: Path,
    progress_every: int,
    bucket_count: int,
) -> tuple[dict[str, int], list[Path]]:
    stats = {
        "dist_rows_total": 0,
        "dist_rows_without_species": 0,
        "dist_rows_without_output_path": 0,
        "dist_rows_written": 0,
        "species_json_files_touched": 0,
        "bucket_files_touched": 0,
    }

    clear_existing_output(output_dir)
    total_bytes = dist_tsv.stat().st_size
    bucket_paths = build_bucket_paths(output_dir, bucket_count)
    open_handles: dict[int, object] = {}
    touched_output_paths: set[Path] = set()
    touched_bucket_indexes: set[int] = set()

    def get_bucket_handle(bucket_index: int, header_line: str):
        handle = open_handles.get(bucket_index)
        if handle is not None:
            return handle
        bucket_path = bucket_paths[bucket_index]
        handle = bucket_path.open("w", encoding="utf-8", newline="", buffering=4 * 1024 * 1024)
        handle.write(header_line)
        open_handles[bucket_index] = handle
        return handle

    try:
        with dist_tsv.open("r", encoding="utf-8-sig", newline="", buffering=4 * 1024 * 1024) as handle:
            header_line = handle.readline()
            if not header_line:
                return stats, []

            header_fields = split_tsv_line(header_line)
            index_map = validate_dist_header(header_fields, dist_tsv)
            species_idx = index_map["species"]

            while True:
                raw_line = handle.readline()
                if not raw_line:
                    break

                stats["dist_rows_total"] += 1
                if progress_every > 0 and stats["dist_rows_total"] % progress_every == 0:
                    processed_bytes = handle.tell()
                    print(
                        f"[split] {format_progress_percent(processed_bytes, total_bytes)} "
                        f"{build_progress_bar(processed_bytes, total_bytes)} "
                        f"dist_rows_total={stats['dist_rows_total']:,} "
                        f"dist_rows_written={stats['dist_rows_written']:,} "
                        f"species_json_files_touched={stats['species_json_files_touched']:,} "
                        f"bucket_files_touched={stats['bucket_files_touched']:,}",
                        flush=True,
                    )

                row = split_tsv_line(raw_line)
                if len(row) < len(header_fields):
                    row.extend([""] * (len(header_fields) - len(row)))

                species_name = normalize_species_name(row[species_idx] if species_idx < len(row) else "")
                if species_name is None:
                    stats["dist_rows_without_species"] += 1
                    continue

                output_path = species_to_output_path.get(species_name)
                if output_path is None:
                    stats["dist_rows_without_output_path"] += 1
                    continue

                bucket_index = bucket_index_for_species(species_name, bucket_count)
                get_bucket_handle(bucket_index, header_line).write(raw_line)
                stats["dist_rows_written"] += 1
                if output_path not in touched_output_paths:
                    touched_output_paths.add(output_path)
                    stats["species_json_files_touched"] += 1
                if bucket_index not in touched_bucket_indexes:
                    touched_bucket_indexes.add(bucket_index)
                    stats["bucket_files_touched"] += 1
    finally:
        for handle in open_handles.values():
            handle.close()

    touched_bucket_paths = [bucket_paths[index] for index in sorted(touched_bucket_indexes)]
    return stats, touched_bucket_paths


def blank_distribution_group() -> OrderedDict[str, object]:
    return OrderedDict(
        [
            ("source_counts", OrderedDict()),
            ("country_codes", OrderedDict()),
            (
                "taxonomy_by_source",
                {
                    "GBIF": OrderedDict(
                        [
                            ("family", OrderedDict()),
                            ("genus", OrderedDict()),
                            ("species", OrderedDict()),
                        ]
                    ),
                    "BOLD": OrderedDict(
                        [
                            ("family", OrderedDict()),
                            ("genus", OrderedDict()),
                            ("species", OrderedDict()),
                        ]
                    ),
                },
            ),
            ("records", []),
        ]
    )


def add_row_to_distribution_group(group: OrderedDict[str, object], row: dict[str, str]) -> None:
    source_counts: OrderedDict[str, int] = group["source_counts"]  # type: ignore[assignment]
    country_codes: OrderedDict[str, None] = group["country_codes"]  # type: ignore[assignment]
    taxonomy_by_source: dict[str, OrderedDict[str, OrderedDict[str, None]]] = group["taxonomy_by_source"]  # type: ignore[assignment]
    records: list[OrderedDict[str, object]] = group["records"]  # type: ignore[assignment]

    source = clean_value(row["source"]) or ""
    country_code = clean_value(row["countryCode"])
    origin_family = clean_value(row["origin_family"])
    origin_genus = clean_value(row["origin_genus"])
    origin_species = normalize_species_name(row["origin_species"])

    if source not in source_counts:
        source_counts[source] = 0
    source_counts[source] += 1
    if country_code is not None and country_code not in country_codes:
        country_codes[country_code] = None
    if source in taxonomy_by_source:
        add_unique_value(taxonomy_by_source[source]["family"], origin_family)
        add_unique_value(taxonomy_by_source[source]["genus"], origin_genus)
        add_unique_value(taxonomy_by_source[source]["species"], origin_species)

    records.append(
        OrderedDict(
            [
                ("id", clean_value(row["id"]) or ""),
                ("source", source),
                ("country_code", country_code),
                ("country", country_name_from_code(country_code)),
                ("latitude", parse_float(row["lat"])),
                ("longitude", parse_float(row["lon"])),
            ]
        )
    )


def distribution_group_to_payload(group: OrderedDict[str, object]) -> OrderedDict[str, object]:
    source_counts: OrderedDict[str, int] = group["source_counts"]  # type: ignore[assignment]
    country_codes: OrderedDict[str, None] = group["country_codes"]  # type: ignore[assignment]
    taxonomy_by_source: dict[str, OrderedDict[str, OrderedDict[str, None]]] = group["taxonomy_by_source"]  # type: ignore[assignment]
    records: list[OrderedDict[str, object]] = group["records"]  # type: ignore[assignment]

    payload = OrderedDict(
        [
            ("count", len(records)),
            ("sources", list(source_counts.keys())),
            ("source_counts", source_counts),
            ("country_codes", list(country_codes.keys())),
        ]
    )
    if "GBIF" in source_counts:
        payload["gbif_taxonomy"] = build_source_taxonomy_payload(taxonomy_by_source["GBIF"])
    if "BOLD" in source_counts:
        payload["bold_taxonomy"] = build_source_taxonomy_payload(taxonomy_by_source["BOLD"])
    payload["records"] = records
    return payload


def init_finalize_worker(species_to_output: dict[str, str], indent: int) -> None:
    global WORKER_SPECIES_TO_OUTPUT, WORKER_INDENT, WORKER_COUNTRY_CACHE
    WORKER_SPECIES_TO_OUTPUT = species_to_output
    WORKER_INDENT = indent
    WORKER_COUNTRY_CACHE = {}


def process_bucket(bucket_path_str: str) -> dict[str, int]:
    if WORKER_SPECIES_TO_OUTPUT is None:
        raise RuntimeError("Finalize worker was not initialized.")

    bucket_path = Path(bucket_path_str)
    stats = {
        "bucket_files_seen": 1,
        "bucket_rows_seen": 0,
        "json_files_written": 0,
        "distribution_records_written": 0,
    }
    grouped: dict[str, OrderedDict[str, object]] = {}

    with bucket_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Bucket file has no header: {bucket_path}")
        validate_dist_header(reader.fieldnames, bucket_path)

        for row in reader:
            stats["bucket_rows_seen"] += 1
            species_name = normalize_species_name(row.get("species"))
            if species_name is None:
                continue
            output_path_str = WORKER_SPECIES_TO_OUTPUT.get(species_name)
            if output_path_str is None:
                continue
            group = grouped.get(output_path_str)
            if group is None:
                group = blank_distribution_group()
                grouped[output_path_str] = group
            add_row_to_distribution_group(group, row)

    for output_path_str, group in grouped.items():
        payload = distribution_group_to_payload(group)
        write_json_atomic(Path(output_path_str), payload, WORKER_INDENT)
        stats["json_files_written"] += 1
        stats["distribution_records_written"] += payload["count"]

    bucket_path.unlink()
    return stats


def finalize_buckets(
    bucket_paths: list[Path],
    species_to_output_path: dict[str, Path],
    indent: int,
    workers: int,
    progress_every: int,
) -> dict[str, int]:
    stats = {
        "bucket_files_total": len(bucket_paths),
        "bucket_files_completed": 0,
        "bucket_rows_seen": 0,
        "json_files_written": 0,
        "distribution_records_written": 0,
    }
    if not bucket_paths:
        return stats

    species_to_output_str = {species: str(path) for species, path in species_to_output_path.items()}

    with ProcessPoolExecutor(
        max_workers=max(1, workers),
        initializer=init_finalize_worker,
        initargs=(species_to_output_str, indent),
    ) as executor:
        futures = [executor.submit(process_bucket, str(bucket_path)) for bucket_path in bucket_paths]
        total = len(futures)
        for future in as_completed(futures):
            result = future.result()
            stats["bucket_files_completed"] += 1
            stats["bucket_rows_seen"] += result["bucket_rows_seen"]
            stats["json_files_written"] += result["json_files_written"]
            stats["distribution_records_written"] += result["distribution_records_written"]

            if progress_every > 0 and stats["bucket_files_completed"] % progress_every == 0:
                print(
                    f"[finalize] {format_progress_percent(stats['bucket_files_completed'], total)} "
                    f"{build_progress_bar(stats['bucket_files_completed'], total)} "
                    f"bucket_files_completed={stats['bucket_files_completed']:,}/{total:,} "
                    f"json_files_written={stats['json_files_written']:,} "
                    f"distribution_records_written={stats['distribution_records_written']:,}",
                    flush=True,
                )

    total = stats["bucket_files_total"]
    if total > 0 and (progress_every <= 0 or total % progress_every != 0):
        print(
            f"[finalize] {format_progress_percent(total, total)} "
            f"{build_progress_bar(total, total)} "
            f"bucket_files_completed={total:,}/{total:,} "
            f"json_files_written={stats['json_files_written']:,} "
            f"distribution_records_written={stats['distribution_records_written']:,}",
            flush=True,
        )
    return stats


def main() -> int:
    args = parse_args()
    dist_tsv = resolve_repo_path(args.dist_tsv)
    taxonomy_tsv = resolve_repo_path(args.taxonomy_tsv)
    output_dir = resolve_repo_path(args.output_dir)

    species_to_output_path, taxonomy_stats = build_species_output_paths(taxonomy_tsv, output_dir)
    split_stats, bucket_paths = split_distribution_to_buckets(
        dist_tsv=dist_tsv,
        species_to_output_path=species_to_output_path,
        output_dir=output_dir,
        progress_every=args.progress_every,
        bucket_count=max(1, args.bucket_count),
    )
    finalize_stats = finalize_buckets(
        bucket_paths=bucket_paths,
        species_to_output_path=species_to_output_path,
        indent=args.indent,
        workers=args.workers,
        progress_every=args.finalize_progress_every,
    )

    print(f"root={ROOT}")
    print(f"dist_tsv={display_path(dist_tsv)}")
    print(f"taxonomy_tsv={display_path(taxonomy_tsv)}")
    print(f"output_dir={display_path(output_dir)}")
    print(f"bucket_count={max(1, args.bucket_count)}")
    print(f"workers={max(1, args.workers)}")
    print(f"taxonomy_rows_total={taxonomy_stats['taxonomy_rows_total']}")
    print(f"taxonomy_rows_without_species={taxonomy_stats['taxonomy_rows_without_species']}")
    print(f"species_paths_built={taxonomy_stats['species_paths_built']}")
    print(f"dist_rows_total={split_stats['dist_rows_total']}")
    print(f"dist_rows_without_species={split_stats['dist_rows_without_species']}")
    print(f"dist_rows_without_output_path={split_stats['dist_rows_without_output_path']}")
    print(f"dist_rows_written={split_stats['dist_rows_written']}")
    print(f"species_json_files_touched={split_stats['species_json_files_touched']}")
    print(f"bucket_files_touched={split_stats['bucket_files_touched']}")
    print(f"bucket_files_completed={finalize_stats['bucket_files_completed']}")
    print(f"bucket_rows_seen={finalize_stats['bucket_rows_seen']}")
    print(f"json_files_written={finalize_stats['json_files_written']}")
    print(f"distribution_records_written={finalize_stats['distribution_records_written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
