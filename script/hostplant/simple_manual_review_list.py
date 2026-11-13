#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.27.0",
#   "beautifulsoup4>=4.12.0",
# ]
# ///

from __future__ import annotations

import argparse
import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup


UNRESOLVED_TAG = "[Unresolved: Possible Nomen nudum / Typo]（未解决：疑似裸名或拼写错误）"


def s(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_name(name: str) -> str:
    return " ".join(s(name).split()).lower()


@dataclass
class Candidate:
    name: str
    source: str
    link: str
    candidate_id: str = ""
    rank: str = ""


@dataclass
class ChainDecision:
    should_stop: bool = False
    reason: str = ""
    source: str = ""
    link: str = ""


class GhostNameResolver:
    def __init__(self, timeout_sec: float = 20.0, max_attempts: int = 3) -> None:
        self.client = httpx.Client(timeout=timeout_sec)
        self.max_attempts = max_attempts
        self.cache_lock = threading.Lock()
        self.json_cache: dict[str, dict[str, Any]] = {}
        self.text_cache: dict[str, str] = {}

    def close(self) -> None:
        self.client.close()

    def _get_json(self, url: str) -> dict[str, Any]:
        with self.cache_lock:
            cached = self.json_cache.get(url)
        if cached is not None:
            return cached

        payload: dict[str, Any] = {}
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
                payload = resp.json()
                break
            except Exception:  # noqa: BLE001
                if attempt == self.max_attempts:
                    return {}
                time.sleep(0.3 * attempt)

        with self.cache_lock:
            self.json_cache[url] = payload
        return payload

    def _get_text(self, url: str) -> str:
        with self.cache_lock:
            cached = self.text_cache.get(url)
        if cached is not None:
            return cached

        text = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
                text = resp.text
                break
            except Exception:  # noqa: BLE001
                if attempt == self.max_attempts:
                    return ""
                time.sleep(0.3 * attempt)

        with self.cache_lock:
            self.text_cache[url] = text
        return text

    @staticmethod
    def gnr_url(name: str) -> str:
        return f"https://verifier.globalnames.org/api/v1/verifications/{quote_plus(name)}"

    @staticmethod
    def bold_url(name: str) -> str:
        return "https://portal.boldsystems.org/api/counts?query=" + quote_plus(f"tax:species:{name}")

    @staticmethod
    def lepindex_url(epithet: str) -> str:
        return (
            "https://www.nhm.ac.uk/our-science/data/lepindex/search/list"
            f"?indexed_from=1&page_no=1&page_size=30&search_type=equals&snoc={quote_plus(epithet)}"
        )

    def gnr_lookup(self, name: str) -> dict[str, str]:
        payload = self._get_json(self.gnr_url(name))
        first = ((payload.get("names") or [{}])[0]) if isinstance(payload, dict) else {}
        best = first.get("bestResult") if isinstance(first, dict) else {}
        best = best if isinstance(best, dict) else {}
        return {
            "match_type": s(first.get("matchType")).upper(),
            "canonical": s(best.get("currentCanonicalSimple")) or s(best.get("matchedCanonicalSimple")),
            "classification_path": s(best.get("classificationPath")),
        }

    def bold_count(self, name: str) -> int:
        payload = self._get_json(self.bold_url(name))
        try:
            return int(payload.get("records", 0) or 0)
        except Exception:  # noqa: BLE001
            return 0

    def lepindex_lookup(self, epithet: str) -> dict[str, Any]:
        url = self.lepindex_url(epithet)
        html = self._get_text(url)
        if not html:
            return {"records": 0, "genera": set(), "families": set(), "url": url}

        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        m = re.search(r"Records\s+\d+\s*-\s*\d+\s+of\s+(\d+)", text, flags=re.IGNORECASE)
        records = int(m.group(1)) if m else 0

        soup = BeautifulSoup(html, "html.parser")
        genera: set[str] = set()
        families: set[str] = set()
        data_table = soup.find("table", class_=lambda c: c and "dataTable_ms" in c)
        if data_table:
            for tr in data_table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 6:
                    continue
                genus = s(tds[3].get_text(" ", strip=True)).upper()
                family = s(tds[5].get_text(" ", strip=True))
                if genus:
                    genera.add(genus)
                if family:
                    families.add(family)

        return {"records": records, "genera": genera, "families": families, "url": url}

    def decide(self, origin_name: str) -> ChainDecision:
        origin_name = s(origin_name)
        tokens = origin_name.split()
        if len(tokens) < 2:
            return ChainDecision()

        query_genus = normalize_name(tokens[0]).upper()
        epithet = tokens[-1]

        gnr = self.gnr_lookup(origin_name)
        gnr_mt = s(gnr.get("match_type")).upper()
        gnr_canonical = s(gnr.get("canonical"))
        gnr_path = normalize_name(s(gnr.get("classification_path")))
        gnr_is_species = len(gnr_canonical.split()) >= 2
        gnr_in_lepi = "lepidoptera" in gnr_path
        gnr_strict_ok = gnr_mt == "EXACT" and gnr_is_species and gnr_in_lepi
        if gnr_strict_ok:
            return ChainDecision()

        fuzzy_like = gnr_mt in {
            "NOMATCH",
            "FUZZY",
            "PARTIALEXACT",
            "PARTIALFUZZY",
            "PARTIALNOMATCH",
        } or (not gnr_is_species)
        if not fuzzy_like:
            return ChainDecision()

        bold_records = self.bold_count(origin_name)
        if bold_records > 0:
            return ChainDecision()

        lep = self.lepindex_lookup(epithet)
        records = int(lep.get("records", 0) or 0)
        genera = {normalize_name(x).upper() for x in lep.get("genera", set()) if s(x)}
        families = {s(x) for x in lep.get("families", set()) if s(x)}
        if records <= 0 or not genera:
            return ChainDecision()

        same_genus_found = query_genus in genera
        cross_family = len(families) >= 2
        if same_genus_found:
            return ChainDecision()

        detail = "LepIndex 检索到同词尾记录但属不一致"
        if cross_family:
            detail += "，且分布跨多个科"
        reason = (
            "GNR 模糊/部分匹配仍无法给出可直接采用的鳞翅目物种；"
            "BOLD 无记录；"
            f"{detail}。按规则停止追查并打标。"
        )
        return ChainDecision(
            should_stop=True,
            reason=reason,
            source="GNR->BOLD->LepIndex",
            link=";".join(
                [
                    self.gnr_url(origin_name),
                    self.bold_url(origin_name),
                    s(lep.get("url")),
                ]
            ),
        )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Excel on Windows often needs UTF-8 BOM to avoid garbled Chinese text.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def stage_source_zh(stage: str) -> str:
    stage = s(stage)
    if stage.startswith("gbif_subfamily_backfill"):
        return "GBIF/NCBI"
    if stage.startswith("gbif"):
        return "GBIF"
    if stage.startswith("ncbi"):
        return "NCBI"
    return "未知"


def source_to_zh(source: str) -> str:
    source_raw = s(source)
    if source_raw == "GNR->BOLD->LepIndex":
        return source_raw
    source = source_raw.upper()
    if source == "GBIF":
        return "GBIF"
    if source == "NCBI":
        return "NCBI"
    return "未知"


def reason_to_zh(reason: str) -> str:
    reason = s(reason)
    if reason.startswith("ambiguous_gbif_match"):
        if "HIGHERRANK" in reason:
            return "GBIF返回上级匹配（非明确物种），程序无法自动定种。"
        if "FUZZY" in reason:
            return "GBIF返回模糊匹配（疑似拼写差异），程序无法自动确认。"
        return "GBIF返回多候选/不一致结果，程序无法自动确认。"
    if reason == "ambiguous_genus_when_backfilling_subfamily":
        return "补亚科时，NCBI属名有多个同名候选。"
    if reason == "ambiguous_genus_candidates":
        return "NCBI属名有多个候选，无法自动选择。"
    if reason.startswith("exception:"):
        return "查询过程出现异常，需人工复核。"
    return f"程序未能自动确定（{reason}）。"


def parse_recommended_name(rec: str) -> str:
    rec = s(rec)
    if not rec:
        return ""
    parts = rec.split("|")
    if len(parts) >= 2:
        return s(parts[1])
    return rec


def parse_recommended_id(rec: str) -> str:
    rec = s(rec)
    if not rec:
        return ""
    parts = rec.split("|")
    if len(parts) >= 1:
        return s(parts[0])
    return ""


def gbif_link(gbif_id: str) -> str:
    gbif_id = s(gbif_id)
    if not gbif_id:
        return ""
    return f"https://www.gbif.org/species/{gbif_id}"


def ncbi_link(taxid: str) -> str:
    taxid = s(taxid)
    if not taxid:
        return ""
    return f"https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={taxid}"


def is_too_broad_name(name: str) -> bool:
    broad = {
        "animalia",
        "arthropoda",
        "insecta",
        "lepidoptera",
        "eukaryota",
        "metazoa",
        "viridiplantae",
        "chordata",
    }
    return normalize_name(name) in broad


def parse_candidates(candidates_json: str) -> list[Candidate]:
    text = s(candidates_json)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    candidates: list[Candidate] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rank = s(item.get("rank")).upper()
        source = "GBIF" if any(k in item for k in ["usageKey", "speciesKey", "nubKey"]) else "NCBI"
        candidate_id = ""
        link = ""
        if source == "GBIF":
            gbif_id = s(item.get("usageKey")) or s(item.get("speciesKey")) or s(item.get("key"))
            candidate_id = gbif_id
            link = gbif_link(gbif_id)
        else:
            taxid = s(item.get("taxid")) or s(item.get("TaxId"))
            candidate_id = taxid
            link = ncbi_link(taxid)
            # NCBI 同名属跨界常见（例如昆虫/植物），若能识别到非鳞翅目则过滤
            lineage = item.get("lineage") or []
            order_names = [
                s(n.get("scientific_name"))
                for n in lineage
                if isinstance(n, dict) and s(n.get("rank")).lower() == "order"
            ]
            if order_names and all(normalize_name(x) != "lepidoptera" for x in order_names):
                continue

        name = (
            s(item.get("scientificName"))
            or s(item.get("scientific_name"))
            or s(item.get("canonicalName"))
            or s(item.get("species"))
        )
        if rank in {"KINGDOM", "PHYLUM", "CLASS", "ORDER", "CLADE"}:
            # 太高阶的候选对人工定种帮助不大，优先丢弃
            continue
        if name:
            candidates.append(
                Candidate(
                    name=name,
                    source=source,
                    link=link,
                    candidate_id=candidate_id,
                    rank=rank,
                )
            )
    return candidates


def extract_gbif_metrics(group: list[dict[str, str]]) -> tuple[str, str, str, str]:
    confidences: set[str] = set()
    match_types: set[str] = set()
    statuses: set[str] = set()
    homonym_notes: set[str] = set()

    for row in group:
        stage = s(row.get("stage")).lower()
        if not stage.startswith("gbif"):
            continue
        text = s(row.get("candidates_json"))
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            continue

        for item in data:
            if not isinstance(item, dict):
                continue
            if not any(k in item for k in ["usageKey", "speciesKey", "key"]):
                continue

            conf = item.get("confidence")
            if conf is not None and s(conf):
                confidences.add(s(conf))

            mt = s(item.get("matchType")).upper()
            if mt:
                match_types.add(mt)

            st = s(item.get("status")).upper()
            if st:
                statuses.add(st)

            note = s(item.get("note"))
            if "homonym" in note.lower():
                homonym_notes.add(note)

    confidence_text = " / ".join(sorted(confidences, key=lambda x: int(x) if x.isdigit() else x))
    match_type_text = " / ".join(sorted(match_types))
    status_text = " / ".join(sorted(statuses))

    if homonym_notes:
        homonym_text = "；".join(sorted(homonym_notes))
    elif "SYNONYM" in statuses:
        homonym_text = "SYNONYM（同物异名，非同名）"
    elif match_types:
        homonym_text = "未见 homonym 提示"
    else:
        homonym_text = ""

    return confidence_text, match_type_text, status_text, homonym_text


def parse_recommended_candidate(rec: str, stage: str, source_links: str) -> Candidate | None:
    rec = s(rec)
    if not rec:
        return None
    parts = rec.split("|")
    if len(parts) < 2:
        return None
    cand_id = s(parts[0])
    name = s(parts[1])
    if not name:
        return None

    stage = s(stage)
    source_links = s(source_links)
    source = "未知"
    link = ""

    if "ncbi" in stage.lower() or "ncbi" in source_links.lower():
        source = "NCBI"
        if cand_id.isdigit():
            link = ncbi_link(cand_id)
    elif "gbif" in stage.lower() or "gbif" in source_links.lower():
        source = "GBIF"
        if cand_id.isdigit():
            link = gbif_link(cand_id)
    else:
        if cand_id.isdigit():
            # 默认先按 GBIF 处理（review 中 gbif_species 占多数）
            source = "GBIF"
            link = gbif_link(cand_id)

    return Candidate(name=name, source=source, link=link, candidate_id=cand_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "生成中文人工校正清单：原始名、校正后的名字、来源、链接、"
            "GBIF匹配类型、GBIF置信度、GBIF分类状态、GBIF重名状态、为什么需要校正。"
        )
    )
    parser.add_argument(
        "--review-csv",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.review.csv",
        help="输入 review.csv 路径",
    )
    parser.add_argument(
        "--output-csv",
        default="/home/iLepbase/data/raw/hostplant/人工校正清单_简化.csv",
        help="输出简化清单路径",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=5,
        help="每个原始名最多输出候选数（默认5）",
    )
    parser.add_argument(
        "--only-multi-candidates",
        action="store_true",
        help="仅输出同一个原始名存在多个候选的冲突项（人工二选一/多选一）。",
    )
    parser.add_argument(
        "--disable-ghost-stop",
        action="store_true",
        help="关闭 GNR->BOLD->LepIndex 幽灵名拦截逻辑（默认开启）。",
    )
    parser.add_argument(
        "--chain-workers",
        type=int,
        default=6,
        help="在线链路并发数（默认6）。",
    )
    parser.add_argument(
        "--chain-progress-every",
        type=int,
        default=200,
        help="在线链路每处理 N 个名称打印一次进度（默认200）。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_path = Path(args.review_csv)
    out_path = Path(args.output_csv)

    if not review_path.exists():
        raise FileNotFoundError(f"review file not found: {review_path}")

    rows = read_csv(review_path)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        name = s(row.get("origin Species"))
        if name:
            grouped[name].append(row)

    chain_decision_by_origin: dict[str, ChainDecision] = {}
    if not args.disable_ghost_stop:
        resolver = GhostNameResolver()
        try:
            origin_names = sorted(grouped.keys(), key=lambda x: x.lower())
            total_names = len(origin_names)
            workers = max(1, int(args.chain_workers))
            if workers == 1:
                for idx, origin_name in enumerate(origin_names, 1):
                    chain_decision_by_origin[origin_name] = resolver.decide(origin_name)
                    if args.chain_progress_every and (
                        idx % args.chain_progress_every == 0 or idx == total_names
                    ):
                        print(f"[chain] {idx}/{total_names}")
            else:
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chain") as pool:
                    future_map = {
                        pool.submit(resolver.decide, origin_name): origin_name
                        for origin_name in origin_names
                    }
                    for idx, fut in enumerate(as_completed(future_map), 1):
                        origin_name = future_map[fut]
                        try:
                            chain_decision_by_origin[origin_name] = fut.result()
                        except Exception as exc:  # noqa: BLE001
                            chain_decision_by_origin[origin_name] = ChainDecision(
                                should_stop=False,
                                reason=f"chain_error:{type(exc).__name__}",
                            )
                        if args.chain_progress_every and (
                            idx % args.chain_progress_every == 0 or idx == total_names
                        ):
                            print(f"[chain] {idx}/{total_names}")
        finally:
            resolver.close()

    out_rows: list[dict[str, str]] = []
    total_candidates = 0
    unresolved_marked = 0

    for origin_name in sorted(grouped.keys(), key=lambda x: x.lower()):
        group = grouped[origin_name]
        reason_set = {reason_to_zh(s(r.get("reason"))) for r in group if s(r.get("reason"))}
        gbif_confidence, gbif_match_type, gbif_status, gbif_homonym = extract_gbif_metrics(group)

        chain_decision = chain_decision_by_origin.get(origin_name, ChainDecision())
        if chain_decision.should_stop:
            reason_text = "；".join(sorted(reason_set))
            if reason_text:
                reason_text = f"{reason_text}；{chain_decision.reason}"
            else:
                reason_text = chain_decision.reason
            out_rows.append(
                {
                    "原始名": origin_name,
                    "校正后的名字": UNRESOLVED_TAG,
                    "来源": source_to_zh(chain_decision.source),
                    "链接": chain_decision.link or "（无可用详情链接）",
                    "GBIF匹配类型": gbif_match_type,
                    "GBIF置信度": gbif_confidence,
                    "GBIF分类状态": gbif_status,
                    "GBIF重名状态": gbif_homonym,
                    "为什么需要校正": reason_text,
                }
            )
            total_candidates += 1
            unresolved_marked += 1
            continue

        recommended_names = {
            normalize_name(parse_recommended_name(s(r.get("recommended_candidate"))))
            for r in group
            if s(r.get("recommended_candidate"))
        }
        recommended_names = {x for x in recommended_names if x}
        recommended_ids = {
            parse_recommended_id(s(r.get("recommended_candidate")))
            for r in group
            if s(r.get("recommended_candidate"))
        }
        recommended_ids = {x for x in recommended_ids if x}

        candidates: list[Candidate] = []
        for r in group:
            candidates.extend(parse_candidates(s(r.get("candidates_json"))))

        # 若推荐项不在候选中，补一个推荐候选（并尽量给可点击链接）
        existing_keys = {(normalize_name(c.name), c.source, c.link, c.candidate_id) for c in candidates}
        for r in group:
            rec_obj = parse_recommended_candidate(
                s(r.get("recommended_candidate")),
                s(r.get("stage")),
                s(r.get("source_links")),
            )
            if rec_obj and not is_too_broad_name(rec_obj.name):
                key = (
                    normalize_name(rec_obj.name),
                    rec_obj.source,
                    rec_obj.link,
                    rec_obj.candidate_id,
                )
                if key not in existing_keys:
                    existing_keys.add(key)
                    candidates.append(rec_obj)

        # 去重并保序
        seen = set()
        dedup_candidates: list[Candidate] = []
        for cand in candidates:
            key = (normalize_name(cand.name), cand.source, cand.link, cand.candidate_id)
            if key and key not in seen:
                seen.add(key)
                dedup_candidates.append(cand)

        # 若没有候选，则至少放程序推荐；再没有就留待人工检索
        if not dedup_candidates:
            for r in group:
                rec_name = parse_recommended_name(s(r.get("recommended_candidate")))
                if rec_name and not is_too_broad_name(rec_name):
                    dedup_candidates.append(
                        Candidate(
                            name=rec_name,
                            source=stage_source_zh(s(r.get("stage"))),
                            link=s(r.get("source_links")),
                            candidate_id=parse_recommended_id(s(r.get("recommended_candidate"))),
                        )
                    )
                    break
        if not dedup_candidates:
            genus_hint = origin_name.split()[0] if origin_name.split() else ""
            if genus_hint:
                dedup_candidates = [Candidate(name=f"{genus_hint}（仅属级候选）", source="未知", link="")]
            else:
                dedup_candidates = [Candidate(name="（无候选，请人工检索）", source="未知", link="")]

        # 推荐候选靠前
        def candidate_sort_key(cand: Candidate) -> tuple[int, str]:
            is_rec = 1 if normalize_name(cand.name) in recommended_names else 0
            return (-is_rec, cand.name.lower())

        dedup_candidates = sorted(dedup_candidates, key=candidate_sort_key)[: max(1, args.max_candidates)]

        if args.only_multi_candidates and len(dedup_candidates) <= 1:
            continue

        reason_text = "；".join(sorted(reason_set))

        for cand in dedup_candidates:
            is_recommended = False
            if cand.candidate_id and cand.candidate_id in recommended_ids:
                is_recommended = True
            elif normalize_name(cand.name) in recommended_names and not recommended_ids:
                is_recommended = True
            mark = "" if is_recommended else ""
            out_rows.append(
                {
                    "原始名": origin_name,
                    "校正后的名字": f"{mark}{cand.name}",
                    "来源": source_to_zh(cand.source),
                    "链接": cand.link or "（无可用详情链接）",
                    "GBIF匹配类型": gbif_match_type,
                    "GBIF置信度": gbif_confidence,
                    "GBIF分类状态": gbif_status,
                    "GBIF重名状态": gbif_homonym,
                    "为什么需要校正": reason_text,
                }
            )
            total_candidates += 1

    write_csv(
        out_path,
        out_rows,
        fieldnames=[
            "原始名",
            "校正后的名字",
            "来源",
            "链接",
            "GBIF匹配类型",
            "GBIF置信度",
            "GBIF分类状态",
            "GBIF重名状态",
            "为什么需要校正",
        ],
    )

    print(f"[done] 输入问题物种数: {len(grouped)}")
    print(f"[done] 输出候选行数: {total_candidates}")
    print(f"[done] 幽灵名终止并打标数: {unresolved_marked}")
    print(f"[out] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
