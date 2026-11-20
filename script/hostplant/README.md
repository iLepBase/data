# Hostplant Taxonomy Cleaner

用于清洗鳞翅目物种名并补全分类信息，流程为：

1. 先查 NCBI（物种级）
2. NCBI 未命中再查 GBIF
3. 仍未命中则按 genus 回查 NCBI（只补上级）
4. 歧义结果不自动拍板，写入 `review.csv` 人工复核

当前脚本文件：`hostplant_taxonomy.py`

## 1. 运行环境

- 使用 `uv` 运行（脚本头部已声明依赖，会自动安装）
- Python 由 `uv` 自动管理
- 建议配置 NCBI API Key（可选，但更快）

```bash
export NCBI_API_KEY='your_ncbi_api_key'
```

不配置也能运行，只是请求速率更低。

## 2. 输入数据

默认输入文件：

`/home/iLepbase/data/raw/hostplant/鳞翅目.xlsx`

默认工作表：`Sheet1`

脚本按以下列结构读写（保留原始拼写）：

- `origin Species`
- `subfamliy`
- `sfid`
- `genuis`
- `gid`
- `species`
- `sid`
- `source (ncbi gbif)`
- `source link`
- `note`

## 3. 快速开始

### 3.1 小样本试跑（推荐）

```bash
uv run /home/iLepbase/script/hostplant/hostplant_taxonomy.py \
  --input-xlsx "/home/iLepbase/data/raw/hostplant/鳞翅目.xlsx" \
  --output-csv "/home/iLepbase/data/raw/hostplant/鳞翅目.cleaned.sample.csv" \
  --review-csv "/home/iLepbase/data/raw/hostplant/鳞翅目.review.sample.csv" \
  --cache-db "/home/iLepbase/data/raw/hostplant/taxonomy_cache.sqlite" \
  --limit 200 \
  --skip-filled
```

### 3.2 全量运行

```bash
uv run /home/iLepbase/script/hostplant/hostplant_taxonomy.py \
  --input-xlsx "/home/iLepbase/data/raw/hostplant/鳞翅目.xlsx" \
  --output-csv "/home/iLepbase/data/raw/hostplant/鳞翅目.cleaned.csv" \
  --review-csv "/home/iLepbase/data/raw/hostplant/鳞翅目.review.csv" \
  --cache-db "/home/iLepbase/data/raw/hostplant/taxonomy_cache.sqlite" \
  --skip-filled
```

## 4. 输出文件

### 4.1 主结果 `cleaned.csv`

在原列结构基础上回填分类结果：

- `source (ncbi gbif)`：`ncbi` 或 `gbif`
- `source link`：对应来源链接（必要时包含多链接）
- `note`：补充说明（如 `genus_fallback`、`review_needed`）

### 4.2 复核文件 `review.csv`

歧义项统一输出，字段包括：

- `origin Species`
- `stage`（歧义发生阶段）
- `reason`（原因）
- `query`
- `candidates_json`（候选原始数据）
- `recommended_candidate`（脚本给出的建议项）
- `source_links`
- `created_at`

## 5. 常用参数

- `--input-xlsx`：输入 xlsx 路径
- `--sheet-name`：工作表名，默认 `Sheet1`
- `--output-csv`：主结果输出路径
- `--review-csv`：复核输出路径
- `--cache-db`：SQLite 缓存路径
- `--limit`：只处理前 N 个唯一物种（调试）
- `--skip-filled`：跳过已填完整分类行
- `--overwrite-existing`：覆盖已有非空值（默认不覆盖）
- `--progress-every`：每 N 条打印一次进度

## 6. 缓存与断点续跑

脚本会把 NCBI/GBIF 请求缓存到 `--cache-db`，重复运行会显著加速。

- 同一输入多次运行：优先命中缓存
- 中断后重跑：可继续使用已有缓存

## 7. 结果使用建议

1. 先检查 `review.csv`，处理歧义项  
2. 再把 `cleaned.csv` 合并回主 xlsx  
3. 重要批次建议保留一份 `cache-db` 和日志，方便复现

## 8. 自动分桶（A/B/C/D）

脚本：`bucket_review_tasks.py`

用途：

- 先按 `origin Species` 对 `review.csv` 去重
- 再结合 `cleaned.csv` 生成 4 类人工处理清单（A/B/C/D）

### 8.1 运行命令

```bash
uv run /home/iLepbase/script/hostplant/bucket_review_tasks.py \
  --cleaned-csv "/home/iLepbase/data/raw/hostplant/鳞翅目.cleaned.csv" \
  --review-csv "/home/iLepbase/data/raw/hostplant/鳞翅目.review.csv" \
  --out-dir "/home/iLepbase/data/raw/hostplant" \
  --prefix "鳞翅目"
```

### 8.2 输出文件

- `鳞翅目.review.dedup.csv`：去重后的 review（每个物种一条）
- `鳞翅目.bucket_A.csv`：已有人种级结果，主要核对并补亚科
- `鳞翅目.bucket_B.csv`：已有 genus+subfamily，需补 species/sid
- `鳞翅目.bucket_C.csv`：仅 genus，需补 subfamily+species/sid
- `鳞翅目.bucket_D.csv`：信息缺失较多，全人工处理
- `鳞翅目.manual_all.csv`：A+B+C+D 合并总表

```bash
uv run /home/iLepbase/script/hostplant/hostplant_taxonomy.py \
  --input-xlsx '/home/iLepbase/data/raw/hostplant/鳞翅目.xlsx' \
  --output-csv '/home/iLepbase/data/raw/hostplant/鳞翅目.cleaned.csv' \
  --review-csv '/home/iLepbase/data/raw/hostplant/鳞翅目.review.csv' \
  --cache-db '/home/iLepbase/data/raw/hostplant/taxonomy_cache.sqlite' \
  --skip-filled \
  --workers 16 \
  --progress-every 500
```

## 9. 仅处理 review：GNR -> BOLD -> LepIndex 幽灵名打标

脚本：`simple_manual_review_list.py`

用途：

- 只处理 `review.csv`
- 输出单文件中文人工清单（含 GBIF 置信度/重名状态列）
- 新增链路：`GNR -> BOLD -> LepIndex`
  - 当名称在 GNR 仅模糊/部分命中或未命中
  - 且 BOLD 无记录
  - 且 LepIndex 仅检出明显跨属（可伴随跨科）记录
  - 直接打标：`[Unresolved: Possible Nomen nudum / Typo]（未解决：疑似裸名或拼写错误）`

### 9.1 命令

```bash
uv run /home/iLepbase/script/hostplant/simple_manual_review_list.py \
  --review-csv /home/iLepbase/data/raw/hostplant/Lepidoptera.review.csv \
  --output-csv /home/iLepbase/data/raw/hostplant/人工校正清单.csv \
  --chain-workers 8 \
  --chain-progress-every 200
```

### 9.2 常用参数

- `--disable-ghost-stop`：关闭幽灵名打标逻辑
- `--chain-workers`：在线链路并发数（默认 6）
- `--chain-progress-every`：链路进度打印间隔

### 9.3 输出关键列

- `GBIF匹配类型`：如 `HIGHERRANK/FUZZY/EXACT`
- `GBIF置信度`：GBIF 返回的 `confidence`（单独一列）
- `GBIF分类状态`：如 `ACCEPTED/SYNONYM/DOUBTFUL`
- `GBIF重名状态`：优先显示 GBIF `note` 中的 homonym 信息（如 `2 synonym homonyms`）
