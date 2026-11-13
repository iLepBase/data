#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.27.0",
#   "openpyxl>=3.1.2",
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from openpyxl import load_workbook


EXPECTED_COLUMNS = [
    "origin Species",
    "subfamliy",
    "sfid",
    "genuis",
    "gid",
    "species",
    "authority",
    "basionym",
    "sid",
    "source (ncbi gbif)",
    "source link",
    "note",
]


NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
NCBI_BROWSER_URL = "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi"
GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
GBIF_SPECIES_URL = "https://api.gbif.org/v1/species/{usage_key}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def norm_lower(text: str) -> str:
    return norm_space(text).lower()


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


@dataclass
class ReviewItem:
    origin_species: str
    stage: str
    reason: str
    query: str = ""
    candidates_json: str = ""
    recommended_candidate: str = ""
    source_links: str = ""
    created_at: str = field(default_factory=now_iso)

    def to_row(self) -> dict[str, str]:
        return {
            "origin Species": self.origin_species,
            "stage": self.stage,
            "reason": self.reason,
            "query": self.query,
            "candidates_json": self.candidates_json,
            "recommended_candidate": self.recommended_candidate,
            "source_links": self.source_links,
            "created_at": self.created_at,
        }


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
            params.setdefault("tool", "hostplant_taxonomy_cleaner")

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
    def species_name_match(query: str, candidate_name: str) -> bool:
        q = norm_lower(query)
        c = norm_lower(candidate_name)
        if q == c:
            return True
        return parse_binomial(q) == parse_binomial(c) and parse_binomial(q) != ""

    def resolve_species(
        self, species_name: str
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        ids = self.search_ids(species_name, strict_exact=True)
        if not ids:
            ids = self.search_ids(species_name, strict_exact=False)
        if not ids:
            return "no_match", None, []

        taxa = self.fetch_taxa(ids)
        exact = [
            t
            for t in taxa
            if t.get("rank") == "species"
            and self.species_name_match(species_name, t.get("scientific_name", ""))
        ]
        if len(exact) == 1:
            return "matched", exact[0], taxa
        if len(exact) > 1:
            return "ambiguous", None, taxa

        close = [
            t for t in taxa if self.species_name_match(species_name, t.get("scientific_name", ""))
        ]
        if len(close) == 1:
            return "matched", close[0], taxa
        if len(close) > 1 or len(taxa) > 1:
            return "ambiguous", None, taxa
        return "no_match", None, taxa

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
        self, species_name: str
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], str]:
        payload = self.match(species_name)
        if not payload:
            return "no_match", None, [], "empty_payload"

        usage_key = payload.get("usageKey")
        match_type = to_str(payload.get("matchType")).upper()
        confidence = int(payload.get("confidence", 0) or 0)
        alternatives = payload.get("alternatives") or []

        if not usage_key or match_type == "NONE":
            return "no_match", None, alternatives, "no_usage_key_or_none_match"

        canonical = self._canonical_candidate_name(payload)
        canonical_ok = norm_lower(canonical) == norm_lower(species_name)
        exact_ok = match_type == "EXACT" and confidence >= 95 and canonical_ok
        alt_ok = len(alternatives) == 0

        if exact_ok and alt_ok:
            details = self.fetch_species(usage_key)
            return "matched", details, alternatives, "exact"

        reason_parts = []
        if match_type != "EXACT":
            reason_parts.append(f"matchType={match_type}")
        if confidence < 95:
            reason_parts.append(f"confidence={confidence}")
        if not canonical_ok:
            reason_parts.append("canonical_not_equal")
        if not alt_ok:
            reason_parts.append(f"alternatives={len(alternatives)}")
        return "ambiguous", payload, alternatives, ",".join(reason_parts) or "ambiguous"


