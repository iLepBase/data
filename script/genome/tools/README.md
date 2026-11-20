# Project-local datasets CLI

把 NCBI `datasets` 可执行文件放到当前目录，并命名为：

```text
/dataz/zhanglab-1/iLepbase/script/genome/tools/datasets
```

然后给执行权限：

```bash
chmod +x /dataz/zhanglab-1/iLepbase/script/genome/tools/datasets
```

脚本启动时会优先查找这个路径；如果不存在，再回退到系统 `PATH` 或 `NCBI_DATASETS_BIN`。
