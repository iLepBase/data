# SRA Transcriptome Crawler

批量采集 NCBI SRA 中鳞翅目（Lepidoptera）转录组信息，并整合以下三类数据：

- 本地 SRA CSV
- NCBI BioSample XML
- NCBI SRA RunInfo

最终输出一张标准化的 CSV 表。


## 使用方法

### 运行

```bash
uv run sra-transcriptome-crawler \
  --input sra_ncbi_YYYYMMDD.csv \
  --output transcriptome_data_YYYYMMDD.csv \
  --workers 6 \
  --cache-db ./ncbi_request_cache.sqlite
```

### 命令行参数

- `--input`：输入 CSV 路径，建议文件名格式为 `sra_ncbi_YYYYMMDD.csv`
- `--output`：输出 CSV 路径，建议文件名格式为 `transcriptome_data_YYYYMMDD.csv`
- `--api-key`：可选，NCBI API Key
- `--cache-db`：可选，SQLite 缓存库路径；默认在输出目录创建 `ncbi_request_cache.sqlite`
- `--workers`：可选，并发工作线程数；默认有 API Key 时为 6，无 API Key 时为 3


## I/O说明

### 相关文件

- 原始表：`/home/iLepbase/data/raw/SRA/sra_ncbi_20260323.csv`
- 选列表：`/home/iLepbase/data/raw/SRA/sra_ncbi_20260323_selected_columns.csv`
- transcriptome 筛选表：`/home/iLepbase/data/raw/SRA/sra_ncbi_20260323_transcriptome.csv`
- 最终输出：`/home/iLepbase/data/clean/SRA/transcriptome_data_20260323.csv`
- SQLite 缓存：`/home/iLepbase/data/clean/SRA/ncbi_request_cache.sqlite`
- 失败列表：`/home/iLepbase/data/clean/SRA/failed_accessions.txt`


### 输入文件

按照 `鳞翅目转录组信息爬虫逻辑.docx`下载输入文件 

`/home/iLepbase/data/raw/SRA/sra_ncbi_20260323.csv`

脚本会从输入 CSV 中读取并筛选：

- `Library_Strategy == RNA-Seq`
- `Library_Source == TRANSCRIPTOMIC`


### 输出文件

输出 CSV 包含以下 18 列：

1. `Experiment Accession`
2. `Organism Name`
3. `Instrument`
4. `Sample Accession`
5. `Total Size Mb`
6. `Total Spots`
7. `Total Bases`
8. `Library Strategy`
9. `Library Source`
10. `Library Selection`
11. `Tissue`
12. `Sex`
13. `Stage`
14. `Collection Date`
15. `Geographic location`
16. `Run ID`
17. `Published`
18. `Download Link`


### 数据统计

| 对象 | 行数/数量 | 口径 |
| --- | ---: | --- |
| `sra_ncbi_20260323.csv` | 21475 | 原始 SRA 记录 |
| `sra_ncbi_20260323_selected_columns.csv` | 21475 | 只选列，不筛行 |
| `sra_ncbi_20260323_transcriptome.csv` | 19611 | 过滤为 `RNA-SEQ` + `TRANSCRIPTOMIC` 后的 experiment 数 |
| `transcriptome_data_20260323.csv` | 20374 | 在 19611 个 experiment 基础上按 RunInfo 展开的最终输出行数 |
| `transcriptome_data_20260323.csv` 中唯一 `Experiment Accession` | 19611 | experiment 层级 |
| `transcriptome_data_20260323.csv` 中唯一 `Sample Accession` | 16667 | sample 层级 |
| `transcriptome_data_20260323.csv` 中唯一 `Run ID` | 20362 | run 层级 |
| `runinfo_cache` | 19611 | 每个 experiment 一条缓存 |
| `biosample_uid_lookup` | 16667 | 每个唯一 sample lookup accession 一条缓存 |
| `biosample_xml_cache` | 16652 | 成功抓到 XML 的唯一 sample 数 |
| `failed_accessions.txt` | 15 | 本次 BioSample 抓取失败数 |



## 抓取逻辑

### 步骤 1：筛选本地 SRA CSV

从本地 CSV 中读取记录，只保留：

