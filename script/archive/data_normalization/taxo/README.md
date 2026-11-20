# clean_species

脚本：[clean_species.py](/home/iLepbase/script/clean_species/clean_species.py)

## 依赖安装


推荐直接用 `uv`，运行时自动安装依赖：

```bash
uv run /home/iLepbase/script/clean_species/clean_species.py --help
```


如果不用 `uv`，也可以手动配置：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install httpx openpyxl
```

可选环境变量配置：

```bash
export NCBI_EMAIL="your_email"
```

脚本已内置默认 `NCBI_API_KEY`；如需覆盖，再自行导出 `NCBI_API_KEY`。

## I/O说明

### 输入

- 文件：`.xlsx`、`.csv`、`.tsv`、`.txt`
- 工作表：Excel 输入时默认 `Sheet1`
- 列：
  - `origin Species`
  - `family`
  - `fid`
  - `subfamliy`
  - `sfid`
  - `genuis`
  - `gid`
  - `species`
  - `authority`
  - `basionym`
  - `sid`
  - `source`
  - `source link`
  - `note`

### 输出

设 `--output-prefix=/path/Lepidoptera`，输出：

- `/path/Lepidoptera.drop.dot.csv`
- `/path/Lepidoptera.clean.sp.csv`
- `/path/Lepidoptera.clean.genus.csv`
- `/path/Lepidoptera.review.subfamily.csv`
- `/path/Lepidoptera.review.genus.csv`
- `/path/Lepidoptera.review.none.csv`
- `/path/Lepidoptera.counts.json`


## 参数说明

- `--input-file` / `--input-xlsx`：输入文件路径
- `--sheet-name`：工作表名
- `--output-prefix`：输出前缀
- `--cache-db`：SQLite 缓存路径
- `--limit`：只处理前 N 个唯一物种名
- `--overwrite-existing`：覆盖已有非空值
- `--skip-filled`：跳过已填满行
- `--workers`：并发数
- `--progress-every`：进度打印间隔
- `--fuzzy-max-dist`：拼写容错阈值，默认 `2`

## 运行

```bash
uv run /home/iLepbase/script/clean_species/clean_species.py \
  --input-file /home/iLepbase/data/raw/hostplant/Lepidoptera.xlsx \
  --output-prefix /home/iLepbase/data/raw/hostplant/Lepidoptera \
  --cache-db /home/iLepbase/data/raw/hostplant/taxonomy_cache.sqlite \
  --workers 8 \
  --progress-every 200
```

TSV/TXT 单列表头 `origin Species` 也可直接运行，例如：

```bash
uv run /home/iLepbase/script/clean_species/clean_species.py \
  --input-file /home/iLepbase/data/raw/distribution/lep_species_unique.txt \
  --output-prefix /home/iLepbase/data/raw/distribution/lep_species_unique \
  --cache-db /home/iLepbase/data/raw/distribution/taxonomy_cache.sqlite \
  --workers 8 \
  --progress-every 200
```

## 清洗逻辑

### 预处理

- 读取 Excel。
- 按 `origin Species` 规范化、排序、去重。
- 名字含 `.` 的记录直接进入 `drop.dot.csv`。

### 物种校验

- 依次查询：`NCBI -> GBIF -> iNaturalist`
- 候选名与原名编辑距离 `<= 2` 时，按拼写错误接受。
- 任一来源稳定命中 species，输出 `family/fid/subfamliy/sfid/genuis/gid/species/sid/source/source link`，并进入 `clean.sp.csv`。

### 属级回退

- 三个来源都没定到 species 时，取首词做 `NCBI genus fallback`。
- genus fallback 成功，进入 `clean.genus.csv`。
- genus fallback 歧义，进入 `review.genus.csv`。
- genus fallback 未命中，进入 `review.none.csv`。

### 亚科补填

- 对所有已经定到 `species` 但缺 `subfamily` 的记录，补填顺序：
- `同属回填 -> NCBI genus -> iNaturalist genus`
- 仍缺亚科，进入 `review.subfamily.csv`。
- 当前 `clean.sp.csv` 中不再保留 `subfamliy` 为空的记录。

### review 结果

- `review.genus.csv`：`reason = genus ambiguous`
- `review.none.csv`：`reason = no match`
- `review.subfamily.csv`：`reason = no subfamily`

### 核验

- 生成 `counts.json`
- 检查 `output_total == input_total`
- 不相等时直接报错


## 清洗数据库说明

按优先级进行排序:

1. `NCBI Taxonomy`
   - 用途：主要物种水平匹配
   - 主页：https://www.ncbi.nlm.nih.gov/taxonomy
   - E-utilities：https://www.ncbi.nlm.nih.gov/books/NBK25501/
   - Taxonomy Browser：https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi

2. `GBIF Backbone Taxonomy`
   - 用途：NCBI 物种级未查询到结果后进行二次物种匹配
   - 物种检索页：https://www.gbif.org/species/search
   - Species Match API：https://api.gbif.org/v1/species/match
   - Species API 文档：https://techdocs.gbif.org/en/openapi/v1/species

3. `iNaturalist`
   - 用途：NCBI 和 GBIF 物种级都未稳定命中后，作为第三层物种匹配；同时也用于 genus 级亚科补填
   - 主页：https://www.inaturalist.org
   - Taxa 页面：https://www.inaturalist.org/taxa
   - Taxa Autocomplete API：https://api.inaturalist.org/v1/taxa/autocomplete
   - Taxa Detail API：https://api.inaturalist.org/v1/taxa/{taxon_id}
   - 脚本约束：只接受 `Lepidoptera` 范围内的 taxon，内部固定使用 `taxon_id=47157`
