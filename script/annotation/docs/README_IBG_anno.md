# InsectBase Genome Annotation Downloader

数据库网站：`https://www.insect-genome.com`

功能：批量抓取指定昆虫目（Order）的物种信息，以及对应的 `GFF3 / Protein / CDS` 三类注释文件。

脚本： `down_IBG_genome_anno.py`。


## 目录结构

运行后输出文件结构如下：

```text
data/
├── cds/
├── gff3/
├── protein/
├── info/
├── download_log.jsonl
├── download_report.csv
├── download_species_comparison.json
├── species_info.csv
└── species_summary.json
```

说明：

- `gff3/`：GFF3 文件目录
- `protein/`：Protein 文件目录
- `cds/`：CDS 文件目录
- `download_report.csv`：本次运行的总下载结果表
- `download_log.jsonl`：逐次尝试的详细日志
- `download_species_comparison.json`：物种清单 / 报告 / 实际文件 三方一致性核对
- `species_info.csv`：物种基础信息表
- `species_summary.json`：API 原始提炼后的汇总信息


Note：可以使用 `--csv` 传入自定义物种列表

## 环境依赖与安装说明

### 环境依赖

- Python `>=3.12`
- 依赖：
  - `httpx[socks]`
  - `tqdm`

### 安装：

使用 uv 配置环境

```bash
uv sync
```

使用帮助：

```bash
.venv/bin/python down_IBG_genome_anno.py --help
```

## 脚本运行

### 命令行参数

```text
--order ORDER
--output-dir OUTPUT_DIR
--no-gff
--no-protein
--no-cds
--proxy PROXY
--csv CSV
--min-gff-bytes MIN_GFF_BYTES
--min-gff-lines MIN_GFF_LINES
--min-sequence-bytes MIN_SEQUENCE_BYTES
```

说明：

- `--order`：要抓取的昆虫目，默认 `Lepidoptera`
- `--output-dir`：输出目录，默认 `data`
- `--no-gff`：不下载 GFF3
- `--no-protein`：不下载 Protein
- `--no-cds`：不下载 CDS
- `--proxy`：HTTP/SOCKS 代理
- `--csv`：从本地 CSV 读取物种清单，而不是调用站点 API 抓全量
- `--min-gff-bytes`：GFF3 最小文件大小阈值，低于阈值视为无效
- `--min-gff-lines`：GFF3 最小行数阈值
- `--min-sequence-bytes`：Protein/CDS 最小文件大小阈值


### 示例

下载一个目下的全部物种，抓取 GFF3 / Protein / CDS

```bash
.venv/bin/python down_IBG_genome_anno.py \
  --order Lepidoptera \
  --output-dir data
```

可以使用代理

```bash
.venv/bin/python down_IBG_genome_anno.py \
  --order Lepidoptera \
  --proxy http://127.0.0.1:7890
```

也支持 SOCKS5：

```bash
.venv/bin/python down_IBG_genome_anno.py \
  --order Lepidoptera \
  --proxy socks5://127.0.0.1:1080
```

可以使用 `--no-cds`, `--no-gff`, `--no-protein` 三个参数自定义下载内容


支持自定义 CSV 下载指定物种

```bash
.venv/bin/python down_IBG_genome_anno.py \
  --csv data/species.csv \
  --output-dir data_subset
```

输入 CSV 需要两列：

```csv
ibg_id,species
IBG_00001,Abrostola tripartita
IBG_00002,Bombyx mori
```

约束：

- 必须包含 `ibg_id` 和 `species` 表头
- `species` 不能为空
- `ibg_id` 可为空，但建议保留，便于输出文件命名和后续核对


