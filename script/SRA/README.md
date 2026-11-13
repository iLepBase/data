# SRA 脚本目录说明

本目录用于整理 iLepBase 的 SRA / transcriptome 数据。为避免影响原有运行方式，脚本文件均保留在 `SRA/` 根目录。

## 文件分类

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `sra_transcriptome_crawler.py` | 爬虫 | 批量查询 NCBI SRA、BioSample 和 RunInfo，生成 transcriptome 元数据表。 |
| `normalize_sra.py` | 数据处理 | 规范化 tissue、sex、stage、collection date、geographic location 等字段。 |
| `reextract_biosample_fields.py` | 数据处理 | 从 BioSample XML 缓存中重新提取字段并回填已有 CSV。 |
| `process_xml.py` | 数据处理 | 解析或检查 BioSample XML，用于字段抽取和调试。 |
| `backfill_geographic_coordinates.R` | 数据处理 | 为 transcriptome 记录补全地理坐标。 |
| `pyproject.toml`、`uv.lock` | 环境配置 | `uv` 项目配置和依赖锁定文件。 |
| `docs/` | 文档 | 历史 README 和爬虫逻辑文档。 |

## 常用命令

```bash
cd E:/myh/07iLepbase/script/SRA
uv run sra-transcriptome-crawler --help
python normalize_sra.py --help
python reextract_biosample_fields.py --help
python process_xml.py --help
```

## 建议流程

1. 运行 `sra_transcriptome_crawler.py` 获取或更新 SRA / BioSample / RunInfo 元数据。
2. 运行 `normalize_sra.py` 统一字段格式。
3. 如需从缓存重新补字段，运行 `reextract_biosample_fields.py`。
4. 如需地理坐标，运行 `backfill_geographic_coordinates.R`。
5. 最终结果进入 `script/json/transcriptome_to_species_json.py` 写入主 JSON。

旧版详细说明保存在 `docs/README_sra.md`。
