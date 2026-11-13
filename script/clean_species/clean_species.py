# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx",
#   "openpyxl",
# ]
# ///

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from openpyxl import load_workbook


# =========================
# 输出列与固定配置
# =========================

EXPECTED_COLUMNS = [
    "origin Species",
    "family",
    "fid",
    "subfamliy",
    "sfid",
    "genuis",
    "gid",
    "species",
    "authority",
    "basionym",
    "sid",
    "source",
    "source link",
    "note",
]
INPUT_COLUMN_ALIASES = {
    "source": ["source (ncbi gbif)"],
}


NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
NCBI_BROWSER_URL = "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi"
GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
GBIF_SPECIES_URL = "https://api.gbif.org/v1/species/{usage_key}"
INAT_AUTOCOMPLETE_URL = "https://api.inaturalist.org/v1/taxa/autocomplete"
INAT_TAXA_URL = "https://api.inaturalist.org/v1/taxa/{taxon_id}"
INAT_LEPIDOPTERA_TAXON_ID = 47157
DEFAULT_NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")

# 多个 review 事件同时存在时，优先采用更接近最终人工判断的那条。
REVIEW_STAGE_PRIORITY = {
    "gbif_species": 1,
    "inat_species": 2,
    "gbif_subfamily_backfill": 3,
    "gbif_subfamily_backfill_ncbi_genus": 3,
    "ncbi_genus_fallback": 4,
    "runtime": 9,
}
REVIEW_OUTPUT_COLUMNS = [
    "origin Species",
    "family",
    "fid",
    "subfamliy",
    "genuis",
    "species",
    "source",
    "source link",
    "note",
]
REVIEW_BUCKET_LABELS = {
    "review_subfamily": "亚科待补",
    "review_genus": "物种待定",
    "review_none": "完全未定",
}


