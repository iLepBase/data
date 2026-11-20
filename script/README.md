# script 目录说明

本目录保存 iLepBase 数据库构建过程中使用的脚本。

## 顶层目录

| 目录 | 主要用途 | 当前整理 |
| --- | --- | --- |
| `clean_species/` | 物种名清洗和分类信息补全 | 脚本保留在根目录，旧说明放入 `docs/` |
| `COI/` | BOLD COI 序列拆分和整理 | 脚本保留在根目录 |
| `distribution/` | GBIF / BOLD 分布数据抽取、合并和过滤 | 脚本保留在根目录 |
| `gene_tree/` | BUSCO genome 筛选和基因树输入整理 | 脚本保留在根目录 |
| `genome/` | NCBI genome 下载、解压和清洗 | 脚本保留在根目录；工具在 `tools/`；清洗日志在 `clean/logs/` |
| `genome_anno/` | IBG / NCBI genome annotation 下载和整理 | 脚本保留在根目录；说明文档在 `docs/` |
| `hostplant/` | 寄主植物数据清洗和审核任务生成 | 脚本保留在根目录 |
| `json/` | 将清洗结果写入 species JSON | 脚本保留在根目录；运行日志在 `logs/` |
| `pdb/` | PDB 相关数据清洗 | 清洗脚本在 `clean/` |
| `SRA/` | SRA / transcriptome 爬虫、字段补全和坐标回填 | 脚本保留在根目录；历史说明在 `docs/` |

## 整理原则

- `.py`、`.R`、`.sh` 脚本文件保持原位置，不再移动。
- README、旧说明文档、运行日志、外部工具可以单独放入 `docs/`、`logs/`、`tools/` 等目录。
- `__pycache__/`、`.venv/`、`.git/` 属于运行环境或缓存，不作为正式脚本内容。
- 每个顶层文件夹都保留或补充 README，用中文说明该目录中的文件作用。

## 推荐运行顺序

1. `clean_species/`：统一物种名和 taxonomy。
2. `genome/`、`genome_anno/`、`COI/`、`distribution/`、`hostplant/`、`SRA/`：分别整理各数据类型。
3. `json/run_json_pipeline.py`：把各类清洗结果汇总到 species JSON。
4. `json/rebuild_log_json.py`：刷新统计信息。
