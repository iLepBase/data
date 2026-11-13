#!/usr/bin/env python3
"""
Normalize the Stage column in an SRA transcriptome metadata CSV.

The script maps raw Stage values to a controlled vocabulary loaded from a text
file, adds `Stage1.Corr` and `Stage2.Corr` columns, and writes a review TSV with
mapping details. `Stage1.Corr` is the coarse stage and `Stage2.Corr` preserves
more specific instar information when available.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


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
    "undetermined",
    "restricted access",
}

ORDINAL_WORDS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
)

CARDINAL_WORDS = (
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
)

WORD_TO_NUM = {
    "one": 1,
    "first": 1,
    "two": 2,
    "second": 2,
    "three": 3,
    "third": 3,
    "four": 4,
    "fourth": 4,
    "forth": 4,
    "five": 5,
    "fifth": 5,
    "fith": 5,
    "six": 6,
    "sixth": 6,
    "seven": 7,
    "seventh": 7,
    "eight": 8,
    "eighth": 8,
    "nine": 9,
    "ninth": 9,
    "ten": 10,
    "tenth": 10,
    "eleven": 11,
    "eleventh": 11,
    "twelve": 12,
    "twelfth": 12,
}

ROMAN_TO_NUM = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
    "xi": 11,
    "xii": 12,
}


def normalize_text(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def to_ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix} instar"


def to_l_code(value: int) -> str:
    return f"L{value}"


def to_p_code(value: int) -> str:
    return f"P{value}"


def to_l_day_code(instar: int, day: int) -> str:
    return f"L{instar}D{day}"


def parse_stage_number(token: str) -> int | None:
    token = token.strip().lower()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in WORD_TO_NUM:
        return WORD_TO_NUM[token]
    if token in ROMAN_TO_NUM:
        return ROMAN_TO_NUM[token]
    return None


def load_stage_list(path: Path) -> list[str]:
    stages = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not stages:
        raise ValueError(f"No stage names found in {path}")
    return stages


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def detect_stage_flags(text: str) -> dict[str, bool]:
    return {
        "egg": contains_any(
            text,
            (
                r"\begg\b",
                r"\beggs\b",
                r"\begg mass\b",
                r"\begg stage\b",
            ),
        ),
        "embryo": contains_any(
            text,
            (
                r"\bembryo\b",
                r"\bembryos\b",
                r"\bembyro\b",
                r"\bwhole embryos\b",
                r"\bembryonic\b",
                r"\bhpo\b",
                r"\bpost oviposition\b",
                r"\bafter oviposition\b",
                r"\b\d+\s*hour post oviposition\b",
            ),
        ),
        "larva": contains_any(
            text,
            (
                r"\blarva\b",
                r"\blarvae\b",
                r"\blarve\b",
                r"\blarvae\d+\b",
                r"\blarval\b",
                r"\bcaterpillar\b",
                r"\bneonate\b",
                r"\bpost hatch\b",
                r"\bmature larva\b",
                r"\bwanderer\b",
                r"\bwandering\b",
            ),
        ),
        "instar": contains_any(
            text,
            (
                r"\binstar\b",
                r"\binstars\b",
                r"\binstar[a-z0-9]+\b",
                r"\binatsr\b",
                r"\bl\d+(?:d\d+)?\b",
                r"\b\d+l(?:\d+d?)?\b",
                r"\b\d+(?:st|nd|rd|th)\s+instar\b",
                r"\b(?:"
                + "|".join(ORDINAL_WORDS)
                + r")\s+instar\b",
                r"\b\d+(?:st|nd|rd|th)?-day fifth-?instar\b",
                r"\b(?:"
                + "|".join(ORDINAL_WORDS)
                + r"|\d+(?:st|nd|rd|th)?)\s+age\b",
                r"\b(?:"
                + "|".join(CARDINAL_WORDS)
                + r"|\d+(?:st|nd|rd|th)?)\s+ages?\b",
                r"\bmolting stage [ivx]+\b",
            ),
        ),
        "prepupa": contains_any(
            text,
            (
                r"\bprepupa\b",
                r"\bprepupae\b",
                r"\bprepupal\b",
                r"\bpre-pupa\b",
                r"\bpre pupa\b",
                r"\bexact prepupa\b",
                r"\bpp\b",
            ),
        ),
        "pupa": contains_any(
            text,
            (
                r"\bpupa\b",
                r"\bpupae\b",
                r"\bpupal\b",
                r"\bchrysalis\b",
                r"\bpost-pupation\b",
                r"\bpost pupation\b",
                r"\bafter pupation\b",
                r"\bfrom pupation\b",
                r"\bday ?\d+.*pupa\b",
                r"\bp\d+\b",
                r"\blate-pupal\b",
                r"\b\d+% pupa\b",
            ),
        ),
        "adult": contains_any(
            text,
            (
                r"\badult\b",
                r"\badults\b",
                r"\badults\d+\b",
                r"\badut\b",
                r"\bimago\b",
                r"\bmoth\b",
                r"\bmoths\b",
                r"\bemerg(?:e|ed|ence|ing)\b",
                r"\beclos(?:e|ed|ion)\b",
                r"\beclosion\b",
                r"\bfreshly eclosed\b",
                r"\bvirgin\b",
            ),
        ),
        "explicit_mixed": contains_any(
            text,
            (
                r"\bmixed\b",
                r"\bmixture\b",
                r"\bvarious\b",
                r"\bmultiple\b",
            ),
        ),
    }


def extract_instar_numbers(text: str) -> set[int]:
    numbers: set[int] = set()

    for match in re.finditer(r"\binstar\s*([ivx]+|\d+)\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(r"\binstar([ivx]+|\d+)\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(r"\b(\d+)\s*(?:st|nd|rd|th)?[-\s]*(?:inst(?:ar|er|asr|atsr)|inatsr)\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(
        r"\b("
        + "|".join(WORD_TO_NUM.keys())
        + r")[-\s]*(?:inst(?:ar|er|asr|atsr)|inatsr)\b",
        text,
    ):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(r"\bl(\d+)(?:d\d+)?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(r"\b(\d+)l(?:\d+d?)?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(r"(?<!day[-\s])\b(\d+)\s*(?:st|nd|rd|th)?\s*larva(?:e)?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(
        r"\b("
        + "|".join(WORD_TO_NUM.keys())
        + r")\s+larva(?:e)?\b",
        text,
    ):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(
        r"\b("
        + "|".join(WORD_TO_NUM.keys())
        + r"|\d+(?:st|nd|rd|th)?)\s+ages?\b",
        text,
    ):
        value = parse_stage_number(re.sub(r"(st|nd|rd|th)$", "", match.group(1)))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(r"\b([ivx]+)\s*-\s*([ivx]+)\s+larvae?\s+instar\b", text):
        for group in match.groups():
            value = parse_stage_number(group)
            if value is not None:
                numbers.add(value)

    return {value for value in numbers if 1 <= value <= 20}


def extract_day_numbers(text: str) -> set[int]:
    numbers: set[int] = set()

    for match in re.finditer(r"\bl\d+d(\d+)\b", text):
        numbers.add(int(match.group(1)))

    for match in re.finditer(r"\b\d+l(\d+)d\b", text):
        numbers.add(int(match.group(1)))

    for match in re.finditer(r"\bday[-\s]?(\d+)\b", text):
        numbers.add(int(match.group(1)))

    for match in re.finditer(r"\b(\d+)(?:st|nd|rd|th)?[-\s]*day\b", text):
        numbers.add(int(match.group(1)))

    for match in re.finditer(
        r"\b("
        + "|".join(WORD_TO_NUM.keys())
        + r")[-\s]*day\b",
        text,
    ):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    for match in re.finditer(r"\b(\d+)[-\s]*day[-\s]*old\b", text):
        numbers.add(int(match.group(1)))

    for match in re.finditer(r"\b(\d+)\s*days?\s*old\b", text):
        numbers.add(int(match.group(1)))

    for match in re.finditer(
        r"\b("
        + "|".join(WORD_TO_NUM.keys())
        + r")\s+days?\s+old\b",
        text,
    ):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    return {value for value in numbers if 0 <= value <= 200}


def extract_week_numbers(text: str) -> set[int]:
    numbers: set[int] = set()

    for match in re.finditer(r"\b(\d+)\s*weeks?\b", text):
        numbers.add(int(match.group(1)))

    for match in re.finditer(
        r"\b("
        + "|".join(WORD_TO_NUM.keys())
        + r")\s+weeks?\b",
        text,
    ):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    return {value for value in numbers if 0 <= value <= 52}


def extract_hour_day_numbers(text: str) -> set[int]:
    numbers: set[int] = set()

    for match in re.finditer(r"\b(\d+)\s*(?:hours?|hrs?|hr|h)\b", text):
        hours = int(match.group(1))
        if hours % 24 == 0:
            numbers.add(hours // 24)

    return {value for value in numbers if 0 <= value <= 200}


def major_phases(flags: dict[str, bool]) -> set[str]:
    phases: set[str] = set()
    if flags["egg"] or flags["embryo"]:
        phases.add("early")
    if flags["larva"] or flags["instar"]:
        phases.add("larval")
    if flags["prepupa"]:
        phases.add("prepupa")
    if flags["pupa"]:
        phases.add("pupal")
    if flags["adult"]:
        phases.add("adult")
    return phases


def normalize_stage(raw_value: str, canonical_lookup: dict[str, str]) -> tuple[str, str]:
    text = raw_value.strip()
    normalized = normalize_text(text)

    if normalized in MISSING_VALUES:
        return canonical_lookup["unknown"], "missing_or_unknown"

    exact_match = canonical_lookup.get(normalized)
    if exact_match is not None:
        return exact_match, "exact"

    flags = detect_stage_flags(normalized)
    phases = major_phases(flags)
    has_delimiter = any(delimiter in normalized for delimiter in (",", ";", "|", "/", " and "))

    if flags["explicit_mixed"]:
        return canonical_lookup["mixed"], "mixed_explicit"
    if len(phases) >= 2 and has_delimiter:
        return canonical_lookup["mixed"], "mixed_multi_phase"
    if "adult" in phases and len(phases) >= 2:
        return canonical_lookup["mixed"], "mixed_multi_phase"
    if "pupal" in phases and ("larval" in phases or "early" in phases):
        return canonical_lookup["mixed"], "mixed_multi_phase"

    if flags["prepupa"]:
        return canonical_lookup["prepupa"], "pattern:prepupa"
    if flags["adult"]:
        return canonical_lookup["adult"], "pattern:adult"
    if flags["pupa"]:
        return canonical_lookup["pupa"], "pattern:pupa"
    if flags["instar"]:
        return canonical_lookup["instar"], "pattern:instar"
    if flags["larva"]:
        return canonical_lookup["larva"], "pattern:larva"
    if flags["embryo"]:
        return canonical_lookup["embryo"], "pattern:embryo"
    if flags["egg"]:
        return canonical_lookup["egg"], "pattern:egg"

    return canonical_lookup["unknown"], "no_match"


def collapse_stage(stage_value: str, canonical_lookup: dict[str, str]) -> str:
    if normalize_text(stage_value) == "instar":
        return canonical_lookup["larva"]
    return stage_value


def normalize_stage2(raw_value: str, coarse_stage: str, canonical_lookup: dict[str, str]) -> tuple[str, str]:
    normalized = normalize_text(raw_value)
    coarse_key = normalize_text(coarse_stage)
    day_numbers = extract_day_numbers(normalized)
    week_numbers = extract_week_numbers(normalized)
    hour_day_numbers = extract_hour_day_numbers(normalized)

    def resolve_time_only_code() -> tuple[str, str] | None:
        if len(day_numbers) == 1:
            return f"D{next(iter(day_numbers))}", "time_only_day"
        if not day_numbers and len(week_numbers) == 1:
            return f"W{next(iter(week_numbers))}", "time_only_week"
        if not day_numbers and not week_numbers and len(hour_day_numbers) == 1:
            return f"D{next(iter(hour_day_numbers))}", "time_only_hour_as_day"
        return None

    if coarse_key == "egg":
        time_only = resolve_time_only_code()
        if time_only is not None:
            return time_only
        return "", "blank_no_specific_detail"
    if coarse_key == "embryo":
        time_only = resolve_time_only_code()
        if time_only is not None:
            return time_only
        return "", "blank_no_specific_detail"
    if coarse_key == "adult":
        time_only = resolve_time_only_code()
        if time_only is not None:
            return time_only
        return "", "blank_no_specific_detail"
    if coarse_key == "mixed":
        return "Mixed", "same_as_stage_corr"
    if coarse_key == "unknown":
        time_only = resolve_time_only_code()
        if time_only is not None:
            return time_only
        return "", "blank_no_specific_detail"
    if coarse_key == "prepupa":
        return "PP", "same_as_stage_corr"
    if coarse_key == "pupa":
        for match in re.finditer(r"\bp(\d+)\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_code"
        for match in re.finditer(r"\b(?:day\s*|pupa day\s*|pupal day\s*|day)(\d+)\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*days?\s*(?:after|from)\s*pupation\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*days?\s*post[- ]pupa(?:tion)?\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*[- ]?day[- ]old pupa\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*(?:hours?|hrs?|hr|h)\s*(?:after|from)?\s*pupa(?:tion)?\b", normalized):
            hours = int(match.group(1))
            return to_p_code(0 if hours < 24 else max(1, round(hours / 24))), "specific_pupa_hour"
        if re.search(r"\bearly pupa\b|\bday ?0\b|\bpupa day 0\b|\bp0\b", normalized):
            return "P0", "specific_pupa_code"
        return "", "blank_no_specific_detail"

    if coarse_key != "larva":
        return coarse_stage, "same_as_stage_corr"

    if any(token in normalized for token in (";", "|", " and ")) and (
        "instar" in normalized or "larva" in normalized
    ):
        numbers = extract_instar_numbers(normalized)
        if len(numbers) >= 2:
            return "Mixed", "mixed_multi_instar"

    instar_numbers = extract_instar_numbers(normalized)
    if len(instar_numbers) >= 2:
        return "Mixed", "mixed_multi_instar"
    if "neonate" in normalized and instar_numbers:
        return "Mixed", "mixed_multi_instar"
    if len(instar_numbers) == 1:
        instar = next(iter(instar_numbers))
        if len(day_numbers) == 1:
            return to_l_day_code(instar, next(iter(day_numbers))), "specific_instar_day"
        return to_l_code(instar), "specific_instar"

    if any(
        phrase in normalized
        for phrase in (
            "last instar",
            "last inster",
            "late instar",
            "ultimate instar",
            "final stage",
            "mature larva",
        )
    ):
        return "Last instar", "last_instar"

    if "neonate" in normalized or "post hatch" in normalized:
        if len(day_numbers) == 1:
            return to_l_day_code(1, next(iter(day_numbers))), "neonate_to_first_instar_day"
        return "L1", "neonate_to_first_instar"

    time_only = resolve_time_only_code()
    if time_only is not None:
        return time_only

    return "", "blank_no_specific_detail"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input CSV path.")
    parser.add_argument("--stage-list", required=True, type=Path, help="Controlled stage list.")
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
        help="Overwrite the input CSV with the added stage correction columns.",
    )
    return parser


def resolve_output_paths(
    input_path: Path,
    output_path: Path | None,
    review_path: Path | None,
    in_place: bool,
) -> tuple[Path, Path]:
    if in_place:
        csv_output = input_path
    elif output_path is not None:
        csv_output = output_path
    else:
        csv_output = input_path.with_name(input_path.stem + ".stage_corr.csv")

    if review_path is not None:
        resolved_review = review_path
    else:
        resolved_review = csv_output.with_name(csv_output.stem + ".stage_corr_review.tsv")

    return csv_output, resolved_review


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    canonical_stages = load_stage_list(args.stage_list)
    canonical_lookup = {normalize_text(stage): stage for stage in canonical_stages}
    required = {"egg", "embryo", "larva", "instar", "prepupa", "pupa", "adult", "mixed", "unknown"}
    missing_required = sorted(required.difference(canonical_lookup))
    if missing_required:
        raise ValueError(
            "stage_list.txt is missing required canonical values: " + ", ".join(missing_required)
        )

    output_csv, review_tsv = resolve_output_paths(args.input, args.output, args.review_output, args.in_place)
    temp_csv = output_csv.with_suffix(output_csv.suffix + ".tmp")
    review_counter: Counter[tuple[str, str, str, str, str]] = Counter()

    with args.input.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {args.input}")
        if "Stage" not in reader.fieldnames:
            raise ValueError("Input CSV is missing the 'Stage' column")

        output_fields = [
            field for field in reader.fieldnames if field not in {"Stage.Corr", "Stage1.Corr", "Stage2.Corr"}
        ]
        stage_index = output_fields.index("Stage")
        output_fields[stage_index + 1:stage_index + 1] = ["Stage1.Corr", "Stage2.Corr"]

        with temp_csv.open("w", encoding="utf-8-sig", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=output_fields)
            writer.writeheader()

            for row in reader:
                raw_stage = (row.get("Stage") or "").strip()
                row.pop("Stage.Corr", None)
                row.pop("Stage1.Corr", None)
                row.pop("Stage2.Corr", None)
                stage_corr, reason = normalize_stage(raw_stage, canonical_lookup)
                stage_corr = collapse_stage(stage_corr, canonical_lookup)
                stage2_corr, stage2_reason = normalize_stage2(raw_stage, stage_corr, canonical_lookup)
                row["Stage1.Corr"] = stage_corr
                row["Stage2.Corr"] = stage2_corr
                writer.writerow(row)
                review_counter[(raw_stage or "N/A", stage_corr, stage2_corr, reason, stage2_reason)] += 1

    temp_csv.replace(output_csv)

    with review_tsv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["raw_stage", "count", "stage1_corr", "stage2_corr", "reason", "stage2_reason"])
        for (raw_stage, stage_corr, stage2_corr, reason, stage2_reason), count in sorted(
            review_counter.items(),
            key=lambda item: (-item[1], item[0][1], item[0][2], item[0][0]),
        ):
            writer.writerow([raw_stage, count, stage_corr, stage2_corr, reason, stage2_reason])

    print(f"Wrote normalized CSV: {output_csv}")
    print(f"Wrote review TSV: {review_tsv}")
    print(f"Distinct raw Stage values reviewed: {len(review_counter)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
