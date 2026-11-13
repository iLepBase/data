### Script name: 清洗物种名称，处理常见的拼写错误和格式问题，获取上级分类阶元。

### Date Created:2025-07-31

# 整个逻辑：
# 1）先在NCBI上检索物种名，获取taxid和分类信息；
# 2）对NCBI没有找到的物种名在GBIF上检索；
# 3）可选，GBIF上并没有总科信息，需要将科通过NCBI检索总科；
# 4）对GBIF没有找到的物种名通过属去NCBI查询上级分类阶元。

# Clean the environment
rm(list = ls());gc()

# load packages
library(taxize)
library(openxlsx)
library(dplyr)
library(stringr)

## 开始清洗物种名前，可以排序预览和筛选"."等特殊符号，先简单清洗。
# 读取"input.name"列
data1 <- read.xlsx("测试_标准.xlsx", sheet = "鳞翅目")
# data1 <- read.xlsx("测试_标准.xlsx", sheet = "植物")
# data1 <- read.xlsx("测试_标准.xlsx", sheet = "微生物")

data_splist <- unique(data1[,1]) # 去重一下避免重复


# 1）先在NCBI上检索 ---------------------------------------------------------------
## 由于后续需要分子数据，所以物种名的清洗优先使用NCBI数据库

# NCBI数据库载入密钥可以加快检索速度，注意不能有空格，改成你自己的密钥：
## NCBI密钥获取链接： https://ncbiinsights.ncbi.nlm.nih.gov/2017/11/02/new-api-keys-for-the-e-utilities/
## 没有 API 密钥的情况下每秒 3 个请求，有API就每秒10个请求。
options(ENTREZ_KEY='YOUR_NCBI_API_KEY')

ids_NCBI <- taxize::get_ids(data_splist, db = 'ncbi')

out_id <- data.frame(unlist(ids_NCBI)) # 得到物种在NCBI的taxid
out_id <- na.omit(out_id) #去除没找到taxid的物种行（NA的行）
out_id <- data.frame(rownames(out_id),out_id[,1]) #建立输入的物种名和taxid的对应关系
out_id$rownames.out_id. <- gsub("ncbi.","", out_id$rownames.out_id.) #把上面物种名前面的ncbi.去掉
colnames(out_id) <- c("input.name","ncbi.taxid")

# 输出没有找到对应taxid的物种，后续用其他数据库检索
missing <- setdiff(data_splist, out_id$input.name)

classf <- taxize::classification(out_id$ncbi.taxid,db = 'ncbi') #问询的物种数量较多时（5000+）可能会报错：错误: Bad Request (HTTP 400) 可尝试使用外网
# save(classf,file = 'output/classf.Rdata')

## 分类阶元的函数 --------------------------------------------------------------

lineage <- function(x, rank0) {
  d0 <- paste0('NCBI.', rank0)
  for (i in seq_along(x)) {
    sub <- subset(x[[i]], rank == rank0, select = name)
    if (nrow(sub)) {
      d0 <- cbind(d0, sub)
      colnames(d0)[ncol(d0)] <- names(x)[i]
    }
  }
  d1 <- t(d0)
  d2 <- data.frame(rownames(d1), d1[, 1], stringsAsFactors = FALSE)
  d3 <- d2[-1, ]
  names(d3) <- c('ncbi.taxid', paste0('NCBI.', rank0))
  d3
}

## 一键返回全阶元的函数
# 注意：①NCBI动物的kingdom是Metazoa，不是Animalia
# 注意：②没有总科信息的类群，例如：植物，则将"superfamily"删去
lineage.all <- function(x) {
  # ranks <- c("kingdom","phylum","class","order",
  #            "superfamily","family","genus","species")
  ranks <- c("kingdom","phylum","class","order",
             "family","genus","species")
  lst <- lapply(ranks, function(r) lineage(x, r))
  Reduce(function(a, b) merge(a, b, by = "ncbi.taxid", all.x = TRUE),
         lst, init = out_id)
}

lineage_all <- lineage.all(classf)

# openxlsx::write.xlsx(lineage_all,'NCBI_family.xlsx',rowNames = F,colNames=T)

# 2）上一步找不到的再去GBIF检索 ---------------------------------------------------------
# GBIF不需要密钥
# 出现需要选择的选项，选择ACCEPTED的项，一般的第一个，求准确率建议还是去官网查一下
# 【错误于curl::curl_fetch_memory(x$url$url, handle = x$url$handle):
# Error in the HTTP2 framing layer [api.gbif.org]】
# 由于 taxize 包在调用 GBIF API 时使用了 HTTP/2，而 GBIF 的服务器或中间网络设备对 HTTP/2 支持不稳定，导致连接失败
# 出现这个错误就加上, curlopts = list(http_version = 2L)
ids_GBIF <- taxize::get_ids(missing, db = 'gbif', curlopts = list(http_version = 2L))

