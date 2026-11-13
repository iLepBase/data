# Species-centered data integration

Create and update species JSON documents, attach molecular and ecological data, and rebuild summary statistics.

## Inputs and outputs

Input: curated taxonomy and module-specific data. Output: species JSON files, exported tables and summary statistics.

## Usage

Run from the repository root. Review file paths and required dependencies before executing a data-processing job. Inspect the command-line interface first:

```bash
python script/integration/run_json_pipeline.py --help
```

## Execution order

The pipeline executes taxonomy initialization, genome integration, BUSCO integration, chromosome integration, barcode integration, distribution integration, host-plant integration, transcriptome integration, and statistics rebuilding, in that order. PDB integration is available separately in `pdb_to_species_json.py` and is not invoked by the current pipeline.

```bash
python script/integration/run_json_pipeline.py --help
python script/integration/transcriptome_to_species_json.py --help
python script/integration/pdb_to_species_json.py --help
```

The transcriptome integrator excludes records without valid Run IDs, unmatched taxonomic names and names without a corresponding species JSON file. Repeated Run IDs within a species are collapsed if records agree; conflicting records raise an error. Inspect the reported counters after each run. `stat.json` is a historical snapshot and must be regenerated for a new data release.

## Script inventory

| Script | Interface / role |
| --- | --- |
| `busco_to_species_json.py` | CLI options: `--busco-dir`, `--json-dir`, `--genome-summary-tsv`, `--indent` |
| `chromosome_to_species_json.py` | CLI options: `--ncbi-unzip-dir`, `--json-dir`, `--indent` |
| `COI_to_species_json.py` | CLI options: `--info-tsv`, `--coi-dir`, `--json-dir`, `--log-output`, `--indent`, `--progress-every` |
| `dist_to_species_json.py` | CLI options: `--species-dir`, `--json-dir`, `--indent`, `--workers`, `--task-count` |
| `genome_json_to_tsv.py` | CLI options: `--json-dir`, `--output`, `--species-tsv` |
| `genomes_to_species_json.py` | CLI options: `--unmatched-output`, `--indent` |
| `hostplant_to_species_json.py` | CLI options: `--hostplant-xlsx`, `--taxonomy-tsv`, `--species-dir`, `--json-dir`, `--log-output`, `--indent` |
| `lep_taxo_to_species_json.py` | CLI options: `--input`, `--output`, `--indent`, `--keep-orphans` |
| `log_utils.py` | Helper or environment-configured script; inspect configuration before execution. |
| `pdb_to_species_json.py` | CLI options: `--pdb-tsv`, `--pdb-dir`, `--json-dir`, `--stat-output`, `--indent`, `--progress-every` |
| `rebuild_log_json.py` | CLI options: `--json-dir`, `--stat-output`, `--log-output`, `--indent` |
| `run_json_pipeline.py` | CLI options: `--taxonomy-tsv`, `--json-dir`, `--log-dir` |
| `transcriptome_to_species_json.py` | CLI options: `--transcriptome-csv`, `--taxo-tsv`, `--json-dir`, `--indent`, `--progress-every` |

## Reproducibility

Original defaults may refer to project-specific files or absolute server paths. Supply explicit input/output paths where supported. These are processing utilities, not a bundled copy of every upstream dataset. Live API results can differ between retrieval dates. Retain source accessions and manual-review decisions for each release.
