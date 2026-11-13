# Genome acquisition and cleaning

Download NCBI genome assemblies, extract archives and normalize genome metadata from NCBI, NGDC and InsectBase.

## Inputs and outputs

Input: assembly accession lists and source metadata. Output: downloaded assemblies and normalized assembly tables.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. See configuration below.

## Configuration

`down_ncbi_genome.py` is configured through environment variables, including `NCBI_ACC_FILE`, `NCBI_OUTDIR` and `NCBI_THREADS`. Set them before running the downloader. Install the external NCBI Datasets CLI separately; its binary is not distributed here. The scripts in `clean/` share `clean_genome.py`; keep these files together.

## Script inventory

| Script | Interface / role |
| --- | --- |
| `clean/clean_genome.py` | CLI options: `--source`, `--source-dir`, `--out-dir`, `--overwrite`, `--limit`, `--log-file`, `--threads` |
| `clean/clean_ibg.py` | Helper or environment-configured script; inspect configuration before execution. |
| `clean/clean_ncbi.py` | Helper or environment-configured script; inspect configuration before execution. |
| `clean/clean_ngdc.py` | Helper or environment-configured script; inspect configuration before execution. |
| `down_ncbi_genome.py` | Helper or environment-configured script; inspect configuration before execution. |
| `unzip_ncbi_archives.sh` | Bash processing script; requires a Bash-compatible environment. |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
