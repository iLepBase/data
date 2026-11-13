# iLepBase data processing

Data processing and quality control scripts, filtered and cleaned data tables for iLepBase.

## Repository organization

| Directory | Purpose |
| --- | --- |
| [`script/taxonomy/`](script/taxonomy/README.md) | Reconcile source names with NCBI and GBIF taxonomy and produce candidate matches and manual-review tables. |
| [`script/barcode/`](script/barcode/README.md) | Split BOLD FASTA records by species for downstream integration. |
| [`script/transcriptome/`](script/transcriptome/README.md) | Retrieve SRA and BioSample metadata, normalize sample attributes, re-extract cached fields and backfill geographic coordinates. |
| [`script/genome/`](script/genome/README.md) | Download NCBI genome assemblies, extract archives and normalize genome metadata from NCBI, NGDC and InsectBase. |
| [`script/annotation/`](script/annotation/README.md) | Download InsectBase annotation resources and copy NCBI annotation files into the data layout. |
| [`script/distribution/`](script/distribution/README.md) | Extract GBIF/BOLD occurrence records, reconcile names with the taxonomy table and split results by species. |
| [`script/hostplant/`](script/hostplant/README.md) | Reconcile taxonomic names, prepare manual-review tasks and finalize curated host-association tables. |
| [`script/pdb/`](script/pdb/README.md) | Enrich PDB metadata with taxonomic information and filter against the supported taxonomy. |
| [`script/integration/`](script/integration/README.md) | Create and update species JSON documents, attach molecular and ecological data, and rebuild summary statistics. |
| [`script/gene_tree/`](script/gene_tree/README.md) | Select representative genomes using assembly and BUSCO information and link selected BUSCO runs. This module prepares inputs; it does not by itself infer a phylogeny. |
| [`script/archive/`](script/archive/README.md) | Earlier workflows retained for provenance. |
| [`excluded_records/`](excluded_records/README.md) | Record-level exclusion tables, filtering reasons and audit notes. |

## Processing sequence

1. Reconcile taxonomic names and review unresolved matches.
2. Acquire and normalize each data type using its module guide.
3. Inspect quality-control and manual-review outputs before integration.
4. Build species-centered JSON records with `script/integration/run_json_pipeline.py`; run PDB integration separately if required.
5. Rebuild summary statistics and generate figures from the final retained records, rather than intermediate metadata exports.

These modules are independent utilities with different input requirements, not a single fully automated end-to-end pipeline. The repository does not include all external sequence files, reference resources or API caches needed to recreate the complete database.

## Software and configuration

Python requirements vary by module. Third-party imports include `pandas`, `requests`, `httpx`, `tqdm`, `openpyxl`, `beautifulsoup4` and `pycountry`; consult each script and the module-specific project files. R utilities require the packages imported by their source files. Shell utilities require Bash and standard command-line tools. Install the NCBI Datasets CLI separately for genome downloads.

Use `--help` for argparse-based scripts. Some older utilities use environment variables or settings embedded in their source instead. Original input defaults may contain absolute server paths: supply your own paths before execution. Existing data paths such as `data/clean/SRA/` and `data/clean/COI/` are retained; script directory renaming does not rename those datasets.

Set API credentials locally, for example through `NCBI_API_KEY`. Never commit API keys, request caches, virtual environments or downloaded tool binaries. Data source access and reuse remain subject to the respective providers' terms. A software/data license has not yet been assigned in this repository.

## Version history

Commit `c489990` preserves the original published directory snapshot, with credentials and runtime artifacts excluded. The reorganization changes script locations and documentation while retaining processing logic. See [directory migration](script/MIGRATION.md) for old-to-new paths. Archived files are historical inputs, intermediate results and alternative scripts; their counts are not authoritative release statistics.
