#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path

from normalize_sra import GENERAL_MISSING_VALUES, infer_tissue, normalize_sex, resolve_stage_from_sources


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DB = Path("data/clean/SRA/ncbi_request_cache.sqlite")
DEFAULT_TRANSCRIPTOME_CSV = Path("data/clean/SRA/transcriptome_data_20260323.csv")
DEFAULT_SYNONYM_OUTPUT = Path("data/raw/SRA/biosample/synonym_groups.tsv")

TARGET_FIELDS = OrderedDict(
    [
        (
            "Tissue",
            (
                "tissue",
                "tissuetype",
                "bodypart",
                "bodysite",
                "organismpart",
                "sourcename",
                "hosttissuesampled",
            ),
        ),
        (
            "Sex",
            (
                "sex",
                "gender",
            ),
        ),
        (
            "Stage",
            (
                "devstage",
                "developmentstage",
                "developmentalstage",
                "lifestage",
                "life_stage",
                "lifestage",
                "stage",
                "age",
                "timepoint",
            ),
        ),
        (
            "Collection Date",
            (
                "collectiondate",
            ),
        ),
        (
            "Geographic location",
            (
                "geolocname",
                "geographiclocation",
                "country",
                "geographiclocationcountryandorsea",
                "geographiclocationregionandlocality",
            ),
        ),
        (
            "Geographic coordinates",
            (
                "latlon",
                "latitudeandlongitude",
                "geographiclocationlatitude",
                "geographiclocationlongitude",
            ),
        ),
    ]
)

OUTPUT_FIELDS = tuple(TARGET_FIELDS.keys())
INVALID_COORDINATE_TEXT = {
    "not applicable",
    "not collected",
    "missing",
    "na",
    "n/a",
    "not provided",
    "unknown",
    "none",
    "null",
}
COLLECTION_DATE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{4}/\d{2}/\d{2}$"),
    re.compile(r"^\d{4}-\d{2}$"),
)


