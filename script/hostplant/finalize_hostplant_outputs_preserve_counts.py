#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "openpyxl>=3.1.2",
# ]
# ///

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from openpyxl import load_workbook


OUTPUT_COLUMNS = [
    "origin Species",
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


def s(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", s(text).replace("\xa0", " ")).strip()


def has_dotted_species_marker(text: str) -> bool:
    return "." in norm_space(text)


def split_notes(note: str) -> set[str]:
    return {part.strip() for part in s(note).split("|") if part.strip()}


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


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def load_input_species(path: Path) -> list[str]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(min_row=1, max_row=1, values_only=True)
    headers = [s(value) for value in next(rows)]
    try:
        species_idx = headers.index("origin Species")
    except ValueError as exc:
        raise ValueError(f"'origin Species' not found in {path}") from exc

    names: list[str] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        names.append(s(row[species_idx]))
    wb.close()
    return names


def transform_cleaned_row(row: dict[str, str], origin_species: str) -> dict[str, str]:
    scientific_name = s(row.get("species")) or origin_species
    return {
        "origin Species": origin_species,
        "subfamliy": s(row.get("subfamliy")),
        "sfid": s(row.get("sfid")),
        "genuis": s(row.get("genuis")),
        "gid": s(row.get("gid")),
        "species": s(row.get("species")),
        "authority": normalize_authority(s(row.get("authority")), scientific_name),
        "basionym": s(row.get("basionym")),
        "sid": s(row.get("sid")),
        "source": s(row.get("source (ncbi gbif)")),
        "source link": s(row.get("source link")),
        "note": s(row.get("note")),
    }


def make_dotted_row(origin_species: str) -> dict[str, str]:
    return {
        "origin Species": origin_species,
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
        "note": "skipped_name_contains_dot",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split hostplant cleaning outputs into count-preserving CSV files."
    )
    parser.add_argument(
        "--input-xlsx",
        default="/home/iLepbase/data/raw/hostplant/Lepidoptera.xlsx",
        help="Source workbook containing origin Species",
    )
    parser.add_argument(
        "--cleaned-csv",
        default="/home/iLepbase/data/raw/hostplant/new/Lepidoptera.cleaned.raw.csv",
        help="Intermediate cleaned CSV from hostplant_taxonomy.py",
    )
    parser.add_argument(
        "--out-dir",
        default="/home/iLepbase/data/raw/hostplant/new",
        help="Output directory for final CSV files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_xlsx = Path(args.input_xlsx)
    cleaned_csv = Path(args.cleaned_csv)
    out_dir = Path(args.out_dir)

    if not input_xlsx.exists():
        raise FileNotFoundError(f"input xlsx not found: {input_xlsx}")
    if not cleaned_csv.exists():
        raise FileNotFoundError(f"cleaned csv not found: {cleaned_csv}")

    input_species = load_input_species(input_xlsx)
    cleaned_rows, _ = read_csv(cleaned_csv, encoding="utf-8")

    cleaned_by_name: dict[str, list[dict[str, str]]] = {}
    for row in cleaned_rows:
        key = norm_space(row.get("origin Species", ""))
        if not key:
            continue
        cleaned_by_name.setdefault(key, []).append(row)

    input_non_dot_names = {norm_space(name) for name in input_species if name and not has_dotted_species_marker(name)}
    cleaned_names = set(cleaned_by_name)
    missing_in_cleaned = sorted(input_non_dot_names - cleaned_names)
    extra_in_cleaned = sorted(cleaned_names - input_non_dot_names)
    if missing_in_cleaned or extra_in_cleaned:
        details = {
            "missing_in_cleaned": missing_in_cleaned[:20],
            "extra_in_cleaned": extra_in_cleaned[:20],
            "missing_count": len(missing_in_cleaned),
            "extra_count": len(extra_in_cleaned),
        }
        raise ValueError(f"input/cleaned mismatch: {json.dumps(details, ensure_ascii=False)}")

    outputs: dict[str, list[dict[str, str]]] = {
        "cleaned_sp": [],
        "cleaned_genus": [],
        "review_subfamily": [],
        "review_genus": [],
        "review_none": [],
        "review_dot": [],
    }

    anomalies: list[str] = []
    for origin_species in input_species:
        if has_dotted_species_marker(origin_species):
            outputs["review_dot"].append(make_dotted_row(origin_species))
            continue

        key = norm_space(origin_species)
        candidate_rows = cleaned_by_name.get(key, [])
        if not candidate_rows:
            anomaly_row = make_dotted_row(origin_species)
            anomaly_row["note"] = "missing_from_cleaned"
            outputs["review_none"].append(anomaly_row)
            anomalies.append(origin_species)
            continue
        row = next(
            (item for item in candidate_rows if s(item.get("origin Species")) == origin_species),
            candidate_rows[0],
        )

        final_row = transform_cleaned_row(row, origin_species)
        note_tokens = split_notes(final_row.get("note", ""))
        has_species = bool(s(final_row.get("species")))
        has_genus = bool(s(final_row.get("genuis")))

        if "no_match" in note_tokens:
            outputs["review_none"].append(final_row)
        elif "genus_fallback" in note_tokens and "no_match" not in note_tokens and has_genus and not has_species:
            outputs["cleaned_genus"].append(final_row)
        elif "subfamily_backfill_needs_review" in note_tokens and has_species:
            outputs["review_subfamily"].append(final_row)
        elif "review_needed" in note_tokens:
            if has_species:
                outputs["review_subfamily"].append(final_row)
            elif has_genus:
                outputs["review_genus"].append(final_row)
            else:
                outputs["review_none"].append(final_row)
        else:
            if has_species:
                outputs["cleaned_sp"].append(final_row)
            elif has_genus:
                outputs["cleaned_genus"].append(final_row)
            else:
                outputs["review_none"].append(final_row)
                anomalies.append(origin_species)

    count_summary = {
        "input_total": len(input_species),
        "input_dot_names": len(outputs["review_dot"]),
        "input_non_dot_names": len(input_species) - len(outputs["review_dot"]),
        "cleaned_sp": len(outputs["cleaned_sp"]),
        "cleaned_genus": len(outputs["cleaned_genus"]),
        "review_subfamily": len(outputs["review_subfamily"]),
        "review_genus": len(outputs["review_genus"]),
        "review_none": len(outputs["review_none"]),
        "review_dot": len(outputs["review_dot"]),
        "output_total": sum(len(rows) for rows in outputs.values()),
        "anomaly_rows": len(anomalies),
    }

    if count_summary["output_total"] != count_summary["input_total"]:
        raise ValueError(
            "output count mismatch: "
            + json.dumps(count_summary, ensure_ascii=False, sort_keys=True)
        )

    genus_subfamily_missing_rows = [
        row for row in outputs["cleaned_genus"] if not s(row.get("subfamliy"))
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "Lepidoptera.cleaned.sp.csv", outputs["cleaned_sp"], OUTPUT_COLUMNS)
    write_csv(out_dir / "Lepidoptera.cleaned.genus.csv", outputs["cleaned_genus"], OUTPUT_COLUMNS)
    write_csv(
        out_dir / "Lepidoptera.review.subfamily.csv",
        outputs["review_subfamily"],
        OUTPUT_COLUMNS,
    )
    write_csv(out_dir / "Lepidoptera.review.genus.csv", outputs["review_genus"], OUTPUT_COLUMNS)
    write_csv(out_dir / "Lepidoptera.review.none.csv", outputs["review_none"], OUTPUT_COLUMNS)
    write_csv(out_dir / "Lepidoptera.review.dot.csv", outputs["review_dot"], OUTPUT_COLUMNS)
    write_csv(
        out_dir / "Lepidoptera.review.genus_subfamily_missing.csv",
        genus_subfamily_missing_rows,
        OUTPUT_COLUMNS,
    )

    summary_path = out_dir / "Lepidoptera.counts.json"
    summary_path.write_text(
        json.dumps(count_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for key, value in count_summary.items():
        print(f"[count] {key}={value}")
    print(f"[sidecar] genus_subfamily_missing={len(genus_subfamily_missing_rows)}")
    print(f"[out] {summary_path}")
    print(f"[out] {out_dir / 'Lepidoptera.cleaned.sp.csv'}")
    print(f"[out] {out_dir / 'Lepidoptera.cleaned.genus.csv'}")
    print(f"[out] {out_dir / 'Lepidoptera.review.subfamily.csv'}")
    print(f"[out] {out_dir / 'Lepidoptera.review.genus.csv'}")
    print(f"[out] {out_dir / 'Lepidoptera.review.none.csv'}")
    print(f"[out] {out_dir / 'Lepidoptera.review.dot.csv'}")
    print(f"[out] {out_dir / 'Lepidoptera.review.genus_subfamily_missing.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
