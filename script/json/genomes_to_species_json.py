#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, OrderedDict
from pathlib import Path

from openpyxl import load_workbook
from log_utils import refresh_log_summary


ROOT = Path(__file__).resolve().parents[2]

# External source URL templates used in generated metadata.
NCBI_DATASETS_GENOME_URL = "https://www.ncbi.nlm.nih.gov/datasets/genome/{accession}/"
NCBI_GENOMES_FTP_URL = (
    "https://ftp.ncbi.nlm.nih.gov/genomes/all/{prefix}/{part1}/{part2}/{part3}/"
    "{accession}_{assembly_name}/{accession}_{assembly_name}_genomic.fna.gz"
)
IBG_DETAIL_PAGE_URL = "https://www.insect-genome.com/genome/{ibg_id}"
IBG_DOWNLOAD_URL = (
    "https://www.insect-genome.com/data/genome/{species_filename}/"
    "{species_filename}.genome.fa.tar.bz2"
)
# Default local inputs and outputs.
DEFAULT_GENOME_INPUT = Path("data/raw/genome/Lep_genome_ncbi_20260311.tsv")
DEFAULT_IBG_DIR = Path("data/raw/genome/IBG")
DEFAULT_IBG_XLSX = Path("data/raw/genome/IBG_to_NCBI.xlsx")
DEFAULT_NGDC_DIR = Path("data/raw/genome/NGDC")
DEFAULT_NGDC_XLSX = Path("data/raw/genome/NGDC_20260331.xlsx")
DEFAULT_PUBLICATION_TSV = Path("data/raw/genome/Lep_genome_publications.tsv")
DEFAULT_CLEAN_GENOME_DIR = Path("data/clean/genome/genomic")
DEFAULT_CLEAN_ANNO_DIR = Path("data/clean/genome/anno")
DEFAULT_MAPPING_TSV = Path("data/raw/genome/genome_ncbi_ibg_species_mapping.tsv")
DEFAULT_JSON_DIR = Path("data/clean/json")
DEFAULT_LOG_OUTPUT = Path("script/json/stat.json")
DEFAULT_UNMATCHED_OUTPUT = Path("data/raw/genome/Lep_genome_ncbi_20260311.unmatched_species.tsv")

ALLOWED_ASSEMBLY_LEVELS = {"chromosome", "complete genome"}