class TaxonomyResolver:
    def __init__(self, ncbi: NCBIClient, gbif: GBIFClient):
        self.ncbi = ncbi
        self.gbif = gbif

    @staticmethod
    def blank_fill() -> dict[str, str]:
        return {
            "subfamliy": "",
            "sfid": "",
            "genuis": "",
            "gid": "",
            "species": "",
            "authority": "",
            "basionym": "",
            "sid": "",
            "source (ncbi gbif)": "",
            "source link": "",
            "note": "",
        }

    @staticmethod
    def ncbi_url(taxid: str) -> str:
        return f"https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={taxid}"

    @staticmethod
    def gbif_url(usage_key: str | int) -> str:
        return f"https://www.gbif.org/species/{usage_key}"

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

    def resolve(self, origin_species: str) -> Resolution:
        origin_species = norm_space(origin_species)
        fill = self.blank_fill()
        reviews: list[ReviewItem] = []
        encountered_ambiguity = False

        if has_dotted_species_marker(origin_species):
            fill["note"] = append_note(fill["note"], "skipped_name_contains_dot")
            return Resolution(fill=fill, reviews=reviews, status="skipped_name_contains_dot")

        ncbi_status, ncbi_taxon, ncbi_candidates = self.ncbi.resolve_species(origin_species)
        if ncbi_status == "matched" and ncbi_taxon:
            fill = self._build_from_ncbi_species(ncbi_taxon)
            return Resolution(fill=fill, reviews=reviews, status="ncbi_match")
        if ncbi_status == "ambiguous":
            encountered_ambiguity = True
            reviews.append(
                ReviewItem(
                    origin_species=origin_species,
                    stage="ncbi_species",
                    reason="multiple_or_unclear_ncbi_candidates",
                    query=origin_species,
                    candidates_json=json.dumps(ncbi_candidates, ensure_ascii=False),
                    recommended_candidate=self._recommend_ncbi_candidate(
                        origin_species, ncbi_candidates, expected_rank="species"
                    ),
                    source_links="https://www.ncbi.nlm.nih.gov/taxonomy",
                )
            )

        gbif_status, gbif_payload, gbif_alts, gbif_reason = self.gbif.resolve_species(
            origin_species
        )
        if gbif_status == "matched" and gbif_payload:
            fill = self._build_from_gbif(gbif_payload)
            if not fill["subfamliy"] and fill["genuis"]:
                b_status, b_taxon, b_candidates = self.ncbi.resolve_genus(fill["genuis"])
                if b_status == "matched" and b_taxon:
                    subfamily, sfid = self.ncbi.extract_rank(b_taxon, "subfamily")
                    if subfamily and sfid:
                        fill["subfamliy"] = subfamily
                        fill["sfid"] = sfid
                        fill["source link"] = merge_links(
                            fill["source link"], self.ncbi_url(b_taxon["taxid"])
                        )
                        fill["note"] = append_note(fill["note"], "subfamily_backfilled_from_ncbi_genus")
                elif b_status == "ambiguous":
                    lepidoptera_taxon = self._pick_unique_lineage_taxon(
                        b_candidates, rank_name="order", scientific_name="Lepidoptera"
                    )
                    if lepidoptera_taxon:
                        subfamily, sfid = self.ncbi.extract_rank(lepidoptera_taxon, "subfamily")
                        if subfamily and sfid:
                            fill["subfamliy"] = subfamily
                            fill["sfid"] = sfid
                            fill["source link"] = merge_links(
                                fill["source link"], self.ncbi_url(lepidoptera_taxon["taxid"])
                            )
                            fill["note"] = append_note(
                                fill["note"], "subfamily_backfilled_from_ncbi_genus"
                            )
                        else:
                            encountered_ambiguity = True
                            fill["note"] = append_note(fill["note"], "subfamily_backfill_needs_review")
                            reviews.append(
                                ReviewItem(
                                    origin_species=origin_species,
                                    stage="gbif_subfamily_backfill_ncbi_genus",
                                    reason="lepidoptera_genus_missing_subfamily_when_backfilling_subfamily",
                                    query=fill["genuis"],
                                    candidates_json=json.dumps(b_candidates, ensure_ascii=False),
                                    recommended_candidate=self._recommend_ncbi_candidate(
                                        fill["genuis"], b_candidates, expected_rank="genus"
                                    ),
                                    source_links="https://www.ncbi.nlm.nih.gov/taxonomy",
                                )
                            )
                    else:
                        encountered_ambiguity = True
                        fill["note"] = append_note(fill["note"], "subfamily_backfill_needs_review")
                        reviews.append(
                            ReviewItem(
                                origin_species=origin_species,
                                stage="gbif_subfamily_backfill_ncbi_genus",
                                reason="ambiguous_genus_when_backfilling_subfamily",
                                query=fill["genuis"],
                                candidates_json=json.dumps(b_candidates, ensure_ascii=False),
                                recommended_candidate=self._recommend_ncbi_candidate(
                                    fill["genuis"], b_candidates, expected_rank="genus"
                                ),
                                source_links="https://www.ncbi.nlm.nih.gov/taxonomy",
                            )
                        )
            if encountered_ambiguity:
                fill["note"] = append_note(fill["note"], "review_needed")
                return Resolution(fill=fill, reviews=reviews, status="gbif_match_with_review")
            return Resolution(fill=fill, reviews=reviews, status="gbif_match")

        if gbif_status == "ambiguous":
            encountered_ambiguity = True
            candidates = []
            if gbif_payload:
                candidates.append(gbif_payload)
            candidates.extend(gbif_alts or [])
            reviews.append(
                ReviewItem(
                    origin_species=origin_species,
                    stage="gbif_species",
                    reason=f"ambiguous_gbif_match:{gbif_reason}",
                    query=origin_species,
                    candidates_json=json.dumps(candidates, ensure_ascii=False),
                    recommended_candidate=self._recommend_gbif_candidate(candidates),
                    source_links="https://api.gbif.org/v1/species/match",
                )
            )

        genus = first_token(origin_species)
        if genus:
            genus_status, genus_taxon, genus_candidates = self.ncbi.resolve_genus(genus)
            if genus_status == "matched" and genus_taxon:
                fill = self._build_from_ncbi_genus_fallback(genus_taxon)
                fill["note"] = append_note(fill["note"], "genus_fallback")
                if encountered_ambiguity:
                    fill["note"] = append_note(fill["note"], "review_needed")
                    return Resolution(fill=fill, reviews=reviews, status="genus_fallback_with_review")
                return Resolution(fill=fill, reviews=reviews, status="genus_fallback")
            if genus_status == "ambiguous":
                reviews.append(
                    ReviewItem(
                        origin_species=origin_species,
                        stage="ncbi_genus_fallback",
                        reason="ambiguous_genus_candidates",
                        query=genus,
                        candidates_json=json.dumps(genus_candidates, ensure_ascii=False),
                        recommended_candidate=self._recommend_ncbi_candidate(
                            genus, genus_candidates, expected_rank="genus"
                        ),
                        source_links="https://www.ncbi.nlm.nih.gov/taxonomy",
                    )
                )
                encountered_ambiguity = True

        fill["note"] = append_note(fill["note"], "no_match")
        if encountered_ambiguity:
            fill["note"] = append_note(fill["note"], "review_needed")
            return Resolution(fill=fill, reviews=reviews, status="unresolved_with_review")
        return Resolution(fill=fill, reviews=reviews, status="unresolved")

    def _build_from_ncbi_species(self, taxon: dict[str, Any]) -> dict[str, str]:
        fill = self.blank_fill()
        subfamily, sfid = self.ncbi.extract_rank(taxon, "subfamily")
        genus, gid = self.ncbi.extract_rank(taxon, "genus")
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
        fill["source (ncbi gbif)"] = "ncbi"
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
        genus_name = to_str(payload.get("genus"))
        subfamily_name = to_str(payload.get("subfamily"))
        fill["species"] = species_name
        fill["authority"] = self.gbif.extract_authorship(payload)
        fill["sid"] = to_str(payload.get("speciesKey") or usage_key)
        fill["genuis"] = genus_name
        fill["gid"] = to_str(payload.get("genusKey"))
        fill["subfamliy"] = subfamily_name
        fill["sfid"] = to_str(payload.get("subfamilyKey"))
        fill["source (ncbi gbif)"] = "gbif"
        fill["source link"] = self.gbif_url(usage_key)
        return fill

    def _build_from_ncbi_genus_fallback(self, taxon: dict[str, Any]) -> dict[str, str]:
        fill = self.blank_fill()
        subfamily, sfid = self.ncbi.extract_rank(taxon, "subfamily")
        fill["subfamliy"] = subfamily or ""
        fill["sfid"] = sfid or ""
        fill["genuis"] = taxon.get("scientific_name", "")
        fill["gid"] = taxon.get("taxid", "")
        fill["source (ncbi gbif)"] = "ncbi"
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


