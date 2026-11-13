# Genome annotation acquisition

Download InsectBase annotation resources and copy NCBI annotation files into the data layout.

## Inputs and outputs

Input: source annotation metadata and downloaded annotation files. Output: local annotation resources.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/annotation/down_IBG_genome_anno.py --help
```


## Script inventory

| Script | Interface / role |
| --- | --- |
| `clean/copy_ncbi_annotation_files.py` | CLI options: `--source-dir`, `--out-base`, `--overwrite` |
| `down_IBG_genome_anno.py` | CLI options: `--order`, `--output-dir`, `--no-gff`, `--no-protein`, `--no-genome`, `--no-cds`, `--proxy`, `--csv`, `--min-gff-bytes`, `--min-gff-lines`, `--min-sequence-bytes` |
| `download_whole_genome.py` | CLI options: `--input-file`, `--output-dir`, `--proxy`, `--overwrite`, `--dry-run`, `--timeout`, `--chunk-size`, `--min-bytes` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
