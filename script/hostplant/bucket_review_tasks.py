#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any


STAGE_PRIORITY = {
    "gbif_species": 1,
    "gbif_subfamily_backfill_ncbi_genus": 2,
    "ncbi_genus_fallback": 3,
    "runtime": 9,
}


def s(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def review_sort_key(row: dict[str, str]) -> tuple[int, int, str]:
    stage = s(row.get("stage"))
    prio = STAGE_PRIORITY.get(stage, 99)
    has_reco = 1 if s(row.get("recommended_candidate")) else 0
    created_at = s(row.get("created_at"))
    # smaller prio is better, larger has_reco is better, newer created_at is better
    return (prio, -has_reco, f"{created_at}")


def dedupe_review_rows(review_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in review_rows:
        sp = s(row.get("origin Species"))
        if not sp:
            continue
        grouped[sp].append(row)

    deduped = []
    for sp, rows in grouped.items():
        sorted_rows = sorted(rows, key=review_sort_key)
        selected = sorted_rows[0]
        merged_reasons = sorted({s(x.get("reason")) for x in rows if s(x.get("reason"))})
        merged_stages = sorted({s(x.get("stage")) for x in rows if s(x.get("stage"))})

        out = dict(selected)
        out["origin Species"] = sp
        out["review_row_count"] = str(len(rows))
        out["stages_all"] = " | ".join(merged_stages)
        out["reasons_all"] = " | ".join(merged_reasons)
        deduped.append(out)
    return sorted(deduped, key=lambda x: s(x.get("origin Species")).lower())


def bucket_name(clean_row: dict[str, str]) -> tuple[str, str]:
    source = s(clean_row.get("source (ncbi gbif)"))
    subfamily = s(clean_row.get("subfamliy"))
    genus = s(clean_row.get("genuis"))
    species = s(clean_row.get("species"))
    sid = s(clean_row.get("sid"))

    if species and sid:
        return "A", "has_species_sid_check_only"
    if genus and subfamily and not species:
        return "B", "has_genus_subfamily_fill_species_manual"
    if genus and not subfamily:
        return "C", "has_genus_only_manual"
    if not source:
        return "D", "no_source_full_manual"
    return "D", "fallback_other_manual"


def manual_action(bucket: str) -> str:
    if bucket == "A":
        return "Check recommended candidate; resolve ambiguity; fill/confirm subfamliy/sfid only."
    if bucket == "B":
        return "Confirm species-level match and fill species/sid; keep existing genus/subfamily."
    if bucket == "C":
        return "Fill subfamliy/sfid and species/sid based on manual verification."
    return "Full manual curation needed (source/genus/species unresolved)."


def build_bucket_rows(
    deduped_review_rows: list[dict[str, str]],
    cleaned_map: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    rows_a: list[dict[str, str]] = []
    rows_b: list[dict[str, str]] = []
    rows_c: list[dict[str, str]] = []
    rows_d: list[dict[str, str]] = []

    for rr in deduped_review_rows:
        sp = s(rr.get("origin Species"))
        cr = cleaned_map.get(sp, {})
        bucket, rule = bucket_name(cr)

        out = {
            "origin Species": sp,
            "bucket": bucket,
            "bucket_rule": rule,
            "manual_action": manual_action(bucket),
            "stage_selected": s(rr.get("stage")),
            "reason_selected": s(rr.get("reason")),
            "recommended_candidate": s(rr.get("recommended_candidate")),
            "query": s(rr.get("query")),
            "source_links": s(rr.get("source_links")),
            "created_at_selected": s(rr.get("created_at")),
            "review_row_count": s(rr.get("review_row_count")),
            "stages_all": s(rr.get("stages_all")),
            "reasons_all": s(rr.get("reasons_all")),
            "clean.source": s(cr.get("source (ncbi gbif)")),
            "clean.subfamliy": s(cr.get("subfamliy")),
            "clean.sfid": s(cr.get("sfid")),
            "clean.genuis": s(cr.get("genuis")),
            "clean.gid": s(cr.get("gid")),
            "clean.species": s(cr.get("species")),
            "clean.sid": s(cr.get("sid")),
            "clean.note": s(cr.get("note")),
            "clean.source_link": s(cr.get("source link")),
        }

        if bucket == "A":
            rows_a.append(out)
        elif bucket == "B":
            rows_b.append(out)
        elif bucket == "C":
            rows_c.append(out)
        else:
            rows_d.append(out)

    return rows_a, rows_b, rows_c, rows_d


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deduplicate review.csv and generate A/B/C/D manual curation bucket CSVs."
    )
    parser.add_argument(
        "--cleaned-csv",
        default="/home/iLepbase/data/raw/hostplant/鳞翅目.cleaned.csv",
        help="Path to cleaned CSV",
    )
    parser.add_argument(
        "--review-csv",
        default="/home/iLepbase/data/raw/hostplant/鳞翅目.review.csv",
        help="Path to review CSV",
    )
    parser.add_argument(
        "--out-dir",
        default="/home/iLepbase/data/raw/hostplant",
        help="Output directory for bucket files",
    )
    parser.add_argument(
        "--prefix",
        default="鳞翅目",
        help="Output filename prefix",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cleaned_path = Path(args.cleaned_csv)
    review_path = Path(args.review_csv)
    out_dir = Path(args.out_dir)
    prefix = s(args.prefix) or "hostplant"

    if not cleaned_path.exists():
        raise FileNotFoundError(f"cleaned csv not found: {cleaned_path}")
    if not review_path.exists():
        raise FileNotFoundError(f"review csv not found: {review_path}")

    cleaned_rows = read_csv(cleaned_path)
    review_rows = read_csv(review_path)
    cleaned_map = {s(r.get("origin Species")): r for r in cleaned_rows if s(r.get("origin Species"))}

    deduped_review = dedupe_review_rows(review_rows)
    rows_a, rows_b, rows_c, rows_d = build_bucket_rows(deduped_review, cleaned_map)

    out_fields = [
        "origin Species",
        "bucket",
        "bucket_rule",
        "manual_action",
        "stage_selected",
        "reason_selected",
        "recommended_candidate",
        "query",
        "source_links",
        "created_at_selected",
        "review_row_count",
        "stages_all",
        "reasons_all",
        "clean.source",
        "clean.subfamliy",
        "clean.sfid",
        "clean.genuis",
        "clean.gid",
        "clean.species",
        "clean.sid",
        "clean.note",
        "clean.source_link",
    ]

    write_csv(out_dir / f"{prefix}.review.dedup.csv", deduped_review, fieldnames=[
        "origin Species",
        "stage",
        "reason",
        "query",
        "candidates_json",
        "recommended_candidate",
        "source_links",
        "created_at",
        "review_row_count",
        "stages_all",
        "reasons_all",
    ])
    write_csv(out_dir / f"{prefix}.bucket_A.csv", rows_a, fieldnames=out_fields)
    write_csv(out_dir / f"{prefix}.bucket_B.csv", rows_b, fieldnames=out_fields)
    write_csv(out_dir / f"{prefix}.bucket_C.csv", rows_c, fieldnames=out_fields)
    write_csv(out_dir / f"{prefix}.bucket_D.csv", rows_d, fieldnames=out_fields)
    write_csv(out_dir / f"{prefix}.manual_all.csv", rows_a + rows_b + rows_c + rows_d, fieldnames=out_fields)

    print(f"[done] dedup_review={len(deduped_review)} from raw_review={len(review_rows)}")
    print(f"[done] A={len(rows_a)} B={len(rows_b)} C={len(rows_c)} D={len(rows_d)}")
    print(f"[out] {out_dir / f'{prefix}.review.dedup.csv'}")
    print(f"[out] {out_dir / f'{prefix}.bucket_A.csv'}")
    print(f"[out] {out_dir / f'{prefix}.bucket_B.csv'}")
    print(f"[out] {out_dir / f'{prefix}.bucket_C.csv'}")
    print(f"[out] {out_dir / f'{prefix}.bucket_D.csv'}")
    print(f"[out] {out_dir / f'{prefix}.manual_all.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