def load_input_rows(input_xlsx: Path, sheet_name: str) -> tuple[list[dict[str, str]], list[str]]:
    wb = load_workbook(input_xlsx, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"sheet '{sheet_name}' not found. Available: {wb.sheetnames}")
    ws = wb[sheet_name]

    raw_headers = [to_str(x) for x in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    headers = [h for h in raw_headers if h]
    if not headers:
        headers = EXPECTED_COLUMNS.copy()

    expected_index: dict[str, int] = {}
    for idx, header in enumerate(raw_headers):
        if header in EXPECTED_COLUMNS:
            expected_index[header] = idx
    for idx, expected in enumerate(EXPECTED_COLUMNS):
        expected_index.setdefault(expected, idx)

    rows: list[dict[str, str]] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        item = {}
        for col in EXPECTED_COLUMNS:
            idx = expected_index[col]
            item[col] = to_str(raw[idx]) if idx < len(raw) else ""
        if item["origin Species"]:
            rows.append(item)
    wb.close()
    return rows, EXPECTED_COLUMNS.copy()


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def should_skip_row(row: dict[str, str]) -> bool:
    return all(
        to_str(row.get(col))
        for col in [
            "subfamliy",
            "sfid",
            "genuis",
            "gid",
            "species",
            "authority",
            "sid",
            "source (ncbi gbif)",
        ]
    )


def apply_fill(
    row: dict[str, str], fill: dict[str, str], overwrite_existing: bool
) -> dict[str, str]:
    out = dict(row)
    for col in [
        "subfamliy",
        "sfid",
        "genuis",
        "gid",
        "species",
        "authority",
        "basionym",
        "sid",
        "source (ncbi gbif)",
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
        description="Clean Lepidoptera taxonomy with NCBI->GBIF->NCBI genus fallback."
    )
    parser.add_argument(
        "--input-xlsx",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.xlsx",
        help="Input XLSX path",
    )
    parser.add_argument(
        "--sheet-name",
        default="Sheet1",
        help="Sheet name in XLSX (default: Sheet1)",
    )
    parser.add_argument(
        "--output-csv",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.cleaned.csv",
        help="Main output CSV path",
    )
    parser.add_argument(
        "--review-csv",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.review.csv",
        help="Review CSV path for ambiguous cases",
    )
    parser.add_argument(
        "--cache-db",
        default="/home/iLepbase/data/raw/hostplant/taxonomy_cache.sqlite",
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_xlsx = Path(args.input_xlsx)
    output_csv = Path(args.output_csv)
    review_csv = Path(args.review_csv)
    cache_db = Path(args.cache_db)

    if not input_xlsx.exists():
        print(f"[error] input xlsx not found: {input_xlsx}", file=sys.stderr, flush=True)
        return 2

    print(f"[info] loading rows from {input_xlsx} / sheet={args.sheet_name}", flush=True)
    rows, fieldnames = load_input_rows(input_xlsx, args.sheet_name)
    print(f"[info] loaded rows: {len(rows)}", flush=True)

    species_order: list[str] = []
    species_map: dict[str, str] = {}
    for row in rows:
        if args.skip_filled and should_skip_row(row):
            continue
        original = to_str(row.get("origin Species"))
        key = norm_lower(original)
        if key and key not in species_map:
            species_map[key] = original
            species_order.append(key)

    if args.limit and args.limit > 0:
        species_order = species_order[: args.limit]
    process_keys = set(species_order)
    if args.workers < 1:
        args.workers = 1
    print(f"[info] unique species to resolve: {len(species_order)}", flush=True)
    print(f"[info] workers: {args.workers}", flush=True)

    cache = SQLiteCache(cache_db)
    http = HttpClient(
        cache=cache,
        ncbi_api_key=os.environ.get("NCBI_API_KEY"),
        ncbi_email=os.environ.get("NCBI_EMAIL"),
    )
    ncbi = NCBIClient(http=http)
    gbif = GBIFClient(http=http)
    resolver = TaxonomyResolver(ncbi=ncbi, gbif=gbif)

    result_by_key: dict[str, Resolution] = {}
    stats = {
        "ncbi_match": 0,
        "gbif_match": 0,
        "gbif_match_with_review": 0,
        "genus_fallback": 0,
        "genus_fallback_with_review": 0,
        "skipped_name_contains_dot": 0,
        "unresolved": 0,
        "unresolved_with_review": 0,
        "errors": 0,
    }
    review_items: list[ReviewItem] = []

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
                        query=origin_species,
                        candidates_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
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
            review_items.extend(res.reviews)

            if args.progress_every and (idx % args.progress_every == 0 or idx == total):
                print(
                    "[progress] "
                    f"{idx}/{total} "
                    f"ncbi={stats.get('ncbi_match', 0)} "
                    f"gbif={stats.get('gbif_match', 0) + stats.get('gbif_match_with_review', 0)} "
                    f"genus={stats.get('genus_fallback', 0) + stats.get('genus_fallback_with_review', 0)} "
                    f"review={len(review_items)} "
                    f"errors={stats.get('errors', 0)}",
                    flush=True,
                )

    finally:
        if pool is not None:
            pool.shutdown(wait=True)
        http.close()
        cache.close()

    out_rows: list[dict[str, str]] = []
    for row in rows:
        original = to_str(row.get("origin Species"))
        key = norm_lower(original)
        if key in process_keys and key in result_by_key:
            if result_by_key[key].status == "skipped_name_contains_dot":
                continue
            row = apply_fill(
                row=row,
                fill=result_by_key[key].fill,
                overwrite_existing=args.overwrite_existing,
            )
        out_rows.append(row)

    write_csv(output_csv, out_rows, fieldnames)
    write_csv(
        review_csv,
        [x.to_row() for x in review_items],
        [
            "origin Species",
            "stage",
            "reason",
            "query",
            "candidates_json",
            "recommended_candidate",
            "source_links",
            "created_at",
        ],
    )

    print(f"[done] output csv: {output_csv}", flush=True)
    print(f"[done] review csv: {review_csv}", flush=True)
    print(
        "[summary] "
        + " ".join(f"{k}={v}" for k, v in sorted(stats.items()))
        + f" review_rows={len(review_items)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
