# NCBI Genome download

根据 accession 列表批量下载 NCBI genome 数据，整理输出文件，并在下载完成后生成日志与统计。

当前版本重点增强了这几类问题：

- `datasets` 下载失败时按错误类型处理，不再做无间隔硬重试
- 支持 `proxy / direct / auto` 三种网络模式，默认强制走 `proxy`
- 下载命令增加超时与退避
- 使用 Python 标准库解压，去掉对系统 `unzip` 的依赖
- sequence report 保留真实扩展名，避免把 `jsonl` 误写成 `tsv`

## 前置条件

- Python 3.10+
- `uv`
- NCBI `datasets` CLI 二进制

脚本本身没有额外 Python 依赖，但仍建议通过 `uv` 管理本目录的环境和执行方式。

脚本会按下面顺序寻找 `datasets`：

1. `NCBI_DATASETS_BIN`
2. 项目内置路径 `tools/datasets`
3. 系统 `PATH` 里的 `datasets`

## 使用 uv

初始化环境：

```bash
cd /dataz/zhanglab-1/iLepbase/script/genome
uv sync
```

运行脚本：

```bash
uv run python down_ncbi_genome.py
```

如果你想走项目内置二进制，把官方 `datasets` 可执行文件放到 [tools/README.md](/dataz/zhanglab-1/iLepbase/script/genome/tools/README.md) 说明的位置：

```bash
chmod +x /dataz/zhanglab-1/iLepbase/script/genome/tools/datasets
uv run python down_ncbi_genome.py
```

项目已经在 [pyproject.toml](/dataz/zhanglab-1/iLepbase/script/genome/pyproject.toml) 配置了清华 PyPI 镜像：

```toml
[[tool.uv.index]]
name = "tsinghua"
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true
```

## 默认输入输出

- accession 文件：[accNum_20260311.txt](/dataz/zhanglab-1/iLepbase/script/genome/accNum_20260311.txt)
- API key 文件：[ncbi_api_keys.txt](/dataz/zhanglab-1/iLepbase/script/genome/ncbi_api_keys.txt)
- 输出目录：`/dataz/zhanglab-1/iLepbase/data/clean/genome`

## 常用环境变量

可以在运行前覆盖默认配置：

```bash
NCBI_ACC_FILE=/dataz/zhanglab-1/iLepbase/script/genome/accNum_test.txt \
NCBI_THREADS=4 \
NCBI_RETRY=4 \
NCBI_PROXY_MODE=proxy \
uv run python down_ncbi_genome.py
```

主要环境变量：

- `NCBI_ACC_FILE`：accession 列表文件
- `NCBI_OUTDIR`：输出目录
- `NCBI_THREADS`：并发线程数
- `NCBI_RETRY`：重试次数
- `NCBI_RETRY_BACKOFF_SECONDS`：初始退避秒数
- `NCBI_RETRY_BACKOFF_FACTOR`：退避倍率
- `NCBI_DATASETS_TIMEOUT_SECONDS`：单次 `datasets` 超时
- `NCBI_PROXY_MODE`：`proxy` / `auto` / `direct`，默认 `proxy`
- `NCBI_PROXY_URL`：显式代理地址，优先级高于 `http_proxy` / `https_proxy`
- `NCBI_PROXY_HOST`、`NCBI_PROXY_PORT`、`NCBI_PROXY_SCHEME`：未显式设置代理时的默认代理参数
- `NCBI_DATASETS_BIN`：`datasets` 命令路径或别名
- `NCBI_API_KEYS_FILE`：API key 文件

## 输出说明

脚本调用的 NCBI `datasets` 参数为：

```text
--include genome,gff3,rna,cds,protein,seq-report
```

输出文件：

| 文件名 | 类型 | 说明 |
| --- | --- | --- |
| `download.log` | 日志 | 记录启动信息、重试、回退、成功、失败、跳过，以及 `datasets` 的结构化错误。 |
| `genome_metadata.json` | JSON | accession 级别的明细文件，包含 `status`、输出文件信息和 `md5_check` 结果。 |
| `genome_stats.json` | JSON | 全局汇总统计文件，包含状态计数、md5 汇总、文件覆盖率和总输出大小。 |

accession 子目录文件：

| 文件名模式 | 类型 | 说明 |
| --- | --- | --- |
| `<acc>.fna` | genome FASTA | 基因组序列文件，是判断该 accession 是否已下载的主要标志文件。 |
| `<acc>.gff` | GFF3 | 基因组注释文件。 |
| `<acc>.faa` | protein FASTA | 蛋白序列文件。 |
| `<acc>.cds.fna` | CDS FASTA | CDS 核酸序列文件。 |
| `<acc>.rna.fna` | RNA FASTA | RNA 序列文件。 |
| `<acc>.seq_report.tsv` / `<acc>.seq_report.jsonl` | sequence report | 按 NCBI 数据包中的真实格式保留。 |
| `<acc>.md5sum.txt` | MD5 | NCBI 数据包自带的 md5 列表，脚本会据此校验本地输出文件。 |
