# Genome Clean

统一入口脚本是 [clean_genome.py](/data-z/zhanglab-1/iLepbase/script/genome/clean/clean_genome.py)。

它把不同来源的 genome 文件标准化到同一种输出格式：

- 输出目录默认是 `data/clean/genome/genomic`
- 输出文件名统一为 `<accession>.genomic.fna.gz`
- 日志会同时打印到终端，并写入 `data/clean/genome/genomic/clean_<source>.log`

## 支持来源

### `ncbi`

- 默认输入目录：`data/raw/genome/NCBI_unzip`
- 读取真正的 `*_genomic.fna`
- 自动排除 `*_from_genomic.fna`
- 输出名使用上层目录名，例如 `GCA_002706865.3.genomic.fna.gz`

### `ibg`

- 默认输入目录：`data/raw/genome/IBG`
- 读取 `*.genome.fa.tar.bz2`
- 从 tar.bz2 里提取 genome FASTA，再重新压成 gzip
- 输出名例如 `IBG_00065.genomic.fna.gz`

### `ngdc`

- 默认输入目录：`data/raw/genome/NGDC`
- 读取 `*.genome.fasta.gz`
- 源文件本身已经是 gzip，所以直接复制并重命名
- 输出名例如 `GWHBJUP00000000.genomic.fna.gz`

## 基本用法

运行 NCBI：

```bash
python script/genome/clean/clean_genome.py --source ncbi
```

运行 IBG：

```bash
python script/genome/clean/clean_genome.py --source ibg
```

运行 NGDC：

```bash
python script/genome/clean/clean_genome.py --source ngdc
```

## 常用参数

- `--source`：数据来源，必填，`ncbi` / `ibg` / `ngdc`
- `--source-dir`：自定义输入目录
- `--out-dir`：自定义输出目录
- `--threads`：并行线程数，默认 `8`
- `--overwrite`：覆盖已有输出
- `--limit`：只处理前 N 个输入，适合测试
- `--log-file`：自定义日志路径

示例：

```bash
python script/genome/clean/clean_genome.py --source ncbi --threads 16
python script/genome/clean/clean_genome.py --source ibg --limit 2
python script/genome/clean/clean_genome.py --source ngdc --overwrite
```

## 日志格式

日志包含这些级别：

- `INFO`：本次运行参数
- `START`：哪个文件开始处理
- `OK`：哪个文件处理成功
- `SKIP`：目标文件已存在，未覆盖
- `ERROR`：处理失败
- `SUMMARY`：总数统计

## 兼容说明

目录里原来的 [clean_ncbi.py](/data-z/zhanglab-1/iLepbase/script/genome/clean/clean_ncbi.py)、[clean_ibg.py](/data-z/zhanglab-1/iLepbase/script/genome/clean/clean_ibg.py)、[clean_ngdc.py](/data-z/zhanglab-1/iLepbase/script/genome/clean/clean_ngdc.py) 仍可继续使用。

后续建议优先使用统一入口 [clean_genome.py](/data-z/zhanglab-1/iLepbase/script/genome/clean/clean_genome.py)。
