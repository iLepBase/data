# InsectBase Genome Downloader

数据库网站：`https://www.insect-genome.com`

功能：批量下载指定 IBG ID 或 URL 的基因组

脚本： `down_IBG_genome_anno.py`。


## 脚本说明

适用场景：

- 已知 `IBG_00016` 这类组装 ID
- 或已有详情页 URL，例如 `https://www.insect-genome.com/genome/IBG_00016`
- 希望下载对应物种的 whole genome 压缩包：
  - `https://www.insect-genome.com/data/genome/{species_filename}/{species_filename}.genome.fa.tar.bz2`


示例：

支持使用IBG ID下载：

```bash
.venv/bin/python download_whole_genome.py \
  IBG_00016 \
  --output-dir data/genome_whole
```

支持传输 URL下载：

```bash
.venv/bin/python download_whole_genome.py \
  https://www.insect-genome.com/genome/IBG_00016 \
  --output-dir data/genome_whole
```

支持从文本文件批量读取，每行一个 `IBG ID` 或 URL：

```bash
.venv/bin/python download_whole_genome.py \
  --input-file data/genome_ids.txt \
  --output-dir data/genome_whole
```