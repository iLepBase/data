# Distribution record processing

Extract GBIF/BOLD occurrence records, reconcile names with the taxonomy table and split results by species.

## Inputs and outputs

Input: source occurrence exports and taxonomy tables. Output: normalized, filtered and species-specific occurrence files.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/distribution/filter_dist_by_taxo.py --help
```


## Script inventory

| Script | Interface / role |
| --- | --- |
| `dist_clean.sh` | Bash processing script; requires a Bash-compatible environment. |
| `filter_bold_by_taxo.py` | CLI options: `--bold`, `--taxonomy`, `--output` |
| `filter_dist_by_taxo.py` | CLI options: `--taxo-tsv`, `--dist-tsv`, `--output`, `--progress-every` |
| `filter_gbif_by_taxo.py` | CLI options: `--taxo-tsv`, `--gbif-tsv`, `--output`, `--progress-every` |
| `merged_file.sh` | Bash processing script; requires a Bash-compatible environment. |
| `process_bold.sh` | Bash processing script; requires a Bash-compatible environment. |
| `process_gbif.sh` | Bash processing script; requires a Bash-compatible environment. |
| `split_dist_by_species.py` | CLI options: `--dist-tsv`, `--taxonomy-tsv`, `--output-dir`, `--progress-every`, `--finalize-progress-every`, `--bucket-count`, `--workers`, `--indent` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
