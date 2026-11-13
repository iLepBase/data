# Directory migration

The active module directories remain two levels below the repository root, preserving root-relative path resolution. Existing script basenames and data-directory names are retained. Update external commands and scheduled jobs using the mapping below.

| Previous path | Current path |
| --- | --- |
| `script/clean_species/` | `script/taxonomy/` |
| `script/COI/` | `script/barcode/` |
| `script/SRA/` | `script/transcriptome/` |
| `script/genome_anno/` | `script/annotation/` |
| `script/json/` | `script/integration/` |
| `script/data_normalization/` | `script/archive/data_normalization/` |

Historical workflows were moved as a unit, including their intermediate tables. They are retained for provenance rather than offered as additional active entry points. Restore the original layout from commit `c489990` when reproducing an old invocation. No biological filtering thresholds were changed in this reorganization.