# =========================
# 基础文本工具
# =========================

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def norm_lower(text: str) -> str:
    return norm_space(text).lower()


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance between two strings."""
    if len(a) < len(b):
        return edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(
                min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1))
            )
        prev = curr
    return prev[-1]


def is_fuzzy_match(query: str, candidate: str, max_dist: int = 2) -> bool:
    """True if names match exactly or differ by <= max_dist characters."""
    q = norm_lower(query)
    c = norm_lower(candidate)
    if q == c:
        return True
    return edit_distance(q, c) <= max_dist


def to_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_binomial(text: str) -> str:
    tokens = [x for x in re.split(r"\s+", norm_space(text)) if x]
    if len(tokens) >= 2:
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0] if tokens else ""


def first_token(text: str) -> str:
    tokens = [x for x in re.split(r"\s+", norm_space(text)) if x]
    return tokens[0] if tokens else ""


def has_dotted_species_marker(text: str) -> bool:
    return "." in norm_space(text)


def strip_scientific_name_prefix(text: str, scientific_name: str) -> str:
    text = norm_space(text)
    scientific_name = norm_space(scientific_name)
    if not text or not scientific_name:
        return text
    variants = [scientific_name, f'"{scientific_name}"', f"'{scientific_name}'"]
    for variant in variants:
        if norm_lower(text) == norm_lower(variant):
            return ""
        prefix_re = re.compile(rf"^{re.escape(variant)}\s+", flags=re.IGNORECASE)
        stripped = prefix_re.sub("", text, count=1).strip()
        if stripped != text:
            return stripped
    return text


def cleanup_authority_text(text: str) -> str:
    text = norm_space(text).replace("(", " ").replace(")", " ")
    text = re.sub(r"\s+([,;:])", r"\1", text)
    return norm_space(text).strip("'\" ")


def normalize_authority_text(text: str, scientific_name: str = "") -> str:
    text = norm_space(text)
    if scientific_name:
        stripped = strip_scientific_name_prefix(text, scientific_name)
        if stripped and stripped != text:
            text = stripped
    if not text:
        return ""
    if re.fullmatch(r"\([^)]*\)", text):
        return cleanup_authority_text(text)
    author_token_head = r"[A-Z][A-Za-z.'-]*"
    author_token_tail = r"(?:[A-Z][A-Za-z.'-]*|de|la|van|von|da|del)"
    match = re.search(
        rf"((?:\([^)]*\)\s*)?{author_token_head}(?:\s+{author_token_tail}){{0,3}},\s*\d{{4}}\)?)$",
        text,
    )
    if match:
        text = norm_space(match.group(1))
    return cleanup_authority_text(text)


def html_to_text(text: str) -> str:
    text = html.unescape(to_str(text)).replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    return norm_space(text)


def append_note(existing: str, note: str) -> str:
    existing = to_str(existing)
    note = to_str(note)
    if not existing:
        return note
    if not note:
        return existing
    if note in existing.split(" | "):
        return existing
    return f"{existing} | {note}"


def remove_notes(existing: str, *notes: str) -> str:
    existing = to_str(existing)
    if not existing:
        return ""
    blocked = {to_str(note) for note in notes if to_str(note)}
    kept = [token.strip() for token in existing.split(" | ") if token.strip() and token.strip() not in blocked]
    return " | ".join(kept)


def merge_links(existing: str, new_link: str) -> str:
    existing = to_str(existing)
    new_link = to_str(new_link)
    if not existing:
        return new_link
    if not new_link:
        return existing
    seen = {x.strip() for x in existing.split(";") if x.strip()}
    if new_link in seen:
        return existing
    return f"{existing};{new_link}"


def split_links(text: str) -> list[str]:
    return [x.strip() for x in to_str(text).split(";") if x.strip()]


def merge_link_values(*values: str) -> str:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        for link in split_links(value):
            if link in seen:
                continue
            seen.add(link)
            ordered.append(link)
    return ";".join(ordered)


# =========================
# Review 输出辅助
# =========================

def review_stage_source(stage: str) -> str:
    stage = to_str(stage)
    if stage.startswith("inat"):
        return "iNaturalist"
    if stage.startswith("gbif_subfamily_backfill"):
        return "GBIF/NCBI/iNaturalist"
    if stage.startswith("gbif"):
        return "GBIF"
    if stage.startswith("ncbi"):
        return "NCBI"
    if stage == "runtime":
        return "runtime"
    return ""


def review_reason_human(bucket: str, reason: str) -> str:
    if bucket == "review_subfamily":
        return "no subfamily"
    if bucket == "review_genus":
        return "genus ambiguous"
    reason = to_str(reason)
    return "no match"


def candidate_link(source: str, candidate_id: str) -> str:
    source = norm_lower(source)
    candidate_id = to_str(candidate_id)
    if not candidate_id:
        return ""
    if source == "gbif":
        return f"https://www.gbif.org/species/{candidate_id}"
    if source in {"inat", "inaturalist"}:
        return f"https://www.inaturalist.org/taxa/{candidate_id}"
    if source == "ncbi":
        return f"https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={candidate_id}"
    return ""


def parse_review_recommended_candidate(
    recommended_candidate: str, stage: str, source_links: str
) -> dict[str, str]:
    recommended_candidate = to_str(recommended_candidate)
    if not recommended_candidate:
        return {}
    parts = [to_str(x) for x in recommended_candidate.split("|")]
    candidate_id = parts[0] if parts else ""
    name = parts[1] if len(parts) >= 2 else recommended_candidate
    source = review_stage_source(stage)
    if not source and "gbif" in norm_lower(source_links):
        source = "GBIF"
    link = candidate_link(source, candidate_id) or to_str(source_links)
    return {
        "source": source,
        "candidate_id": candidate_id,
        "name": name,
        "link": link,
    }


def review_sort_key(review: "ReviewItem") -> tuple[int, int, str]:
    stage = to_str(review.stage)
    priority = REVIEW_STAGE_PRIORITY.get(stage, 99)
    has_recommended = 1 if to_str(review.recommended_candidate) else 0
    return (priority, -has_recommended, "")


def select_primary_review(reviews: list["ReviewItem"]) -> "ReviewItem" | None:
    if not reviews:
        return None
    return sorted(reviews, key=review_sort_key)[0]


def build_review_context(
    bucket: str,
    reviews: list["ReviewItem"],
) -> dict[str, str]:
    primary = select_primary_review(reviews)
    recommended = (
        parse_review_recommended_candidate(
            primary.recommended_candidate, primary.stage, primary.source_links
        )
        if primary
        else {}
    )
    return {
        "review_bucket": REVIEW_BUCKET_LABELS.get(bucket, bucket),
        "review_reason_human": review_reason_human(
            bucket,
            primary.reason if primary else "",
        ),
        "review_recommended_candidate": to_str(recommended.get("name")),
        "review_recommended_link": to_str(recommended.get("link")),
    }


def build_review_output_row(row: dict[str, str], context: dict[str, str]) -> dict[str, str]:
    return {
        "origin Species": to_str(row.get("origin Species")),
        "family": to_str(row.get("family")),
        "fid": to_str(row.get("fid")),
        "subfamliy": to_str(row.get("subfamliy")),
        "genuis": to_str(row.get("genuis")),
        "species": to_str(row.get("species")),
        "source": to_str(row.get("source")),
        "source link": to_str(row.get("source link")),
        "note": to_str(context.get("review_reason_human")),
    }


def build_review_none_output_row(row: dict[str, str], context: dict[str, str]) -> dict[str, str]:
    return {
        "origin Species": to_str(row.get("origin Species")),
        "family": to_str(row.get("family")),
        "fid": to_str(row.get("fid")),
        "subfamliy": to_str(row.get("subfamliy")),
        "genuis": to_str(row.get("genuis")),
        "species": to_str(row.get("species")),
        "source": to_str(row.get("source")),
        "source link": to_str(row.get("source link")),
        "note": to_str(context.get("review_reason_human")),
    }


def build_recommended_taxonomy_fill(
    primary_review: "ReviewItem" | None,
    ncbi: "NCBIClient",
    gbif: "GBIFClient",
    inat: "INaturalistClient",
) -> dict[str, str]:
    if primary_review is None:
        return {}

    recommended = parse_review_recommended_candidate(
        primary_review.recommended_candidate,
        primary_review.stage,
        primary_review.source_links,
    )
    source = norm_lower(recommended.get("source", ""))
    candidate_id = to_str(recommended.get("candidate_id"))
    if not source or not candidate_id:
        return {}

    try:
        if source == "gbif":
            payload = gbif.fetch_species(candidate_id)
            species_name = (
                to_str(payload.get("species"))
                or to_str(payload.get("canonicalName"))
                or parse_binomial(to_str(payload.get("scientificName")))
            )
            return {
                "family": to_str(payload.get("family")),
                "fid": to_str(payload.get("familyKey")),
                "subfamliy": to_str(payload.get("subfamily")),
                "sfid": to_str(payload.get("subfamilyKey")),
                "genuis": to_str(payload.get("genus")),
                "gid": to_str(payload.get("genusKey")),
                "species": species_name,
                "sid": to_str(payload.get("speciesKey") or payload.get("key")),
                "source": "gbif",
                "source link": candidate_link("gbif", candidate_id),
            }

        if source in {"inat", "inaturalist"}:
            taxon = inat.fetch_taxon(candidate_id)
            if not taxon or not inat._is_lepidoptera_taxon(taxon):
                return {}
            family, fid = TaxonomyResolver._extract_inat_rank(taxon, "family")
            subfamily, sfid = TaxonomyResolver._extract_inat_rank(taxon, "subfamily")
            genus, gid = TaxonomyResolver._extract_inat_rank(taxon, "genus")
            return {
                "family": family or "",
                "fid": fid or "",
                "subfamliy": subfamily or "",
                "sfid": sfid or "",
                "genuis": genus or "",
                "gid": gid or "",
                "species": to_str(taxon.get("name")),
                "sid": candidate_id,
                "source": "inaturalist",
                "source link": candidate_link("inaturalist", candidate_id),
            }

        if source == "ncbi":
            taxa = ncbi.fetch_taxa([candidate_id])
            if not taxa:
                return {}
            taxon = taxa[0]
            rank = norm_lower(taxon.get("rank", ""))
            family, fid = ncbi.extract_rank(taxon, "family")
            subfamily, _ = ncbi.extract_rank(taxon, "subfamily")
            subfamily, sfid = ncbi.extract_rank(taxon, "subfamily")
            genus, gid = ncbi.extract_rank(taxon, "genus")
            species_name = to_str(taxon.get("scientific_name")) if rank == "species" else ""
            genus_name = to_str(taxon.get("scientific_name")) if rank == "genus" else (genus or "")
            return {
                "family": family or "",
                "fid": fid or "",
                "subfamliy": subfamily or "",
                "sfid": sfid or "",
                "genuis": genus_name,
                "gid": gid or "",
                "species": species_name,
                "sid": candidate_id if rank == "species" else "",
                "source": "ncbi",
                "source link": candidate_link("ncbi", candidate_id),
            }
    except Exception:  # noqa: BLE001
        return {}

    return {}


def enrich_review_row_with_recommendation(
    row: dict[str, str],
    reviews: list["ReviewItem"],
    ncbi: "NCBIClient",
    gbif: "GBIFClient",
    inat: "INaturalistClient",
) -> dict[str, str]:
    primary_review = select_primary_review(reviews)
    candidate_fill = build_recommended_taxonomy_fill(primary_review, ncbi=ncbi, gbif=gbif, inat=inat)
    if not candidate_fill:
        return row

    out = dict(row)
    for col in [
        "family",
        "fid",
        "subfamliy",
        "sfid",
        "genuis",
        "gid",
        "species",
        "sid",
        "source",
        "source link",
    ]:
        if not to_str(out.get(col)) and to_str(candidate_fill.get(col)):
            out[col] = to_str(candidate_fill[col])
    return out


def normalize_promoted_clean_row(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    out["note"] = remove_notes(out.get("note", ""), "no_match", "review_needed")
    return out


def build_subfamily_review_items(
    origin_species: str,
    row: dict[str, str],
    reviews: list["ReviewItem"],
) -> list["ReviewItem"]:
    if reviews:
        return reviews
    # 物种已定但亚科最终仍为空时，补一条简洁的 review 原因。
    return [
        ReviewItem(
            origin_species=origin_species,
            stage="gbif_subfamily_backfill",
            reason="subfamily_missing_after_same_genus_ncbi_inat",
            source_links=merge_link_values(
                row.get("source link", ""),
                "https://www.ncbi.nlm.nih.gov/taxonomy",
                "https://www.inaturalist.org/taxa",
            ),
        )
    ]


# =========================
# 数据结构与网络层
# =========================

@dataclass
class ReviewItem:
    origin_species: str
    stage: str
    reason: str
    recommended_candidate: str = ""
    source_links: str = ""


@dataclass
class Resolution:
    fill: dict[str, str]
    reviews: list[ReviewItem]
    status: str


class SQLiteCache:
    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(str(cache_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
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

    def get(self, namespace: str, cache_key: str) -> str | None:
        with self.lock:
            cur = self.conn.execute(
                "SELECT value FROM http_cache WHERE namespace = ? AND cache_key = ?",
                (namespace, cache_key),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def set(self, namespace: str, cache_key: str, value: str) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO http_cache(namespace, cache_key, value, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key)
                DO UPDATE SET value = excluded.value, created_at = excluded.created_at
                """,
                (namespace, cache_key, value, now_iso()),
            )
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class HttpClient:
    def __init__(
        self,
        cache: SQLiteCache,
        ncbi_api_key: str | None,
        ncbi_email: str | None = None,
        timeout_sec: float = 30.0,
        max_attempts: int = 5,
    ) -> None:
        self.cache = cache
        self.max_attempts = max_attempts
        self.client = httpx.Client(timeout=timeout_sec, follow_redirects=True)
        self.insecure_client = httpx.Client(timeout=timeout_sec, follow_redirects=True, verify=False)
        self.last_call: dict[str, float] = {}
        self.throttle_lock = threading.Lock()
        self.min_interval = {
            "ncbi": 0.11 if ncbi_api_key else 0.34,
            "ncbi_browser": 0.10,
            "gbif": 0.10,
            "inaturalist": 0.70,
        }
        self.ncbi_api_key = to_str(ncbi_api_key)
        self.ncbi_email = to_str(ncbi_email)

    def close(self) -> None:
        self.client.close()
        self.insecure_client.close()

    def _throttle(self, namespace: str) -> None:
        min_wait = self.min_interval.get(namespace, 0.0)
        if min_wait <= 0:
            return
        with self.throttle_lock:
            last = self.last_call.get(namespace, 0.0)
            elapsed = time.monotonic() - last
            if elapsed < min_wait:
                time.sleep(min_wait - elapsed)
            self.last_call[namespace] = time.monotonic()

    @staticmethod
    def _key(url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return url
        encoded = urlencode(sorted((k, str(v)) for k, v in params.items()))
        return f"{url}?{encoded}"

    def get_json(
        self,
        namespace: str,
        url: str,
        params: dict[str, Any] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        cache_key = self._key(url, params)
        if use_cache:
            cached = self.cache.get(namespace, cache_key)
            if cached is not None:
                return json.loads(cached)

        payload = self._request(namespace, url, params=params).json()
        if use_cache:
            self.cache.set(namespace, cache_key, json.dumps(payload, ensure_ascii=False))
        return payload

    def get_text(
        self,
        namespace: str,
        url: str,
        params: dict[str, Any] | None = None,
        use_cache: bool = True,
    ) -> str:
        cache_key = self._key(url, params)
        if use_cache:
            cached = self.cache.get(namespace, cache_key)
            if cached is not None:
                return cached

        text = self._request(namespace, url, params=params).text
        if use_cache:
            self.cache.set(namespace, cache_key, text)
        return text

    def _request(
        self, namespace: str, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        params = dict(params or {})
        if namespace == "ncbi" and self.ncbi_api_key:
            params["api_key"] = self.ncbi_api_key
        if namespace == "ncbi" and self.ncbi_email:
            params["email"] = self.ncbi_email
            params.setdefault("tool", "clean_species_cleaner")

        use_insecure_client = False
        for attempt in range(1, self.max_attempts + 1):
            resp: httpx.Response | None = None
            try:
                self._throttle(namespace)
                client = self.insecure_client if use_insecure_client else self.client
                resp = client.get(url, params=params)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                if resp.status_code >= 400:
                    resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                if (
                    not use_insecure_client
                    and isinstance(exc, httpx.TransportError)
                    and "certificate verify failed" in str(exc).lower()
                ):
                    use_insecure_client = True
                    continue
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                    if exc.response.status_code not in {429, 500, 502, 503, 504}:
                        raise
                if attempt == self.max_attempts:
                    raise
                retry_after = 0.0
                if resp is not None:
                    ra = resp.headers.get("Retry-After", "")
                    if ra.isdigit():
                        retry_after = float(ra)
                backoff = retry_after or (0.5 * (2 ** (attempt - 1))) + random.uniform(
                    0, 0.25
                )
                time.sleep(backoff)
        raise RuntimeError("unreachable")


# =========================
# 分类源客户端
# =========================

class NCBIClient:
    def __init__(self, http: HttpClient):
        self.http = http

    @staticmethod
    def browser_url(taxid: str) -> str:
        return f"{NCBI_BROWSER_URL}?id={taxid}&lvl=0"

    def search_ids(self, name: str, strict_exact: bool) -> list[str]:
        term = f"\"{name}\"[SCIN]" if strict_exact else f"{name}[SCIN]"
        params = {"db": "taxonomy", "retmode": "json", "term": term}
        payload = self.http.get_json("ncbi", NCBI_ESEARCH_URL, params=params)
        return payload.get("esearchresult", {}).get("idlist", [])

    def fetch_taxa(self, taxids: list[str]) -> list[dict[str, Any]]:
        if not taxids:
            return []
        params = {"db": "taxonomy", "id": ",".join(taxids), "retmode": "xml"}
        xml_text = self.http.get_text("ncbi", NCBI_EFETCH_URL, params=params)
        root = ET.fromstring(xml_text)
        taxa = []
        for taxon in root.findall("./Taxon"):
            taxid = to_str(taxon.findtext("TaxId"))
            scientific_name = to_str(taxon.findtext("ScientificName"))
            rank = to_str(taxon.findtext("Rank")).lower()
            authority = ""
            for other_name in taxon.findall("./OtherNames/Name"):
                class_code = norm_lower(to_str(other_name.findtext("ClassCDE")))
                if class_code != "authority":
                    continue
                authority = normalize_authority_text(
                    to_str(other_name.findtext("DispName")), scientific_name
                )
                if authority:
                    break
            lineage_entries = []
            for node in taxon.findall("./LineageEx/Taxon"):
                lineage_entries.append(
                    {
                        "taxid": to_str(node.findtext("TaxId")),
                        "scientific_name": to_str(node.findtext("ScientificName")),
                        "rank": to_str(node.findtext("Rank")).lower(),
                    }
                )
            lineage_entries.append(
                {"taxid": taxid, "scientific_name": scientific_name, "rank": rank}
            )
            taxa.append(
                {
                    "taxid": taxid,
                    "scientific_name": scientific_name,
                    "rank": rank,
                    "authority": authority,
                    "lineage": lineage_entries,
                }
            )
        return taxa

    def fetch_basionym(self, taxid: str) -> str:
        taxid = to_str(taxid)
        if not taxid:
            return ""
        html_text = self.http.get_text(
            "ncbi_browser",
            NCBI_BROWSER_URL,
            params={"id": taxid, "lvl": 0},
        )
        return self.parse_basionym(html_text)

    @staticmethod
    def parse_basionym(html_text: str) -> str:
        html_text = to_str(html_text)
        if not html_text:
            return ""
        match = re.search(
            r'<div class="nomrel">\s*basionym:\s*</div>\s*<div class="subname">\s*(.*?)\s*</div>\s*</div>',
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        return html_to_text(match.group(1))

    @staticmethod
    def extract_rank(
        taxon: dict[str, Any], rank_name: str
    ) -> tuple[str, str] | tuple[None, None]:
        rank_name = rank_name.lower()
        for node in reversed(taxon.get("lineage", [])):
            if to_str(node.get("rank")).lower() == rank_name:
                return to_str(node.get("scientific_name")), to_str(node.get("taxid"))
        return None, None

    @staticmethod
    def species_name_match(query: str, candidate_name: str, fuzzy_max_dist: int = 0) -> tuple[bool, bool]:
        """Return (matched, is_fuzzy). is_fuzzy is True when matched via edit distance."""
        q = norm_lower(query)
        c = norm_lower(candidate_name)
        if q == c:
            return True, False
        if parse_binomial(q) == parse_binomial(c) and parse_binomial(q) != "":
            return True, False
        if fuzzy_max_dist > 0 and is_fuzzy_match(query, candidate_name, fuzzy_max_dist):
            return True, True
        return False, False

    def resolve_species(
        self, species_name: str, fuzzy_max_dist: int = 0
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], str]:
        """Returns (status, taxon, candidates, fuzzy_note).
        fuzzy_note is non-empty when fuzzy matching was used."""
        ids = self.search_ids(species_name, strict_exact=True)
        if not ids:
            ids = self.search_ids(species_name, strict_exact=False)
        if not ids:
            return "no_match", None, [], ""

        taxa = self.fetch_taxa(ids)
        exact = []
        for t in taxa:
            if t.get("rank") != "species":
                continue
            matched, _ = self.species_name_match(species_name, t.get("scientific_name", ""))
            if matched:
                exact.append(t)
        if len(exact) == 1:
            return "matched", exact[0], taxa, ""
        if len(exact) > 1:
            return "ambiguous", None, taxa, ""

        close = []
        for t in taxa:
            matched, _ = self.species_name_match(species_name, t.get("scientific_name", ""))
            if matched:
                close.append(t)
        if len(close) == 1:
            return "matched", close[0], taxa, ""
        if len(close) > 1 or len(taxa) > 1:
            return "ambiguous", None, taxa, ""

        # Try fuzzy matching if enabled
        if fuzzy_max_dist > 0:
            fuzzy = []
            for t in taxa:
                if t.get("rank") != "species":
                    continue
                matched, is_fuzzy = self.species_name_match(
                    species_name, t.get("scientific_name", ""), fuzzy_max_dist
                )
                if matched and is_fuzzy:
                    fuzzy.append(t)
            if len(fuzzy) == 1:
                corrected = fuzzy[0].get("scientific_name", "")
                note = f"fuzzy_match_accepted({species_name}->{corrected})"
                return "matched", fuzzy[0], taxa, note

        return "no_match", None, taxa, ""

    def resolve_genus(
        self, genus_name: str
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        ids = self.search_ids(genus_name, strict_exact=True)
        if not ids:
            ids = self.search_ids(genus_name, strict_exact=False)
        if not ids:
            return "no_match", None, []

        taxa = self.fetch_taxa(ids)
        exact = [
            t
            for t in taxa
            if t.get("rank") == "genus"
            and norm_lower(t.get("scientific_name", "")) == norm_lower(genus_name)
        ]
        if len(exact) == 1:
            return "matched", exact[0], taxa
        if len(exact) > 1:
            return "ambiguous", None, taxa

        if len(taxa) > 1:
            return "ambiguous", None, taxa
        return "no_match", None, taxa


class GBIFClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def match(self, species_name: str) -> dict[str, Any]:
        return self.http.get_json("gbif", GBIF_MATCH_URL, params={"name": species_name})

    def fetch_species(self, usage_key: int | str) -> dict[str, Any]:
        return self.http.get_json(
            "gbif", GBIF_SPECIES_URL.format(usage_key=usage_key), params=None
        )

    @staticmethod
    def _canonical_candidate_name(payload: dict[str, Any]) -> str:
        name = (
            to_str(payload.get("canonicalName"))
            or to_str(payload.get("species"))
            or parse_binomial(to_str(payload.get("scientificName")))
        )
        return norm_space(name)

    @staticmethod
    def extract_authorship(payload: dict[str, Any]) -> str:
        authorship = norm_space(to_str(payload.get("authorship")))
        if authorship:
            return normalize_authority_text(authorship)
        scientific_name = norm_space(to_str(payload.get("scientificName")))
        canonical_name = GBIFClient._canonical_candidate_name(payload)
        return normalize_authority_text(scientific_name, canonical_name)

    def resolve_species(
        self, species_name: str, fuzzy_max_dist: int = 0
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], str, str]:
        """Returns (status, payload, alternatives, reason, fuzzy_note)."""
        payload = self.match(species_name)
        if not payload:
            return "no_match", None, [], "empty_payload", ""

        usage_key = payload.get("usageKey")
        match_type = to_str(payload.get("matchType")).upper()
        confidence = int(payload.get("confidence", 0) or 0)
        alternatives = payload.get("alternatives") or []

        if not usage_key or match_type == "NONE":
            return "no_match", None, alternatives, "no_usage_key_or_none_match", ""

        canonical = self._canonical_candidate_name(payload)
        canonical_ok = norm_lower(canonical) == norm_lower(species_name)
        fuzzy_note = ""
        if not canonical_ok and fuzzy_max_dist > 0:
            if is_fuzzy_match(canonical, species_name, fuzzy_max_dist):
                canonical_ok = True
                fuzzy_note = f"fuzzy_match_accepted({species_name}->{canonical})"
        exact_ok = match_type == "EXACT" and confidence >= 95 and canonical_ok
        alt_ok = len(alternatives) == 0

        if exact_ok and alt_ok:
            details = self.fetch_species(usage_key)
            return "matched", details, alternatives, "exact", fuzzy_note

        reason_parts = []
        if match_type != "EXACT":
            reason_parts.append(f"matchType={match_type}")
        if confidence < 95:
            reason_parts.append(f"confidence={confidence}")
        if not canonical_ok:
            reason_parts.append("canonical_not_equal")
        if not alt_ok:
            reason_parts.append(f"alternatives={len(alternatives)}")
        return "ambiguous", payload, alternatives, ",".join(reason_parts) or "ambiguous", ""


class INaturalistClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def search_species(self, name: str) -> list[dict[str, Any]]:
        payload = self.http.get_json(
            "inaturalist",
            INAT_AUTOCOMPLETE_URL,
            params={
                "q": name,
                "taxon_id": INAT_LEPIDOPTERA_TAXON_ID,
                "rank": "species",
                "per_page": 10,
            },
        )
        results = payload.get("results") or []
        return [
            item
            for item in results
            if isinstance(item, dict) and norm_lower(to_str(item.get("rank"))) == "species"
        ]

    def fetch_taxon(self, taxon_id: int | str) -> dict[str, Any] | None:
        payload = self.http.get_json(
            "inaturalist",
            INAT_TAXA_URL.format(taxon_id=taxon_id),
            params=None,
        )
        results = payload.get("results") or []
        if not isinstance(results, list) or not results:
            return None
        wanted_id = to_str(taxon_id)
        for item in results:
            if isinstance(item, dict) and to_str(item.get("id")) == wanted_id:
                return item
        first = results[0]
        return first if isinstance(first, dict) else None

    @staticmethod
    def _is_lepidoptera_taxon(taxon: dict[str, Any]) -> bool:
        ancestor_ids = [to_str(x) for x in (taxon.get("ancestor_ids") or [])]
        if str(INAT_LEPIDOPTERA_TAXON_ID) in ancestor_ids:
            return True
        for node in taxon.get("ancestors") or []:
            if (
                isinstance(node, dict)
                and norm_lower(to_str(node.get("rank"))) == "order"
                and norm_lower(to_str(node.get("name"))) == "lepidoptera"
            ):
                return True
        return False

    def search_genus(self, name: str) -> list[dict[str, Any]]:
        payload = self.http.get_json(
            "inaturalist",
            INAT_AUTOCOMPLETE_URL,
            params={
                "q": name,
                "taxon_id": INAT_LEPIDOPTERA_TAXON_ID,
                "rank": "genus",
                "per_page": 10,
            },
        )
        results = payload.get("results") or []
        return [
            item
            for item in results
            if isinstance(item, dict) and norm_lower(to_str(item.get("rank"))) == "genus"
        ]

    def resolve_genus(
        self, genus_name: str
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        candidates = self.search_genus(genus_name)
        if not candidates:
            return "no_match", None, []
        exact_active = [
            item
            for item in candidates
            if item.get("is_active") is True
            and norm_lower(to_str(item.get("name"))) == norm_lower(genus_name)
        ]
        if len(exact_active) == 1:
            taxon = self.fetch_taxon(exact_active[0].get("id", ""))
            if taxon and self._is_lepidoptera_taxon(taxon):
                return "matched", taxon, candidates
            return "no_match", None, candidates
        if len(exact_active) > 1:
            return "ambiguous", None, candidates
        return "no_match", None, candidates

    def resolve_species(
        self, species_name: str, fuzzy_max_dist: int = 0
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], str]:
        """Returns (status, taxon, candidates, fuzzy_note)."""
        candidates = self.search_species(species_name)
        if not candidates:
            return "no_match", None, [], ""

        exact_active = [
            item
            for item in candidates
            if item.get("is_active") is True
            and NCBIClient.species_name_match(species_name, to_str(item.get("name")))[0]
        ]
        if len(exact_active) == 1 and len(candidates) == 1:
            taxon = self.fetch_taxon(exact_active[0].get("id", ""))
            if taxon and self._is_lepidoptera_taxon(taxon):
                return "matched", taxon, candidates, ""
            return "no_match", None, candidates, ""

        # Try fuzzy matching if enabled
        if fuzzy_max_dist > 0:
            fuzzy_active = [
                item
                for item in candidates
                if item.get("is_active") is True
                and NCBIClient.species_name_match(
                    species_name, to_str(item.get("name")), fuzzy_max_dist
                )[1]
            ]
            if len(fuzzy_active) == 1:
                taxon = self.fetch_taxon(fuzzy_active[0].get("id", ""))
                if taxon and self._is_lepidoptera_taxon(taxon):
                    corrected = to_str(fuzzy_active[0].get("name"))
                    note = f"fuzzy_match_accepted({species_name}->{corrected})"
                    return "matched", taxon, candidates, note

        return "ambiguous", None, candidates, ""


# =========================
# 主解析流程
# =========================

class TaxonomyResolver:
    def __init__(self, ncbi: NCBIClient, gbif: GBIFClient, inat: INaturalistClient, fuzzy_max_dist: int = 2):
        self.ncbi = ncbi
        self.gbif = gbif
        self.inat = inat
        self.fuzzy_max_dist = fuzzy_max_dist

    @staticmethod
    def blank_fill() -> dict[str, str]:
        return {
            "family": "",
            "fid": "",
            "subfamliy": "",
            "sfid": "",
            "genuis": "",
            "gid": "",
            "species": "",
            "authority": "",
            "basionym": "",
            "sid": "",
            "source": "",
            "source link": "",
            "note": "",
        }

    @staticmethod
    def needs_subfamily_backfill(fill: dict[str, str]) -> bool:
        return (
            norm_lower(fill.get("source", "")) == "gbif"
            and not to_str(fill.get("subfamliy"))
            and bool(to_str(fill.get("genuis")))
        )

    @staticmethod
    def ncbi_url(taxid: str) -> str:
        return f"https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={taxid}"

    @staticmethod
    def gbif_url(usage_key: str | int) -> str:
        return f"https://www.gbif.org/species/{usage_key}"

    @staticmethod
    def inat_url(taxon_id: str | int) -> str:
        return f"https://www.inaturalist.org/taxa/{taxon_id}"

    @staticmethod
    def _pick_unique_lineage_taxon(
        taxa: list[dict[str, Any]], rank_name: str, scientific_name: str
    ) -> dict[str, Any] | None:
        rank_name = norm_lower(rank_name)
        scientific_name = norm_lower(scientific_name)
        matches = []
        for taxon in taxa:
            for node in taxon.get("lineage", []):
                if (
                    norm_lower(node.get("rank", "")) == rank_name
                    and norm_lower(node.get("scientific_name", "")) == scientific_name
                ):
                    matches.append(taxon)
                    break
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _extract_inat_rank(
        taxon: dict[str, Any], rank_name: str
    ) -> tuple[str, str] | tuple[None, None]:
        rank_name = rank_name.lower()
        for node in reversed(taxon.get("ancestors") or []):
            if to_str(node.get("rank")).lower() == rank_name:
                return to_str(node.get("name")), to_str(node.get("id"))
        return None, None

    def _backfill_subfamily_from_ncbi_genus(
        self,
        origin_species: str,
        fill: dict[str, str],
        reviews: list[ReviewItem],
    ) -> bool:
        if fill["subfamliy"] or not fill["genuis"]:
            return False

        b_status, b_taxon, b_candidates = self.ncbi.resolve_genus(fill["genuis"])
        if b_status == "matched" and b_taxon:
            family, fid = self.ncbi.extract_rank(b_taxon, "family")
            subfamily, sfid = self.ncbi.extract_rank(b_taxon, "subfamily")
            if family and fid:
                fill["family"] = family
                fill["fid"] = fid
            if subfamily and sfid:
                fill["subfamliy"] = subfamily
                fill["sfid"] = sfid
                fill["source link"] = merge_links(fill["source link"], self.ncbi_url(b_taxon["taxid"]))
                fill["note"] = append_note(fill["note"], "subfamily_backfilled_from_ncbi_genus")
            return False

        if b_status == "ambiguous":
            lepidoptera_taxon = self._pick_unique_lineage_taxon(
                b_candidates, rank_name="order", scientific_name="Lepidoptera"
            )
            if lepidoptera_taxon:
                family, fid = self.ncbi.extract_rank(lepidoptera_taxon, "family")
                subfamily, sfid = self.ncbi.extract_rank(lepidoptera_taxon, "subfamily")
                if family and fid:
                    fill["family"] = family
                    fill["fid"] = fid
                if subfamily and sfid:
                    fill["subfamliy"] = subfamily
                    fill["sfid"] = sfid
                    fill["source link"] = merge_links(
                        fill["source link"], self.ncbi_url(lepidoptera_taxon["taxid"])
                    )
                    fill["note"] = append_note(fill["note"], "subfamily_backfilled_from_ncbi_genus")
                    return False
                reason = (
                    "lepidoptera_genus_missing_subfamily_when_backfilling_subfamily"
                )
            else:
                reason = "ambiguous_genus_when_backfilling_subfamily"

            fill["note"] = append_note(fill["note"], "subfamily_backfill_needs_review")
            reviews.append(
                ReviewItem(
                    origin_species=origin_species,
                    stage="gbif_subfamily_backfill_ncbi_genus",
                    reason=reason,
                    recommended_candidate=self._recommend_ncbi_candidate(
                        fill["genuis"], b_candidates, expected_rank="genus"
                    ),
                    source_links="https://www.ncbi.nlm.nih.gov/taxonomy",
                )
            )
            return True

        return False

    def resolve(self, origin_species: str) -> Resolution:
        origin_species = norm_space(origin_species)
        fill = self.blank_fill()
        reviews: list[ReviewItem] = []
        fuzzy = self.fuzzy_max_dist

        if has_dotted_species_marker(origin_species):
            fill["note"] = append_note(fill["note"], "skip_dot")
            return Resolution(fill=fill, reviews=reviews, status="skip_dot")

        # --- NCBI species ---
        ncbi_status, ncbi_taxon, ncbi_candidates, ncbi_fuzzy = self.ncbi.resolve_species(
            origin_species, fuzzy_max_dist=fuzzy
        )
        if ncbi_status == "matched" and ncbi_taxon:
            fill = self._build_from_ncbi_species(ncbi_taxon)
            if ncbi_fuzzy:
                fill["note"] = append_note(fill["note"], ncbi_fuzzy)
            return Resolution(fill=fill, reviews=reviews, status="ncbi_match")
        # NCBI ambiguous -> continue to GBIF (no review yet)

        # --- GBIF species ---
        gbif_status, gbif_payload, gbif_alts, gbif_reason, gbif_fuzzy = self.gbif.resolve_species(
            origin_species, fuzzy_max_dist=fuzzy
        )
        if gbif_status == "matched" and gbif_payload:
            fill = self._build_from_gbif(gbif_payload)
            if gbif_fuzzy:
                fill["note"] = append_note(fill["note"], gbif_fuzzy)
            return Resolution(fill=fill, reviews=reviews, status="gbif_match")
        # GBIF ambiguous -> continue to iNaturalist (no review yet)

        # --- iNaturalist species ---
        inat_status, inat_taxon, inat_candidates, inat_fuzzy = self.inat.resolve_species(
            origin_species, fuzzy_max_dist=fuzzy
        )
        if inat_status == "matched" and inat_taxon:
            fill = self._build_from_inat(inat_taxon)
            if inat_fuzzy:
                fill["note"] = append_note(fill["note"], inat_fuzzy)
            return Resolution(fill=fill, reviews=reviews, status="inat_match")

        # --- All three sources failed at species level -> genus fallback first ---
        genus = first_token(origin_species)
        if genus:
            genus_status, genus_taxon, genus_candidates = self.ncbi.resolve_genus(genus)
            if genus_status == "matched" and genus_taxon:
                fill = self._build_from_ncbi_genus_fallback(genus_taxon)
                fill["note"] = append_note(fill["note"], "genus_fallback")
                return Resolution(fill=fill, reviews=reviews, status="genus_fallback")
        else:
            genus_status = "no_match"
            genus_candidates = []

        # 只有物种链走到 iNaturalist 仍失败，且 genus fallback 也失败时才进入 review。
        if inat_status == "ambiguous":
            reviews.append(
                ReviewItem(
                    origin_species=origin_species,
                    stage="inat_species",
                    reason="ambiguous_inat_match:multiple_or_inexact_candidates",
                    recommended_candidate=self._recommend_inat_candidate(
                        origin_species, inat_candidates
                    ),
                    source_links="https://api.inaturalist.org/v1/taxa/autocomplete",
                )
            )
        else:
            reviews.append(
                ReviewItem(
                    origin_species=origin_species,
                    stage="inat_species",
                    reason="no_inat_match_after_ncbi_gbif_failed",
                    source_links="https://api.inaturalist.org/v1/taxa/autocomplete",
                )
            )

        if genus and genus_status == "ambiguous":
            reviews.append(
                ReviewItem(
                    origin_species=origin_species,
                    stage="ncbi_genus_fallback",
                    reason="ambiguous_genus_candidates",
                    recommended_candidate=self._recommend_ncbi_candidate(
                        genus, genus_candidates, expected_rank="genus"
                    ),
                    source_links="https://www.ncbi.nlm.nih.gov/taxonomy",
                )
            )
        elif genus and genus_status == "no_match":
            reviews.append(
                ReviewItem(
                    origin_species=origin_species,
                    stage="ncbi_genus_fallback",
                    reason="no_genus_match_after_species_chain_failed",
                    source_links="https://www.ncbi.nlm.nih.gov/taxonomy",
                )
            )

        fill["note"] = append_note(fill["note"], "no_match")
        fill["note"] = append_note(fill["note"], "review_needed")
        return Resolution(fill=fill, reviews=reviews, status="unresolved_with_review")

    def _build_from_ncbi_species(self, taxon: dict[str, Any]) -> dict[str, str]:
        fill = self.blank_fill()
        family, fid = self.ncbi.extract_rank(taxon, "family")
        subfamily, sfid = self.ncbi.extract_rank(taxon, "subfamily")
        genus, gid = self.ncbi.extract_rank(taxon, "genus")
        fill["family"] = family or ""
        fill["fid"] = fid or ""
        fill["subfamliy"] = subfamily or ""
        fill["sfid"] = sfid or ""
        fill["genuis"] = genus or ""
        fill["gid"] = gid or ""
        fill["species"] = taxon.get("scientific_name", "")
        fill["authority"] = to_str(taxon.get("authority"))
        try:
            fill["basionym"] = self.ncbi.fetch_basionym(taxon.get("taxid", ""))
        except Exception:  # noqa: BLE001
            fill["basionym"] = ""
        fill["sid"] = taxon.get("taxid", "")
        fill["source"] = "ncbi"
        fill["source link"] = self.ncbi_url(taxon.get("taxid", ""))
        return fill

    def _build_from_gbif(self, payload: dict[str, Any]) -> dict[str, str]:
        fill = self.blank_fill()
        usage_key = payload.get("key") or payload.get("usageKey") or payload.get("speciesKey")
        species_name = (
            to_str(payload.get("species"))
            or to_str(payload.get("canonicalName"))
            or parse_binomial(to_str(payload.get("scientificName")))
        )
        family_name = to_str(payload.get("family"))
        genus_name = to_str(payload.get("genus"))
        subfamily_name = to_str(payload.get("subfamily"))
        fill["family"] = family_name
        fill["fid"] = to_str(payload.get("familyKey"))
        fill["species"] = species_name
        fill["authority"] = self.gbif.extract_authorship(payload)
        fill["sid"] = to_str(payload.get("speciesKey") or usage_key)
        fill["genuis"] = genus_name
        fill["gid"] = to_str(payload.get("genusKey"))
        fill["subfamliy"] = subfamily_name
        fill["sfid"] = to_str(payload.get("subfamilyKey"))
        fill["source"] = "gbif"
        fill["source link"] = self.gbif_url(usage_key)
        return fill

    def _build_from_inat(self, taxon: dict[str, Any]) -> dict[str, str]:
        fill = self.blank_fill()
        family, fid = self._extract_inat_rank(taxon, "family")
        subfamily, sfid = self._extract_inat_rank(taxon, "subfamily")
        genus, gid = self._extract_inat_rank(taxon, "genus")
        fill["family"] = family or ""
        fill["fid"] = fid or ""
        fill["subfamliy"] = subfamily or ""
        fill["sfid"] = sfid or ""
        fill["genuis"] = genus or ""
        fill["gid"] = gid or ""
        fill["species"] = to_str(taxon.get("name"))
        fill["authority"] = ""
        fill["basionym"] = ""
        fill["sid"] = to_str(taxon.get("id"))
        fill["source"] = "inaturalist"
        fill["source link"] = self.inat_url(taxon.get("id", ""))
        return fill

    def _build_from_ncbi_genus_fallback(self, taxon: dict[str, Any]) -> dict[str, str]:
        fill = self.blank_fill()
        family, fid = self.ncbi.extract_rank(taxon, "family")
        subfamily, sfid = self.ncbi.extract_rank(taxon, "subfamily")
        fill["family"] = family or ""
        fill["fid"] = fid or ""
        fill["subfamliy"] = subfamily or ""
        fill["sfid"] = sfid or ""
        fill["genuis"] = taxon.get("scientific_name", "")
        fill["gid"] = taxon.get("taxid", "")
        fill["source"] = "ncbi"
        fill["source link"] = self.ncbi_url(taxon.get("taxid", ""))
        return fill

    @staticmethod
    def _recommend_ncbi_candidate(
        query: str, candidates: list[dict[str, Any]], expected_rank: str
    ) -> str:
        if not candidates:
            return ""
        q = norm_lower(query)

        def score(item: dict[str, Any]) -> tuple[int, int]:
            rank = norm_lower(to_str(item.get("rank")))
            sci = norm_lower(to_str(item.get("scientific_name")))
            exact = 1 if sci == q or parse_binomial(sci) == parse_binomial(q) else 0
            rank_match = 1 if rank == expected_rank else 0
            return (exact, rank_match)

        chosen = sorted(candidates, key=score, reverse=True)[0]
        taxid = to_str(chosen.get("taxid"))
        sci = to_str(chosen.get("scientific_name"))
        rank = to_str(chosen.get("rank"))
        return f"{taxid}|{sci}|{rank}"

    @staticmethod
    def _recommend_gbif_candidate(candidates: list[dict[str, Any]]) -> str:
        if not candidates:
            return ""

        def score(item: dict[str, Any]) -> tuple[int, int]:
            mt = to_str(item.get("matchType")).upper()
            conf = int(item.get("confidence", 0) or 0)
            exact = 1 if mt == "EXACT" else 0
            return (exact, conf)

        chosen = sorted(candidates, key=score, reverse=True)[0]
        usage_key = to_str(chosen.get("usageKey") or chosen.get("key"))
        sci = to_str(chosen.get("scientificName") or chosen.get("canonicalName"))
        mt = to_str(chosen.get("matchType"))
        conf = to_str(chosen.get("confidence"))
        return f"{usage_key}|{sci}|{mt}|{conf}"

    @staticmethod
    def _recommend_inat_candidate(query: str, candidates: list[dict[str, Any]]) -> str:
        if not candidates:
            return ""
        q = norm_lower(query)

        def score(item: dict[str, Any]) -> tuple[int, int, int]:
            rank = norm_lower(to_str(item.get("rank")))
            name = norm_lower(to_str(item.get("name")))
            exact = 1 if name == q or parse_binomial(name) == parse_binomial(q) else 0
            active = 1 if item.get("is_active") is True else 0
            rank_match = 1 if rank == "species" else 0
            return (exact, active, rank_match)

        chosen = sorted(candidates, key=score, reverse=True)[0]
        taxon_id = to_str(chosen.get("id"))
        name = to_str(chosen.get("name"))
        rank = to_str(chosen.get("rank"))
        active = "1" if chosen.get("is_active") is True else "0"
        return f"{taxon_id}|{name}|{rank}|{active}"


# =========================
# 文件读写
# =========================

def build_expected_index(raw_headers: list[str]) -> dict[str, int | None]:
    if not any(raw_headers):
        raw_headers = EXPECTED_COLUMNS.copy()

    expected_index: dict[str, int | None] = {col: None for col in EXPECTED_COLUMNS}
    for idx, header in enumerate(raw_headers):
        if header in EXPECTED_COLUMNS:
            expected_index[header] = idx
    for canonical, aliases in INPUT_COLUMN_ALIASES.items():
        if expected_index.get(canonical) is not None:
            continue
        for alias in aliases:
            if alias in raw_headers:
                expected_index[canonical] = raw_headers.index(alias)
                break
    if not any(raw_headers):
        for idx, expected in enumerate(EXPECTED_COLUMNS):
            expected_index[expected] = idx
    return expected_index


def row_from_values(raw: tuple[Any, ...] | list[Any], expected_index: dict[str, int | None]) -> dict[str, str]:
    item = {}
    for col in EXPECTED_COLUMNS:
        idx = expected_index[col]
        item[col] = to_str(raw[idx]) if idx is not None and idx < len(raw) else ""
    return item


def load_xlsx_rows(input_path: Path, sheet_name: str) -> tuple[list[dict[str, str]], list[str]]:
    wb = load_workbook(input_path, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"sheet '{sheet_name}' not found. Available: {wb.sheetnames}")
    ws = wb[sheet_name]

    raw_headers = [to_str(x) for x in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    expected_index = build_expected_index(raw_headers)

    rows: list[dict[str, str]] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        item = row_from_values(raw, expected_index)
        if item["origin Species"]:
            rows.append(item)
    wb.close()
    return rows, EXPECTED_COLUMNS.copy()


def detect_delimiter(input_path: Path) -> str:
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        return ","
    if suffix in {".tsv", ".txt"}:
        return "\t"

    sample = input_path.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        return "\t"


def load_delimited_rows(input_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    delimiter = detect_delimiter(input_path)
    rows: list[dict[str, str]] = []

    with input_path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        try:
            raw_headers = [to_str(x) for x in next(reader)]
        except StopIteration:
            return [], EXPECTED_COLUMNS.copy()

        expected_index = build_expected_index(raw_headers)
        for raw in reader:
            item = row_from_values(raw, expected_index)
            if item["origin Species"]:
                rows.append(item)

    return rows, EXPECTED_COLUMNS.copy()


def load_input_rows(input_path: Path, sheet_name: str) -> tuple[list[dict[str, str]], list[str]]:
    suffix = input_path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        return load_xlsx_rows(input_path, sheet_name)
    if suffix in {".csv", ".tsv", ".txt"}:
        return load_delimited_rows(input_path)
    raise ValueError(
        f"unsupported input format: {input_path.suffix or '<no suffix>'}. "
        "Supported: .xlsx, .xlsm, .xltx, .xltm, .csv, .tsv, .txt"
    )


def write_csv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str],
    encoding: str = "utf-8",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=encoding) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def should_skip_row(row: dict[str, str]) -> bool:
    return all(
        to_str(row.get(col))
        for col in [
            "family",
            "fid",
            "subfamliy",
            "sfid",
            "genuis",
            "gid",
            "species",
            "authority",
            "sid",
            "source",
        ]
    )


def apply_fill(
    row: dict[str, str], fill: dict[str, str], overwrite_existing: bool
) -> dict[str, str]:
    out = dict(row)
    for col in [
        "family",
        "fid",
        "subfamliy",
        "sfid",
        "genuis",
        "gid",
        "species",
        "authority",
        "basionym",
        "sid",
        "source",
    ]:
        new_val = to_str(fill.get(col))
        old_val = to_str(out.get(col))
        if not new_val:
            continue
        if overwrite_existing or not old_val:
            out[col] = new_val

    out["source link"] = merge_links(out.get("source link", ""), fill.get("source link", ""))
    out["note"] = append_note(out.get("note", ""), fill.get("note", ""))
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean species names with NCBI -> GBIF -> iNaturalist -> NCBI genus fallback."
    )
    parser.add_argument(
        "--input-file",
        "--input-xlsx",
        dest="input_file",
        default="Lepidoptera.xlsx",
        help="Input path (.xlsx/.csv/.tsv/.txt)",
    )
    parser.add_argument(
        "--sheet-name",
        default="Sheet1",
        help="Sheet name in XLSX (default: Sheet1)",
    )
    parser.add_argument(
        "--output-prefix",
        default="Lepidoptera",
        help="Output file prefix (e.g. /path/to/Lepidoptera -> Lepidoptera.clean.sp.csv etc.)",
    )
    parser.add_argument(
        "--cache-db",
        default="taxonomy_cache.sqlite",
        help="SQLite cache path",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only first N unique species names (0 means all)",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite existing non-empty taxonomy columns",
    )
    parser.add_argument(
        "--skip-filled",
        action="store_true",
        help="Skip rows where all key taxonomy fields are already non-empty",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=200,
        help="Print progress every N unique species",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent workers (species-level). Use 8-20 when NCBI_API_KEY is set.",
    )
    parser.add_argument(
        "--fuzzy-max-dist",
        type=int,
        default=2,
        help="Max edit distance for fuzzy name matching (0 disables fuzzy matching)",
    )
    return parser.parse_args()


# =========================
# 主入口
# =========================

def main() -> int:
    args = parse_args()
    input_path = Path(args.input_file)
    prefix = args.output_prefix
    cache_db = Path(args.cache_db)

    if not input_path.exists():
        print(f"[error] input file not found: {input_path}", file=sys.stderr, flush=True)
        return 2

    sheet_info = f" / sheet={args.sheet_name}" if input_path.suffix.lower() in {".xlsx", ".xlsm", ".xltx", ".xltm"} else ""
    print(f"[info] loading rows from {input_path}{sheet_info}", flush=True)
    rows, fieldnames = load_input_rows(input_path, args.sheet_name)
    print(f"[info] loaded rows: {len(rows)}", flush=True)

    species_order: list[str] = []
    species_map: dict[str, str] = {}
    base_row_by_key: dict[str, dict[str, str]] = {}
    for row in rows:
        if args.skip_filled and should_skip_row(row):
            continue
        original = to_str(row.get("origin Species"))
        key = norm_lower(original)
        if key and key not in species_map:
            species_map[key] = original
            base_row_by_key[key] = row
            species_order.append(key)

    # Sort by origin Species alphabetically after dedup
    species_order = sorted(species_order, key=lambda k: species_map[k].lower())

    if args.limit and args.limit > 0:
        species_order = species_order[: args.limit]
    process_keys = set(species_order)
    if args.workers < 1:
        args.workers = 1
    print(f"[info] unique species to resolve: {len(species_order)}", flush=True)
    print(f"[info] workers: {args.workers}", flush=True)
    print(f"[info] fuzzy_max_dist: {args.fuzzy_max_dist}", flush=True)

    cache = SQLiteCache(cache_db)
    http = HttpClient(
        cache=cache,
        ncbi_api_key=os.environ.get("NCBI_API_KEY", DEFAULT_NCBI_API_KEY),
        ncbi_email=os.environ.get("NCBI_EMAIL"),
    )
    ncbi = NCBIClient(http=http)
    gbif = GBIFClient(http=http)
    inat = INaturalistClient(http=http)
    resolver = TaxonomyResolver(ncbi=ncbi, gbif=gbif, inat=inat, fuzzy_max_dist=args.fuzzy_max_dist)

    result_by_key: dict[str, Resolution] = {}
    stats = {
        "ncbi_match": 0,
        "gbif_match": 0,
        "inat_match": 0,
        "genus_fallback": 0,
        "skip_dot": 0,
        "unresolved_with_review": 0,
        "review_subfamily": 0,
        "errors": 0,
    }
    review_count = 0

    def resolve_one(key: str) -> tuple[str, Resolution]:
        origin_species = species_map[key]
        try:
            res = resolver.resolve(origin_species)
        except Exception as exc:  # noqa: BLE001
            res = Resolution(
                fill=TaxonomyResolver.blank_fill(),
                reviews=[
                    ReviewItem(
                        origin_species=origin_species,
                        stage="runtime",
                        reason=f"exception:{type(exc).__name__}",
                    )
                ],
                status="unresolved_with_review",
            )
            res.fill["note"] = append_note(res.fill["note"], f"error:{type(exc).__name__}")
            res.fill["note"] = append_note(res.fill["note"], "review_needed")
        return key, res

    pool: ThreadPoolExecutor | None = None
    try:
        total = len(species_order)
        if args.workers == 1:
            iterator = ((idx, resolve_one(key)) for idx, key in enumerate(species_order, 1))
        else:
            pool = ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="resolver")
            futures = [pool.submit(resolve_one, key) for key in species_order]
            iterator = ((idx, fut.result()) for idx, fut in enumerate(as_completed(futures), 1))

        for idx, result in iterator:
            key, res = result
            result_by_key[key] = res
            stats[res.status] = stats.get(res.status, 0) + 1
            if res.status == "unresolved_with_review" and any(
                to_str(x.reason).startswith("exception:") for x in res.reviews
            ):
                stats["errors"] += 1
            review_count += len(res.reviews)

            if args.progress_every and (idx % args.progress_every == 0 or idx == total):
                print(
                    "[progress] "
                    f"{idx}/{total} "
                    f"ncbi={stats.get('ncbi_match', 0)} "
                    f"gbif={stats.get('gbif_match', 0)} "
                    f"inat={stats.get('inat_match', 0)} "
                    f"genus={stats.get('genus_fallback', 0)} "
                    f"review={review_count} "
                    f"errors={stats.get('errors', 0)}",
                    flush=True,
                )

    finally:
        if pool is not None:
            pool.shutdown(wait=True)

    # 亚科补填必须等整批物种都解析完，才能做同属回填。
    genus_groups: dict[str, list[str]] = {}
    for key in species_order:
        if key not in result_by_key:
            continue
        res = result_by_key[key]
        genus = to_str(res.fill.get("genuis"))
        if genus:
            genus_groups.setdefault(norm_lower(genus), []).append(key)

    for genus_lower, keys in genus_groups.items():
        # Find a donor with subfamily info
        donor_family = ""
        donor_fid = ""
        donor_subfamily = ""
        donor_sfid = ""
        for k in keys:
            fam = to_str(result_by_key[k].fill.get("family"))
            fid = to_str(result_by_key[k].fill.get("fid"))
            sf = to_str(result_by_key[k].fill.get("subfamliy"))
            sfid = to_str(result_by_key[k].fill.get("sfid"))
            if sf and sfid:
                donor_family = fam
                donor_fid = fid
                donor_subfamily = sf
                donor_sfid = sfid
                break
        if not donor_subfamily:
            continue
        for k in keys:
            res = result_by_key[k]
            if not resolver.needs_subfamily_backfill(res.fill):
                continue
            if not to_str(res.fill.get("subfamliy")) and to_str(res.fill.get("genuis")):
                if donor_family and donor_fid:
                    res.fill["family"] = donor_family
                    res.fill["fid"] = donor_fid
                res.fill["subfamliy"] = donor_subfamily
                res.fill["sfid"] = donor_sfid
                res.fill["note"] = append_note(res.fill["note"], "subfamily_backfilled_from_same_genus")

    # 同属仍补不上时，再用 NCBI genus 补亚科。
    for key in species_order:
        if key not in result_by_key:
            continue
        res = result_by_key[key]
        if not resolver.needs_subfamily_backfill(res.fill):
            continue
        origin_species = species_map[key]
        resolver._backfill_subfamily_from_ncbi_genus(origin_species, res.fill, res.reviews)

    # 最后一层再用 iNaturalist genus 补亚科。
    for key in species_order:
        if key not in result_by_key:
            continue
        res = result_by_key[key]
        if not resolver.needs_subfamily_backfill(res.fill):
            continue
        genus_name = to_str(res.fill.get("genuis"))
        try:
            inat_g_status, inat_g_taxon, _ = inat.resolve_genus(genus_name)
            if inat_g_status == "matched" and inat_g_taxon:
                fam, fid = TaxonomyResolver._extract_inat_rank(inat_g_taxon, "family")
                sf, sfid = TaxonomyResolver._extract_inat_rank(inat_g_taxon, "subfamily")
                if fam and fid:
                    res.fill["family"] = fam
                    res.fill["fid"] = fid
                if sf and sfid:
                    res.fill["subfamliy"] = sf
                    res.fill["sfid"] = sfid
                    res.fill["source link"] = merge_links(
                        res.fill["source link"], TaxonomyResolver.inat_url(inat_g_taxon.get("id", ""))
                    )
                    res.fill["note"] = append_note(res.fill["note"], "subfamily_backfilled_from_inat_genus")
        except Exception:  # noqa: BLE001
            pass

    # Final pass: build each row once, then push it into the target output bucket.
    drop_dot_rows: list[dict[str, str]] = []
    clean_sp_rows: list[dict[str, str]] = []
    clean_genus_rows: list[dict[str, str]] = []
    review_genus_rows: list[dict[str, str]] = []
    review_subfamily_rows: list[dict[str, str]] = []
    review_none_rows: list[dict[str, str]] = []

    for key in species_order:
        if key not in result_by_key:
            continue
        res = result_by_key[key]
        base_row = base_row_by_key.get(key)
        if base_row is None:
            base_row = {"origin Species": species_map[key]}
            for col in EXPECTED_COLUMNS:
                base_row.setdefault(col, "")
        applied = apply_fill(
            row=base_row,
            fill=res.fill,
            overwrite_existing=args.overwrite_existing,
        )

        if res.status == "skip_dot":
            drop_dot_rows.append(applied)
            continue

        origin_species = to_str(applied.get("origin Species"))
        has_species = bool(to_str(applied.get("species")))
        has_genus = bool(to_str(applied.get("genuis")))
        has_subfamily = bool(to_str(applied.get("subfamliy")))
        source = norm_lower(applied.get("source", ""))

        if res.status == "unresolved_with_review":
            has_runtime_error = any(
                to_str(item.reason).startswith("exception:") for item in res.reviews
            )
            primary_review = select_primary_review(res.reviews)
            enriched_applied = enrich_review_row_with_recommendation(
                dict(applied),
                res.reviews,
                ncbi=ncbi,
                gbif=gbif,
                inat=inat,
            )
            if not has_runtime_error and bool(to_str(enriched_applied.get("species"))):
                clean_sp_rows.append(normalize_promoted_clean_row(enriched_applied))
                if not to_str(enriched_applied.get("subfamliy")):
                    context = build_review_context("review_subfamily", res.reviews)
                    review_subfamily_rows.append(build_review_output_row(enriched_applied, context))
                    stats["review_subfamily"] += 1
                continue
            bucket = (
                "review_genus"
                if (
                    not has_runtime_error
                    and primary_review is not None
                    and to_str(primary_review.reason) == "ambiguous_genus_candidates"
                )
                else "review_none"
            )
            context = build_review_context(bucket, res.reviews)
            review_row = build_review_output_row(enriched_applied, context)
            if bucket == "review_genus":
                review_genus_rows.append(review_row)
            else:
                review_none_rows.append(build_review_none_output_row(enriched_applied, context))
        elif has_species and not has_subfamily:
            clean_sp_rows.append(applied)
            subfamily_reviews = build_subfamily_review_items(origin_species, applied, res.reviews)
            context = build_review_context("review_subfamily", subfamily_reviews)
            review_row = build_review_output_row(applied, context)
            review_subfamily_rows.append(review_row)
            stats["review_subfamily"] += 1
        elif has_species:
            clean_sp_rows.append(applied)
        elif has_genus:
            clean_genus_rows.append(applied)
        else:
            context = build_review_context("review_none", res.reviews)
            review_none_rows.append(
                build_review_none_output_row(
                    enrich_review_row_with_recommendation(
                        dict(applied),
                        res.reviews,
                        ncbi=ncbi,
                        gbif=gbif,
                        inat=inat,
                    ),
                    context,
                )
            )

    # Write output files
    write_csv(Path(f"{prefix}.drop.dot.csv"), drop_dot_rows, fieldnames)
    print(f"[done] {prefix}.drop.dot.csv: {len(drop_dot_rows)} rows", flush=True)

    write_csv(Path(f"{prefix}.clean.sp.csv"), clean_sp_rows, fieldnames)
    print(f"[done] {prefix}.clean.sp.csv: {len(clean_sp_rows)} rows", flush=True)

    write_csv(Path(f"{prefix}.clean.genus.csv"), clean_genus_rows, fieldnames)
    print(f"[done] {prefix}.clean.genus.csv: {len(clean_genus_rows)} rows", flush=True)

    write_csv(
        Path(f"{prefix}.review.genus.csv"),
        review_genus_rows,
        REVIEW_OUTPUT_COLUMNS,
        encoding="utf-8-sig",
    )
    print(f"[done] {prefix}.review.genus.csv: {len(review_genus_rows)} rows", flush=True)

    write_csv(
        Path(f"{prefix}.review.subfamily.csv"),
        review_subfamily_rows,
        REVIEW_OUTPUT_COLUMNS,
        encoding="utf-8-sig",
    )
    print(
        f"[done] {prefix}.review.subfamily.csv: {len(review_subfamily_rows)} rows",
        flush=True,
    )

    write_csv(
        Path(f"{prefix}.review.none.csv"),
        review_none_rows,
        REVIEW_OUTPUT_COLUMNS,
        encoding="utf-8-sig",
    )
    print(f"[done] {prefix}.review.none.csv: {len(review_none_rows)} rows", flush=True)

    count_summary = {
        "loaded_rows": len(rows),
        "input_total": len(species_order),
        "input_dot_names": len(drop_dot_rows),
        "input_non_dot_names": len(species_order) - len(drop_dot_rows),
        "cleaned_sp": len(clean_sp_rows),
        "cleaned_genus": len(clean_genus_rows),
        "review_subfamily": len(review_subfamily_rows),
        "review_genus": len(review_genus_rows),
        "review_none": len(review_none_rows),
        "drop_dot": len(drop_dot_rows),
        "output_total": (
            len(drop_dot_rows)
            + len(clean_sp_rows)
            + len(clean_genus_rows)
            + len(review_genus_rows)
            + len(review_none_rows)
        ),
        "anomaly_rows": 0,
        "errors": stats.get("errors", 0),
    }
    if count_summary["output_total"] != count_summary["input_total"]:
        count_summary["anomaly_rows"] = abs(
            count_summary["output_total"] - count_summary["input_total"]
        )
        raise ValueError(
            "output count mismatch: "
            + json.dumps(count_summary, ensure_ascii=False, sort_keys=True)
        )

    counts_path = Path(f"{prefix}.counts.json")
    counts_path.write_text(
        json.dumps(count_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[done] {counts_path}: 1 file", flush=True)

    http.close()
    cache.close()

    print(
        "[summary] "
        + " ".join(f"{k}={v}" for k, v in sorted(stats.items()))
        + f" output_total={count_summary['output_total']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