@dataclass
class AttributeRecord:
    labels: list[str]
    normalized_labels: list[str]
    value: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-extract BioSample Tissue/Sex/Stage/Collection Date/Geographic location "
            "from cached NCBI BioSample XML, update transcriptome_data CSV in place, "
            "and regenerate synonym_groups.tsv."
        )
    )
    parser.add_argument(
        "--cache-db",
        default=str(DEFAULT_CACHE_DB),
        help="SQLite cache DB path, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--transcriptome-csv",
        default=str(DEFAULT_TRANSCRIPTOME_CSV),
        help="Transcriptome CSV path to update in place, relative to the iLepbase root by default.",
    )
    parser.add_argument(
        "--synonym-output",
        default=str(DEFAULT_SYNONYM_OUTPUT),
        help="Synonym-group TSV output path, relative to the iLepbase root by default.",
    )
    return parser.parse_args()


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def to_repo_relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def clean_value(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def normalize_label(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum())


EMPTY_LIKE_TEXT = {normalize_label(value) for value in GENERAL_MISSING_VALUES if value}


def is_empty_like(value: str | None) -> bool:
    cleaned = clean_value(value)
    if cleaned is None:
        return True
    return normalize_label(cleaned) in EMPTY_LIKE_TEXT


DECIMAL_PAIR_PATTERN = re.compile(
    r"^\s*[-+]?\d+(?:\.\d+)?\s*[,;/]\s*[-+]?\d+(?:\.\d+)?\s*$",
    re.IGNORECASE,
)
CARDINAL_PAIR_PATTERN = re.compile(
    r"^\s*[-+]?\d+(?:\.\d+)?\s*[NS]\s+[-+]?\d+(?:\.\d+)?\s*[EW]\s*$",
    re.IGNORECASE,
)
LABELED_COORD_PATTERN = re.compile(
    r"^\s*lat(?:itude)?\s*[:=]\s*[-+]?\d+(?:\.\d+)?\s*[,;/ ]+\s*lon(?:gitude)?\s*[:=]\s*[-+]?\d+(?:\.\d+)?\s*$",
    re.IGNORECASE,
)
CARDINAL_EXTRACT_PATTERN = re.compile(
    r"^\s*([-+]?\d+(?:\.\d+)?)\s*([NS])\s+([-+]?\d+(?:\.\d+)?)\s*([EW])\s*$",
    re.IGNORECASE,
)
LABELED_EXTRACT_PATTERN = re.compile(
    r"^\s*lat(?:itude)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*[,;/ ]+\s*lon(?:gitude)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)


def normalized_target_lookup() -> dict[str, tuple[str, int]]:
    lookup: dict[str, tuple[str, int]] = {}
    for field, labels in TARGET_FIELDS.items():
        for index, label in enumerate(labels):
            lookup[normalize_label(label)] = (field, index)
    return lookup


TARGET_LABEL_LOOKUP = normalized_target_lookup()


def parse_attribute_records(raw_xml: str) -> list[AttributeRecord]:
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return []

    attributes: list[AttributeRecord] = []
    for attribute in root.findall(".//Attribute"):
        labels: list[str] = []
        for value in attribute.attrib.values():
            cleaned = clean_value(value)
            if cleaned and cleaned not in labels:
                labels.append(cleaned)
        if not labels:
            continue

        text = " ".join(part.strip() for part in attribute.itertext() if part and part.strip())
        cleaned_text = clean_value(text)
        if cleaned_text is None:
            continue

        normalized_labels = []
        for label in labels:
            normalized = normalize_label(label)
            if normalized and normalized not in normalized_labels:
                normalized_labels.append(normalized)
        if not normalized_labels:
            continue

        attributes.append(
            AttributeRecord(
                labels=labels,
                normalized_labels=normalized_labels,
                value=cleaned_text,
            )
        )
    return attributes


def choose_target_field(attribute: AttributeRecord) -> tuple[str | None, int | None]:
    best_field = None
    best_rank = None
    for normalized_label in attribute.normalized_labels:
        match = TARGET_LABEL_LOOKUP.get(normalized_label)
        if match is None:
            continue
        field, rank = match
        if best_rank is None or rank < best_rank:
            best_field = field
            best_rank = rank
    return best_field, best_rank


def looks_like_coordinates(value: str) -> bool:
    text = value.strip()
    return bool(
        DECIMAL_PAIR_PATTERN.fullmatch(text)
        or CARDINAL_PAIR_PATTERN.fullmatch(text)
        or LABELED_COORD_PATTERN.fullmatch(text)
    )


def normalize_coordinate_text(value: str | None) -> str | None:
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    if cleaned.lower() in INVALID_COORDINATE_TEXT:
        return None
    return cleaned


def format_coordinate_value(value: float) -> str:
    formatted = f"{value:.8f}".rstrip("0").rstrip(".")
    return formatted if formatted not in {"-0", "+0", ""} else "0"


def parse_coordinate_component(value: str | None, direction: str | None = None) -> float | None:
    cleaned = normalize_coordinate_text(value)
    if cleaned is None:
        return None
    try:
        numeric = float(cleaned)
    except ValueError:
        return None
    if direction is not None and direction.upper() in {"S", "W"}:
        numeric = -abs(numeric)
    return numeric


def normalize_coordinate_pair(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    return f"{format_coordinate_value(lat)}, {format_coordinate_value(lon)}"


def parse_coordinate_pair(value: str | None) -> str | None:
    cleaned = normalize_coordinate_text(value)
    if cleaned is None:
        return None

    cardinal_match = CARDINAL_EXTRACT_PATTERN.fullmatch(cleaned)
    if cardinal_match:
        lat = parse_coordinate_component(cardinal_match.group(1), cardinal_match.group(2))
        lon = parse_coordinate_component(cardinal_match.group(3), cardinal_match.group(4))
        return normalize_coordinate_pair(lat, lon)

    labeled_match = LABELED_EXTRACT_PATTERN.fullmatch(cleaned)
    if labeled_match:
        lat = parse_coordinate_component(labeled_match.group(1))
        lon = parse_coordinate_component(labeled_match.group(2))
        return normalize_coordinate_pair(lat, lon)

    if DECIMAL_PAIR_PATTERN.fullmatch(cleaned):
        parts = re.split(r"\s*[,;/]\s*", cleaned)
        if len(parts) == 2:
            lat = parse_coordinate_component(parts[0])
            lon = parse_coordinate_component(parts[1])
            return normalize_coordinate_pair(lat, lon)

    return None


def looks_like_collection_date(value: str | None) -> bool:
    cleaned = clean_value(value)
    if cleaned is None:
        return False
    return any(pattern.fullmatch(cleaned) for pattern in COLLECTION_DATE_PATTERNS)


def build_global_value_fallback(attributes: list[AttributeRecord]) -> dict[str, str]:
    tissue_candidates: OrderedDict[str, None] = OrderedDict()
    sex_candidates: OrderedDict[str, None] = OrderedDict()
    stage_candidates: OrderedDict[str, None] = OrderedDict()
    collection_date_candidates: OrderedDict[str, None] = OrderedDict()
    coordinate_candidates: OrderedDict[str, None] = OrderedDict()

    for attribute in attributes:
        value = clean_value(attribute.value)
        if value is None:
            continue

        tissue_value, _ = infer_tissue(value)
        if tissue_value not in {"", "unknown", "other"}:
            tissue_candidates[value] = None

        sex_value, _ = normalize_sex(value)
        if sex_value not in {"", "unknown", "other"}:
            sex_candidates[value] = None

        stage1_value, stage2_value, _, _ = resolve_stage_from_sources("", value)
        if stage1_value not in {"", "unknown", "other"}:
            stage_candidates[value] = None

        if looks_like_collection_date(value):
            collection_date_candidates[value] = None

        coordinate_value = parse_coordinate_pair(value)
        if coordinate_value is not None:
            coordinate_candidates[coordinate_value] = None

    fallback: dict[str, str] = {}
    if len(tissue_candidates) == 1:
        fallback["Tissue"] = next(iter(tissue_candidates))
    if len(sex_candidates) == 1:
        fallback["Sex"] = next(iter(sex_candidates))
    if len(stage_candidates) == 1:
        fallback["Stage"] = next(iter(stage_candidates))
    if len(collection_date_candidates) == 1:
        fallback["Collection Date"] = next(iter(collection_date_candidates))
    if len(coordinate_candidates) == 1:
        fallback["Geographic coordinates"] = next(iter(coordinate_candidates))
    return fallback


def build_extracted_fields(attributes: list[AttributeRecord]) -> dict[str, str]:
    extracted: dict[str, tuple[int, str]] = {}
    latitude = None
    longitude = None
    coordinate_candidate: tuple[int, str] | None = None
    stage_candidates: list[tuple[int, int, str]] = []
    for index, attribute in enumerate(attributes):
        field, rank = choose_target_field(attribute)
        if field is None or rank is None:
            continue
        if field == "Geographic location" and looks_like_coordinates(attribute.value):
            field = "Geographic coordinates"
            rank = 0
        if field == "Stage":
            stage_candidates.append((rank, index, attribute.value))
            continue
        if field == "Geographic coordinates":
            if "geographiclocationlatitude" in attribute.normalized_labels:
                latitude = attribute.value
                continue
            if "geographiclocationlongitude" in attribute.normalized_labels:
                longitude = attribute.value
                continue
            if coordinate_candidate is None or rank < coordinate_candidate[0]:
                coordinate_candidate = (rank, attribute.value)
            continue
        current = extracted.get(field)
        if current is None or rank < current[0]:
            extracted[field] = (rank, attribute.value)
    if latitude is not None and longitude is not None:
        lat_value = parse_coordinate_component(latitude)
        lon_value = parse_coordinate_component(longitude)
        normalized_pair = normalize_coordinate_pair(lat_value, lon_value)
        if normalized_pair is not None:
            extracted["Geographic coordinates"] = (0, normalized_pair)
    elif coordinate_candidate is not None:
        normalized_pair = parse_coordinate_pair(coordinate_candidate[1])
        if normalized_pair is not None:
            extracted["Geographic coordinates"] = (coordinate_candidate[0], normalized_pair)
    if stage_candidates:
        seen_stage_values: OrderedDict[str, None] = OrderedDict()
        for _, _, value in sorted(stage_candidates, key=lambda item: (item[0], item[1])):
            cleaned = clean_value(value)
            if cleaned is not None and cleaned not in seen_stage_values:
                seen_stage_values[cleaned] = None
        if seen_stage_values:
            extracted["Stage"] = (0, "; ".join(seen_stage_values.keys()))
    return {field: value for field, (_, value) in extracted.items()}


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak_{timestamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def load_cached_xml_by_request_accession(cache_db: Path) -> dict[str, str]:
    cache: dict[str, str] = {}
    connection = sqlite3.connect(cache_db)
    try:
        cursor = connection.execute(
            """
            SELECT request_accession, raw_xml
            FROM biosample_xml_cache
            """
        )
        for request_accession, raw_xml in cursor:
            if request_accession and raw_xml:
                cache[str(request_accession)] = str(raw_xml)
    finally:
        connection.close()
    return cache


def write_synonym_groups(
    synonym_output: Path,
    cached_xml_by_request_accession: dict[str, str],
) -> tuple[Path | None, int]:
    synonym_output.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup_file(synonym_output)

    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for raw_xml in cached_xml_by_request_accession.values():
        for attribute in parse_attribute_records(raw_xml):
            field, _ = choose_target_field(attribute)
            for normalized_label in attribute.normalized_labels:
                match = TARGET_LABEL_LOOKUP.get(normalized_label)
                group_name = field if match is not None else "UNCLASSIFIED"
                if match is None:
                    group_name = "UNCLASSIFIED"
                elif match[0] == "Geographic location" and looks_like_coordinates(attribute.value):
                    group_name = "Geographic coordinates"
                elif match[0] == "Geographic coordinates":
                    group_name = "Geographic coordinates"
                else:
                    group_name = match[0]
                entry = grouped.setdefault(
                    (group_name, normalized_label),
                    {
                        "raw_labels": OrderedDict(),
                        "count": 0,
                        "examples": OrderedDict(),
                    },
                )
                entry["count"] += 1
                for raw_label in attribute.labels:
                    if normalize_label(raw_label) == normalized_label:
                        entry["raw_labels"][raw_label] = None
                if len(entry["examples"]) < 3:
                    entry["examples"][attribute.value] = None

    with synonym_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["group", "normalized_label", "raw_labels", "count", "example_values"])
        for (field, normalized_label), entry in sorted(grouped.items()):
            writer.writerow(
                [
                    field,
                    normalized_label,
                    " | ".join(entry["raw_labels"].keys()),
                    entry["count"],
                    " | ".join(entry["examples"].keys()),
                ]
            )

    return backup_path, len(grouped)


def update_transcriptome_csv(
    transcriptome_csv: Path,
    cached_xml_by_request_accession: dict[str, str],
) -> tuple[Path | None, int, Counter[str], int]:
    backup_path = backup_file(transcriptome_csv)
    temp_path = transcriptome_csv.with_name(f"{transcriptome_csv.name}.tmp")
    updated_rows = 0
    cache_hits = 0
    field_updates: Counter[str] = Counter()

    with transcriptome_csv.open("r", encoding="utf-8-sig", newline="") as input_handle, temp_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as output_handle:
        reader = csv.DictReader(input_handle)
        if reader.fieldnames is None:
            raise ValueError(f"Transcriptome CSV has no header: {transcriptome_csv}")
        fieldnames = list(reader.fieldnames)
        for field in OUTPUT_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)
        writer = csv.DictWriter(output_handle, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            sample_accession = clean_value(row.get("Sample Accession"))
            raw_xml = None if sample_accession is None else cached_xml_by_request_accession.get(sample_accession)
            if raw_xml is not None:
                cache_hits += 1
                attributes = parse_attribute_records(raw_xml)
                # Prefer direct field-based extraction; only fall back to global value-based
                # matching when the target field was not extracted and the CSV cell is empty-like.
                extracted = build_extracted_fields(attributes)
                fallback = build_global_value_fallback(attributes)
                row_changed = False
                for field in OUTPUT_FIELDS:
                    new_value = extracted.get(field)
                    if new_value is None and is_empty_like(row.get(field)):
                        new_value = fallback.get(field)
                    if new_value is None:
                        continue
                    old_value = clean_value(row.get(field)) or ""
                    if old_value != new_value:
                        row[field] = new_value
                        field_updates[field] += 1
                        row_changed = True
                if row_changed:
                    updated_rows += 1
            writer.writerow(row)

    temp_path.replace(transcriptome_csv)
    return backup_path, updated_rows, field_updates, cache_hits


def main() -> int:
    args = parse_args()
    cache_db = resolve_repo_path(args.cache_db)
    transcriptome_csv = resolve_repo_path(args.transcriptome_csv)
    synonym_output = resolve_repo_path(args.synonym_output)

    cached_xml_by_request_accession = load_cached_xml_by_request_accession(cache_db)
    synonym_backup, synonym_group_count = write_synonym_groups(
        synonym_output=synonym_output,
        cached_xml_by_request_accession=cached_xml_by_request_accession,
    )
    transcriptome_backup, updated_rows, field_updates, cache_hits = update_transcriptome_csv(
        transcriptome_csv=transcriptome_csv,
        cached_xml_by_request_accession=cached_xml_by_request_accession,
    )

    print(f"root={ROOT}")
    print(f"cache_db={to_repo_relative_path(cache_db)}")
    print(f"synonym_output={to_repo_relative_path(synonym_output)}")
    print(f"synonym_backup={to_repo_relative_path(synonym_backup) if synonym_backup else 'N/A'}")
    print(f"synonym_group_count={synonym_group_count}")
    print(f"transcriptome_csv={to_repo_relative_path(transcriptome_csv)}")
    print(f"transcriptome_backup={to_repo_relative_path(transcriptome_backup) if transcriptome_backup else 'N/A'}")
    print(f"cached_biosamples={len(cached_xml_by_request_accession)}")
    print(f"transcriptome_rows_with_cache_hit={cache_hits}")
    print(f"transcriptome_rows_updated={updated_rows}")
    for field in OUTPUT_FIELDS:
        print(f"{field.replace(' ', '_').lower()}_updates={field_updates[field]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