out_id2 <- data.frame(unlist(ids_GBIF)) # 得到物种在GBIF的gbifID
out_id2 <- na.omit(out_id2) #去除没找到taxid的物种行（NA的行）
out_id2 <- data.frame(rownames(out_id2),out_id2[,1]) #建立输入的物种名和taxid的对应关系
out_id2$rownames.out_id2. <- gsub("gbif.","", out_id2$rownames.out_id2.) #把上面物种名前面的gbif.去掉
colnames(out_id2) <- c("input.name","gbifID") # 加个点

# 输出没有找到对应gbifID的物种
missing2 <- setdiff(missing, out_id2$input.name)

# 若报错可以重新运行这一步
classf2 <- taxize::classification(out_id2$gbifID,db = 'gbif')
# save(classf,file = 'output/classf.Rdata')

### 分类阶元的函数---------------------------------------------------------------
lineage2 <- function(x, rank0) {
  d0 <- paste0('GBIF.', rank0)
  for (i in seq_along(x)) {
    sub <- subset(x[[i]], rank == rank0, select = name)
    if (NROW(sub)) {
      d0 <- cbind(d0, sub)
      colnames(d0)[ncol(d0)] <- names(x)[i]
    }
  }
  d1 <- t(d0)
  d2 <- data.frame(gbifID = rownames(d1)[-1],
                   tmp    = d1[-1, 1],
                   stringsAsFactors = FALSE)
  names(d2)[2] <- paste0('GBIF.', rank0)
  d2
}

lineage.all2 <- function(x) {
  ranks <- c("kingdom","phylum","class","order",
             "family","genus","species")     # GBIF 没有 superfamily
  lst   <- lapply(ranks, function(r) lineage2(x, r))
  Reduce(function(a, b) merge(a, b, by = "gbifID", all.x = TRUE),
         lst, init = out_id2)
}

# 调用
lineage_all2 <- lineage.all2(classf2)
# openxlsx::write.xlsx(lineage_all2,'GBIF.xlsx',rowNames = F,colNames=T)


# 3）GBIF输出的结果没有总科信息，需要通过科去NCBI查询总科，不需要则跳过此步骤 ----------------------------------------
# 先提取GBIF输出的科名
data1_family <- unique(lineage_all2$GBIF.family)
options(ENTREZ_KEY='YOUR_NCBI_API_KEY')

# 注意，下面这一步因为有同属、科名的情况，所以需要手动选择数字，回车。
#  status  rank division scientificname commonname     uid genus species subsp modificationdate
# 1 active clade     bees     Anthophila       bees 3042114                     2025/02/27 00:00
# 2 active genus    moths     Anthophila             999306                     2011/04/04 00:00
ids_NCBI_family <- taxize::get_ids(data1_family, db = 'ncbi')

out_id3 <- data.frame(unlist(ids_NCBI_family)) # 得到科在NCBI的taxid
out_id3 <- na.omit(out_id3) #去除没找到taxid的物种行（NA的行）
out_id3 <- data.frame(rownames(out_id3),out_id3[,1]) #建立输入的物种名和taxid的对应关系
out_id3$rownames.out_id3. <- gsub("ncbi.","", out_id3$rownames.out_id3.) #把上面物种名前面的ncbi.去掉
colnames(out_id3) <- c("input.name","ncbi.taxid")

# 输出没有找到对应taxid的物种，后续用其他数据库检索
missing3 <- setdiff(data1_family, out_id3$input.name)

classf3 <- taxize::classification(out_id3$ncbi.taxid,db = 'ncbi') #问询的物种数量较多时（5000+）可能会报错：错误: Bad Request (HTTP 400) 可尝试使用外网
# save(classf,file = 'output/classf.Rdata')

### 分类阶元的函数-----------------------
lineage3 <- function(x, rank0) {
  d0 <- paste0('NCBI.', rank0)
  for (i in seq_along(x)) {
    sub <- subset(x[[i]], rank == rank0, select = name)
    if (NROW(sub)) {
      d0 <- cbind(d0, sub)
      colnames(d0)[ncol(d0)] <- names(x)[i]
    }
  }
  d1 <- t(d0)
  d2 <- data.frame(ncbi.taxid = rownames(d1)[-1],
                   tmp        = d1[-1, 1],
                   stringsAsFactors = FALSE)
  names(d2)[2] <- paste0('NCBI.', rank0)
  d2
}

lineage.all3 <- function(x) {
  ranks <- c("kingdom","phylum","class","order",
             "superfamily","family")   # 这里只到 family，没有 genus/species
  lst   <- lapply(ranks, function(r) lineage3(x, r))
  Reduce(function(a, b) merge(a, b, by = "ncbi.taxid", all.x = TRUE),
         lst, init = out_id3)
}

