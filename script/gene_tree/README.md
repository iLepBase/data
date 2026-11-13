# Comparative-genomics preparation

Select representative genomes using assembly and BUSCO information and link selected BUSCO runs. This module prepares inputs; it does not by itself infer a phylogeny.

## Inputs and outputs

Input: genome/BUSCO summaries and BUSCO run directories. Output: selected-genome tables and links to BUSCO results.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/gene_tree/select_best_busco_genomes.py --help
```


## Script inventory

| Script | Interface / role |
| --- | --- |
| `link_selected_busco_runs.py` | CLI options: `--selected-tsv`, `--busco-root`, `--output-dir`, `--manifest` |
| `select_best_busco_genomes.py` | CLI options: `--input-tsv`, `--output-tsv` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
