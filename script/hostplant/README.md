# Host-plant association processing

Reconcile taxonomic names, prepare manual-review tasks and finalize curated host-association tables.

## Inputs and outputs

Input: association workbooks and review decisions. Output: cleaned association tables and manual-review reports.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/hostplant/hostplant_taxonomy.py --help
```

## Curation

Start with `hostplant_taxonomy.py`, inspect the review outputs, and use the review/postprocessing helpers as required. The two `finalize_hostplant_outputs*` scripts implement different finalization strategies; they are alternatives, not mandatory consecutive steps. Do not select a strategy solely to preserve a desired record count.

## Script inventory

| Script | Interface / role |
| --- | --- |
| `bucket_review_tasks.py` | CLI options: `--cleaned-csv`, `--review-csv`, `--out-dir`, `--prefix` |
| `finalize_hostplant_outputs.py` | CLI options: `--cleaned-csv`, `--manual-csv`, `--out-clean-sp`, `--out-clean-genus`, `--out-review-genus`, `--out-review-none` |
| `finalize_hostplant_outputs_preserve_counts.py` | CLI options: `--input-xlsx`, `--cleaned-csv`, `--out-dir` |
| `hostplant_taxonomy.py` | CLI options: `--input-xlsx`, `--sheet-name`, `--output-csv`, `--review-csv`, `--cache-db`, `--limit`, `--overwrite-existing`, `--skip-filled`, `--progress-every`, `--workers` |
| `postprocess_cleaned_csv.py` | CLI options: `--input-csv`, `--output-csv`, `--drop-note` |
| `simple_manual_review_list.py` | CLI options: `--review-csv`, `--output-csv`, `--max-candidates`, `--only-multi-candidates`, `--disable-ghost-stop`, `--chain-workers`, `--chain-progress-every` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
