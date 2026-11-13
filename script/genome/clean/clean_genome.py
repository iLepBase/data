#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sys
import tarfile
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_REL = Path("data/clean/genome/genomic")
DEFAULT_THREADS = max(1, min(8, os.cpu_count() or 1))
IBG_SUFFIXES = (".genome.fa.tar.bz2", ".genome.fasta.tar.bz2", ".genome.fna.tar.bz2")
NGDC_SUFFIXES = (".genome.fa.gz", ".genome.fasta.gz", ".genome.fna.gz")


@dataclass(frozen=True)
class Job:
    source_path: Path
    output_name: str


@dataclass(frozen=True)
class SourceSpec:
    default_dir: Path
    log_name: str
    finder: Callable[[Path], list[Job]]
    writer: Callable[[Job, Path], None]


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Standardize genome files from NCBI / IBG / NGDC into "
            "<accession>.genomic.fna.gz."
        )
    )
    parser.add_argument(
        "--source",
        choices=("ncbi", "ibg", "ngdc"),
        required=True,
        help="Data source to process.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Input directory. Default depends on --source.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_REL,
        help=f"Output directory, relative to the iLepbase project root by default. Default: {DEFAULT_OUT_REL}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing .gz output file.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help="Process only the first N matched inputs.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Log file path. Default: <out-dir>/clean_<source>.log",
    )
    parser.add_argument(
        "--threads",
        type=positive_int,
        default=DEFAULT_THREADS,
        help=f"Number of files to process in parallel. Default: {DEFAULT_THREADS}",
    )
    return parser.parse_args(argv)


def resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def strip_suffix(name: str, suffixes: tuple[str, ...]) -> str:
    for suffix in suffixes:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    raise RuntimeError(f"unexpected file name: {name}")


def find_ncbi_jobs(source_dir: Path) -> list[Job]:
    jobs = []
    for path in sorted(source_dir.rglob("*")):
        name = path.name
        if path.is_file() and name.endswith("_genomic.fna") and "_from_genomic.fna" not in name:
            jobs.append(Job(path, f"{path.parent.name}.genomic.fna.gz"))
    return jobs


def find_ibg_jobs(source_dir: Path) -> list[Job]:
    return [
        Job(path, f"{strip_suffix(path.name, IBG_SUFFIXES)}.genomic.fna.gz")
        for path in sorted(source_dir.iterdir())
        if path.is_file() and path.name.endswith(IBG_SUFFIXES)
    ]


def find_ngdc_jobs(source_dir: Path) -> list[Job]:
    return [
        Job(path, f"{strip_suffix(path.name, NGDC_SUFFIXES)}.genomic.fna.gz")
        for path in sorted(source_dir.iterdir())
        if path.is_file() and path.name.endswith(NGDC_SUFFIXES)
    ]


def write_from_plain_fasta(job: Job, tmp_path: Path) -> None:
    with job.source_path.open("rb") as src_handle, gzip.open(tmp_path, "wb") as gz_handle:
        shutil.copyfileobj(src_handle, gz_handle)


def select_ibg_member(archive_handle: tarfile.TarFile, archive_path: Path) -> tarfile.TarInfo:
    regular_files = [member for member in archive_handle if member.isfile()]
    if not regular_files:
        raise RuntimeError(f"archive does not contain any regular files: {archive_path}")

    fasta_members = [
        member
        for member in regular_files
        if member.name.lower().endswith((".fa", ".fna", ".fasta"))
    ]
    if len(fasta_members) == 1:
        return fasta_members[0]
    if len(regular_files) == 1:
        return regular_files[0]

    names = ", ".join(member.name for member in regular_files[:10])
    raise RuntimeError(f"archive contains multiple candidate files: {archive_path} ({names})")


def write_from_ibg_archive(job: Job, tmp_path: Path) -> None:
    with tarfile.open(job.source_path, "r:bz2") as archive_handle:
        member = select_ibg_member(archive_handle, job.source_path)
        extracted = archive_handle.extractfile(member)
        if extracted is None:
            raise RuntimeError(f"failed to extract member from archive: {member.name}")
        with extracted, gzip.open(tmp_path, "wb") as gz_handle:
            shutil.copyfileobj(extracted, gz_handle)


def copy_gzip_file(job: Job, tmp_path: Path) -> None:
    shutil.copyfile(job.source_path, tmp_path)


