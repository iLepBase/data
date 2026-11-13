#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


UNRESOLVED_TAG = "[Unresolved: Possible Nomen nudum / Typo]（未解决：疑似裸名或拼写错误）"
CLEAN_BLOCK_NOTES = {"review_needed", "no_match"}


def s(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def split_notes(note: str) -> set[str]:
    return {part.strip() for part in s(note).split("|") if part.strip()}


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", s(text).replace("\xa0", " ")).strip()


def strip_scientific_name_prefix(text: str, scientific_name: str) -> str:
    text = norm_space(text)
    scientific_name = norm_space(scientific_name)
    if not text or not scientific_name:
        return text
    variants = [scientific_name, f'"{scientific_name}"', f"'{scientific_name}'"]
    for variant in variants:
        pattern = re.compile(rf"^{re.escape(variant)}\s+", flags=re.IGNORECASE)
        stripped = pattern.sub("", text, count=1).strip()
        if stripped != text:
            return stripped
    return text


def normalize_authority(authority: str, scientific_name: str = "") -> str:
    text = strip_scientific_name_prefix(authority, scientific_name)
    text = text.replace("(", " ").replace(")", " ")
    text = re.sub(r"\s+([,;:])", r"\1", text)
    text = norm_space(text).strip("'\" ")
    return text


def read_csv(path: Path, encoding: str = "utf-8") -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding=encoding, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def write_csv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str],
    encoding: str = "utf-8",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def remap_clean_fieldnames(fieldnames: list[str]) -> list[str]:
    return ["source" if name == "source (ncbi gbif)" else name for name in fieldnames]


def transform_clean_row(row: dict[str, str], cleaned_fieldnames: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    scientific_name = s(row.get("species")) or s(row.get("origin Species"))
    for key in cleaned_fieldnames:
        if key == "source":
            out[key] = s(row.get("source (ncbi gbif)", ""))
        elif key == "authority":
            out[key] = normalize_authority(row.get("authority", ""), scientific_name)
        else:
            out[key] = s(row.get(key, ""))
    return out


def is_clean_species_row(row: dict[str, str]) -> bool:
    note_tokens = split_notes(row.get("note", ""))
    if note_tokens & CLEAN_BLOCK_NOTES:
        return False
    return bool(s(row.get("species")))


def is_clean_genus_row(row: dict[str, str]) -> bool:
    note_tokens = split_notes(row.get("note", ""))
    if note_tokens & CLEAN_BLOCK_NOTES:
        return False
    return bool(s(row.get("genuis"))) and not s(row.get("species"))


def is_review_none_row(row: dict[str, str]) -> bool:
    return UNRESOLVED_TAG in s(row.get("校正后的名字"))


def is_review_genus_row(row: dict[str, str]) -> bool:
    name = s(row.get("校正后的名字"))
    if not name or is_review_none_row(row):
        return False
    if "（仅属级候选）" in name:
        return True
    return len(name.split()) == 1


def dedupe_rows(rows: list[dict[str, str]], fieldnames: list[str]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = tuple(s(row.get(name, "")) for name in fieldnames)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate species/genus clean outputs and split manual review outputs."
    )
    parser.add_argument(
        "--cleaned-csv",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.cleaned.csv",
        help="Input cleaned CSV path",
    )
    parser.add_argument(
        "--manual-csv",
        default="/home/iLepbase/data/raw/hostplant/人工校正清单.csv",
        help="Input manual review CSV path",
    )
    parser.add_argument(
        "--out-clean-sp",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.cleaned.sp.csv",
        help="Output species-level clean CSV path",
    )
    parser.add_argument(
        "--out-clean-genus",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.cleaned.genus.csv",
        help="Output genus-level clean CSV path",
    )
    parser.add_argument(
        "--out-review-genus",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.review.genus.csv",
        help="Output genus-only review CSV path",
    )
    parser.add_argument(
        "--out-review-none",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.review.none.csv",
        help="Output unresolved review CSV path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cleaned_csv = Path(args.cleaned_csv)
    manual_csv = Path(args.manual_csv)

    if not cleaned_csv.exists():
        raise FileNotFoundError(f"cleaned csv not found: {cleaned_csv}")
    if not manual_csv.exists():
        raise FileNotFoundError(f"manual csv not found: {manual_csv}")

    clean_rows, clean_fieldnames_raw = read_csv(cleaned_csv, encoding="utf-8")
    clean_fieldnames = remap_clean_fieldnames(clean_fieldnames_raw)

    sp_rows = [
        transform_clean_row(row, clean_fieldnames)
        for row in clean_rows
        if is_clean_species_row(row)
    ]
    genus_rows = [
        transform_clean_row(row, clean_fieldnames)
        for row in clean_rows
        if is_clean_genus_row(row)
    ]

    manual_rows, manual_fieldnames = read_csv(manual_csv, encoding="utf-8-sig")
    review_genus_rows = dedupe_rows(
        [dict(row) for row in manual_rows if is_review_genus_row(row)],
        manual_fieldnames,
    )
    review_none_rows = dedupe_rows(
        [dict(row) for row in manual_rows if is_review_none_row(row)],
        manual_fieldnames,
    )

    write_csv(Path(args.out_clean_sp), sp_rows, clean_fieldnames, encoding="utf-8")
    write_csv(Path(args.out_clean_genus), genus_rows, clean_fieldnames, encoding="utf-8")
    write_csv(
        Path(args.out_review_genus),
        review_genus_rows,
        manual_fieldnames,
        encoding="utf-8-sig",
    )
    write_csv(
        Path(args.out_review_none),
        review_none_rows,
        manual_fieldnames,
        encoding="utf-8-sig",
    )

    input_counter = Counter()
    for row in clean_rows:
        note_tokens = split_notes(row.get("note", ""))
        if "review_needed" in note_tokens:
            input_counter["review_needed"] += 1
        if "no_match" in note_tokens:
            input_counter["no_match"] += 1
        if s(row.get("species")):
            input_counter["species_any"] += 1
        elif s(row.get("genuis")):
            input_counter["genus_any"] += 1
        else:
            input_counter["none_any"] += 1

    print(f"[done] cleaned_input_rows={len(clean_rows)}")
    print(f"[done] cleaned_species_rows={len(sp_rows)}")
    print(f"[done] cleaned_genus_rows={len(genus_rows)}")
    print(
        "[done] cleaned_filtered_out="
        f"review_needed:{input_counter['review_needed']} "
        f"no_match:{input_counter['no_match']}"
    )
    print(f"[done] manual_input_rows={len(manual_rows)}")
    print(f"[done] review_genus_rows={len(review_genus_rows)}")
    print(f"[done] review_none_rows={len(review_none_rows)}")
    print(f"[out] {args.out_clean_sp}")
    print(f"[out] {args.out_clean_genus}")
    print(f"[out] {args.out_review_genus}")
    print(f"[out] {args.out_review_none}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
