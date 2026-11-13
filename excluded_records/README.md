# Excluded records

This directory documents records excluded during data processing, quality control and species-centered integration in iLepBase. It provides an audit trail linking excluded records to their source identifiers and exclusion reasons.

## Available exclusion tables

| Data type | File | Excluded records | Processing context |
| --- | --- | ---: | --- |
| Transcriptome | [transcriptome_excluded_218.csv](transcriptome_excluded_218.csv) | 218 | Audit of the 20,374-row metadata table against the taxonomy mapping used to reproduce 20,156 retained records. |

Only the transcriptome exclusion table is currently included. Exclusion tables for other data types should be added as their record-level audits are completed. Their absence does not imply that no records were excluded.

## Table contents

Retain the original source fields wherever possible. Each exclusion table should identify:

- The original record identifier or accession and original taxonomic name.
- The source database, source file or publication, where available.
- The processing step and explicit reason for exclusion.
- A source-row reference or another identifier that permits tracing the record back to its input.

Field names may differ between source datasets. The transcriptome table preserves all original columns and adds `Source CSV row` and `Exclusion reason`. The source-row reference uses the original CSV row numbering, including its header.

## Transcriptome exclusion summary

| Exclusion reason | Records | Explanation |
| --- | ---: | --- |
| Missing valid Run ID | 12 | Run ID and download-link fields were recorded as `N/A`; the integrator requires a valid Run ID. These records were labelled as Bicyclus anynana. |
| Unmatched taxonomy | 206 | Original names could not be mapped to species in the taxonomy table used for the audit; see the breakdown below. |
| Total | 218 | 20,374 input records minus 218 excluded records equals 20,156 retained records. |

The 206 taxonomy-unmatched records comprise:

| Name category | Records | Explanation |
| --- | ---: | --- |
| Higher-rank names only | 119 | Identified only at genus, family or order level, without a resolved species assignment. |
| Unidentified or provisional taxa | 71 | Names containing unidentified or provisional designations that could not be linked to species in the mapping table. |
| Records labelled as Cordyceps militaris | 12 | The supplied organism name identifies a fungus, outside the Lepidoptera taxonomic scope. |
| Unresolved binomial names | 4 | Records labelled as Nymphalis c-aureum were not resolved by the mapping table used for this audit. |

These categories describe the observed metadata and mapping outcomes. An unmatched name does not establish that the name is invalid, and exclusion does not imply poor sequencing quality. A missing Run ID in the local metadata does not establish that a run is unavailable from the source database. Such records may be reconsidered after identifier recovery, taxonomic reconciliation or verification of the biological sample.

## Adding and updating exclusion tables

Use descriptive names such as `genome_excluded_records.csv`, `barcode_excluded_records.csv` or `distribution_excluded_records.csv`. Keep data types in separate tables and add each table to the inventory above. Document the input snapshot, filtering criteria and audit date when known; do not infer missing provenance.

Keep exclusion categories explicit and distinguish missing identifiers, unresolved taxonomy, duplicates and data-quality failures. Do not replace these reasons with a generic label such as "abnormal". If a later release restores an excluded record, document the resolution and the affected release rather than silently overwriting the audit trail. Counts in this directory refer to the stated processing snapshot and may differ from later database releases.
