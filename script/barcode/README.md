# DNA barcode processing

Split BOLD FASTA records by species for downstream integration.

## Inputs and outputs

Input: BOLD sequence and metadata exports. Output: species-specific FASTA files.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/barcode/split_bold_fasta_by_species.py --help
```


## Script inventory

| Script | Interface / role |
| --- | --- |
| `split_bold_fasta_by_species.py` | CLI options: `--info-tsv`, `--input-fasta`, `--output-dir`, `--filtered-info-tsv`, `--max-open-files`, `--progress-every` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