SOURCES = {
    # Three sources share one main flow. Only file discovery and write strategy differ.
    "ncbi": SourceSpec(
        default_dir=Path("data/raw/genome/NCBI_unzip"),
        log_name="clean_ncbi.log",
        finder=find_ncbi_jobs,
        writer=write_from_plain_fasta,
    ),
    "ibg": SourceSpec(
        default_dir=Path("data/raw/genome/IBG"),
        log_name="clean_ibg.log",
        finder=find_ibg_jobs,
        writer=write_from_ibg_archive,
    ),
    "ngdc": SourceSpec(
        default_dir=Path("data/raw/genome/NGDC"),
        log_name="clean_ngdc.log",
        finder=find_ngdc_jobs,
        writer=copy_gzip_file,
    ),
}


class Logger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.log_path.open("a", encoding="utf-8")

    def log(self, level: str, message: str, *, stream: object | None = None) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}"
        with self.lock:
            print(line, file=stream or sys.stdout, flush=True)
            self.handle.write(f"{line}\n")
            self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def validate_unique_outputs(jobs: list[Job]) -> None:
    seen: dict[str, Path] = {}
    conflicts: dict[str, list[Path]] = {}

    for job in jobs:
        if job.output_name in seen:
            conflicts.setdefault(job.output_name, [seen[job.output_name]]).append(job.source_path)
            continue
        seen[job.output_name] = job.source_path

    if not conflicts:
        return

    details = []
    for output_name, paths in sorted(conflicts.items()):
        details.append(f"{output_name}: {', '.join(str(path) for path in paths)}")
    raise RuntimeError("multiple inputs map to the same output name: " + "; ".join(details))


def write_output(
    job: Job,
    output_path: Path,
    overwrite: bool,
    writer: Callable[[Job, Path], None],
) -> str:
    if output_path.exists() and not overwrite:
        return f"[SKIP] {output_path.name} already exists"

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)

        writer(job, tmp_path)
        os.replace(tmp_path, output_path)
        return f"[OK] {display_path(job.source_path)} -> {display_path(output_path)}"
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
        raise


def run_one(
    job: Job,
    spec: SourceSpec,
    out_dir: Path,
    overwrite: bool,
    logger: Logger,
) -> str:
    output_path = out_dir / job.output_name
    logger.log("START", f"{display_path(job.source_path)} -> {display_path(output_path)}")
    try:
        message = write_output(job, output_path, overwrite, spec.writer)
        if message.startswith("[SKIP]"):
            logger.log("SKIP", message.removeprefix("[SKIP] ").strip())
            return "skipped"
        logger.log("OK", message.removeprefix("[OK] ").strip())
        return "success"
    except Exception as exc:
        logger.log("ERROR", f"{display_path(job.source_path)}: {exc}", stream=sys.stderr)
        return "failed"


def run_many(
    jobs: list[Job],
    spec: SourceSpec,
    out_dir: Path,
    overwrite: bool,
    threads: int,
    logger: Logger,
) -> tuple[int, int, int]:
    success = 0
    skipped = 0
    failed = 0
    max_workers = min(threads, len(jobs))

    if max_workers == 1:
        for job in jobs:
            status = run_one(job, spec, out_dir, overwrite, logger)
            success += status == "success"
            skipped += status == "skipped"
            failed += status == "failed"
        return success, skipped, failed

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_one, job, spec, out_dir, overwrite, logger) for job in jobs]
        for future in as_completed(futures):
            status = future.result()
            success += status == "success"
            skipped += status == "skipped"
            failed += status == "failed"

    return success, skipped, failed


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    spec = SOURCES[args.source]
    source_dir = resolve_project_path(args.source_dir or spec.default_dir)
    out_dir = resolve_project_path(args.out_dir)

    if not source_dir.is_dir():
        print(f"[ERROR] source directory not found: {source_dir}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolve_project_path(args.log_file) if args.log_file else out_dir / spec.log_name
    logger = Logger(log_path)

    try:
        logger.log(
            "INFO",
            (
                f"source={args.source} "
                f"source_dir={display_path(source_dir)} "
                f"out_dir={display_path(out_dir)} "
                f"log_file={display_path(log_path)} "
                f"overwrite={args.overwrite} limit={args.limit} threads={args.threads}"
            ),
        )

        jobs = spec.finder(source_dir)
        if args.limit is not None:
            jobs = jobs[: args.limit]

        if not jobs:
            logger.log("ERROR", f"no inputs found in: {source_dir}", stream=sys.stderr)
            return 1

        validate_unique_outputs(jobs)
        success, skipped, failed = run_many(
            jobs,
            spec,
            out_dir,
            args.overwrite,
            args.threads,
            logger,
        )
        logger.log(
            "SUMMARY",
            f"source={args.source} total={len(jobs)} success={success} skipped={skipped} failed={failed}",
        )
        return 0 if failed == 0 else 1
    except RuntimeError as exc:
        logger.log("ERROR", str(exc), stream=sys.stderr)
        return 1
    finally:
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
