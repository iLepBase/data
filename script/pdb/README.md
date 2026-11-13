# Protein structure metadata processing

Enrich PDB metadata with taxonomic information and filter against the supported taxonomy.

## Inputs and outputs

Input: PDB metadata tables. Output: taxonomically enriched and filtered tables.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/pdb/clean/pdb_clean_taxo.py --help
```


## Script inventory

| Script | Interface / role |
| --- | --- |
| `clean/pdb_clean_add_order.py` | CLI options: `--input`, `--cache-db`, `--in-place`, `--output-dir`, `--overwrite-existing` |
| `clean/pdb_clean_taxo.py` | CLI options: `--clean-file`, `--summary-file`, `--output-file`, `--clean-key-column`, `--summary-key-column`, `--pdb-id-column`, `--keep-summary-rows` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
