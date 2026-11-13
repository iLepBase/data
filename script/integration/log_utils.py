from __future__ import annotations

import json
from collections import Counter, OrderedDict
from pathlib import Path


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def build_log_summary(json_dir: Path) -> OrderedDict[str, object]:
    taxo_species_by_source: Counter[str] = Counter()
    genome_species_by_source: Counter[str] = Counter()
    genome_records_by_source: Counter[str] = Counter()
    annotation_records_by_source: Counter[str] = Counter()
    annotation_path_counts: Counter[str] = Counter()
    publication_records_by_source: Counter[str] = Counter()
    distribution_species_by_source: Counter[str] = Counter()
    busco_records_by_source: Counter[str] = Counter()
    chromosome_records_by_source: Counter[str] = Counter()

    taxo_species_total = 0
    taxo_records_total = 0
    species_with_genome = 0
    species_with_annotation = 0
    species_with_publications = 0
    species_with_distribution = 0
    species_with_hostplant = 0
    species_with_busco = 0
    species_with_chromosomes = 0
    species_with_coi = 0
    species_with_transcriptome = 0
    species_with_pdb = 0

    genome_records_total = 0
    annotation_records_total = 0
    genome_records_with_publications = 0
    publication_records_total = 0
    distribution_records_total = 0
    hostplant_records_total = 0
    genome_records_with_busco = 0
    genome_records_with_chromosomes = 0
    chromosome_entries_total = 0
    coi_records_total = 0
    transcriptome_records_total = 0
    pdb_records_total = 0
    pdb_records_with_entities = 0
    pdb_records_with_metadata = 0
    pdb_records_with_structures = 0

    for json_path in json_dir.glob("*.json"):
        taxo_species_total += 1
        document = json.loads(json_path.read_text(encoding="utf-8"))
        record_count = document.get("record_count")
        if isinstance(record_count, int):
            taxo_records_total += record_count

        taxo_sources = document.get("sources") or []
        if isinstance(taxo_sources, list):
            for source in taxo_sources:
                cleaned_source = clean_value(source)
                if cleaned_source:
                    taxo_species_by_source[cleaned_source] += 1

        records = document.get("genome") or []
        real_records = [
            record
            for record in records
            if record.get("genome_size_bytes") is not None
        ]
        if real_records:
            species_with_genome += 1
        genome_records_total += len(real_records)

        has_annotation = False
        has_publications = False
        has_distribution = False
        has_busco = False
        has_chromosomes = False

        sources = sorted(
            {
                clean_value(record.get("source"))
                for record in real_records
                if clean_value(record.get("source"))
            }
        )
        for source in sources:
            genome_species_by_source[source] += 1

        for record in real_records:
            source = clean_value(record.get("source"))
            if source:
                genome_records_by_source[source] += 1

            publications = _as_list(record.get("publications"))
            if publications:
                has_publications = True
                genome_records_with_publications += 1
                publication_records_total += len(publications)
                if source:
                    publication_records_by_source[source] += len(publications)

            busco = record.get("busco")
            if isinstance(busco, dict) and busco:
                has_busco = True
                genome_records_with_busco += 1
                if source:
                    busco_records_by_source[source] += 1

            chromosomes = _as_list(record.get("chromosomes"))
            if chromosomes:
                has_chromosomes = True
                genome_records_with_chromosomes += 1
                chromosome_entries_total += len(chromosomes)
                if source:
                    chromosome_records_by_source[source] += 1

        for record in records:
            annotation = _as_list(record.get("annotation"))
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
        if has_busco:
            species_with_busco += 1
        if has_chromosomes:
            species_with_chromosomes += 1

        distribution = document.get("distribution") or {}
        if isinstance(distribution, dict):
            distribution_count = distribution.get("count")
            distribution_sources = distribution.get("sources") or []
            if isinstance(distribution_count, int):
                has_distribution = True
                distribution_records_total += distribution_count
                if isinstance(distribution_sources, list):
                    for source in distribution_sources:
                        cleaned_source = clean_value(source)
                        if cleaned_source:
                            distribution_species_by_source[cleaned_source] += 1
        if has_distribution:
            species_with_distribution += 1

        hostplant = document.get("Hostplant") or document.get("hostplant") or {}
        if isinstance(hostplant, dict):
            hostplant_count = hostplant.get("count")
            if isinstance(hostplant_count, int):
                species_with_hostplant += 1
                hostplant_records_total += hostplant_count

        coi = document.get("COI") or document.get("coi") or {}
        if isinstance(coi, dict):
            coi_count = coi.get("count")
            if isinstance(coi_count, int):
                species_with_coi += 1
                coi_records_total += coi_count

        transcriptome = document.get("Transcriptome") or document.get("transcriptome") or {}
        if isinstance(transcriptome, dict):
            transcriptome_count = transcriptome.get("count")
            if isinstance(transcriptome_count, int):
                species_with_transcriptome += 1
                transcriptome_records_total += transcriptome_count

        pdb = document.get("PDB") or document.get("pdb") or {}
        if isinstance(pdb, dict):
            pdb_count = pdb.get("count")
            pdb_records = _as_list(pdb.get("records"))
            if isinstance(pdb_count, int):
                species_with_pdb += 1
                pdb_records_total += pdb_count
            for record in pdb_records:
                entities = record.get("entities")
                if isinstance(entities, list) and entities:
                    pdb_records_with_entities += 1
                if clean_value(record.get("metadata")):
                    pdb_records_with_metadata += 1
                if clean_value(record.get("structures")):
                    pdb_records_with_structures += 1

    return OrderedDict(
        [
            (
                "taxo",
                OrderedDict(
                    [
                        ("species_total", taxo_species_total),
                        ("records_total", taxo_records_total),
                        (
                            "species_by_source",
                            OrderedDict(sorted(taxo_species_by_source.items())),
                        ),
                    ]
                ),
            ),
            (
                "genome",
                OrderedDict(
                    [
                        ("species_total", species_with_genome),
                        ("records_total", genome_records_total),
                        (
                            "species_by_source",
                            OrderedDict(sorted(genome_species_by_source.items())),
                        ),
                        (
                            "records_by_source",
                            OrderedDict(sorted(genome_records_by_source.items())),
                        ),
                        (
                            "annotation",
                            OrderedDict(
                                [
                                    ("species_total", species_with_annotation),
                                    ("records_total", annotation_records_total),
                                    (
                                        "records_by_source",
                                        OrderedDict(sorted(annotation_records_by_source.items())),
                                    ),
                                    (
                                        "path_counts",
                                        OrderedDict(sorted(annotation_path_counts.items())),
                                    ),
                                ]
                            ),
                        ),
                        (
                            "publications",
                            OrderedDict(
                                [
                                    ("species_total", species_with_publications),
                                    ("genome_records_total", genome_records_with_publications),
                                    ("records_total", publication_records_total),
                                    (
                                        "records_by_source",
                                        OrderedDict(sorted(publication_records_by_source.items())),
                                    ),
                                ]
                            ),
                        ),
                        (
                            "busco",
                            OrderedDict(
                                [
                                    ("species_total", species_with_busco),
                                    ("genome_records_total", genome_records_with_busco),
                                    (
                                        "records_by_source",
                                        OrderedDict(sorted(busco_records_by_source.items())),
                                    ),
                                ]
                            ),
                        ),
                        (
                            "chromosomes",
                            OrderedDict(
                                [
                                    ("species_total", species_with_chromosomes),
                                    ("genome_records_total", genome_records_with_chromosomes),
                                    ("entries_total", chromosome_entries_total),
                                    (
                                        "records_by_source",
                                        OrderedDict(sorted(chromosome_records_by_source.items())),
                                    ),
                                ]
                            ),
                        ),
                    ]
                ),
            ),
            (
                "Transcriptome",
                OrderedDict(
                    [
                        ("species_total", species_with_transcriptome),
                        ("records_total", transcriptome_records_total),
                    ]
                ),
            ),
            (
                "Hostplant",
                OrderedDict(
                    [
                        ("species_total", species_with_hostplant),
                        ("records_total", hostplant_records_total),
                    ]
                ),
            ),
            (
                "distribution",
                OrderedDict(
                    [
                        ("species_total", species_with_distribution),
                        ("records_total", distribution_records_total),
                        (
                            "species_by_source",
                            OrderedDict(sorted(distribution_species_by_source.items())),
                        ),
                    ]
                ),
            ),
            (
                "COI",
                OrderedDict(
                    [
                        ("species_total", species_with_coi),
                        ("records_total", coi_records_total),
                    ]
                ),
            ),
            (
                "PDB",
                OrderedDict(
                    [
                        ("species_total", species_with_pdb),
                        ("records_total", pdb_records_total),
                        ("records_with_entities", pdb_records_with_entities),
                        ("records_with_metadata", pdb_records_with_metadata),
                        ("records_with_structures", pdb_records_with_structures),
                    ]
                ),
            ),
        ]
    )


def update_log_json(log_output: Path, summary: OrderedDict[str, object], indent: int) -> None:
    if log_output.exists():
        with log_output.open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=OrderedDict)
    else:
        document = OrderedDict()

    for key, value in summary.items():
        document[key] = value

    with log_output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")


def refresh_log_summary(json_dir: Path, log_output: Path, indent: int) -> OrderedDict[str, object]:
    summary = build_log_summary(json_dir)
    update_log_json(log_output, summary, indent)
    return summary