- `RNA-Seq`
- `TRANSCRIPTOMIC`

### 步骤 2：获取 BioSample 属性

从 BioSample XML 中模糊匹配并提取：

- `Tissue`
- `Sex`
- `Stage`
- `Collection Date`
- `Geographic location`

缺失值统一填充为 `N/A`。

### 步骤 3：获取 Run 信息

根据 `Experiment Accession` 查询 RunInfo，提取：

- `Run ID`
- `Published`

并自动拼接下载链接：

```text
https://sra-pub-run-odp.s3.amazonaws.com/sra/{Run_ID}/{Run_ID}
```

### 断点续传

脚本每处理 100 条记录自动保存一次进度。

运行过程中会在输出目录生成：

- `transcriptome_data_YYYYMMDD.checkpoint.json`
- `transcriptome_data_YYYYMMDD.partial.csv`
- `failed_accessions.txt`
- `ncbi_request_cache.sqlite`

说明：

- 如果任务中断，再次执行相同命令会自动从断点继续
- 任务正常完成后，`.checkpoint.json` 和 `.partial.csv` 会自动删除
- `failed_accessions.txt` 会保留，方便后续重跑

### 失败重试与限速

- 每个网络请求失败后自动重试 3 次
- 每次重试间隔 5 秒
- 无 API Key：最多 3 次请求/秒
- 有 API Key：最多 10 次请求/秒

### 运行日志

脚本会在终端打印：

- 当前正在处理的 `Experiment` 和 `Sample`
- 当前进度，例如 `Progress: 120/3587`
- checkpoint 保存信息
- 请求失败和重试信息




## 数据不一致说明

### 三个层级

- `Experiment Accession`：实验层级
- `Sample Accession`：样本层级
- `Run ID`：运行层级

### 说明

原始 SRA 表和 `selected_columns.csv` 行数应该一致

#### `transcriptome.csv`

- 只保留 `Library Strategy = RNA-SEQ`
- 且 `Library Source = TRANSCRIPTOMIC`

#### `transcriptome_data_YYYYMMDD.csv`

- 先继承 `transcriptome.csv` 里的实验记录
- 再补 BioSample 字段
- 再按 RunInfo 展开
- 如果一个 `Experiment Accession` 对应多个 `Run ID`，最终输出会变成多行
- 所以最终 CSV 行数通常大于等于 `transcriptome.csv`

#### 关于 `transcriptome.csv` 与 `transcriptome_data_20260323.csv` 不一致

因为最终输出按 `Run ID` 展开。

- 本次有 `379` 个 `Experiment Accession` 对应多个 `Run ID`
- 因此在 `19611` 条 experiment 记录基础上，多展开了 `763` 行
- 最终得到 `20374` 行
- 另外有 `12` 行没有拿到 `Run ID`，保留为 `Run ID = N/A`


```text
19611 个 experiment + 763 条多 run 展开出来的额外行 = 20374 行最终输出
```

#### 关于 `biosample_xml_cache` 与`transcriptome.csv` 不一致

因为 BioSample 缓存按 `Sample Accession` 去重，不是按 `Experiment Accession`。

存在很多 experiment 共用了同一个 sample的情况
例如同一个 `Sample Accession` 可能对应几十到上百个 experiment，比如

- `SRS2147825` 对应 `128` 个不同 experiment
- `SRS2531778` 对应 `128` 个不同 experiment
- `SRS2004116` 对应 `116` 个不同 experiment


#### 关于 `biosample_uid_lookup` 与 `biosample_xml_cache` 不一致

本次有 15 个 accession 查到了 lookup 流程，但没有成功拿到 XML，所以：

- `biosample_uid_lookup = 16667`
- `biosample_xml_cache = 16652`
- 差值正好等于 `failed_accessions.txt` 里的 15 条失败记录

这 15 个失败 accession 为：

```text
biosample:DRS020532
biosample:DRS020533
biosample:DRS020534
biosample:DRS020535
biosample:DRS020536
biosample:DRS020537
biosample:DRS020538
biosample:DRS020539
biosample:DRS020540
biosample:DRS020541
biosample:DRS020542
biosample:DRS020543
biosample:DRS377880
biosample:SRS6872242
biosample:SRS6872243
```

