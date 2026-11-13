# Transcriptome metadata processing

Retrieve SRA and BioSample metadata, normalize sample attributes, re-extract cached fields and backfill geographic coordinates.

## Inputs and outputs

Input: SRA exports and BioSample XML/cache records. Output: normalized transcriptome CSV tables and review reports.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/transcriptome/sra_transcriptome_crawler.py --help
```

## Workflow

1. Retrieve metadata with `sra_transcriptome_crawler.py`.
2. Standardize sex, developmental stage, tissue and locality fields with `normalize_sra.py`.
3. Use `reextract_biosample_fields.py` when rebuilding fields from cached BioSample XML.
4. Use `backfill_geographic_coordinates.R` for coordinate backfilling where locality information supports it.
5. Integrate records with `../integration/transcriptome_to_species_json.py`.

The archived normalizer is a different implementation. It has not been merged into the active normalizer. Identical crawler/XML-parser copies are retained only in the archive for provenance. A processed metadata table is not necessarily the final species-linked table: integration additionally checks Run IDs and taxonomy matches.

The local `pyproject.toml` and `uv.lock` describe the crawler environment; they are not a complete environment specification for all Python and R scripts in this repository.

## Script inventory

| Script | Interface / role |
| --- | --- |
| `backfill_geographic_coordinates.R` | R processing script; inspect input paths and package imports before execution. |
| `normalize_sra.py` | CLI options: `--input`, `--output`, `--review-dir`, `--cache-db`, `--synonym-output`, `--skip-cache-backfill`, `--in-place` |
| `process_xml.py` | CLI options: `--input`, `--output-dir`, `--fields-name`, `--flat-name`, `--table`, `--xml-column`, `--id-columns`, `--skip-wide`, `--skip-missing`, `--limit` |
| `reextract_biosample_fields.py` | CLI options: `--cache-db`, `--transcriptome-csv`, `--synonym-output` |
| `sra_transcriptome_crawler.py` | CLI options: `--input`, `--output`, `--api-key`, `--cache-db`, `--workers` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
