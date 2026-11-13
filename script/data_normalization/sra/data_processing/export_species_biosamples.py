#!/usr/bin/env python3
"""
Export BioSample XML cache into one JSON file per species.

Rules:
- Only keep species listed in `data/raw/SRA/clean_taxo/sra.clean.sp.csv`
- Each species gets one JSON file
- Each JSON file contains all BioSample records for that species
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = REPO_ROOT / "data/clean/SRA/ncbi_request_cache.sqlite"
DEFAULT_SPECIES_CSV = REPO_ROOT / "data/raw/SRA/clean_taxo/sra.clean.sp.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/clean/SRA/species"


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def safe_filename(species_name: str) -> str:
    normalized = normalize_space(species_name).replace(" ", "_")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    return normalized.strip("._") or "unknown_species"


def xml_element_to_data(element: ET.Element) -> object:
    children = list(element)
    text = normalize_space(element.text)

    payload: dict[str, object] = {}
    if element.attrib:
        payload["@attributes"] = dict(element.attrib)

    if not children:
        if payload:
            if text:
                payload["#text"] = text
            return payload
        return text

    grouped: dict[str, list[object]] = defaultdict(list)
    for child in children:
        grouped[child.tag].append(xml_element_to_data(child))

    for tag, values in grouped.items():
        payload[tag] = values[0] if len(values) == 1 else values

    if text:
        payload["#text"] = text

    return payload


def load_target_species(csv_path: Path) -> dict[str, dict[str, str]]:
    species_rows: dict[str, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            species_name = normalize_space(row.get("species"))
            if not species_name:
                continue
            if species_name not in species_rows:
                species_rows[species_name] = {key: value for key, value in row.items()}
    return species_rows


def extract_species_name(biosample: ET.Element) -> str:
    organism_name = normalize_space(biosample.findtext("./Description/Organism/OrganismName"))
    if organism_name:
        return organism_name
    organism_node = biosample.find("./Description/Organism")
    if organism_node is not None:
        return normalize_space(organism_node.attrib.get("taxonomy_name"))
    return ""


def extract_attribute_count(biosample: ET.Element) -> int:
    return len(biosample.findall("./Attributes/Attribute"))


def build_record(
    request_accession: str,
    resolved_biosample_accession: str | None,
    sra_sample_accession: str | None,
    fetched_at: str,
    biosample: ET.Element,
) -> dict[str, object]:
    organism_node = biosample.find("./Description/Organism")
    return {
        "request_accession": request_accession,
        "resolved_biosample_accession": resolved_biosample_accession,
        "sra_sample_accession": sra_sample_accession,
        "fetched_at": fetched_at,
        "biosample_accession": biosample.attrib.get("accession"),
        "biosample_id": biosample.attrib.get("id"),
        "title": normalize_space(biosample.findtext("./Description/Title")),
        "taxonomy_id": organism_node.attrib.get("taxonomy_id") if organism_node is not None else "",
        "taxonomy_name": organism_node.attrib.get("taxonomy_name") if organism_node is not None else "",
        "organism_name": normalize_space(biosample.findtext("./Description/Organism/OrganismName")),
        "attribute_count": extract_attribute_count(biosample),
        "biosample": xml_element_to_data(biosample),
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=DEFAULT_SQLITE_PATH,
        help=f"SQLite cache path. Default: {DEFAULT_SQLITE_PATH.relative_to(REPO_ROOT)}",
    )
    parser.add_argument(
        "--species-csv",
        type=Path,
        default=DEFAULT_SPECIES_CSV,
        help=f"Species CSV path. Default: {DEFAULT_SPECIES_CSV.relative_to(REPO_ROOT)}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR.relative_to(REPO_ROOT)}",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    target_species = load_target_species(args.species_csv)
    species_records: dict[str, list[dict[str, object]]] = defaultdict(list)

    conn = sqlite3.connect(args.sqlite)
    cursor = conn.execute(
        """
        SELECT request_accession, resolved_biosample_accession, sra_sample_accession, raw_xml, fetched_at
        FROM biosample_xml_cache
        """
    )

    xml_rows = 0
    matched_rows = 0
    parse_errors = 0

    for request_accession, resolved_biosample_accession, sra_sample_accession, raw_xml, fetched_at in cursor:
        xml_rows += 1
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            parse_errors += 1
            continue

        biosample = root.find(".//BioSample")
        if biosample is None and root.tag == "BioSample":
            biosample = root
        if biosample is None:
            continue

        species_name = extract_species_name(biosample)
        if not species_name or species_name not in target_species:
            continue

        matched_rows += 1
        species_records[species_name].append(
            build_record(
                request_accession=request_accession,
                resolved_biosample_accession=resolved_biosample_accession,
                sra_sample_accession=sra_sample_accession,
                fetched_at=fetched_at,
                biosample=biosample,
            )
        )

    conn.close()

    written_species = 0
    for species_name, records in species_records.items():
        records.sort(
            key=lambda record: (
                str(record.get("biosample_accession") or ""),
                str(record.get("request_accession") or ""),
            )
        )
        payload = {
            "species": species_name,
            "species_metadata": target_species.get(species_name, {}),
            "biosample_count": len(records),
            "biosamples": records,
        }
        output_path = args.output_dir / f"{safe_filename(species_name)}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written_species += 1

    unmatched_species = sorted(set(target_species).difference(species_records))

    print(f"Total XML rows scanned: {xml_rows}")
    print(f"Matched XML rows: {matched_rows}")
    print(f"Species requested: {len(target_species)}")
    print(f"Species written: {written_species}")
    print(f"Species unmatched: {len(unmatched_species)}")
    print(f"XML parse errors: {parse_errors}")
    print(f"Output directory: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
