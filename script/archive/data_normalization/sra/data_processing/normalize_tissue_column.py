#!/usr/bin/env python3
"""
Normalize the Tissue column in an SRA transcriptome metadata CSV.

The script maps raw Tissue values to a controlled vocabulary loaded from a text
file, adds `Tissue1.Corr` and `Tissue2.Corr` columns, and writes a review TSV
with mapping details. `Tissue2.Corr` is the fine-grained normalized tissue,
while `Tissue1.Corr` is a broader category derived from an embedded mapping
dictionary.

Example:
    python normalize_tissue_column.py \
        --input /home/iLepbase/data/clean/SRA/transcriptome_data_20260323.csv \
        --tissue-list /home/iLepbase/data/clean/SRA/tissue_list.txt \
        --in-place
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

UNKNOWN_TISSUE = "unknown"

TISSUE_LEVEL1_MAP = {
    "midgut": "digestive",
    "foregut": "digestive",
    "hindgut": "digestive",
    "gut": "digestive",
    "salivary gland": "digestive",
    "labial gland": "digestive",
    "silk gland": "digestive",
    "head": "head_neck",
    "mouthpart": "head_neck",
    "thorax": "thorax",
    "leg": "thorax",
    "wing": "thorax",
    "foot": "thorax",
    "abdomen": "abdomen",
    "ovipositor": "abdomen",
    "epidermis": "body_wall",
    "horn": "body_wall",
    "hemolymph": "circulatory",
    "heart": "circulatory",
    "fat body": "circulatory",
    "malpighian tubule": "excretory",
    "trachea": "respiratory",
    "muscle": "musculoskeletal",
    "brain": "nervous",
    "nervous system": "nervous",
    "antenna": "sensory",
    "eye": "sensory",
    "ovary": "reproductive",
    "testis": "reproductive",
    "reproductive system": "reproductive",
    "reproductive tract": "reproductive",
    "ejaculatory duct": "reproductive",
    "oviduct": "reproductive",
    "accessory gland": "reproductive",
    "pheromone gland": "endocrine",
    "prothoracic gland": "endocrine",
    "whole body": "whole_body",
    "carcass": "other",
    "serosa": "other",
    "unknown": "unknown",
}


MISSING_VALUES = {
    "",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "missing",
    "not applicable",
    "not collected",
    "not provided",
    "not available",
    "unknown",
}

CELL_LINE_MARKERS = (
    "cell line",
    "cell-line",
    "cell_line",
    "cultured cell",
    "cultured cells",
    "cell culture",
    "cells",
    "bmn",
    "bme",
    "cell-line",
)

WHOLE_BODY_EXACT_VALUES = {
    "body",
    "whole",
    "whole individual",
    "individual",
    "immature individual",
    "full body",
    "total body",
    "entire organism",
    "entirety",
    "the whole",
    "whole adult",
    "adult",
    "adults",
    "adult female",
    "adult male",
    "adult, body",
    "larval",
    "imago",
    "eggs",
    "holl body",
    "all",
    "individuls",
    "entire",
    "entire insect",
    "whole silkworm",
    "whole animals",
    "complete worm",
    "insect body",
    "body of beet armyworm",
    "all the insect body",
    "the whole adult",
    "the whole female aduit",
    "the whole male aduit",
    "adult body",
    "total tissue",
    "all tissue",
    "all tissues",
}

WHOLE_BODY_PATTERNS = (
    r"\bwhole body\b",
    r"\bwholebody\b",
    r"\bwhole organism\b",
    r"\bwhole specimen\b",
    r"\bwhole animal\b",
    r"\bwhole insect\b",
    r"\bwhole insects\b",
    r"\bwhole worm\b",
    r"\bwhole worm\d+\b",
    r"\bwhole animals\b",
    r"\bwhole larva\b",
    r"\bwhole larvae\b",
    r"\bwhole larvae x \d+\b",
    r"\bwhole larve\b",
    r"\bwhole embryos\b",
    r"\bwhole bodies\b",
    r"\bwhole organisms, pooled\b",
    r"\bwhole silkworm\b",
    r"\bentire body\b",
    r"\bentire insect\b",
    r"\ball body\b",
    r"\bthe whole body\b",
    r"\bthe whole insect\b",
    r"\bfemale adult whole worm\b",
    r"\badult moth\b",
    r"\blarval body\b",
    r"\bwhole insect tissue\b",
    r"\bwhole b\.?mori larva\b",
    r"\bwhole l\d+ larvae\b",
    r"\bwhole body\d+\b",
    r"\bbody\d+\b",
    r"\begg\d+\b",
    r"\blarvae\d+\b",
    r"\b\d+larvae\b",
    r"\badults?\d+\b",
    r"\bwhole\b.*\bbody\b",
    r"\bwhole bady\b",
    r"\bwhole\b.*\borganism\b",
    r"\binsect body\b",
    r"\bbody of beet armyworm\b",
    r"\b(?:male|female)\.body\b",
    r"\b(?:male|female)\s+adults?\b",
    r"\badult body\b",
    r"\badult female\b",
    r"\badult male\b",
    r"\bthe whole adult\b",
    r"\bthe whole female aduit\b",
    r"\bthe whole male aduit\b",
    r"\bmoth\b",
    r"\blarva\b",
    r"\blarvae\b",
    r"\bpupa\b",
    r"\bpupae\b",
    r"\bembryo\b",
    r"\bembryos\b",
    r"\bembyro\b",
    r"\begg\b",
    r"\beggs\b",
)

CANONICAL_PATTERNS = {
    "midgut": (
        r"\bmid[\s_-]*gut(?:\d+)?\b",
        r"\bmigdut\b",
        r"\bmiddut\b",
    ),
    "foregut": (r"\bfore[\s_-]*gut\b",),
    "hindgut": (r"\bhind[\s_-]*gut\b",),
    "gut": (
        r"\bgut\b",
        r"\balimentary tract\b",
        r"\balimentary tracts\b",
        r"\balimentary canal\b",
        r"\bintestinal tissue\b",
        r"\bmesenteron\b",
    ),
    "fat body": (
        r"\bfat[\s_-]*body\b",
        r"\bfatbody\b",
        r"\bfatbody\d+\b",
        r"\bfat\b",
        r"\bfatt[\s_-]*body\b",
        r"\bfatty[\s_-]*body\b",
        r"\bfatabody\b",
        r"\bfatbady\d*\b",
    ),
    "antenna": (
        r"\bantenna\b",
        r"\bantennae\b",
        r"\bantennae\d+(?:-\d+)?\b",
        r"\bantennal\b",
        r"\bantenae\b",
        r"\bantena\b",
        r"\bantennea\b",
        r"\bfeeler\b",
    ),
    "head": (r"\bhead(?:s)?(?:\d+(?:-\d+)?)?\b",),
    "silk gland": (
        r"\bsilk[\s_-]*gland\b",
        r"\bsilk[\s_-]*glands\b",
        r"\bsilkgland\d+\b",
        r"\banterior[\s_-]*silk[\s_-]*gland\b",
        r"\banterior[\s_-]*silk[\s_-]*glands\b",
        r"\banterior[\s_-]*silk[\s_-]*gland\d+\b",
        r"\bposterior[\s_-]*silk[\s_-]*gland\b",
        r"\bposterior[\s_-]*silk[\s_-]*glands\b",
        r"\bposterior[\s_-]*silk[\s_-]*gland\d+\b",
        r"\bmiddle[\s_-]*silk[\s_-]*gland\b",
        r"\bmiddle[\s_-]*silk[\s_-]*glands\b",
        r"\bmiddle[\s_-]*silk[\s_-]*gland\d+\b",
        r"\bpostsilk[\s_-]*gland\b",
        r"\bsilk glandl\b",
    ),
    "leg": (
        r"\bleg\b",
        r"\blegs\b",
        r"\bforeleg\b",
        r"\bforelegs\b",
        r"\bhindleg\b",
        r"\bhindlegs\b",
    ),
    "wing": (
        r"\bwing\b",
        r"\bwings\b",
        r"\bforewing\d*\b",
        r"\bforewings\b",
        r"\bhindwing\d*\b",
        r"\bhindwings\b",
        r"\bpupal forewings\b",
        r"\bwing disc\b",
        r"\bwing sector\b",
        r"\beyespot\b",
    ),
    "brain": (
        r"\bbrain(?:s)?(?:\d+)?\b",
        r"\bbrian-\d+\b",
        r"\bbrian\d+\b",
        r"\bganglion\b",
    ),
    "mouthpart": (
        r"\bmouthpart\b",
        r"\bmouthparts\b",
        r"\bproboscis\b",
        r"\bproboscises\b",
        r"\bmaxilla\b",
        r"\bmaxlla\b",
        r"\bmaxillae\b",
        r"\bmaxillary galea\b",
        r"\bmaxillary gelea\b",
        r"\bepipharynx\b",
        r"\blabrum\b",
        r"\btentacle\b",
        r"\bmaxillary palp\b",
        r"\bmaxillary palps\b",
        r"\bpalp\b",
        r"\bpalps\b",
    ),
    "epidermis": (r"\bepidermis\b", r"\bintegument\b", r"\bcuticle\b"),
    "ovary": (
        r"\bovary\b",
        r"\bovaries\b",
        r"\bovarian\b",
        r"\bovariole\b",
        r"\bovery\b",
    ),
    "abdomen": (r"\babdomen\b", r"\babdominal\b", r"\bsegment a\d+\b"),
    "thorax": (r"\bthorax\b", r"\bthoracic\b"),
    "testis": (r"\btestis\b", r"\btestes\b", r"\btesticles\b", r"\btestis\d+\b"),
    "reproductive system": (
        r"\breproductive system\b",
        r"\bgonad\b",
        r"\bgonads\b",
        r"\bsperm bundles\b",
    ),
    "reproductive tract": (
        r"\breproductive tract\b",
        r"\breproductive organ\b",
        r"\breproductive organs\b",
        r"\bbursa\b",
    ),
    "ejaculatory duct": (
        r"\bejaculatory duct\b",
        r"\bductus ejaculatorius simplex\b",
    ),
    "oviduct": (r"\boviduct\b",),
    "hemolymph": (
        r"\bhemolymph\b",
        r"\bhaemolymph(?:\s+\d+)?\b",
        r"\bhemocyte\b",
        r"\bhemocytes\b",
        r"\bhemacyte\b",
        r"\bblood cell\b",
    ),
    "malpighian tubule": (
        r"\bmalpighian tubule\b",
        r"\bmalpighian tubules\b",
        r"\bmalpighian tube\b",
        r"\bmalpighian tubes\b",
        r"\bmalpighian tubes?\d+\b",
        r"\bmalpighian[\s_-]*tubule\b",
        r"\bmalphigian tubules\b",
        r"\bmalpighi[_ ]replacate\b",
    ),
    "pheromone gland": (
        r"\bpheromone gland\b",
        r"\bpheromone glands\b",
        r"\bpheomone gland\b",
        r"\bpheromonal gland\b",
    ),
    "accessory gland": (
        r"\baccessory gland\b",
        r"\baccessory glands\b",
        r"\bsex accessory gland\b",
        r"\bbursal gland\b",
        r"\bsex gland\b",
    ),
    "labial gland": (r"\blabial glands?\b", r"\blabila glands?\b"),
    "salivary gland": (r"\bsalivary glands?\b",),
    "muscle": (r"\bmuscle\b", r"\bmuscles\b"),
    "eye": (r"\beye\b", r"\beyes\b", r"\bretina\b"),
    "ovipositor": (r"\bovipositor\b", r"\bovipositors\b"),
    "foot": (
        r"\bfoot\b",
        r"\bfeet\b",
        r"\btarsi\b",
        r"\btarsus\b",
        r"\bforetarsi\b",
        r"\bmidtarsi\b",
        r"\bhindtarsi\b",
        r"\bproleg\b",
    ),
    "nervous system": (
        r"\bcentral nervous system\b",
        r"\bnervous system\b",
        r"\blarval nervous system\b",
        r"\bventral nerve cord\b",
        r"\bnerve cord\b",
        r"\bnerve chord\b",
        r"\bventral nerve\b",
        r"\bnerve\b",
    ),
    "serosa": (r"\bserosa\b",),
    "prothoracic gland": (r"\bprothoracic gland\b", r"\bprothoracic glands\b"),
    "trachea": (r"\btrachea\b", r"\btracheal\b"),
    "heart": (r"\bheart\b", r"\bdorsal vessel\b"),
    "horn": (r"\bhorn\b",),
    "carcass": (r"\bcarcass\b",),
    "whole body": WHOLE_BODY_PATTERNS,
}


def normalize_text(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def looks_like_coordinate(value: str) -> bool:
    text = normalize_text(value)
    return bool(
        re.search(r"\b\d+(?:\.\d+)?\s*[ns]\b.*\b\d+(?:\.\d+)?\s*[ew]\b", text)
        or re.search(r"\b\d+(?:\.\d+)?\s*[ew]\b.*\b\d+(?:\.\d+)?\s*[ns]\b", text)
    )


def load_tissue_list(path: Path) -> list[str]:
    tissues = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not tissues:
        raise ValueError(f"No tissue names found in {path}")
    return tissues


def resolve_unknown_tissue(canonical_lookup: dict[str, str]) -> str:
    if UNKNOWN_TISSUE in canonical_lookup:
        return canonical_lookup[UNKNOWN_TISSUE]
    if "other" in canonical_lookup:
        return canonical_lookup["other"]
    raise ValueError("tissue_list.txt must include either 'unknown' or 'other'")


def collapse_tissue_level1(tissue2_value: str) -> str:
    parts = [part.strip() for part in tissue2_value.split(";") if part.strip()]
    categories: list[str] = []
    for part in parts:
        category = TISSUE_LEVEL1_MAP.get(part, "unknown")
        if category not in categories:
            categories.append(category)
    return ";".join(categories) if categories else "unknown"


def infer_tissue(
    raw_value: str,
    canonical_lookup: dict[str, str],
    canonical_order: dict[str, int],
    unknown_tissue_value: str,
) -> tuple[str, str]:
    value = raw_value.strip()
    normalized = normalize_text(value)

    if normalized in MISSING_VALUES:
        return unknown_tissue_value, "missing"
    if looks_like_coordinate(normalized):
        return unknown_tissue_value, "coordinate_like"
    if any(marker in normalized for marker in CELL_LINE_MARKERS):
        return unknown_tissue_value, "cell_line_or_cells"
    if normalized in WHOLE_BODY_EXACT_VALUES:
        return "whole body", "pattern:whole body"

    exact_match = canonical_lookup.get(normalized)
    if exact_match is not None:
        return exact_match, "exact"

    hits: list[str] = []
    for canonical, patterns in CANONICAL_PATTERNS.items():
        if canonical not in canonical_lookup:
            continue
        if any(re.search(pattern, normalized) for pattern in patterns):
            hits.append(canonical)

    if "midgut" in hits and "gut" in hits:
        hits.remove("gut")
    if "foregut" in hits and "gut" in hits:
        hits.remove("gut")
    if "hindgut" in hits and "gut" in hits:
        hits.remove("gut")
    if "ejaculatory duct" in hits and "reproductive tract" in hits:
        hits.remove("reproductive tract")
    if "oviduct" in hits and "reproductive tract" in hits:
        hits.remove("reproductive tract")
    if "ovary" in hits and "reproductive system" in hits:
        hits.remove("reproductive system")
    if "testis" in hits and "reproductive system" in hits:
        hits.remove("reproductive system")
    if "reproductive tract" in hits and "reproductive system" in hits:
        hits.remove("reproductive system")
    if normalized.startswith("head") and "without antenna" in normalized and "antenna" in hits:
        hits.remove("antenna")
    if "foot" in hits and "leg" in hits and "tarsi" not in normalized and "foot" not in normalized and "feet" not in normalized:
        hits.remove("foot")
    if "whole body" in hits and len(hits) > 1:
        if any(token in normalized for token in ("without", "minus", "apart from")):
            pass
        else:
            hits.remove("whole body")

    unique_hits = sorted(set(hits), key=lambda tissue: canonical_order.get(tissue, 10**9))
    if len(unique_hits) == 1:
        return canonical_lookup[unique_hits[0]], f"pattern:{unique_hits[0]}"
    if len(unique_hits) > 1:
        joined = ";".join(canonical_lookup[tissue] for tissue in unique_hits)
        return joined, "multi_match:" + "|".join(unique_hits)
    return unknown_tissue_value, "no_match"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input CSV path.")
    parser.add_argument("--tissue-list", required=True, type=Path, help="Controlled tissue list.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output CSV path. If omitted, use --in-place or write a suffixed file.",
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        help="Optional review TSV path. Defaults next to the output CSV.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input CSV with the added tissue correction columns.",
    )
    return parser


def resolve_output_paths(input_path: Path, output_path: Path | None, review_path: Path | None, in_place: bool) -> tuple[Path, Path]:
    if in_place:
        csv_output = input_path
    elif output_path is not None:
        csv_output = output_path
    else:
        csv_output = input_path.with_name(input_path.stem + ".tissue_corr.csv")

    if review_path is not None:
        resolved_review = review_path
    else:
        resolved_review = csv_output.with_name(csv_output.stem + ".tissue_corr_review.tsv")

    return csv_output, resolved_review


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    canonical_tissues = load_tissue_list(args.tissue_list)
    canonical_lookup = {normalize_text(tissue): tissue for tissue in canonical_tissues}
    canonical_order = {
        normalize_text(tissue): index for index, tissue in enumerate(canonical_tissues)
    }
    unknown_tissue_value = resolve_unknown_tissue(canonical_lookup)

    output_csv, review_tsv = resolve_output_paths(args.input, args.output, args.review_output, args.in_place)
    temp_csv = output_csv.with_suffix(output_csv.suffix + ".tmp")

    review_counter: Counter[tuple[str, str, str, str]] = Counter()

    with args.input.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {args.input}")
        if "Tissue" not in reader.fieldnames:
            raise ValueError("Input CSV is missing the 'Tissue' column")

        output_fields = [
            field
            for field in reader.fieldnames
            if field not in {"Tissue.Corr", "Tissue1.Corr", "Tissue2.Corr"}
        ]
        tissue_index = output_fields.index("Tissue")
        output_fields[tissue_index + 1:tissue_index + 1] = ["Tissue1.Corr", "Tissue2.Corr"]

        with temp_csv.open("w", encoding="utf-8-sig", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=output_fields)
            writer.writeheader()

            for row in reader:
                raw_tissue = (row.get("Tissue") or "").strip()
                row.pop("Tissue.Corr", None)
                row.pop("Tissue1.Corr", None)
                row.pop("Tissue2.Corr", None)
                tissue2_corr, reason = infer_tissue(
                    raw_tissue,
                    canonical_lookup,
                    canonical_order,
                    unknown_tissue_value,
                )
                tissue1_corr = collapse_tissue_level1(tissue2_corr)
                row["Tissue1.Corr"] = tissue1_corr
                row["Tissue2.Corr"] = tissue2_corr
                writer.writerow(row)
                review_counter[(raw_tissue or "N/A", tissue1_corr, tissue2_corr, reason)] += 1

    temp_csv.replace(output_csv)

    with review_tsv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["raw_tissue", "count", "tissue1_corr", "tissue2_corr", "reason"])
        for (raw_tissue, tissue1_corr, tissue2_corr, reason), count in sorted(
            review_counter.items(),
            key=lambda item: (-item[1], item[0][2], item[0][1], item[0][0]),
        ):
            writer.writerow([raw_tissue, count, tissue1_corr, tissue2_corr, reason])

    print(f"Wrote normalized CSV: {output_csv}")
    print(f"Wrote review TSV: {review_tsv}")
    print(f"Distinct raw Tissue values reviewed: {len(review_counter)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
