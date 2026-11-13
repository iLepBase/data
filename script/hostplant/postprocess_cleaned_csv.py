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


DEFAULT_DROP_NOTES = ("no_match", "review_needed")


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


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Post-process cleaned taxonomy CSV by dropping unresolved rows and "
            "normalizing authority text."
        )
    )
    parser.add_argument(
        "--input-csv",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.cleaned.csv",
        help="Input cleaned CSV path",
    )
    parser.add_argument(
        "--output-csv",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.cleaned.final.csv",
        help="Output cleaned CSV path",
    )
    parser.add_argument(
        "--drop-note",
        action="append",
        dest="drop_notes",
        help=(
            "Drop rows whose note contains this token. "
            "Can be used multiple times. Defaults to no_match and review_needed."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_csv = Path(args.input_csv)
    output_csv = Path(args.output_csv)
    drop_notes = {s(x) for x in (args.drop_notes or DEFAULT_DROP_NOTES) if s(x)}

    if not input_csv.exists():
        raise FileNotFoundError(f"input csv not found: {input_csv}")

    rows, fieldnames = read_csv(input_csv)
    kept_rows: list[dict[str, str]] = []
    dropped_counter: Counter[str] = Counter()
    authority_changed = 0
    dropped_rows = 0

    for row in rows:
        note_tokens = split_notes(row.get("note", ""))
        hit_tokens = sorted(note_tokens & drop_notes)
        if hit_tokens:
            dropped_rows += 1
            for token in hit_tokens:
                dropped_counter[token] += 1
            continue

        out = dict(row)
        old_authority = s(out.get("authority"))
        scientific_name = s(out.get("species")) or s(out.get("origin Species"))
        new_authority = normalize_authority(old_authority, scientific_name)
        if new_authority != old_authority:
            authority_changed += 1
            out["authority"] = new_authority
        kept_rows.append(out)

    write_csv(output_csv, kept_rows, fieldnames)

    print(f"[done] input_rows={len(rows)}")
    print(f"[done] kept_rows={len(kept_rows)}")
    print(f"[done] dropped_rows={dropped_rows}")
    print(f"[done] authority_normalized={authority_changed}")
    if dropped_counter:
        print(
            "[done] dropped_by_note="
            + " ".join(f"{token}:{count}" for token, count in sorted(dropped_counter.items()))
        )
    print(f"[out] {output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
