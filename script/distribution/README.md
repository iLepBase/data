# distribution 脚本说明

该目录下的脚本主要用于整理鳞翅目分布数据，包括：

- 从 GBIF/BOLD 原始文件中提取分布记录
- 合并 GBIF 与 BOLD 分布表
- 按 taxonomy 清单筛选 GBIF、BOLD 或合并后的 distribution 数据
- 调用上游物种清洗脚本处理分布物种名

本文档中的路径示例均以仓库根目录 `iLepbase/` 为起点书写。

## 目录脚本

### 1. `process_gbif.sh`

用途：

- 从 `data/raw/distribution/0021218-260226173443078.csv` 提取 GBIF 中的鳞翅目记录
- 保留 `id/family/genus/species/countryCode/lat/lon/source`
- 当前版本不做去重，直接输出过滤结果

默认输入输出：

- 输入：`data/raw/distribution/0021218-260226173443078.csv`
- 输出：`data/raw/distribution/distribution_gbif.csv`

运行示例：

```bash
bash script/distribution/process_gbif.sh
```

可选参数：

```bash
PROGRESS_EVERY=500000 bash script/distribution/process_gbif.sh \
  data/raw/distribution/0021218-260226173443078.csv \
  data/raw/distribution/distribution_gbif.csv
```

说明：

- 若系统安装了 `pv`，脚本会显示字节级读取进度
- 输出文件没有表头

### 2. `process_bold.sh`

用途：

- 从 BOLD 原始数据中提取鳞翅目分布记录
- 解析 `coord` 字段，拆分为 `lat/lon`
- 输出去重后的 BOLD 分布表

默认输入输出：

- 输入：`data/raw/distribution/BOLD_Public_27-FEB-2026.tsv`
- 输出：`data/raw/distribution/distribution_bold.csv`

运行示例：

```bash
bash script/distribution/process_bold.sh
```

说明：

- 若输入是 tar 归档，脚本会自动在归档内查找 `.tsv` 文件
- 输出文件没有表头
- 当前脚本内部仍使用 `sort -u -S 8G` 去重

### 3. `merged_file.sh`

用途：

- 合并 GBIF 与 BOLD 提取后的分布文件
- 在结果文件首行写入统一表头

默认输入输出：

- 输入1：`data/raw/distribution/distribution_gbif.csv`
- 输入2：`data/raw/distribution/distribution_bold.csv`
- 输出：`data/raw/distribution/lepidoptera_distribution.tsv`

运行示例：

```bash
bash script/distribution/merged_file.sh
```

输出表头：

```tsv
id	family	genus	species	countryCode	lat	lon	source
```

### 4. `filter_gbif_by_taxo.py`

用途：

- 使用 taxonomy 表中的 `origin Species` 匹配 GBIF 物种名
- 支持直接读取 GBIF 原始导出，或读取已经提取好的 `distribution_gbif.csv`
- 输出 taxonomy 字段和 GBIF 字段的组合表

默认输入输出：

- taxonomy：`data/clean/taxo/lep_taxo_20260414.tsv`
- GBIF：`data/raw/distribution/distribution_gbif.csv`
- 输出：`data/clean/distribution/dist_gbif_clean.tsv`

运行示例：

```bash
python script/distribution/filter_gbif_by_taxo.py
```

输出字段：

```tsv
taxo_family	taxo_subfamily	taxo_genus	taxo_species	gbifID	gbif_family	gbif_genus	gbif_species	countryCode	latitude	longitude	source
```

### 5. `filter_bold_by_taxo.py`

用途：

- 使用 taxonomy 表中的 `origin Species` 匹配 BOLD 物种名
- 从 BOLD 原始表中提取 taxonomy 信息和部分 BOLD 元数据

默认输入输出：

- BOLD：`data/raw/distribution/BOLD_Public_27-FEB-2026.tsv`
- taxonomy：`data/clean/taxo/lep_taxo_20260414.tsv`
- 输出：`data/clean/distribution/bold_info.tsv`

运行示例：

```bash
python script/distribution/filter_bold_by_taxo.py
```

输出字段：

```tsv
taxo_species	taxo_family	taxo_subfamily	taxo_genus	bold_processid	bold_family	bold_subfamily	bold_genus	bold_species	latitude	longitude	bold_country_ocean	bold_country_iso	bold_nuc_basecount
```

### 6. `filter_dist_by_taxo.py`

用途：

- 使用 taxonomy 表中的 `origin Species` 匹配合并分布表中的 `species`
- 保留 taxonomy 中的 `family/subfamily/genus/species`
- 保留 distribution 中的全部列
- 为避免列名冲突，将 distribution 自带的 `family/genus/species` 重命名为 `origin_family/origin_genus/origin_species`

默认输入输出：

- taxonomy：`data/clean/taxo/lep_taxo_20260414.tsv`
- distribution：`data/raw/distribution/lepidoptera_distribution.tsv`
- 输出：`data/clean/distribution/lep_dist_clean_YYYYMMDD.tsv`

说明：

- `YYYYMMDD` 为脚本运行当天日期
- 若显式传入 `--output`，则使用传入的路径

运行示例：

```bash
python script/distribution/filter_dist_by_taxo.py
```

指定输出示例：

```bash
python script/distribution/filter_dist_by_taxo.py \
  --output data/clean/distribution/lep_dist_clean_20260415.tsv
```

输出字段结构：

```tsv
family	subfamily	genus	species	id	origin_family	origin_genus	origin_species	countryCode	lat	lon	source
```

### 7. `dist_clean.sh`

用途：

- 调用 `script/clean_species/clean_species.py`
- 对 `data/raw/distribution/lep_species_unique.txt` 做物种名清洗

运行示例：

```bash
bash script/distribution/dist_clean.sh
```

说明：

- 依赖 `uv`
- 使用的 taxonomy 缓存库为 `data/raw/hostplant/taxonomy_cache.sqlite`

## 依赖与注意事项

基础依赖：

- `bash`
- `python3`
- `uv`，用于 `dist_clean.sh`
- `tar`、`gzip`

可选依赖：

- `pv`，用于 `process_gbif.sh` 的读取进度显示

数据注意事项：

- `data/clean/taxo/lep_taxo_20260414.tsv` 的真实列名包含拼写：`subfamliy`、`genuis`
- taxonomy 匹配键使用 `origin Species`
- 大文件处理会占用较长时间和大量磁盘空间，特别是：
  - `process_gbif.sh`
  - `merged_file.sh`
  - `filter_dist_by_taxo.py`

## 推荐处理顺序

如果要从原始数据重新生成整套 distribution，建议顺序如下：

```bash
bash script/distribution/process_gbif.sh
bash script/distribution/process_bold.sh
bash script/distribution/merged_file.sh
python script/distribution/filter_dist_by_taxo.py
```
