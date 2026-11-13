#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPECIES_DIR = Path("data/clean/distribution/species")
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_WORKERS = 16

WORKER_JSON_DIR: Path | None = None
WORKER_INDENT = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write compact per-species distribution summaries into the distribution field of per-species JSON files."
    )
    parser.add_argument(
        "--species-dir",
        default=str(DEFAULT_SPECIES_DIR),
        help="Per-species distribution JSON directory, relative to the iLepbase root by default.",
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
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Number of worker processes for JSON writeback.",
    )
    parser.add_argument(
        "--task-count",
        type=int,
        default=64,
        help="Number of parallel work chunks. Larger values improve load balancing.",
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


def build_distribution_summary(species_json: Path) -> OrderedDict[str, object]:
    with species_json.open("r", encoding="utf-8") as handle:
        document = json.load(handle, object_pairs_hook=OrderedDict)

    sources = document.get("sources")
    if not isinstance(sources, list):
        sources = []

    count = document.get("count")
    if not isinstance(count, int):
        raise ValueError(f"Distribution JSON missing integer 'count': {species_json}")

    return OrderedDict(
        [
            ("count", count),
            ("sources", sources),
            ("path", to_repo_relative_path(species_json)),
        ]
    )


def write_json_atomic(json_path: Path, document: OrderedDict[str, object], indent: int) -> None:
    temp_path = json_path.with_name(f"{json_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")
    temp_path.replace(json_path)


def init_worker(json_dir: str, indent: int) -> None:
    global WORKER_JSON_DIR, WORKER_INDENT
    WORKER_JSON_DIR = Path(json_dir)
    WORKER_INDENT = indent


def process_species_batch(batch: list[str]) -> dict[str, int]:
    if WORKER_JSON_DIR is None:
        raise RuntimeError("Worker JSON directory was not initialized.")

    stats = {
        "species_files_seen": 0,
        "json_files_updated": 0,
        "missing_json_files": 0,
        "distribution_counts_written": 0,
    }

    for species_json_str in batch:
        species_json = Path(species_json_str)
        stats["species_files_seen"] += 1
        json_path = WORKER_JSON_DIR / f"{species_json.stem}.json"
        if not json_path.exists():
            stats["missing_json_files"] += 1
            continue

        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)

        distribution = build_distribution_summary(species_json)
        if document.get("distribution") == distribution:
            continue
        document["distribution"] = distribution
        write_json_atomic(json_path, document, WORKER_INDENT)

        stats["json_files_updated"] += 1
        stats["distribution_counts_written"] += distribution["count"]

    return stats


def chunk_list(items: list[Path], chunk_count: int) -> list[list[str]]:
    if not items:
        return []
    chunk_count = max(1, min(chunk_count, len(items)))
    chunks: list[list[str]] = [[] for _ in range(chunk_count)]
    for index, item in enumerate(items):
        chunks[index % chunk_count].append(str(item))
    return [chunk for chunk in chunks if chunk]


def run_full_update(
    species_dir: Path,
    json_dir: Path,
    indent: int,
    workers: int,
    task_count: int,
) -> int:
    species_paths = sorted(species_dir.glob("*.json"))
    tasks = chunk_list(species_paths, task_count)
    stats = {
        "species_files_seen": 0,
        "json_files_updated": 0,
        "missing_json_files": 0,
        "distribution_counts_written": 0,
        "tasks_completed": 0,
    }

    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=init_worker,
        initargs=(str(json_dir), indent),
    ) as executor:
        futures = [executor.submit(process_species_batch, task) for task in tasks]
        total_tasks = len(futures)
        for future in as_completed(futures):
            result = future.result()
            stats["tasks_completed"] += 1
            for key in ("species_files_seen", "json_files_updated", "missing_json_files", "distribution_counts_written"):
                stats[key] += result[key]
            print(
                f"[writeback] tasks_completed={stats['tasks_completed']:,}/{total_tasks:,} "
                f"json_files_updated={stats['json_files_updated']:,} "
                f"distribution_counts_written={stats['distribution_counts_written']:,}",
                flush=True,
            )

    print(f"root={ROOT}")
    print(f"species_dir={species_dir}")
    print(f"json_dir={json_dir}")
    print(f"workers={workers}")
    print(f"task_count={len(tasks)}")
    print(f"species_files_seen={stats['species_files_seen']}")
    print(f"json_files_updated={stats['json_files_updated']}")
    print(f"missing_json_files={stats['missing_json_files']}")
    print(f"distribution_counts_written={stats['distribution_counts_written']}")
    return 0


def main() -> int:
    args = parse_args()
    species_dir = resolve_repo_path(args.species_dir)
    json_dir = resolve_repo_path(args.json_dir)

    return run_full_update(
        species_dir=species_dir,
        json_dir=json_dir,
        indent=args.indent,
        workers=args.workers,
        task_count=args.task_count,
    )


if __name__ == "__main__":
    raise SystemExit(main())
