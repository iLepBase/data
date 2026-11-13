# Lepidoptera COI Data

**Author:** menyahui, liueic   
**Date:** 2026-03-10 


## 数据说明

数据更新时间: 2026-02-27

数据来源: BOLD Systems（Barcode of Life Data Systems）: https://www.boldsystems.org/

原始数据位置:
[BOLD_Public_27-FEB-2026_FASTA.gz](/home/iLepbase/data/raw/COI/BOLD_Public_27-FEB-2026_FASTA.gz)

下载说明: 见 [distribution / README.md](/home/iLepbase/data/raw/distribution/README.md)


## 数据处理

将下载的 BOLD 数据压缩包解压到当前目录：

```bash
gzip -dk data/raw/COI/BOLD_Public_27-FEB-2026_FASTA.gz
```

根据 FASTA 头信息中的分类信息，从数据中提取 鳞翅目（Lepidoptera） 的序列

```bash
awk '/^>/ {p=index($0,"Lepidoptera")} p' BOLD_Public_27-FEB-2026_FASTA.fasta > lepidoptera_COI_BOLD_27-FEB-2026.fasta
```

## 数据统计

输出文件: [lepidoptera_COI_BOLD_27-FEB-2026.fasta](/home/iLepbase/data/raw/COI/lepidoptera_COI_BOLD_27-FEB-2026.fasta)

- 大小： 1.5G  
- 数目： 2,178,330