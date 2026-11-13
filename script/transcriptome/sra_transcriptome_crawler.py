#!/usr/bin/env python3
"""
Batch-collect Lepidoptera transcriptome metadata from NCBI SRA and BioSample.

Usage:
    uv run sra-transcriptome-crawler \
        --input sra_ncbi_YYYYMMDD.csv \
        --output transcriptome_data_YYYYMMDD.csv \
        [--api-key YOUR_NCBI_API_KEY]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

CHECKPOINT_INTERVAL = 100
RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 30
INTERNAL_BIOSAMPLE_LOOKUP_COLUMN = "BioSample Lookup Accession"
DEFAULT_CACHE_DB_NAME = "ncbi_request_cache.sqlite"

INPUT_NAME_PATTERN = re.compile(r"^sra_ncbi_\d{8}\.csv$", re.IGNORECASE)
OUTPUT_NAME_PATTERN = re.compile(r"^transcriptome_data_\d{8}\.csv$", re.IGNORECASE)

BASE_COLUMNS = [
    "Experiment Accession",
    "Organism Name",
    "Instrument",
    "Sample Accession",
    "Total Size Mb",
    "Total Spots",
    "Total Bases",
    "Library Strategy",
    "Library Source",
    "Library Selection",
]

BIOSAMPLE_COLUMNS = [
    "Tissue",
    "Sex",
    "Stage",
    "Collection Date",
    "Geographic location",
]

RUN_COLUMNS = [
    "Run ID",
    "Published",
    "Download Link",
]

OUTPUT_COLUMNS = BASE_COLUMNS + BIOSAMPLE_COLUMNS + RUN_COLUMNS
INTERMEDIATE_COLUMNS = BASE_COLUMNS + BIOSAMPLE_COLUMNS + ["biosample_raw"] + RUN_COLUMNS

INPUT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "Experiment Accession": (
        "Experiment Accession",
        "Experiment_Accession",
        "Experiment",
        "Experiment accession",
    ),
    "Organism Name": (
        "Organism Name",
        "Organism_Name",
        "Organism",
        "Scientific Name",
        "Scientific_Name",
    ),
    "Instrument": (
        "Instrument",
        "Instrument Model",
        "Instrument_Model",
        "Model",
    ),
    "Sample Accession": (
        "Sample Accession",
        "Sample_Accession",
        "Sample",
        "BioSample",
        "Biosample",
    ),
    "Total Size Mb": (
        "Total Size Mb",
        "Total_Size_Mb",
        "size_MB",
        "Size_MB",
    ),
    "Total Spots": (
        "Total Spots",
        "Total_Spots",
        "spots",
        "Spots",
    ),
    "Total Bases": (
        "Total Bases",
        "Total_Bases",
        "bases",
        "Bases",
    ),
    "Library Strategy": (
        "Library Strategy",
        "Library_Strategy",
        "LibraryStrategy",
    ),
    "Library Source": (
        "Library Source",
        "Library_Source",
        "LibrarySource",
    ),
    "Library Selection": (
        "Library Selection",
        "Library_Selection",
        "LibrarySelection",
    ),
}

OPTIONAL_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    INTERNAL_BIOSAMPLE_LOOKUP_COLUMN: (
        "BioSample",
        "Biosample",
        "BioSample Accession",
        "BioSample_Accession",
    ),
}

BIOSAMPLE_FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "Tissue": ("tissue", "tissue type", "body part"),
    "Sex": ("sex", "gender"),
    "Stage": ("dev_stage", "development stage", "life stage", "developmental stage"),
    "Collection Date": ("collection_date", "collection date"),
    "Geographic location": ("geo_loc_name", "geographic location", "country"),
}


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


BIOSAMPLE_SYNONYM_INDEX = {
    field: {normalize_text(synonym) for synonym in synonyms}
    for field, synonyms in BIOSAMPLE_FIELD_SYNONYMS.items()
}


def clean_value(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except TypeError:
        pass

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return "N/A"
    return text


def attribute_matches(candidate_labels: list[str], synonyms: set[str]) -> bool:
    for candidate in candidate_labels:
        normalized_candidate = normalize_text(candidate)
        if not normalized_candidate:
            continue
        for synonym in synonyms:
            if (
                normalized_candidate == synonym
                or synonym in normalized_candidate
                or normalized_candidate in synonym
            ):
                return True
    return False


class RateLimiter:
    """Simple sliding-window limiter to respect NCBI request limits."""

    def __init__(self, max_calls_per_second: int) -> None:
        self.max_calls_per_second = max_calls_per_second
        self.request_times: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            while True:
                now = time.monotonic()
                while self.request_times and now - self.request_times[0] >= 1.0:
                    self.request_times.popleft()

                if len(self.request_times) < self.max_calls_per_second:
                    self.request_times.append(now)
                    return

                sleep_seconds = 1.0 - (now - self.request_times[0])
                time.sleep(max(sleep_seconds, 0.01))


@dataclass(frozen=True)
class CheckpointPaths:
    state_file: Path
    partial_output_file: Path
    failed_accessions_file: Path


class SqliteRequestCache:
    """Persist raw NCBI responses locally for later reuse and re-parsing."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self.lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS biosample_uid_lookup (
                    query_accession TEXT PRIMARY KEY,
                    resolved_uid TEXT,
                    raw_response_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS biosample_xml_cache (
                    request_accession TEXT PRIMARY KEY,
                    lookup_id TEXT NOT NULL,
                    resolved_biosample_accession TEXT,
                    sra_sample_accession TEXT,
                    raw_xml TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_biosample_xml_resolved_accession
                ON biosample_xml_cache (resolved_biosample_accession);

                CREATE INDEX IF NOT EXISTS idx_biosample_xml_sra_sample_accession
                ON biosample_xml_cache (sra_sample_accession);

                CREATE TABLE IF NOT EXISTS runinfo_cache (
                    experiment_accession TEXT PRIMARY KEY,
                    raw_csv TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def get_biosample_uid_entry(self, query_accession: str) -> sqlite3.Row | None:
        with self.lock:
            cursor = self.connection.execute(
                """
                SELECT query_accession, resolved_uid, raw_response_json, fetched_at
                FROM biosample_uid_lookup
                WHERE query_accession = ?
                """,
                (query_accession,),
            )
            return cursor.fetchone()

    def cache_biosample_uid(
        self,
        query_accession: str,
        resolved_uid: str | None,
        raw_response_json: str,
    ) -> None:
        with self.lock:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO biosample_uid_lookup (
                        query_accession,
                        resolved_uid,
                        raw_response_json,
                        fetched_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(query_accession) DO UPDATE SET
                        resolved_uid = excluded.resolved_uid,
                        raw_response_json = excluded.raw_response_json,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        query_accession,
                        resolved_uid,
                        raw_response_json,
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )

    def get_biosample_xml(self, request_accession: str) -> str | None:
        with self.lock:
            cursor = self.connection.execute(
                """
                SELECT raw_xml
                FROM biosample_xml_cache
                WHERE request_accession = ?
                """,
                (request_accession,),
            )
            row = cursor.fetchone()
            return None if row is None else str(row["raw_xml"])

    def cache_biosample_xml(
        self,
        request_accession: str,
        lookup_id: str,
        raw_xml: str,
    ) -> None:
        resolved_biosample_accession = None
        sra_sample_accession = None

        try:
            root = ET.fromstring(raw_xml)
            biosample_node = root.find(".//BioSample")
            if biosample_node is None and root.tag == "BioSample":
                biosample_node = root

            if biosample_node is not None:
                resolved_biosample_accession = biosample_node.attrib.get("accession")

            sra_id_node = root.find('.//Id[@db="SRA"]')
            if sra_id_node is not None and sra_id_node.text:
                sra_sample_accession = clean_value(sra_id_node.text)
        except ET.ParseError:
            pass

        with self.lock:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO biosample_xml_cache (
                        request_accession,
                        lookup_id,
                        resolved_biosample_accession,
                        sra_sample_accession,
                        raw_xml,
                        fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_accession) DO UPDATE SET
                        lookup_id = excluded.lookup_id,
                        resolved_biosample_accession = excluded.resolved_biosample_accession,
                        sra_sample_accession = excluded.sra_sample_accession,
                        raw_xml = excluded.raw_xml,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        request_accession,
                        lookup_id,
                        resolved_biosample_accession,
                        sra_sample_accession,
                        raw_xml,
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )

    def get_runinfo_csv(self, experiment_accession: str) -> str | None:
        with self.lock:
            cursor = self.connection.execute(
                """
                SELECT raw_csv
                FROM runinfo_cache
                WHERE experiment_accession = ?
                """,
                (experiment_accession,),
            )
            row = cursor.fetchone()
            return None if row is None else str(row["raw_csv"])

    def cache_runinfo_csv(self, experiment_accession: str, raw_csv: str) -> None:
        with self.lock:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO runinfo_cache (
                        experiment_accession,
                        raw_csv,
                        fetched_at
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(experiment_accession) DO UPDATE SET
                        raw_csv = excluded.raw_csv,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        experiment_accession,
                        raw_csv,
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )


class SharedComputationCache:
    """Ensure the same key is only computed once across concurrent workers."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.inflight: dict[str, threading.Event] = {}
        self.lock = threading.Lock()

    def get_or_compute(self, key: str, compute: Any) -> Any:
        while True:
            with self.lock:
                if key in self.values:
                    return self.values[key]

                event = self.inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self.inflight[key] = event
                    should_compute = True
                else:
                    should_compute = False

            if should_compute:
                try:
                    value = compute()
                except Exception:
                    with self.lock:
                        self.inflight.pop(key, None)
                        event.set()
                    raise

                with self.lock:
                    self.values[key] = value
                    self.inflight.pop(key, None)
                    event.set()
                return value

            event.wait()


@dataclass
class FetchOutcome:
    payload: Any
    failed_accession: str | None = None
    error_message: str | None = None


@dataclass
class ProcessedRecord:
    row_index: int
    experiment_accession: str
    sample_accession: str
    output_rows: list[dict[str, str]]
    failed_accessions: list[str]
    error_messages: list[str]


class NcbiClient:
    BIOSAMPLE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    BIOSAMPLE_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    RUNINFO_URL = "https://trace.ncbi.nlm.nih.gov/Traces/sra-db-be/runinfo"

    def __init__(
        self,
        api_key: str | None = None,
        sqlite_cache: SqliteRequestCache | None = None,
    ) -> None:
        self.api_key = api_key
        self.sqlite_cache = sqlite_cache
        self.rate_limiter = RateLimiter(10 if api_key else 3)
        self.session_lock = threading.Lock()
        self.sessions: dict[int, requests.Session] = {}

    def _get_session(self) -> requests.Session:
        thread_id = threading.get_ident()
        with self.session_lock:
            session = self.sessions.get(thread_id)
            if session is None:
                session = requests.Session()
                session.headers.update(
                    {
                        "User-Agent": "sra-transcriptome-crawler/0.1",
                        "Accept": "*/*",
                    }
                )
                self.sessions[thread_id] = session
            return session

    def request_text(self, url: str, params: dict[str, Any]) -> str:
        request_params = dict(params)
        if self.api_key and "eutils.ncbi.nlm.nih.gov" in url:
            request_params.setdefault("api_key", self.api_key)

        total_attempts = RETRY_COUNT + 1
        last_error: Exception | None = None
        for attempt in range(1, total_attempts + 1):
            self.rate_limiter.wait()
            try:
                response = self._get_session().get(
                    url,
                    params=request_params,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt == total_attempts:
                    break
                print(
                    (
                        f"Request failed for {url} (attempt {attempt}/{total_attempts}). "
                        f"Retrying in {RETRY_DELAY_SECONDS}s: {exc}"
                    ),
                    file=sys.stderr,
                )
                time.sleep(RETRY_DELAY_SECONDS)

        raise RuntimeError(
            f"Request failed after {RETRY_COUNT} retries: {url} params={request_params}"
        ) from last_error

    def close(self) -> None:
        with self.session_lock:
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()

    def search_biosample_uid(self, accession: str) -> str | None:
        if self.sqlite_cache is not None:
            cached_entry = self.sqlite_cache.get_biosample_uid_entry(accession)
            if cached_entry is not None:
                return (
                    None
                    if cached_entry["resolved_uid"] in (None, "")
                    else str(cached_entry["resolved_uid"])
                )

        raw_response = self.request_text(
            self.BIOSAMPLE_SEARCH_URL,
            {
                "db": "biosample",
                "term": accession,
                "retmax": 1,
                "retmode": "json",
                "tool": "sra_transcriptome_crawler",
            },
        )
        payload = json.loads(raw_response)
        id_list = payload.get("esearchresult", {}).get("idlist", [])
        resolved_uid = id_list[0] if id_list else None

        if self.sqlite_cache is not None:
            self.sqlite_cache.cache_biosample_uid(
                query_accession=accession,
                resolved_uid=resolved_uid,
                raw_response_json=raw_response,
            )

        return resolved_uid

    def fetch_biosample_xml(self, sample_accession: str) -> str:
        if self.sqlite_cache is not None:
            cached_xml = self.sqlite_cache.get_biosample_xml(sample_accession)
            if cached_xml is not None:
                return cached_xml

        lookup_id = sample_accession
        normalized_accession = sample_accession.strip().upper()

        # SRA Sample accessions such as SRS/ERS/DRS cannot be sent directly to
        # BioSample efetch, otherwise the numeric suffix may be misinterpreted.
        if normalized_accession.startswith(("SRS", "ERS", "DRS")):
            biosample_uid = self.search_biosample_uid(sample_accession)
            if biosample_uid is None:
                raise RuntimeError(
                    f"Unable to resolve BioSample UID from sample accession: {sample_accession}"
                )
            lookup_id = biosample_uid

        raw_xml = self.request_text(
            self.BIOSAMPLE_URL,
            {
                "db": "biosample",
                "id": lookup_id,
                "retmode": "xml",
                "tool": "sra_transcriptome_crawler",
            },
        )

        if self.sqlite_cache is not None:
            self.sqlite_cache.cache_biosample_xml(
                request_accession=sample_accession,
                lookup_id=lookup_id,
                raw_xml=raw_xml,
            )

        return raw_xml

    def fetch_runinfo_csv(self, experiment_accession: str) -> str:
        if self.sqlite_cache is not None:
            cached_csv = self.sqlite_cache.get_runinfo_csv(experiment_accession)
            if cached_csv is not None:
                return cached_csv

        raw_csv = self.request_text(
            self.RUNINFO_URL,
            {
                "acc": experiment_accession,
            },
        )

        if self.sqlite_cache is not None:
            self.sqlite_cache.cache_runinfo_csv(
                experiment_accession=experiment_accession,
                raw_csv=raw_csv,
            )

        return raw_csv


def empty_biosample_record(raw_xml: str = "N/A") -> dict[str, str]:
    record = {column: "N/A" for column in BIOSAMPLE_COLUMNS}
    record["biosample_raw"] = clean_value(raw_xml)
    return record


def empty_run_record() -> dict[str, str]:
    return {
        "Run ID": "N/A",
        "Published": "N/A",
        "Download Link": "N/A",
    }


def parse_biosample_xml(raw_xml: str) -> dict[str, str]:
    record = empty_biosample_record(raw_xml)
    if clean_value(raw_xml) == "N/A":
        return record

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return record

    for attribute in root.findall(".//Attribute"):
        attribute_text = " ".join(part.strip() for part in attribute.itertext() if part.strip())
        value = clean_value(attribute_text)
        if value == "N/A":
            continue

        candidate_labels = [str(label) for label in attribute.attrib.values() if str(label).strip()]
        if not candidate_labels:
            continue

        for field_name, synonyms in BIOSAMPLE_SYNONYM_INDEX.items():
            if record[field_name] != "N/A":
                continue
            if attribute_matches(candidate_labels, synonyms):
                record[field_name] = value
                break

    return record


def parse_runinfo_csv(raw_csv: str) -> list[dict[str, str]]:
    if clean_value(raw_csv) == "N/A":
        return [empty_run_record()]

    reader = csv.DictReader(StringIO(raw_csv))
    runs: list[dict[str, str]] = []
    for row in reader:
        run_id = clean_value(row.get("Run"))
        if run_id == "N/A":
            continue

        published = clean_value(row.get("Published"))
        if published == "N/A":
            published = clean_value(row.get("ReleaseDate"))
        runs.append(
            {
                "Run ID": run_id,
                "Published": published,
                "Download Link": f"https://sra-pub-run-odp.s3.amazonaws.com/sra/{run_id}/{run_id}",
            }
        )

    return runs or [empty_run_record()]


def resolve_column_mapping(columns: list[str]) -> dict[str, str]:
    normalized_columns = {normalize_text(column): column for column in columns}
    mapping: dict[str, str] = {}
    missing_fields: list[str] = []

    for target_field, aliases in INPUT_COLUMN_ALIASES.items():
        matched_column = None
        for alias in aliases:
            if normalize_text(alias) in normalized_columns:
                matched_column = normalized_columns[normalize_text(alias)]
                break

        if matched_column is None:
            missing_fields.append(target_field)
        else:
            mapping[target_field] = matched_column

    if missing_fields:
        raise ValueError(
            "Input CSV is missing required columns: " + ", ".join(missing_fields)
        )

    return mapping


def resolve_optional_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized_columns = {normalize_text(column): column for column in columns}
    for alias in aliases:
        matched_column = normalized_columns.get(normalize_text(alias))
        if matched_column is not None:
            return matched_column
    return None


def load_and_filter_input(input_path: Path) -> pd.DataFrame:
    if not INPUT_NAME_PATTERN.match(input_path.name):
        print(
            (
                f"Warning: input filename '{input_path.name}' does not match the expected "
                "pattern sra_ncbi_YYYYMMDD.csv."
            ),
            file=sys.stderr,
        )

    dataframe = pd.read_csv(input_path, dtype=str, low_memory=False)
    column_mapping = resolve_column_mapping(list(dataframe.columns))

    selected = pd.DataFrame(
        {
            target_field: dataframe[source_field].map(clean_value)
            for target_field, source_field in column_mapping.items()
        }
    )

    biosample_lookup_source = resolve_optional_column(
        list(dataframe.columns),
        OPTIONAL_COLUMN_ALIASES[INTERNAL_BIOSAMPLE_LOOKUP_COLUMN],
    )
    if biosample_lookup_source is not None:
        selected[INTERNAL_BIOSAMPLE_LOOKUP_COLUMN] = dataframe[biosample_lookup_source].map(
            clean_value
        )
        selected[INTERNAL_BIOSAMPLE_LOOKUP_COLUMN] = selected[
            INTERNAL_BIOSAMPLE_LOOKUP_COLUMN
        ].where(
            selected[INTERNAL_BIOSAMPLE_LOOKUP_COLUMN].ne("N/A"),
            selected["Sample Accession"],
        )
    else:
        selected[INTERNAL_BIOSAMPLE_LOOKUP_COLUMN] = selected["Sample Accession"]

    strategy_mask = selected["Library Strategy"].str.upper().eq("RNA-SEQ")
    source_mask = selected["Library Source"].str.upper().eq("TRANSCRIPTOMIC")
    filtered = selected.loc[strategy_mask & source_mask].reset_index(drop=True)
    return filtered


def build_cache_db_path(output_path: Path, cache_db_arg: str | None) -> Path:
    if cache_db_arg:
        return Path(cache_db_arg).expanduser().resolve()
    return output_path.parent / DEFAULT_CACHE_DB_NAME


def build_checkpoint_paths(output_path: Path) -> CheckpointPaths:
    return CheckpointPaths(
        state_file=output_path.with_name(f"{output_path.stem}.checkpoint.json"),
        partial_output_file=output_path.with_name(f"{output_path.stem}.partial.csv"),
        failed_accessions_file=output_path.parent / "failed_accessions.txt",
    )


def load_failed_accessions(failed_accessions_path: Path) -> set[str]:
    if not failed_accessions_path.exists():
        return set()

    return {
        line.strip()
        for line in failed_accessions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def write_failed_accessions(failed_accessions_path: Path, failed_accessions: set[str]) -> None:
    failed_accessions_path.write_text(
        "\n".join(sorted(failed_accessions)) + ("\n" if failed_accessions else ""),
        encoding="utf-8",
    )


def backfill_biosample_cache_from_partial(
    partial_output_path: Path,
    sqlite_cache: SqliteRequestCache,
) -> int:
    if not partial_output_path.exists():
        return 0

    try:
        dataframe = pd.read_csv(
            partial_output_path,
            dtype=str,
            usecols=["Sample Accession", "biosample_raw"],
            low_memory=False,
        ).fillna("N/A")
    except ValueError:
        return 0

    dataframe = dataframe.loc[
        dataframe["Sample Accession"].ne("N/A") & dataframe["biosample_raw"].ne("N/A")
    ].drop_duplicates(subset=["Sample Accession"])

    inserted_rows = 0
    for row in dataframe.to_dict(orient="records"):
        sample_accession = clean_value(row.get("Sample Accession"))
        raw_xml = clean_value(row.get("biosample_raw"))
        if sample_accession == "N/A" or raw_xml == "N/A":
            continue

        if sqlite_cache.get_biosample_xml(sample_accession) is None:
            sqlite_cache.cache_biosample_xml(
                request_accession=sample_accession,
                lookup_id=sample_accession,
                raw_xml=raw_xml,
            )
            inserted_rows += 1

    return inserted_rows


def load_checkpoint_state(
    checkpoint_paths: CheckpointPaths,
    input_path: Path,
    total_records: int,
) -> int:
    if checkpoint_paths.state_file.exists() and checkpoint_paths.partial_output_file.exists():
        state = json.loads(checkpoint_paths.state_file.read_text(encoding="utf-8"))
        checkpoint_input = state.get("input_path")
        if checkpoint_input != str(input_path.resolve()):
            raise ValueError(
                "Checkpoint file belongs to a different input file. Please remove it or "
                "use the matching input."
            )

        processed_records = int(state.get("processed_records", 0))
        if processed_records > total_records:
            raise ValueError("Checkpoint state is inconsistent with the filtered input size.")
        return processed_records

    if checkpoint_paths.state_file.exists() != checkpoint_paths.partial_output_file.exists():
        raise ValueError(
            "Checkpoint state file and partial output are inconsistent. Please remove the "
            "orphaned progress file before rerunning."
        )

    return 0


def save_checkpoint_state(
    checkpoint_paths: CheckpointPaths,
    input_path: Path,
    output_path: Path,
    processed_records: int,
    total_records: int,
) -> None:
    state = {
        "input_path": str(input_path.resolve()),
        "output_path": str(output_path.resolve()),
        "processed_records": processed_records,
        "total_records": total_records,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    checkpoint_paths.state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_rows_to_partial_csv(
    rows: list[dict[str, str]],
    partial_output_path: Path,
) -> None:
    if not rows:
        return

    dataframe = pd.DataFrame(rows, columns=INTERMEDIATE_COLUMNS).fillna("N/A")
    write_header = not partial_output_path.exists()
    dataframe.to_csv(
        partial_output_path,
        mode="a",
        index=False,
        header=write_header,
        encoding="utf-8-sig" if write_header else "utf-8",
    )


def finalize_output_file(checkpoint_paths: CheckpointPaths, output_path: Path) -> None:
    if checkpoint_paths.partial_output_file.exists():
        dataframe = pd.read_csv(
            checkpoint_paths.partial_output_file,
            dtype=str,
            low_memory=False,
        ).fillna("N/A")
        for column in OUTPUT_COLUMNS:
            if column not in dataframe.columns:
                dataframe[column] = "N/A"
        dataframe = dataframe[OUTPUT_COLUMNS]
    else:
        dataframe = pd.DataFrame(columns=OUTPUT_COLUMNS)

    dataframe.to_csv(output_path, index=False, encoding="utf-8-sig")

    if checkpoint_paths.state_file.exists():
        checkpoint_paths.state_file.unlink()
    if checkpoint_paths.partial_output_file.exists():
        checkpoint_paths.partial_output_file.unlink()


def checkpoint_progress(
    pending_rows: list[dict[str, str]],
    checkpoint_paths: CheckpointPaths,
    input_path: Path,
    output_path: Path,
    processed_records: int,
    total_records: int,
    failed_accessions: set[str],
) -> None:
    append_rows_to_partial_csv(pending_rows, checkpoint_paths.partial_output_file)
    pending_rows.clear()
    save_checkpoint_state(
        checkpoint_paths=checkpoint_paths,
        input_path=input_path,
        output_path=output_path,
        processed_records=processed_records,
        total_records=total_records,
    )
    write_failed_accessions(checkpoint_paths.failed_accessions_file, failed_accessions)
    print(
        f"Checkpoint saved: {processed_records}/{total_records}",
        file=sys.stderr,
    )


def build_output_rows(
    base_record: dict[str, str],
    biosample_record: dict[str, str],
    run_records: list[dict[str, str]],
) -> list[dict[str, str]]:
    output_rows: list[dict[str, str]] = []
    for run_record in run_records:
        row = {column: clean_value(base_record.get(column)) for column in BASE_COLUMNS}
        for column in BIOSAMPLE_COLUMNS:
            row[column] = clean_value(biosample_record.get(column))
        row["biosample_raw"] = clean_value(biosample_record.get("biosample_raw"))
        for column in RUN_COLUMNS:
            row[column] = clean_value(run_record.get(column))
        output_rows.append(row)
    return output_rows


class RecordProcessor:
    """Thread-safe row processor that preserves deterministic output order upstream."""

    def __init__(
        self,
        records: list[dict[str, str]],
        client: NcbiClient,
    ) -> None:
        self.records = records
        self.client = client
        self.biosample_cache = SharedComputationCache()
        self.runinfo_cache = SharedComputationCache()

    def _fetch_biosample_outcome(self, biosample_lookup_accession: str) -> FetchOutcome:
        if biosample_lookup_accession == "N/A":
            return FetchOutcome(
                payload=empty_biosample_record(),
                failed_accession=f"biosample:{biosample_lookup_accession}",
            )

        try:
            raw_xml = self.client.fetch_biosample_xml(biosample_lookup_accession)
            return FetchOutcome(payload=parse_biosample_xml(raw_xml))
        except Exception as exc:
            return FetchOutcome(
                payload=empty_biosample_record(),
                failed_accession=f"biosample:{biosample_lookup_accession}",
                error_message=(
                    f"BioSample fetch failed for {biosample_lookup_accession}: {exc}"
                ),
            )

    def _fetch_runinfo_outcome(self, experiment_accession: str) -> FetchOutcome:
        if experiment_accession == "N/A":
            return FetchOutcome(
                payload=[empty_run_record()],
                failed_accession=f"experiment:{experiment_accession}",
            )

        try:
            raw_csv = self.client.fetch_runinfo_csv(experiment_accession)
            return FetchOutcome(payload=parse_runinfo_csv(raw_csv))
        except Exception as exc:
            return FetchOutcome(
                payload=[empty_run_record()],
                failed_accession=f"experiment:{experiment_accession}",
                error_message=f"RunInfo fetch failed for {experiment_accession}: {exc}",
            )

    def process_row(self, row_index: int) -> ProcessedRecord:
        record = self.records[row_index]
        base_record = {column: clean_value(record.get(column)) for column in BASE_COLUMNS}

        experiment_accession = base_record["Experiment Accession"]
        sample_accession = base_record["Sample Accession"]
        biosample_lookup_accession = clean_value(
            record.get(INTERNAL_BIOSAMPLE_LOOKUP_COLUMN)
        )

        biosample_outcome: FetchOutcome = self.biosample_cache.get_or_compute(
            biosample_lookup_accession,
            lambda: self._fetch_biosample_outcome(biosample_lookup_accession),
        )
        runinfo_outcome: FetchOutcome = self.runinfo_cache.get_or_compute(
            experiment_accession,
            lambda: self._fetch_runinfo_outcome(experiment_accession),
        )

        failed_accessions = [
            failed_accession
            for failed_accession in (
                biosample_outcome.failed_accession,
                runinfo_outcome.failed_accession,
            )
            if failed_accession is not None
        ]
        error_messages = [
            error_message
            for error_message in (
                biosample_outcome.error_message,
                runinfo_outcome.error_message,
            )
            if error_message is not None
        ]

        return ProcessedRecord(
            row_index=row_index,
            experiment_accession=experiment_accession,
            sample_accession=sample_accession,
            output_rows=build_output_rows(
                base_record=base_record,
                biosample_record=biosample_outcome.payload,
                run_records=runinfo_outcome.payload,
            ),
            failed_accessions=failed_accessions,
            error_messages=error_messages,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Lepidoptera transcriptome metadata from NCBI SRA and BioSample."
    )
    parser.add_argument("--input", required=True, help="Path to sra_ncbi_YYYYMMDD.csv")
    parser.add_argument(
        "--output",
        required=True,
        help="Path to transcriptome_data_YYYYMMDD.csv",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional NCBI API key for a higher request limit.",
    )
    parser.add_argument(
        "--cache-db",
        default=None,
        help=(
            "Optional SQLite cache DB path. Default: create ncbi_request_cache.sqlite "
            "in the output directory."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Optional number of concurrent worker threads. Default: 6 with API key, "
            "3 without API key."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    cache_db_path = build_cache_db_path(output_path, args.cache_db)
    worker_count = args.workers if args.workers is not None else (6 if args.api_key else 3)
    worker_count = max(worker_count, 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    if not OUTPUT_NAME_PATTERN.match(output_path.name):
        print(
            (
                f"Warning: output filename '{output_path.name}' does not match the expected "
                "pattern transcriptome_data_YYYYMMDD.csv."
            ),
            file=sys.stderr,
        )

    print(f"Loading input CSV: {input_path}")
    try:
        filtered_records = load_and_filter_input(input_path)
    except Exception as exc:
        print(f"Failed to read or filter input CSV: {exc}", file=sys.stderr)
        return 1
    total_records = len(filtered_records)
    print(f"Filtered transcriptome records: {total_records}")
    print(f"Worker threads: {worker_count}")

    checkpoint_paths = build_checkpoint_paths(output_path)
    try:
        sqlite_cache = SqliteRequestCache(cache_db_path)
    except Exception as exc:
        print(f"Failed to initialize SQLite cache DB: {exc}", file=sys.stderr)
        return 1
    print(f"SQLite cache DB: {cache_db_path}")

    if total_records == 0:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )
        write_failed_accessions(checkpoint_paths.failed_accessions_file, set())
        sqlite_cache.close()
        print(f"No matching records found. Empty output written to: {output_path}")
        return 0

    try:
        start_index = load_checkpoint_state(
            checkpoint_paths=checkpoint_paths,
            input_path=input_path,
            total_records=total_records,
        )
    except Exception as exc:
        sqlite_cache.close()
        print(f"Failed to load checkpoint state: {exc}", file=sys.stderr)
        return 1
    resume_mode = (
        checkpoint_paths.state_file.exists() and checkpoint_paths.partial_output_file.exists()
    )
    if start_index:
        print(f"Resuming from checkpoint: {start_index}/{total_records}")

    if resume_mode:
        backfilled_rows = backfill_biosample_cache_from_partial(
            checkpoint_paths.partial_output_file,
            sqlite_cache,
        )
        if backfilled_rows:
            print(
                f"Backfilled {backfilled_rows} BioSample XML records into SQLite cache."
            )

    client = NcbiClient(api_key=args.api_key, sqlite_cache=sqlite_cache)
    record_items = filtered_records.to_dict(orient="records")
    processor = RecordProcessor(record_items, client)
    failed_accessions = (
        load_failed_accessions(checkpoint_paths.failed_accessions_file)
        if resume_mode
        else set()
    )
    if not resume_mode:
        write_failed_accessions(checkpoint_paths.failed_accessions_file, failed_accessions)
    pending_rows: list[dict[str, str]] = []
    reported_error_messages: set[str] = set()
    processed_records = start_index

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for batch_start in range(start_index, total_records, CHECKPOINT_INTERVAL):
                batch_end = min(batch_start + CHECKPOINT_INTERVAL, total_records)
                batch_indices = list(range(batch_start, batch_end))

                for processed_record in executor.map(processor.process_row, batch_indices):
                    for error_message in processed_record.error_messages:
                        if error_message not in reported_error_messages:
                            print(error_message, file=sys.stderr)
                            reported_error_messages.add(error_message)

                    failed_accessions.update(processed_record.failed_accessions)
                    pending_rows.extend(processed_record.output_rows)
                    processed_records = processed_record.row_index + 1

                    print(
                        f"[{processed_records}/{total_records}] Processed "
                        f"Experiment={processed_record.experiment_accession} "
                        f"Sample={processed_record.sample_accession}"
                    )
                    print(f"Progress: {processed_records}/{total_records}")

                checkpoint_progress(
                    pending_rows=pending_rows,
                    checkpoint_paths=checkpoint_paths,
                    input_path=input_path,
                    output_path=output_path,
                    processed_records=processed_records,
                    total_records=total_records,
                    failed_accessions=failed_accessions,
                )

    except KeyboardInterrupt:
        print("Interrupted by user. Saving checkpoint before exit...", file=sys.stderr)
        if pending_rows:
            checkpoint_progress(
                pending_rows=pending_rows,
                checkpoint_paths=checkpoint_paths,
                input_path=input_path,
                output_path=output_path,
                processed_records=processed_records,
                total_records=total_records,
                failed_accessions=failed_accessions,
            )
        return 130
    except Exception as exc:
        print(f"Crawler failed unexpectedly: {exc}", file=sys.stderr)
        if pending_rows:
            checkpoint_progress(
                pending_rows=pending_rows,
                checkpoint_paths=checkpoint_paths,
                input_path=input_path,
                output_path=output_path,
                processed_records=processed_records,
                total_records=total_records,
                failed_accessions=failed_accessions,
            )
        return 1
    finally:
        client.close()
        sqlite_cache.close()

    finalize_output_file(checkpoint_paths, output_path)
    print(f"Finished. Output written to: {output_path}")
    print(f"Failed accessions written to: {checkpoint_paths.failed_accessions_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
