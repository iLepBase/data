# SRA — NCBI 鳞翅目转录组数据采集与处理

从 NCBI SRA/BioSample 批量采集鳞翅目转录组元数据，并进行清洗、标准化处理。

## 目录结构

```
sra/
├── rawdata/            # 原始数据（爬虫抓取的 CSV、XML 缓存等）
├── crawler/            # 爬虫代码
│   ├── sra_transcriptome_crawler.py   # SRA 转录组元数据批量采集
│   └── process_xml.py                 # XML 解析与处理
├── data_processing/    # 数据处理代码
│   ├── normalize_sra.py               # Tissue/Sex/Stage 列标准化
│   ├── normalize_stage_column.py      # Stage 列标准化
│   ├── normalize_tissue_column.py     # Tissue 列标准化
│   └── export_species_biosamples.py   # 按物种导出 BioSample JSON
├── doc/                # 文档
│   ├── README_sra.md
│   ├── README_sra.html
│   └── NCBI 鳞翅目转录组信息爬虫逻辑.docx
├── results/            # 处理结果输出
├── pyproject.toml
├── uv.lock
└── README.md
```

## 使用方法

```bash
# 安装依赖
uv sync

# 1. 爬虫：采集 SRA 转录组元数据
uv run sra-transcriptome-crawler \
    --input rawdata/sra_ncbi_YYYYMMDD.csv \
    --output rawdata/transcriptome_data_YYYYMMDD.csv

# 2. 数据标准化
uv run python data_processing/normalize_sra.py \
    --input rawdata/transcriptome_data_YYYYMMDD.csv \
    --review-dir results/

# 3. 按物种导出 BioSample
uv run python data_processing/export_species_biosamples.py
```