# 调用
lineage_all3 <- lineage.all3(classf3)

#　在lineage_all2中新增一列GBIF.superfamily，根据lineage_all3中NCBI.family与lineage_all2中的GBIF.family相同的科名，获取对应的总科名
lineage_missing <- lineage_all2 %>%
  left_join(lineage_all3 %>% select(NCBI.family, NCBI.superfamily) %>%
      rename(GBIF.family = NCBI.family),by = "GBIF.family") # 将NCBI.family重命名为GBIF.family用于匹配

# 将NCBI.superfamily移动到GBIF.family前
lineage_missing <- lineage_missing %>%
  relocate(NCBI.superfamily, .before = ncol(.) - 3)
# openxlsx::write.xlsx(lineage_missing,'missing.xlsx',rowNames = F,colNames=T)

ncbi_part <- lineage_all %>%
  transmute(
    input.name = input.name,
    kingdom    = NCBI.kingdom,
    phylum     = NCBI.phylum,
    class      = NCBI.class,
    order      = NCBI.order,
    superfamily= NCBI.superfamily,
    family     = NCBI.family,
    genus      = NCBI.genus,
    species    = NCBI.species,
    source     = "NCBI"
  )

gbif_part <- lineage_missing %>%
  transmute(
    input.name = input.name,
    kingdom    = GBIF.kingdom,
    phylum     = GBIF.phylum,
    class      = GBIF.class,
    order      = GBIF.order,
    superfamily= NCBI.superfamily,
    family     = GBIF.family,
    genus      = GBIF.genus,
    species    = GBIF.species,
    source     = "GBIF"
  )

combined <- bind_rows(ncbi_part, gbif_part)

# 4）上述NCBI和GBIF均找不到则通过属去NCBI找上级分类界元 ------------------------------------------------
# missing3 <- missing
missing3_df <- tibble(
  species = missing3,
  input.name   = word(missing3, 1)   # 取第一个空格前的单词(属名)
)

missingdata <- unique(missing3_df) # 去重一下

options(ENTREZ_KEY='YOUR_NCBI_API_KEY')

# 这一步运行后，会有同属、科的物种，需要手动选择你想要的物种类群
ids_miss <- taxize::get_ids(missingdata$input.name, db = 'ncbi')

out_id4 <- data.frame(unlist(ids_miss)) # 得到物种在NCBI的taxid
out_id4 <- na.omit(out_id4) #去除没找到taxid的物种行（NA的行）
out_id4 <- data.frame(rownames(out_id4),out_id4[,1]) #建立输入的物种名和taxid的对应关系
out_id4$rownames.out_id4. <- gsub("ncbi.","", out_id4$rownames.out_id4.) #把上面物种名前面的ncbi.去掉
colnames(out_id4) <- c("input.name","ncbi.taxid")

# 输出没有找到对应taxid的物种
## 但不建议后面用GBIF检索，因为GBIF不会输出物种类群，有可能动物划分到了植物等错误。
# 如果这一步剩的不多了可选择手动去GBIF搜
missing4 <- setdiff(missingdata$input.name, out_id4$input.name)

classf4 <- taxize::classification(out_id4$ncbi.taxid,db = 'ncbi')
# save(classf,file = 'output/classf.Rdata')

### 分类阶元的函数-----------------------
lineage4 <- function(x, rank0) {
  d0 <- paste0('NCBI.', rank0)
  for (i in seq_along(x)) {
    sub <- subset(x[[i]], rank == rank0, select = name)
    if (NROW(sub)) {
      d0 <- cbind(d0, sub)
      colnames(d0)[ncol(d0)] <- names(x)[i]
    }
  }
  d1 <- t(d0)
  d2 <- data.frame(ncbi.taxid = rownames(d1)[-1],
                   tmp        = d1[-1, 1],
                   stringsAsFactors = FALSE)
  names(d2)[2] <- paste0('NCBI.', rank0)
  d2
}


lineage.all4 <- function(x) {
  ranks <- c("kingdom","phylum","class","order",
             "superfamily","family","genus")
  lst   <- lapply(ranks, function(r) lineage4(x, r))
  Reduce(function(a, b) merge(a, b, by = "ncbi.taxid", all.x = TRUE),
         lst, init = out_id4)
}

# 调用
lineage_all4 <- lineage.all4(classf4)

# 匹配回去
missing3_combined <- missing3_df %>%
  left_join(lineage_all4,
            by = c("input.name" = "NCBI.genus"))
# openxlsx::write.xlsx(lineage_all4,'NCBI_genus.xlsx',rowNames = F,colNames=T)

