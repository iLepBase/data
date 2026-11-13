#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import quote


DEFAULT_CSV_ID_COLUMNS = [
    "Experiment Accession",
    "Sample Accession",
    "Run ID",
    "Organism Name",
]
DEFAULT_SQLITE_ID_COLUMNS = [
    "request_accession",
    "lookup_id",
    "resolved_biosample_accession",
    "sra_sample_accession",
    "fetched_at",
]
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


def clean_text(value: str | None) -> str:
    text = (value or "").replace("\ufeff", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_label(value: str | None) -> str:
    return clean_text(value).replace("/", "_")


def add_pair(pairs: list[tuple[str, str]], field: str, value: str | None) -> None:
    text = clean_text(value)
    if text:
        pairs.append((field, text))


def extract_biosample_pairs(xml_text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    xml_text = clean_text(xml_text)
    if not xml_text or xml_text == "N/A":
        return pairs

    root = ET.fromstring(xml_text)
    biosample = root.find("./BioSample")
    if biosample is None:
        return pairs

    for key, value in biosample.attrib.items():
        add_pair(pairs, f"BioSample/@{key}", value)

    id_seen: Counter[str] = Counter()
    for node in biosample.findall("./Ids/Id"):
        label = normalize_label(node.attrib.get("db") or node.attrib.get("db_label") or "Id")
        id_seen[label] += 1
        suffix = "" if id_seen[label] == 1 else f"[{id_seen[label]}]"
        base = f"BioSample/Ids/{label}{suffix}"
        add_pair(pairs, f"{base}/text", node.text)
        for key, value in node.attrib.items():
            add_pair(pairs, f"{base}/@{key}", value)

    add_pair(pairs, "BioSample/Description/Title", biosample.findtext("./Description/Title"))
    organism = biosample.find("./Description/Organism")
    if organism is not None:
        for key, value in organism.attrib.items():
            add_pair(pairs, f"BioSample/Description/Organism/@{key}", value)
        add_pair(
            pairs,
            "BioSample/Description/Organism/OrganismName",
            organism.findtext("./OrganismName"),
        )

    add_pair(pairs, "BioSample/Owner/Name", biosample.findtext("./Owner/Name"))
    for index, contact in enumerate(biosample.findall("./Owner/Contacts/Contact"), 1):
        add_pair(
            pairs,
            f"BioSample/Owner/Contacts/Contact[{index}]/Name/First",
            contact.findtext("./Name/First"),
        )
        add_pair(
            pairs,
            f"BioSample/Owner/Contacts/Contact[{index}]/Name/Last",
            contact.findtext("./Name/Last"),
        )

    for index, model in enumerate(biosample.findall("./Models/Model"), 1):
        add_pair(pairs, f"BioSample/Models/Model[{index}]", model.text)

    package = biosample.find("./Package")
    if package is not None:
        add_pair(pairs, "BioSample/Package/text", package.text)
        for key, value in package.attrib.items():
            add_pair(pairs, f"BioSample/Package/@{key}", value)

    attr_seen: Counter[str] = Counter()
    for attr in biosample.findall("./Attributes/Attribute"):
        label = normalize_label(
            attr.attrib.get("attribute_name")
            or attr.attrib.get("harmonized_name")
            or attr.attrib.get("display_name")
            or "Attribute"
        )
        attr_seen[label] += 1
        suffix = "" if attr_seen[label] == 1 else f"[{attr_seen[label]}]"
        base = f"BioSample/Attributes/{label}{suffix}"
        add_pair(pairs, f"{base}/text", attr.text)
        for key, value in attr.attrib.items():
            add_pair(pairs, f"{base}/@{key}", value)

    for index, link in enumerate(biosample.findall("./Links/Link"), 1):
        base = f"BioSample/Links/Link[{index}]"
        add_pair(pairs, f"{base}/text", link.text)
        for key, value in link.attrib.items():
            add_pair(pairs, f"{base}/@{key}", value)

    status = biosample.find("./Status")
    if status is not None:
        for key, value in status.attrib.items():
            add_pair(pairs, f"BioSample/Status/@{key}", value)

    return pairs


def detect_source_type(input_path: Path) -> str:
    return "sqlite" if input_path.suffix.lower() in SQLITE_SUFFIXES else "csv"


def default_id_columns(source_type: str) -> list[str]:
    if source_type == "sqlite":
        return DEFAULT_SQLITE_ID_COLUMNS.copy()
    return DEFAULT_CSV_ID_COLUMNS.copy()


def default_xml_column(source_type: str) -> str:
    if source_type == "sqlite":
        return "raw_xml"
    return "biosample_raw"


def sqlite_uri(input_path: Path) -> str:
    return "file:" + quote(str(input_path)) + "?mode=ro&immutable=1"


def open_sqlite_readonly(input_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(sqlite_uri(input_path), uri=True)


def quote_sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Expand BioSample XML into a field summary and a wide table."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input CSV or SQLite path.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for output files.",
    )
    parser.add_argument(
        "--fields-name",
        default=None,
        help="Output filename for the field summary TSV.",
    )
    parser.add_argument(
        "--flat-name",
        default=None,
        help="Output filename for the wide flat CSV.",
    )
    parser.add_argument(
        "--table",
        default="biosample_xml_cache",
        help="SQLite table name when --input points to a SQLite database.",
    )
    parser.add_argument(
        "--xml-column",
        default=None,
        help="XML column name. Defaults: biosample_raw for CSV, raw_xml for SQLite.",
    )
    parser.add_argument(
        "--id-columns",
        nargs="+",
        default=None,
        help="Identifier columns to keep in the parsed outputs.",
    )
    parser.add_argument(
        "--skip-wide",
        action="store_true",
        help="Skip writing the wide flat CSV.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip writing the missing/parse-error TSV.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N rows (0 means all rows).",
    )
    return parser.parse_args()


def validate_columns(
    fieldnames: list[str] | None,
    id_columns: list[str],
    xml_column: str,
    source_label: str,
) -> None:
    if not fieldnames:
        raise ValueError(f"{source_label} has no columns")
    if xml_column not in fieldnames:
        raise ValueError(f"{source_label} missing required XML column: {xml_column}")
    missing = [column for column in id_columns if column not in fieldnames]
    if missing:
        raise ValueError(f"{source_label} missing id columns: {', '.join(missing)}")


def load_csv_rows(input_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = [{key: clean_text(value) for key, value in row.items()} for row in reader]
    return fieldnames, rows


def load_sqlite_rows(input_path: Path, table: str) -> tuple[list[str], list[dict[str, str]]]:
    conn = open_sqlite_readonly(input_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT * FROM {quote_sql_identifier(table)}")
        except sqlite3.OperationalError as exc:
            raise ValueError(f"sqlite table not found or unreadable: {table}") from exc
        fieldnames = [item[0] for item in cur.description or []]
        rows = []
        for row in cur:
            rows.append({key: clean_text(row[key]) for key in fieldnames})
    finally:
        conn.close()
    return fieldnames, rows


def load_rows(input_path: Path, source_type: str, table: str) -> tuple[list[str], list[dict[str, str]]]:
    if source_type == "sqlite":
        return load_sqlite_rows(input_path, table)
    return load_csv_rows(input_path)


def write_missing_rows(
    path: Path,
    rows: list[dict[str, str]],
    id_columns: list[str],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["row_number", *id_columns, "missing_reason"],
            delimiter="\t",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_field_summary(path: Path, field_counter: Counter[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["biosample_field", "row_count"], delimiter="\t")
        writer.writeheader()
        for field, count in field_counter.most_common():
            writer.writerow({"biosample_field": field, "row_count": count})


def summarize_rows(
    rows: list[dict[str, str]],
    id_columns: list[str],
    xml_column: str,
    limit: int,
) -> tuple[Counter[str], list[dict[str, str]], int]:
    field_counter: Counter[str] = Counter()
    missing_rows: list[dict[str, str]] = []
    total_rows = 0

    for row_number, row in enumerate(rows, 1):
        if limit and row_number > limit:
            break
        total_rows += 1
        raw_xml = row.get(xml_column, "")
        id_part = {column: row.get(column, "") for column in id_columns}
        try:
            pairs = extract_biosample_pairs(raw_xml)
            missing_reason = "N/A" if clean_text(raw_xml) in {"", "N/A"} else ""
        except ET.ParseError:
            pairs = []
            missing_reason = "parse_error:ParseError"

        unique_fields = {field for field, _ in pairs}
        field_counter.update(unique_fields)
        if missing_reason:
            missing_rows.append(
                {
                    "row_number": str(row_number),
                    **id_part,
                    "missing_reason": missing_reason,
                }
            )

    return field_counter, missing_rows, total_rows


def write_wide_table(
    rows: list[dict[str, str]],
    wide_path: Path,
    id_columns: list[str],
    fields: list[str],
    xml_column: str,
    limit: int,
) -> None:
    with wide_path.open("w", encoding="utf-8", newline="") as output_handle:
        writer = csv.DictWriter(output_handle, fieldnames=[*id_columns, *fields])
        writer.writeheader()

        for row_number, row in enumerate(rows, 1):
            if limit and row_number > limit:
                break
            raw_xml = row.get(xml_column, "")
            try:
                pairs = extract_biosample_pairs(raw_xml)
            except ET.ParseError:
                pairs = []
            flat = {field: value for field, value in pairs}
            writer.writerow(
                {
                    **{column: row.get(column, "") for column in id_columns},
                    **{field: flat.get(field, "") for field in fields},
                }
            )


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    source_type = detect_source_type(input_path)
    output_dir = Path(args.output_dir)
    id_columns = list(args.id_columns or default_id_columns(source_type))
    xml_column = args.xml_column or default_xml_column(source_type)

    if not input_path.exists():
        print(f"[error] input not found: {input_path}", file=sys.stderr)
        return 2

    try:
        fieldnames, rows = load_rows(input_path, source_type, args.table)
        source_label = f"sqlite table '{args.table}'" if source_type == "sqlite" else "input CSV"
        validate_columns(fieldnames, id_columns, xml_column, source_label)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{input_path.stem}.{args.table}" if source_type == "sqlite" else input_path.stem
    fields_path = output_dir / (args.fields_name or f"{stem}.biosample_fields.tsv")
    missing_path = output_dir / f"{stem}.biosample_missing.tsv"
    wide_path = output_dir / (args.flat_name or f"{stem}.biosample_flat.csv")

    print(f"[info] source_type={source_type}")
    if source_type == "sqlite":
        print(f"[info] sqlite_table={args.table}")
    print(f"[info] xml_column={xml_column}")
    print(f"[info] id_columns={','.join(id_columns)}")

    field_counter, missing_rows, total_rows = summarize_rows(
        rows=rows,
        id_columns=id_columns,
        xml_column=xml_column,
        limit=args.limit,
    )
    write_field_summary(fields_path, field_counter)
    if not args.skip_missing:
        write_missing_rows(missing_path, missing_rows, id_columns)

    if not args.skip_wide:
        write_wide_table(
            rows=rows,
            wide_path=wide_path,
            id_columns=id_columns,
            fields=sorted(field_counter),
            xml_column=xml_column,
            limit=args.limit,
        )

    print(f"[done] rows={total_rows}")
    print(f"[done] unique_fields={len(field_counter)}")
    print(f"[done] fields={fields_path}")
    if not args.skip_missing:
        print(f"[done] missing={missing_path}")
    if not args.skip_wide:
        print(f"[done] wide={wide_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