def add_path_argument(
    parser: argparse.ArgumentParser,
    flag: str,
    default: Path,
    help_text: str,
) -> None:
    parser.add_argument(
        flag,
        default=str(default),
        help=f"{help_text}, relative to the iLepbase root by default.",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add NCBI, IBG and NGDC genome fields into species JSON files."
    )
    add_path_argument(parser, "--genome-input", DEFAULT_GENOME_INPUT, "NCBI genome TSV path")
    add_path_argument(parser, "--ibg-dir", DEFAULT_IBG_DIR, "IBG genome directory")
    add_path_argument(parser, "--ibg-xlsx", DEFAULT_IBG_XLSX, "IBG metadata xlsx")
    add_path_argument(parser, "--ngdc-dir", DEFAULT_NGDC_DIR, "NGDC genome directory")
    add_path_argument(parser, "--ngdc-xlsx", DEFAULT_NGDC_XLSX, "NGDC metadata xlsx")
    add_path_argument(parser, "--publication-tsv", DEFAULT_PUBLICATION_TSV, "Genome publication TSV path")
    add_path_argument(parser, "--json-dir", DEFAULT_JSON_DIR, "Species JSON directory")
    add_path_argument(parser, "--mapping-tsv", DEFAULT_MAPPING_TSV, "Species mapping TSV path")
    add_path_argument(parser, "--clean-genome-dir", DEFAULT_CLEAN_GENOME_DIR, "Cleaned genome directory")
    add_path_argument(parser, "--clean-anno-dir", DEFAULT_CLEAN_ANNO_DIR, "Cleaned annotation directory")
    add_path_argument(parser, "--log-output", DEFAULT_LOG_OUTPUT, "Stat JSON path")
    parser.add_argument(
        "--unmatched-output",
        default="",
        help="Optional TSV path for genome rows that still cannot be matched.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level.",
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


def clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def dedupe_dicts(
    records: list[OrderedDict[str, str | None]],
) -> list[OrderedDict[str, str | None]]:
    deduped: list[OrderedDict[str, str | None]] = []
    seen: set[tuple[tuple[str, str | None], ...]] = set()
    for record in records:
        key = tuple(record.items())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def dedupe_object_dicts(
    records: list[OrderedDict[str, object]],
) -> list[OrderedDict[str, object]]:
    deduped: list[OrderedDict[str, object]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for record in records:
        key = tuple(
            (name, json.dumps(value, ensure_ascii=False, sort_keys=True))
            for name, value in record.items()
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


GENOME_ROW_FIELDS = [
    "source",
    "Organism Name",
    "Assembly Name",
    "Assembly Accession",
    "Assembly Level",
    "Assembly Release Date",
    "Assembly Stats Number of Scaffolds",
    "Source Link",
    "Download Link",
]


def blank_genome_row() -> OrderedDict[str, str | None]:
    return OrderedDict((field, None) for field in GENOME_ROW_FIELDS)


def normalize_species_filename(species_name: str | None) -> str | None:
    cleaned = clean_value(species_name)
    if cleaned is None:
        return None
    return cleaned.replace(" ", "_")


def is_allowed_assembly_level(value: str | None) -> bool:
    cleaned = clean_value(value)
    if cleaned is None:
        return False
    return cleaned.lower() in ALLOWED_ASSEMBLY_LEVELS


def normalize_assembly_name_for_ftp(assembly_name: str | None) -> str | None:
    cleaned = clean_value(assembly_name)
    if cleaned is None:
        return None
    return (
        cleaned.replace(" ", "_")
        .replace("/", "_")
        .replace("(", "_")
        .replace(")", "_")
    )


def build_ncbi_source_link(accession: str | None) -> str | None:
    cleaned = clean_value(accession)
    if cleaned is None:
        return None
    return NCBI_DATASETS_GENOME_URL.format(accession=cleaned)


def build_ncbi_download_link(
    accession: str | None,
    assembly_name: str | None,
) -> str | None:
    cleaned_accession = clean_value(accession)
    cleaned_assembly_name = normalize_assembly_name_for_ftp(assembly_name)
    if cleaned_accession is None or cleaned_assembly_name is None:
        return None

    prefix, rest = cleaned_accession.split("_", 1)
    numeric_part = rest.split(".", 1)[0]
    if len(numeric_part) != 9:
        return None

    return NCBI_GENOMES_FTP_URL.format(
        prefix=prefix,
        part1=numeric_part[0:3],
        part2=numeric_part[3:6],
        part3=numeric_part[6:9],
        accession=cleaned_accession,
        assembly_name=cleaned_assembly_name,
    )


def build_ibg_source_link(ibg_id: str | None) -> str | None:
    cleaned = clean_value(ibg_id)
    if cleaned is None:
        return None
    return IBG_DETAIL_PAGE_URL.format(ibg_id=cleaned)


def build_ibg_download_link(species_name: str | None) -> str | None:
    species_filename = normalize_species_filename(species_name)
    if species_filename is None:
        return None
    return IBG_DOWNLOAD_URL.format(species_filename=species_filename)


def build_local_genome_path(
    accession: str | None,
    clean_genome_dir: Path,
) -> str | None:
    cleaned = clean_value(accession)
    if cleaned is None:
        return None
    return to_repo_relative_path(clean_genome_dir / f"{cleaned}.genomic.fna.gz")


def build_relative_file_size(relative_path: str | None) -> int | None:
    cleaned = clean_value(relative_path)
    if cleaned is None:
        return None
    path = resolve_repo_path(cleaned)
    if not path.exists():
        return None
    return path.stat().st_size


def candidate_publication_accessions(accession: str | None) -> list[str]:
    cleaned = clean_value(accession)
    if cleaned is None:
        return []
    candidates = [cleaned]
    if cleaned.startswith("GCA_"):
        candidates.append("GCF_" + cleaned[4:])
    elif cleaned.startswith("GCF_"):
        candidates.append("GCA_" + cleaned[4:])
    return candidates


PUBLICATION_CONTENT_FIELDS = (
    "PMID",
    "DOI",
    "PMCID",
    "Title",
    "Authors",
    "Journal",
    "Volume",
    "Issue",
    "Pages",
    "Publication Date",
    "ISSN",
    "Publication Labels",
)


def parse_delimited_list(value: str | None, delimiter: str) -> list[str]:
    cleaned = clean_value(value)
    if cleaned is None:
        return []
    return [item.strip() for item in cleaned.split(delimiter) if item.strip()]


def build_publication_record(row: dict[str, str | None]) -> OrderedDict[str, object] | None:
    if not any(clean_value(row.get(field)) for field in PUBLICATION_CONTENT_FIELDS):
        return None

    record = OrderedDict(
        [
            ("pmid", clean_value(row.get("PMID"))),
            ("doi", clean_value(row.get("DOI"))),
            ("pmcid", clean_value(row.get("PMCID"))),
            ("title", clean_value(row.get("Title"))),
            ("authors", parse_delimited_list(row.get("Authors"), ";")),
            ("journal", clean_value(row.get("Journal"))),
            ("volume", clean_value(row.get("Volume"))),
            ("issue", clean_value(row.get("Issue"))),
            ("pages", clean_value(row.get("Pages"))),
            ("publication_date", clean_value(row.get("Publication Date"))),
            ("issn", clean_value(row.get("ISSN"))),
            ("labels", parse_delimited_list(row.get("Publication Labels"), ",")),
        ]
    )
    return OrderedDict(
        (key, value)
        for key, value in record.items()
        if value not in (None, [], "")
    )


def load_publication_map(
    publication_tsv: Path,
) -> dict[str, list[OrderedDict[str, object]]]:
    publication_map: dict[str, list[OrderedDict[str, object]]] = {}
    with publication_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            accession = clean_value(row.get("Assembly Accession"))
            if accession is None:
                continue
            publication = build_publication_record(row)
            if publication is None:
                continue
            publication_map.setdefault(accession, []).append(publication)
    return {
        accession: dedupe_object_dicts(records)
        for accession, records in publication_map.items()
    }


def build_genome_publications(
    accession: str | None,
    publication_map: dict[str, list[OrderedDict[str, object]]],
) -> list[OrderedDict[str, object]] | None:
    matched: list[OrderedDict[str, object]] = []
    for candidate in candidate_publication_accessions(accession):
        matched.extend(publication_map.get(candidate, []))
    deduped = dedupe_object_dicts(matched)
    return deduped or None


def build_annotation_path(
    accession: str | None,
    clean_anno_dir: Path,
    subdir: str,
    suffix: str,
    annotation_aliases: dict[str, str] | None = None,
) -> str | None:
    cleaned = clean_value(accession)
    if cleaned is None:
        return None
    candidates = [cleaned]
    if annotation_aliases:
        alias = annotation_aliases.get(cleaned)
        if alias and alias not in candidates:
            candidates.append(alias)
    for candidate in candidates:
        path = clean_anno_dir / subdir / f"{candidate}{suffix}"
        if path.exists():
            return to_repo_relative_path(path)
    return None


def build_annotation_groups(
    clean_anno_dir: Path,
    annotation_species_map: dict[str, str],
    annotation_accession_metadata: dict[str, OrderedDict[str, str | None]],
    genome_accession_species_map: dict[str, str],
    species_to_path: OrderedDict[str, Path],
) -> OrderedDict[str, list[OrderedDict[str, object]]]:
    specs = [
        ("cds", ".cds.fa", "cds_path"),
        ("gff3", ".gff3", "gff3_path"),
        ("protein", ".anno.pep.fa", "protein_path"),
    ]

    grouped_by_species: OrderedDict[str, dict[str, OrderedDict[str, object]]] = OrderedDict()

    for subdir, suffix, field_name in specs:
        for path in sorted((clean_anno_dir / subdir).glob(f"*{suffix}")):
            if not path.is_file():
                continue
            accession = path.name[: -len(suffix)]
            species = genome_accession_species_map.get(accession)
            if species is None:
                linked_accession = clean_value(
                    (annotation_accession_metadata.get(accession) or {}).get("linked_accession")
                )
                if linked_accession:
                    species = genome_accession_species_map.get(linked_accession)
            if species is None:
                raw_species = annotation_species_map.get(accession)
                species, _ = resolve_species_match(raw_species, species_to_path)
                if species is None:
                    continue

            species_records = grouped_by_species.setdefault(species, OrderedDict())
            record = species_records.get(accession)
            if record is None:
                source_value = clean_value(
                    (annotation_accession_metadata.get(accession) or {}).get("source")
                ) or ("IBG" if accession.startswith("IBG_") else "NCBI")
                assembly_level_value = clean_value(
                    (annotation_accession_metadata.get(accession) or {}).get("assembly_level")
                )
                if source_value == "IBG" and not is_allowed_assembly_level(assembly_level_value):
                    continue
                record = OrderedDict(
                    [
                        (
                            "source",
                            source_value,
                        ),
                        ("species_name", species),
                        ("annotation_accession", accession),
                        ("assembly_level", assembly_level_value),
                        ("cds_path", None),
                        ("gff3_path", None),
                        ("protein_path", None),
                    ]
                )
                species_records[accession] = record
            record[field_name] = to_repo_relative_path(path)

    result: OrderedDict[str, list[OrderedDict[str, object]]] = OrderedDict()
    for species, records in grouped_by_species.items():
        result[species] = list(records.values())
    return result


def build_genome_accession_species_map(
    genome_groups: OrderedDict[str, list[OrderedDict[str, str | None]]],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for species, records in genome_groups.items():
        for record in records:
            accession = clean_value(record.get("assembly_accession"))
            if accession:
                mapping[accession] = species
    return mapping


def build_annotation_payload(
    annotation_record: OrderedDict[str, object],
) -> OrderedDict[str, object]:
    cds_path = annotation_record.get("cds_path")
    gff3_path = annotation_record.get("gff3_path")
    protein_path = annotation_record.get("protein_path")
    return OrderedDict(
        [
            ("source", annotation_record.get("source")),
            ("cds_path", cds_path),
            ("cds_size_bytes", build_relative_file_size(cds_path)),
            ("gff3_path", gff3_path),
            ("gff3_size_bytes", build_relative_file_size(gff3_path)),
            ("protein_path", protein_path),
            ("protein_size_bytes", build_relative_file_size(protein_path)),
        ]
    )


def build_annotation_only_genome_record(
    annotation_record: OrderedDict[str, object],
    clean_genome_dir: Path,
    publication_map: dict[str, list[OrderedDict[str, object]]],
) -> OrderedDict[str, object]:
    accession = clean_value(str(annotation_record.get("annotation_accession") or ""))
    source = annotation_record.get("source")
    species_name = clean_value(str(annotation_record.get("species_name") or ""))
    local_path = build_local_genome_path(accession, clean_genome_dir)
    source_link = None
    download_link = None
    if source == "IBG":
        source_link = build_ibg_source_link(accession)
        download_link = build_ibg_download_link(species_name)
    elif source == "NCBI":
        source_link = build_ncbi_source_link(accession)
    return OrderedDict(
        [
            ("source", source),
            ("assembly_accession", accession),
            ("assembly_level", annotation_record.get("assembly_level")),
            ("assembly_release_date", None),
            ("number_of_scaffolds", None),
            ("source_link", source_link),
            ("download_link", download_link),
            ("local_path", local_path),
            ("genome_size_bytes", build_relative_file_size(local_path)),
            ("publications", build_genome_publications(accession, publication_map)),
            ("annotation", [build_annotation_payload(annotation_record)]),
        ]
    )


def merge_genome_and_annotation_records(
    genome_records: list[OrderedDict[str, object]] | None,
    annotation_records: list[OrderedDict[str, object]] | None,
    annotation_aliases: dict[str, list[str]],
    clean_genome_dir: Path,
) -> list[OrderedDict[str, object]]:
    merged: list[OrderedDict[str, object]] = []
    annotation_by_accession: OrderedDict[str, list[OrderedDict[str, object]]] = OrderedDict()
    for record in annotation_records or []:
        accession = clean_value(str(record.get("annotation_accession") or ""))
        if accession:
            annotation_by_accession.setdefault(accession, []).append(record)

    for genome_record in genome_records or []:
        merged_record = OrderedDict(genome_record)
        accession = clean_value(str(merged_record.get("assembly_accession") or ""))
        matched_annotations: list[OrderedDict[str, object]] = []
        if accession and accession in annotation_by_accession:
            matched_annotations.extend(annotation_by_accession.pop(accession))
        if accession and accession in annotation_aliases:
            for alias in annotation_aliases[accession]:
                if alias in annotation_by_accession:
                    matched_annotations.extend(annotation_by_accession.pop(alias))
        if matched_annotations:
            merged_record["annotation"] = [
                build_annotation_payload(record) for record in matched_annotations
            ]
        merged.append(merged_record)

    return merged


def load_ncbi_genome_rows(
    genome_path: Path,
) -> tuple[list[OrderedDict[str, str | None]], int]:
    genome_rows: list[OrderedDict[str, str | None]] = []
    skipped_rows = 0

    with genome_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            cleaned_row = blank_genome_row()
            cleaned_row["source"] = "NCBI"
            cleaned_row["Organism Name"] = clean_value(row.get("Organism Name"))
            cleaned_row["Assembly Name"] = clean_value(row.get("Assembly Name"))
            cleaned_row["Assembly Accession"] = clean_value(row.get("Assembly Accession"))
            cleaned_row["Assembly Level"] = clean_value(row.get("Assembly Level"))
            if not is_allowed_assembly_level(cleaned_row["Assembly Level"]):
                continue
            cleaned_row["Assembly Release Date"] = clean_value(
                row.get("Assembly Release Date")
            )
            cleaned_row["Assembly Stats Number of Scaffolds"] = clean_value(
                row.get("Assembly Stats Number of Scaffolds")
            )
            cleaned_row["Source Link"] = build_ncbi_source_link(
                cleaned_row["Assembly Accession"]
            )
            cleaned_row["Download Link"] = build_ncbi_download_link(
                cleaned_row["Assembly Accession"],
                cleaned_row["Assembly Name"],
            )
            genome_rows.append(cleaned_row)
            if clean_value(cleaned_row.get("Organism Name")) is None:
                skipped_rows += 1

    return genome_rows, skipped_rows


def load_xlsx_rows(
    xlsx_path: Path,
) -> list[dict[str, str | None]]:
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    worksheet = workbook[workbook.sheetnames[0]]
    rows = worksheet.iter_rows(values_only=True)
    header = next(rows)
    items: list[dict[str, str | None]] = []

    if header is None:
        return items

    for row in rows:
        item: dict[str, str | None] = {}
        for idx, name in enumerate(header):
            if name is None:
                continue
            value = row[idx] if idx < len(row) else None
            text = None if value is None else str(value).strip()
            item[str(name).strip()] = text or None
        items.append(item)

    workbook.close()
    return items


def generate_species_mapping_tsv(
    genome_path: Path,
    ibg_xlsx_path: Path,
    mapping_tsv_path: Path,
) -> None:
    ncbi_rows = []
    ncbi_accessions: set[str] = set()
    with genome_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            species = clean_value(row.get("Organism Name"))
            accession = clean_value(row.get("Assembly Accession"))
            if species is None or accession is None:
                continue
            ncbi_rows.append(
                OrderedDict(
                    [
                        ("clean_species_name", species),
                        ("family", None),
                        ("genus", None),
                        ("record_type", "ncbi_genome"),
                        ("ncbi_genome_accession", accession),
                        ("ibg_id", None),
                        ("ibg_in_genome_xlsx", "0"),
                        ("ibg_keep_genome", "0"),
                        ("ibg_linked_ncbi_accession", None),
                        ("ibg_assembly_level", None),
                    ]
                )
            )
            ncbi_accessions.add(accession)

    ibg_rows = []
    for row in load_xlsx_rows(ibg_xlsx_path):
        species = clean_value(row.get("species"))
        ibg_id = clean_value(row.get("ibg_id"))
        assembly_level = clean_value(row.get("assembly_level"))
        linked_accession = clean_value(row.get("number"))
        if species is None or ibg_id is None:
            continue
        ibg_rows.append(
            OrderedDict(
                [
                    ("clean_species_name", species),
                    ("family", None),
                    ("genus", None),
                    ("record_type", "ibg_mapping"),
                    ("ncbi_genome_accession", None),
                    ("ibg_id", ibg_id),
                    ("ibg_in_genome_xlsx", "1"),
                    (
                        "ibg_keep_genome",
                        "1"
                        if linked_accession is None or linked_accession not in ncbi_accessions
                        else "0",
                    ),
                    ("ibg_linked_ncbi_accession", linked_accession),
                    ("ibg_assembly_level", assembly_level),
                ]
            )
        )

    fieldnames = [
        "clean_species_name",
        "family",
        "genus",
        "record_type",
        "ncbi_genome_accession",
        "ibg_id",
        "ibg_in_genome_xlsx",
        "ibg_keep_genome",
        "ibg_linked_ncbi_accession",
        "ibg_assembly_level",
    ]
    mapping_tsv_path.parent.mkdir(parents=True, exist_ok=True)
    with mapping_tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(ncbi_rows)
        writer.writerows(ibg_rows)


def load_mapping_rows(
    mapping_tsv_path: Path,
) -> list[OrderedDict[str, str | None]]:
    rows: list[OrderedDict[str, str | None]] = []
    with mapping_tsv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            rows.append(
                OrderedDict((key, clean_value(value)) for key, value in row.items())
            )
    return rows


def build_mapping_indexes(
    mapping_rows: list[OrderedDict[str, str | None]],
) -> tuple[
    list[OrderedDict[str, str | None]],
    dict[str, list[str]],
    dict[str, str],
    dict[str, OrderedDict[str, str | None]],
    dict[str, OrderedDict[str, str | None]],
]:
    ibg_genome_rows: list[OrderedDict[str, str | None]] = []
    annotation_aliases: dict[str, list[str]] = {}
    annotation_species_map: dict[str, str] = {}
    annotation_species_metadata: dict[str, OrderedDict[str, str | None]] = {}
    annotation_accession_metadata: dict[str, OrderedDict[str, str | None]] = {}

    for row in mapping_rows:
        species = clean_value(row.get("clean_species_name"))
        if species is None:
            continue

        annotation_species_metadata.setdefault(
            species,
            OrderedDict(
                [
                    ("family", clean_value(row.get("family"))),
                    ("genus", clean_value(row.get("genus"))),
                ]
            ),
        )

        record_type = clean_value(row.get("record_type"))
        ncbi_accession = clean_value(row.get("ncbi_genome_accession"))
        ibg_id = clean_value(row.get("ibg_id"))
        linked_accession = clean_value(row.get("ibg_linked_ncbi_accession"))

        if record_type == "ncbi_genome" and ncbi_accession:
            annotation_species_map.setdefault(ncbi_accession, species)

        if record_type == "ibg_mapping":
            if ibg_id:
                annotation_species_map.setdefault(ibg_id, species)
                annotation_accession_metadata.setdefault(
                    ibg_id,
                    OrderedDict(
                        [
                            ("source", "IBG"),
                            ("assembly_level", clean_value(row.get("ibg_assembly_level"))),
                            ("linked_accession", linked_accession),
                        ]
                    ),
                )
            if linked_accession and ibg_id:
                annotation_aliases.setdefault(linked_accession, [])
                if ibg_id not in annotation_aliases[linked_accession]:
                    annotation_aliases[linked_accession].append(ibg_id)
                annotation_species_map.setdefault(linked_accession, species)
            if row.get("ibg_keep_genome") == "1" and ibg_id:
                ibg_row = blank_genome_row()
                ibg_row["source"] = "IBG"
                ibg_row["Organism Name"] = species
                ibg_row["Assembly Accession"] = ibg_id
                ibg_row["Assembly Level"] = clean_value(row.get("ibg_assembly_level"))
                if not is_allowed_assembly_level(ibg_row["Assembly Level"]):
                    continue
                ibg_row["Source Link"] = build_ibg_source_link(ibg_id)
                ibg_row["Download Link"] = build_ibg_download_link(species)
                ibg_genome_rows.append(ibg_row)

    return (
        ibg_genome_rows,
        annotation_aliases,
        annotation_species_map,
        annotation_species_metadata,
        annotation_accession_metadata,
    )


def parse_accession_from_download_link(download_link: str | None) -> str | None:
    cleaned = clean_value(download_link)
    if cleaned is None:
        return None

    patterns = [
        r"(GCA_\d+\.\d+)",
        r"(GCF_\d+\.\d+)",
        r"(GWH[A-Z0-9]+\.\d+)",
        r"(GWH[A-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            return match.group(1)
    return None


def load_ngdc_genome_rows(
    ngdc_dir: Path,
    ngdc_xlsx_path: Path,
) -> list[OrderedDict[str, str | None]]:
    accessions = {
        path.name.replace(".genome.fasta.gz", "")
        for path in sorted(ngdc_dir.glob("*.genome.fasta.gz"))
    }
    if not accessions:
        return []

    ngdc_rows = load_xlsx_rows(ngdc_xlsx_path)
    genome_rows: list[OrderedDict[str, str | None]] = []
    for row in ngdc_rows:
        accession = parse_accession_from_download_link(row.get("下载链接"))
        if accession is None or accession not in accessions:
            continue
        cleaned_row = blank_genome_row()
        cleaned_row["source"] = "NGDC"
        cleaned_row["Organism Name"] = clean_value(row.get("species"))
        cleaned_row["Assembly Accession"] = accession
        cleaned_row["Assembly Level"] = clean_value(row.get("assembly_level"))
        if not is_allowed_assembly_level(cleaned_row["Assembly Level"]):
            continue
        cleaned_row["Source Link"] = clean_value(row.get("来源链接"))
        cleaned_row["Download Link"] = clean_value(row.get("下载链接"))
        genome_rows.append(cleaned_row)
    return genome_rows


def load_genome_rows(
    ncbi_path: Path,
    ibg_rows: list[OrderedDict[str, str | None]],
    ngdc_dir: Path,
    ngdc_xlsx_path: Path,
) -> tuple[list[OrderedDict[str, str | None]], list[str], int]:
    ncbi_rows, skipped_rows = load_ncbi_genome_rows(ncbi_path)
    ngdc_rows = load_ngdc_genome_rows(ngdc_dir, ngdc_xlsx_path)
    genome_rows = [*ncbi_rows, *ibg_rows, *ngdc_rows]
    return genome_rows, list(GENOME_ROW_FIELDS), skipped_rows


def build_genome_record(
    row: OrderedDict[str, str | None],
    clean_genome_dir: Path,
    publication_map: dict[str, list[OrderedDict[str, object]]],
) -> OrderedDict[str, str | None]:
    accession = clean_value(row.get("Assembly Accession"))
    local_path = build_local_genome_path(
        accession,
        clean_genome_dir,
    )
    return OrderedDict(
        [
            ("source", clean_value(row.get("source"))),
            ("assembly_accession", accession),
            ("assembly_level", clean_value(row.get("Assembly Level"))),
            (
                "assembly_release_date",
                clean_value(row.get("Assembly Release Date")),
            ),
            (
                "number_of_scaffolds",
                clean_value(row.get("Assembly Stats Number of Scaffolds")),
            ),
            ("source_link", clean_value(row.get("Source Link"))),
            ("download_link", clean_value(row.get("Download Link"))),
            ("local_path", local_path),
            ("genome_size_bytes", build_relative_file_size(local_path)),
            ("publications", build_genome_publications(accession, publication_map)),
        ]
    )


def load_species_json_paths(
    json_dir: Path,
) -> OrderedDict[str, Path]:
    species_to_path: OrderedDict[str, Path] = OrderedDict()
    for json_path in sorted(json_dir.glob("*.json")):
        with json_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        species_name = clean_value(document.get("species"))
        if species_name is None:
            continue
        species_to_path[species_name] = json_path
    return species_to_path


def resolve_species_match(
    organism_name: str | None,
    species_to_path: OrderedDict[str, Path],
) -> tuple[str | None, str | None]:
    if organism_name is None:
        return None, None

    if organism_name in species_to_path:
        return organism_name, "exact"

    parts = organism_name.split()
    if len(parts) >= 3:
        first_two_words = " ".join(parts[:2])
        if first_two_words in species_to_path:
            return first_two_words, "first_two_words"

    return None, None


def build_genome_groups(
    genome_rows: list[OrderedDict[str, str | None]],
    species_to_path: OrderedDict[str, Path],
    clean_genome_dir: Path,
    publication_map: dict[str, list[OrderedDict[str, object]]],
) -> tuple[
    OrderedDict[str, list[OrderedDict[str, str | None]]],
    int,
    int,
]:
    genome_groups: OrderedDict[str, list[OrderedDict[str, str | None]]] = OrderedDict()
    first_two_rows = 0
    first_two_species_names: set[str] = set()

    for row in genome_rows:
        organism_name = clean_value(row.get("Organism Name"))
        matched_species, matched_by = resolve_species_match(organism_name, species_to_path)
        if matched_species is None:
            continue

        if matched_by == "first_two_words" and organism_name is not None:
            first_two_rows += 1
            first_two_species_names.add(organism_name)

        genome_groups.setdefault(matched_species, []).append(
            build_genome_record(
                row,
                clean_genome_dir,
                publication_map,
            )
        )

    for species_name, records in list(genome_groups.items()):
        genome_groups[species_name] = dedupe_object_dicts(records)

    return (
        genome_groups,
        first_two_rows,
        len(first_two_species_names),
    )


def write_document(
    json_path: Path,
    document: OrderedDict[str, object],
    indent: int,
) -> None:
    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")


GENOME_OWNED_FIELDS = {
    "source",
    "assembly_accession",
    "assembly_level",
    "assembly_release_date",
    "number_of_scaffolds",
    "source_link",
    "download_link",
    "local_path",
    "genome_size_bytes",
    "publications",
    "annotation",
}


def genome_record_merge_key(record: OrderedDict[str, object]) -> tuple[object, object]:
    return (
        clean_value(record.get("source")),
        clean_value(record.get("assembly_accession")),
    )


def preserve_existing_genome_fields(
    existing_records: list[OrderedDict[str, object]] | None,
    new_records: list[OrderedDict[str, object]],
) -> list[OrderedDict[str, object]]:
    existing_by_key: dict[tuple[object, object], OrderedDict[str, object]] = {}
    for record in existing_records or []:
        key = genome_record_merge_key(record)
        existing_by_key[key] = record

    preserved_records: list[OrderedDict[str, object]] = []
    for record in new_records:
        merged_record = OrderedDict(record)
        existing_record = existing_by_key.get(genome_record_merge_key(record))
        if existing_record is not None:
            for key, value in existing_record.items():
                if key in GENOME_OWNED_FIELDS:
                    continue
                if key not in merged_record:
                    merged_record[key] = value
        preserved_records.append(merged_record)
    return preserved_records


def update_species_jsons(
    species_to_path: OrderedDict[str, Path],
    genome_groups: OrderedDict[str, list[OrderedDict[str, str | None]]],
    annotation_groups: OrderedDict[str, list[OrderedDict[str, object]]],
    annotation_aliases: dict[str, str],
    clean_genome_dir: Path,
    indent: int,
) -> tuple[int, int]:
    updated_files = 0
    matched_species = 0

    for species_name, json_path in species_to_path.items():
        with json_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle, object_pairs_hook=OrderedDict)
        original_document = json.loads(
            json.dumps(loaded, ensure_ascii=False),
            object_pairs_hook=OrderedDict,
        )
        existing_genome_records = loaded.get("genome") or []

        if "ncbi_genomes" in loaded:
            del loaded["ncbi_genomes"]
        if "genome" in loaded:
            del loaded["genome"]
        loaded["genome_count"] = 0
        if "annotation" in loaded:
            del loaded["annotation"]
        if "annotation_count" in loaded:
            del loaded["annotation_count"]

        merged_genome_records = merge_genome_and_annotation_records(
            genome_records=genome_groups.get(species_name),
            annotation_records=annotation_groups.get(species_name),
            annotation_aliases=annotation_aliases,
            clean_genome_dir=clean_genome_dir,
        )
        if merged_genome_records:
            preserved_records = preserve_existing_genome_fields(
                existing_records=existing_genome_records,
                new_records=merged_genome_records,
            )
            loaded["genome"] = preserved_records
            loaded["genome_count"] = len(merged_genome_records)
            matched_species += 1

        if loaded == original_document:
            continue
        write_document(json_path, loaded, indent)
        updated_files += 1

    return updated_files, matched_species


def collect_unmatched_rows(
    genome_rows: list[OrderedDict[str, str | None]],
    species_to_path: OrderedDict[str, Path],
) -> list[OrderedDict[str, str | None]]:
    unmatched_rows: list[OrderedDict[str, str | None]] = []
    for row in genome_rows:
        organism_name = clean_value(row.get("Organism Name"))
        matched_species, _ = resolve_species_match(organism_name, species_to_path)
        if matched_species is None:
            unmatched_rows.append(row)
    return unmatched_rows


def write_unmatched_rows(
    unmatched_output: Path,
    fieldnames: list[str],
    unmatched_rows: list[OrderedDict[str, str | None]],
) -> None:
    unmatched_output.parent.mkdir(parents=True, exist_ok=True)
    output_fieldnames = fieldnames + ["reason"]
    with unmatched_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fieldnames, delimiter="\t")
        writer.writeheader()
        for row in unmatched_rows:
            output_row = OrderedDict(row)
            species_name = clean_value(row.get("Organism Name"))
            output_row["reason"] = (
                "missing_organism_name"
                if species_name is None
                else "species_not_found_in_json"
            )
            writer.writerow(output_row)


def collect_unmatched_annotation_species(
    annotation_species_map: dict[str, str],
    species_to_path: OrderedDict[str, Path],
) -> list[str]:
    unmatched: list[str] = []
    seen: set[str] = set()
    for species_name in annotation_species_map.values():
        cleaned = clean_value(species_name)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        matched_species, _ = resolve_species_match(
            cleaned,
            species_to_path,
        )
        if matched_species is None:
            unmatched.append(cleaned)
    return unmatched


def count_unmatched_species(
    unmatched_rows: list[OrderedDict[str, str | None]],
) -> int:
    return len(
        {
            clean_value(row.get("Organism Name"))
            for row in unmatched_rows
            if clean_value(row.get("Organism Name")) is not None
        }
    )


def build_log_summary(
    json_dir: Path,
) -> OrderedDict[str, object]:
    species_by_source: Counter[str] = Counter()
    records_by_source: Counter[str] = Counter()
    annotation_records_by_source: Counter[str] = Counter()
    annotation_path_counts: Counter[str] = Counter()
    publication_records_by_source: Counter[str] = Counter()
    distribution_species_by_source: Counter[str] = Counter()
    json_files_total = 0
    species_with_genome = 0
    species_with_annotation = 0
    species_with_publications = 0
    species_with_distribution = 0
    genome_records_total = 0
    annotation_records_total = 0
    genome_records_with_publications = 0
    publication_records_total = 0
    distribution_records_total = 0

    for json_path in json_dir.glob("*.json"):
        json_files_total += 1
        document = json.loads(json_path.read_text(encoding="utf-8"))
        records = document.get("genome") or []
        real_records = [
            record
            for record in records
            if record.get("genome_size_bytes") is not None
        ]
        if real_records:
            species_with_genome += 1
        has_annotation = False
        has_publications = False
        genome_records_total += len(real_records)
        sources = sorted(
            {
                clean_value(record.get("source"))
                for record in real_records
                if clean_value(record.get("source"))
            }
        )
        for source in sources:
            species_by_source[source] += 1
        for record in real_records:
            source = clean_value(record.get("source"))
            if source:
                records_by_source[source] += 1
            publications = record.get("publications") or []
            if isinstance(publications, dict):
                publications = [publications]
            if publications:
                has_publications = True
                genome_records_with_publications += 1
                publication_records_total += len(publications)
                if source:
                    publication_records_by_source[source] += len(publications)
        for record in records:
            annotation = record.get("annotation") or []
            if isinstance(annotation, dict):
                annotation = [annotation]
            if annotation:
                has_annotation = True
                annotation_records_total += len(annotation)
                for item in annotation:
                    annotation_source = clean_value(item.get("source"))
                    if annotation_source:
                        annotation_records_by_source[annotation_source] += 1
                    for field in ("cds_path", "gff3_path", "protein_path"):
                        if clean_value(item.get(field)):
                            annotation_path_counts[field] += 1
        if has_annotation:
            species_with_annotation += 1
        if has_publications:
            species_with_publications += 1

        distribution = document.get("distribution") or {}
        if isinstance(distribution, dict):
            distribution_count = distribution.get("count")
            distribution_sources = distribution.get("sources") or []
            if isinstance(distribution_count, int):
                species_with_distribution += 1
                distribution_records_total += distribution_count
                if isinstance(distribution_sources, list):
                    for source in distribution_sources:
                        cleaned_source = clean_value(source)
                        if cleaned_source:
                            distribution_species_by_source[cleaned_source] += 1

    return OrderedDict(
        [
            ("json_files_total", json_files_total),
            ("species_with_genome", species_with_genome),
            ("genome_records_total", genome_records_total),
            ("species_by_source", OrderedDict(sorted(species_by_source.items()))),
            ("records_by_source", OrderedDict(sorted(records_by_source.items()))),
            ("species_with_publications", species_with_publications),
            ("genome_records_with_publications", genome_records_with_publications),
            ("publication_records_total", publication_records_total),
            (
                "publication_records_by_source",
                OrderedDict(sorted(publication_records_by_source.items())),
            ),
            ("species_with_annotation", species_with_annotation),
            ("annotation_records_total", annotation_records_total),
            (
                "annotation_records_by_source",
                OrderedDict(sorted(annotation_records_by_source.items())),
            ),
            ("annotation_path_counts", OrderedDict(sorted(annotation_path_counts.items()))),
            ("species_with_distribution", species_with_distribution),
            ("distribution_records_total", distribution_records_total),
            (
                "distribution_species_by_source",
                OrderedDict(sorted(distribution_species_by_source.items())),
            ),
        ]
    )


def update_log_json(
    log_output: Path,
    log_summary: OrderedDict[str, object],
    indent: int,
) -> None:
    if log_output.exists():
        with log_output.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)
    else:
        document = OrderedDict()

    for key, value in log_summary.items():
        document[key] = value

    with log_output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")


def main() -> int:
    args = parse_args()
    genome_path = resolve_repo_path(args.genome_input)
    ibg_dir = resolve_repo_path(args.ibg_dir)
    ibg_xlsx_path = resolve_repo_path(args.ibg_xlsx)
    ngdc_dir = resolve_repo_path(args.ngdc_dir)
    ngdc_xlsx_path = resolve_repo_path(args.ngdc_xlsx)
    publication_tsv = resolve_repo_path(args.publication_tsv)
    mapping_tsv = resolve_repo_path(args.mapping_tsv)
    json_dir = resolve_repo_path(args.json_dir)
    clean_genome_dir = resolve_repo_path(args.clean_genome_dir)
    clean_anno_dir = resolve_repo_path(args.clean_anno_dir)
    log_output = resolve_repo_path(args.log_output)
    unmatched_output = (
        resolve_repo_path(args.unmatched_output)
        if args.unmatched_output
        else resolve_repo_path(str(DEFAULT_UNMATCHED_OUTPUT))
    )

    generate_species_mapping_tsv(
        genome_path=genome_path,
        ibg_xlsx_path=ibg_xlsx_path,
        mapping_tsv_path=mapping_tsv,
    )
    mapping_rows = load_mapping_rows(mapping_tsv)
    (
        ibg_rows,
        annotation_aliases,
        annotation_species_map,
        annotation_species_metadata,
        annotation_accession_metadata,
    ) = build_mapping_indexes(mapping_rows)
    publication_map = load_publication_map(publication_tsv)
    genome_rows, fieldnames, skipped_rows = load_genome_rows(
        genome_path,
        ibg_rows,
        ngdc_dir,
        ngdc_xlsx_path,
    )
    ibg_skipped_rows = sum(
        1
        for row in mapping_rows
        if row.get("record_type") == "ibg_mapping" and row.get("ibg_keep_genome") == "0"
    )
    ibg_skipped_species = len(
        {
            clean_value(row.get("clean_species_name"))
            for row in mapping_rows
            if row.get("record_type") == "ibg_mapping" and row.get("ibg_keep_genome") == "0"
        }
    )
    species_to_path = load_species_json_paths(json_dir)

    unmatched_before_clean = collect_unmatched_rows(genome_rows, species_to_path)
    unmatched_before_clean_rows = len(unmatched_before_clean)
    unmatched_before_clean_species = count_unmatched_species(unmatched_before_clean)

    (
        genome_groups,
        first_two_rows,
        first_two_species,
    ) = build_genome_groups(
        genome_rows=genome_rows,
        species_to_path=species_to_path,
        clean_genome_dir=clean_genome_dir,
        publication_map=publication_map,
    )
    genome_accession_species_map = build_genome_accession_species_map(genome_groups)
    annotation_groups = build_annotation_groups(
        clean_anno_dir,
        annotation_species_map,
        annotation_accession_metadata,
        genome_accession_species_map,
        species_to_path,
    )
    updated_files, matched_species = update_species_jsons(
        species_to_path,
        genome_groups,
        annotation_groups,
        annotation_aliases,
        clean_genome_dir,
        args.indent,
    )
    unmatched_rows = collect_unmatched_rows(genome_rows, species_to_path)
    unmatched_genome_species = count_unmatched_species(unmatched_rows)
    write_unmatched_rows(unmatched_output, fieldnames, unmatched_rows)

    log_output.parent.mkdir(parents=True, exist_ok=True)
    refresh_log_summary(
        json_dir=json_dir,
        log_output=log_output,
        indent=args.indent,
    )

    print(f"root={ROOT}")
    print(f"genome_input={genome_path}")
    print(f"ibg_dir={ibg_dir}")
    print(f"ibg_xlsx={ibg_xlsx_path}")
    print(f"ngdc_dir={ngdc_dir}")
    print(f"ngdc_xlsx={ngdc_xlsx_path}")
    print(f"publication_tsv={publication_tsv}")
    print(f"mapping_tsv={mapping_tsv}")
    print(f"json_dir={json_dir}")
    print(f"clean_genome_dir={clean_genome_dir}")
    print(f"clean_anno_dir={clean_anno_dir}")
    print(f"log_output={log_output}")
    print(f"unmatched_output={unmatched_output}")
    print(f"json_files_updated={updated_files}")
    print(f"species_with_genome={matched_species}")
    print(f"matched_by_first_two_words_rows={first_two_rows}")
    print(f"matched_by_first_two_words_species={first_two_species}")
    print(f"ibg_skipped_when_number_in_ncbi_rows={ibg_skipped_rows}")
    print(f"ibg_skipped_when_number_in_ncbi_species={ibg_skipped_species}")
    print(f"unmatched_before_clean_rows={unmatched_before_clean_rows}")
    print(f"unmatched_before_clean_species={unmatched_before_clean_species}")
    print(f"unmatched_genome_species={unmatched_genome_species}")
    print(f"unmatched_genome_rows={len(unmatched_rows)}")
    print(f"skipped_genome_rows_without_organism_name={skipped_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
