# Taxonomic name reconciliation

Reconcile source names with NCBI and GBIF taxonomy and produce candidate matches and manual-review tables.

## Inputs and outputs

Input: source species lists or workbooks. Output: standardized taxonomy and review tables.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/taxonomy/clean_species.py --help
```


## Script inventory

| Script | Interface / role |
| --- | --- |
| `0.species_name_clean.R` | R processing script; inspect input paths and package imports before execution. |
| `clean_species.py` | CLI options: `--input-file`, `--input-xlsx`, `--sheet-name`, `--output-prefix`, `--cache-db`, `--limit`, `--overwrite-existing`, `--skip-filled`, `--progress-every`, `--workers`, `--fuzzy-max-dist` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
