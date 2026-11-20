# gene_tree 目录说明

本目录用于整理基因树分析前的 BUSCO genome 输入。脚本文件保持在当前目录原位。

## 文件分类

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `select_best_busco_genomes.py` | 数据处理脚本 | 从 genome summary 中为每个物种选择 BUSCO 表现最好的 genome。 |
| `link_selected_busco_runs.py` | 数据处理脚本 | 将选中的 BUSCO 结果链接到统一目录，供后续基因树分析使用。 |
