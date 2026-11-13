#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
GBIF_SPECIES_URL = "https://api.gbif.org/v1/species/{usage_key}"
INAT_TAXA_URL = "https://api.inaturalist.org/v1/taxa/{taxon_id}"
DEFAULT_CACHE_DB = Path("/home/iLepbase/data/raw/taxo/taxonomy_cache.sqlite")
DEFAULT_NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")


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


def detect_encoding(path: Path) -> str:
    with path.open("rb") as handle:
        head = handle.read(3)
    return "utf-8-sig" if head.startswith(b"\xef\xbb\xbf") else "utf-8"


def normalize_source(value: str) -> str:
    value = (value or "").strip().lower()
    if value in {"inat", "inaturalist"}:
        return "inaturalist"
    return value


def first_nonempty(row: dict[str, str], columns: list[str]) -> str:
    for column in columns:
        value = (row.get(column) or "").strip()
        if value:
            return value
    return ""


def split_links(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def parse_id_from_link(link: str, source: str) -> str:
    if source == "ncbi":
        match = re.search(r"[?&]id=(\d+)\b", link)
        return match.group(1) if match else ""
    if source == "gbif":
        match = re.search(r"/species/(\d+)\b", link)
        return match.group(1) if match else ""
    if source == "inaturalist":
        match = re.search(r"/taxa/(\d+)\b", link)
        return match.group(1) if match else ""
    return ""


def extract_lookup(row: dict[str, str]) -> tuple[str, str]:
    source = normalize_source(row.get("source", ""))
    links = split_links(row.get("source link", ""))
    id_columns = ["sid", "gid", "sfid", "fid"]

    if source in {"ncbi", "gbif", "inaturalist"}:
        lookup_id = first_nonempty(row, id_columns)
        if not lookup_id:
            for link in links:
                lookup_id = parse_id_from_link(link, source)
                if lookup_id:
                    break
        if lookup_id:
            return source, lookup_id

    for candidate in ("ncbi", "gbif", "inaturalist"):
        for link in links:
            lookup_id = parse_id_from_link(link, candidate)
            if lookup_id:
                return candidate, lookup_id

    return "", ""


class SQLiteJsonCache:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS http_cache (
                namespace TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(namespace, cache_key)
            )
            """
        )
        self.conn.commit()

    def get(self, namespace: str, key: str) -> dict[str, str] | None:
        cur = self.conn.execute(
            "SELECT value FROM http_cache WHERE namespace = ? AND cache_key = ?",
            (namespace, key),
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def set(self, namespace: str, key: str, value: dict[str, str]) -> None:
        self.conn.execute(
            """
            INSERT INTO http_cache(namespace, cache_key, value, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(namespace, cache_key)
            DO UPDATE SET value = excluded.value, created_at = excluded.created_at
            """,
            (namespace, key, json.dumps(value, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class OrderResolver:
    def __init__(self, cache: SQLiteJsonCache) -> None:
        self.cache = cache
        self.ncbi_api_key = os.environ.get("NCBI_API_KEY", DEFAULT_NCBI_API_KEY)
        self.ncbi_email = os.environ.get("NCBI_EMAIL", "")
        self.memo: dict[tuple[str, str], dict[str, str]] = {}

    def _fetch_text(self, url: str, params: dict[str, str] | None = None) -> str:
        full_url = url
        if params:
            full_url = f"{url}?{urllib.parse.urlencode(sorted(params.items()))}"
        request = urllib.request.Request(
            full_url,
            headers={"User-Agent": "pdb-clean-order/1.0"},
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read().decode("utf-8")
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(str(last_error) if last_error else "request failed")

    def _fetch_json(self, url: str) -> dict:
        text = self._fetch_text(url)
        return json.loads(text)

    @staticmethod
    def _order_from_ncbi_taxon(taxon: ET.Element) -> dict[str, str]:
        taxid = (taxon.findtext("TaxId") or "").strip()
        scientific_name = (taxon.findtext("ScientificName") or "").strip()
        rank = (taxon.findtext("Rank") or "").strip().lower()
        if rank == "order":
            return {"order": scientific_name, "oid": taxid}

        for node in taxon.findall("./LineageEx/Taxon"):
            node_rank = (node.findtext("Rank") or "").strip().lower()
            if node_rank == "order":
                return {
                    "order": (node.findtext("ScientificName") or "").strip(),
                    "oid": (node.findtext("TaxId") or "").strip(),
                }
        return {"order": "", "oid": ""}

    def resolve_ncbi_many(self, taxids: set[str]) -> None:
        pending = []
        for taxid in sorted(taxids):
            key = ("ncbi", taxid)
            if key in self.memo:
                continue
            cached = self.cache.get("order_enrich_ncbi", taxid)
            if cached is not None:
                self.memo[key] = cached
                continue
            pending.append(taxid)

        for start in range(0, len(pending), 100):
            batch = pending[start : start + 100]
            params = {
                "db": "taxonomy",
                "id": ",".join(batch),
                "retmode": "xml",
            }
            if self.ncbi_api_key:
                params["api_key"] = self.ncbi_api_key
            if self.ncbi_email:
                params["email"] = self.ncbi_email
                params["tool"] = "pdb_clean_add_order"
            root = ET.fromstring(self._fetch_text(NCBI_EFETCH_URL, params))
            found = set()
            for taxon in root.findall("./Taxon"):
                taxid = (taxon.findtext("TaxId") or "").strip()
                value = self._order_from_ncbi_taxon(taxon)
                self.memo[("ncbi", taxid)] = value
                self.cache.set("order_enrich_ncbi", taxid, value)
                found.add(taxid)
            for taxid in batch:
                if taxid in found:
                    continue
                value = {"order": "", "oid": ""}
                self.memo[("ncbi", taxid)] = value
                self.cache.set("order_enrich_ncbi", taxid, value)

    def resolve_gbif(self, usage_key: str) -> dict[str, str]:
        key = ("gbif", usage_key)
        if key in self.memo:
            return self.memo[key]
        cached = self.cache.get("order_enrich_gbif", usage_key)
        if cached is not None:
            self.memo[key] = cached
            return cached

        payload = self._fetch_json(GBIF_SPECIES_URL.format(usage_key=usage_key))
        value = {
            "order": str(payload.get("order") or "").strip(),
            "oid": str(payload.get("orderKey") or "").strip(),
        }
        self.memo[key] = value
        self.cache.set("order_enrich_gbif", usage_key, value)
        return value

    def resolve_inat(self, taxon_id: str) -> dict[str, str]:
        key = ("inaturalist", taxon_id)
        if key in self.memo:
            return self.memo[key]
        cached = self.cache.get("order_enrich_inaturalist", taxon_id)
        if cached is not None:
            self.memo[key] = cached
            return cached

        payload = self._fetch_json(INAT_TAXA_URL.format(taxon_id=taxon_id))
        result = (payload.get("results") or [{}])[0]
        if (result.get("rank") or "").strip().lower() == "order":
            value = {
                "order": str(result.get("name") or "").strip(),
                "oid": str(result.get("id") or "").strip(),
            }
        else:
            value = {"order": "", "oid": ""}
            for node in result.get("ancestors") or []:
                if str(node.get("rank") or "").strip().lower() != "order":
                    continue
                value = {
                    "order": str(node.get("name") or "").strip(),
                    "oid": str(node.get("id") or "").strip(),
                }
                break
        self.memo[key] = value
        self.cache.set("order_enrich_inaturalist", taxon_id, value)
        return value

    def resolve(self, source: str, lookup_id: str) -> dict[str, str]:
        if not source or not lookup_id:
            return {"order": "", "oid": ""}
        key = (source, lookup_id)
        if key in self.memo:
            return self.memo[key]
        if source == "ncbi":
            self.resolve_ncbi_many({lookup_id})
            return self.memo.get(key, {"order": "", "oid": ""})
        if source == "gbif":
            return self.resolve_gbif(lookup_id)
        if source == "inaturalist":
            return self.resolve_inat(lookup_id)
        return {"order": "", "oid": ""}


def build_output_fieldnames(fieldnames: list[str]) -> list[str]:
    existing = [field for field in fieldnames if field not in {"order", "oid"}]
    if "family" in existing:
        index = existing.index("family")
        return existing[:index] + ["order", "oid"] + existing[index:]
    return existing + ["order", "oid"]


def load_rows(path: Path) -> tuple[list[dict[str, str]], list[str], str, str]:
    delimiter = detect_delimiter(path)
    encoding = detect_encoding(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError(f"CSV/TSV has no header: {path}")
        rows = [{key: (value or "") for key, value in row.items()} for row in reader]
    return rows, list(reader.fieldnames), delimiter, encoding


def write_rows(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str],
    delimiter: str,
    encoding: str,
) -> None:
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add order/oid columns to clean taxonomy CSV/TSV files."
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        type=Path,
        nargs="+",
        required=True,
        help="Input clean CSV/TSV files",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_CACHE_DB,
        help=f"SQLite cache DB path (default: {DEFAULT_CACHE_DB})",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Rewrite each input file in place",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Write enriched files to this directory instead of overwriting inputs",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Recompute rows that already have non-empty order/oid",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.in_place and args.output_dir is None:
        raise ValueError("Use --in-place or provide --output-dir")

    cache = SQLiteJsonCache(args.cache_db)
    resolver = OrderResolver(cache)

    loaded_files = []
    ncbi_taxids: set[str] = set()

    for input_path in args.inputs:
        rows, fieldnames, delimiter, encoding = load_rows(input_path)
        lookups = []
        for row in rows:
            if (
                not args.overwrite_existing
                and (row.get("order") or "").strip()
                and (row.get("oid") or "").strip()
            ):
                lookups.append(("", ""))
                continue
            source, lookup_id = extract_lookup(row)
            lookups.append((source, lookup_id))
            if source == "ncbi" and lookup_id:
                ncbi_taxids.add(lookup_id)
        loaded_files.append((input_path, rows, fieldnames, delimiter, encoding, lookups))

    resolver.resolve_ncbi_many(ncbi_taxids)

    for input_path, rows, fieldnames, delimiter, encoding, lookups in loaded_files:
        output_rows = []
        updated_rows = 0
        resolved_rows = 0
        for row, (source, lookup_id) in zip(rows, lookups, strict=True):
            out_row = dict(row)
            if "order" not in out_row:
                out_row["order"] = ""
            if "oid" not in out_row:
                out_row["oid"] = ""
            if (
                args.overwrite_existing
                or not (out_row.get("order") or "").strip()
                or not (out_row.get("oid") or "").strip()
            ):
                value = resolver.resolve(source, lookup_id)
                if value.get("order") or value.get("oid"):
                    resolved_rows += 1
                if args.overwrite_existing or not (out_row.get("order") or "").strip():
                    out_row["order"] = value.get("order", "")
                if args.overwrite_existing or not (out_row.get("oid") or "").strip():
                    out_row["oid"] = value.get("oid", "")
                if out_row["order"] != row.get("order", "") or out_row["oid"] != row.get("oid", ""):
                    updated_rows += 1
            output_rows.append(out_row)

        output_fieldnames = build_output_fieldnames(fieldnames)
        if args.in_place:
            output_path = input_path
        else:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = args.output_dir / input_path.name
        write_rows(output_path, output_rows, output_fieldnames, delimiter, encoding)
        print(
            "[done] "
            f"{output_path} "
            f"rows={len(output_rows)} "
            f"updated={updated_rows} "
            f"resolved={resolved_rows}"
        )

    cache.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
