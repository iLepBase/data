# `script/json`

这个目录用于构建和更新 `data/clean/json/*.json` 物种主 JSON。

## 目录说明

- `run_json_pipeline.py`
  主入口。按固定顺序执行各个 JSON 构建/补充脚本。
- `rebuild_log_json.py`
  从当前主 JSON 重新统计并刷新 `script/json/stat.json`。
- `log_utils.py`
  `stat.json` 的统计逻辑。
- `logs/`
  pipeline 运行日志目录。
- `stat.json`
  当前主 JSON 的汇总统计结果。

## 当前 pipeline 顺序

`run_json_pipeline.py` 当前按以下顺序执行：

1. `lep_taxo_to_species_json.py`
2. `genomes_to_species_json.py`
3. `busco_to_species_json.py`
4. `chromosome_to_species_json.py`
5. `COI_to_species_json.py`
6. `dist_to_species_json.py`
7. `hostplant_to_species_json.py`
8. `transcriptome_to_species_json.py`
9. `rebuild_log_json.py`

说明：

- 各步骤之间是串行执行的。
- 单个步骤内部是否并行，取决于该脚本自己的实现。
- `rebuild_log_json.py` 放在最后，用于统一刷新 `stat.json`。

## 各脚本职责

### `lep_taxo_to_species_json.py`

- 输入：`data/clean/taxo/lep_taxo_*.tsv`
- 输出：`data/clean/json/*.json`
- 作用：根据 taxonomy TSV 初始化每个物种的主 JSON。

### `genomes_to_species_json.py`

- 作用：写入 genome、annotation、publication 等基因组相关字段。

### `busco_to_species_json.py`

- 输入：`data/raw/genome/busco`
- 作用：写入 genome 记录下的 `busco`，并同步 `genome_summary_db.tsv` 中的 BUSCO 列。

### `chromosome_to_species_json.py`

- 作用：写入 genome 记录下的 `chromosomes`。

### `COI_to_species_json.py`

- 输入：
  - `data/raw/COI/bold_info.tsv`
  - `data/clean/COI/species/*.fasta`
- 作用：写入主 JSON 的 `COI` 字段。
- 当前默认 COI FASTA 目录：`data/clean/COI/species`

### `dist_to_species_json.py`

- 输入：`data/clean/distribution/species/*.json`
- 作用：把分布明细文件摘要写入主 JSON 的 `distribution` 字段。

### `hostplant_to_species_json.py`

- 输入：
  - `data/raw/hostplant/寄主植物_reviewed_20260419.xlsx`
  - `data/clean/taxo/lep_taxo_20260415.tsv`
- 输出：
  - `data/clean/hostplant/species/*.json`
  - 主 JSON 的 `Hostplant` 摘要字段
- 作用：写入寄主植物明细和主 JSON 摘要。

### `transcriptome_to_species_json.py`

- 作用：写入主 JSON 的 `Transcriptome` 字段。

### `pdb_to_species_json.py`

- 作用：写入主 JSON 的 `PDB` 字段。
- 说明：当前不在 `run_json_pipeline.py` 默认顺序里，需要单独执行。

### `genome_json_to_tsv.py`

- 作用：把主 JSON 中的 genome 信息导出为 TSV。
- 说明：这是导出脚本，不属于主 JSON 构建 pipeline。

## 常用命令

### 跑完整 pipeline

```bash
python /data-z/zhanglab-1/iLepbase/script/json/run_json_pipeline.py
```

### 只重建统计

```bash
python /data-z/zhanglab-1/iLepbase/script/json/rebuild_log_json.py
```

### 单独刷新 COI

```bash
python /data-z/zhanglab-1/iLepbase/script/json/COI_to_species_json.py
```

### 单独刷新 Hostplant

```bash
python /data-z/zhanglab-1/iLepbase/script/json/hostplant_to_species_json.py
```

## 主要输出位置

- 主 JSON：`data/clean/json/*.json`
- distribution 明细：`data/clean/distribution/species/*.json`
- hostplant 明细：`data/clean/hostplant/species/*.json`
- 统计文件：`script/json/stat.json`
- pipeline 日志：`script/json/logs/*.log`

## 维护约定

- 主 JSON 顶层统计以 `rebuild_log_json.py` / `log_utils.py` 为准。
- 新增数据模块时，通常需要同时修改：
  - 对应写入脚本
  - `run_json_pipeline.py`
  - `log_utils.py`
  - 必要时更新本 README
