"""
Insect Genome Crawler
爬取 insect-genome.com 网站上指定目(Order)的昆虫基因组数据
"""

import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tqdm import tqdm


BASE_URL = "https://www.insect-genome.com"
API_BASE = f"{BASE_URL}/api/genome/genomes/"
RESOURCE_CONFIG: dict[str, dict[str, Any]] = {
    "gff3": {
        "label": "GFF3",
        "dir_name": "gff3",
        "url_suffix": ".gff3",
        "file_suffix": ".gff3",
    },
    "protein": {
        "label": "Protein",
        "dir_name": "protein",
        "url_suffix": ".anno.pep.fa",
        "file_suffix": ".anno.pep.fa",
    },
    "cds": {
        "label": "CDS",
        "dir_name": "cds",
        "url_suffix": ".cds.fa",
        "file_suffix": ".cds.fa",
    },
}


class InsectGenomeCrawler:
    def __init__(
        self,
        order: str = "Lepidoptera",
        output_dir: str = "data",
        proxy: str | None = None,
        min_gff_bytes: int = 10_240,
        min_gff_lines: int = 10,
        min_sequence_bytes: int = 1_024,
    ):
        self.order = order
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.proxy = proxy
        self.min_gff_bytes = min_gff_bytes
        self.min_gff_lines = min_gff_lines
        self.min_sequence_bytes = min_sequence_bytes
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        self.resource_dirs: dict[str, Path] = {}
        for resource_type, config in RESOURCE_CONFIG.items():
            resource_dir = self.output_dir / config["dir_name"]
            resource_dir.mkdir(exist_ok=True)
            self.resource_dirs[resource_type] = resource_dir

        self.info_dir = self.output_dir / "info"
        self.info_dir.mkdir(exist_ok=True)
        self.download_report_path = self.output_dir / "download_report.csv"
        self.download_log_path = self.output_dir / "download_log.jsonl"
        self.download_comparison_path = self.output_dir / "download_species_comparison.json"
        self.species_list_path: Path | None = None
        self.species_list_source = ""

        self.client = httpx.Client(
            timeout=180.0,
            follow_redirects=True,
            proxy=self.proxy if self.proxy else None,
        )

    def fetch_page(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        """获取指定页的数据"""
        params = {
            "format": "json",
            "order": self.order,
            "page": page,
            "page_size": page_size,
        }
        response = self.client.get(API_BASE, params=params)
        response.raise_for_status()
        return response.json()

    def fetch_all_species(self) -> list[dict[str, Any]]:
        """获取所有物种数据（处理分页）"""
        print(f"正在获取 Order={self.order} 的物种列表...")
        first_page = self.fetch_page(page=1, page_size=100)
        total_count = first_page["count"]
        results = first_page["results"]

        print(f"共 {total_count} 个物种，开始分页获取...")

        page_size = 100
        total_pages = (total_count + page_size - 1) // page_size

        for page in tqdm(range(2, total_pages + 1), desc="获取物种列表"):
            try:
                data = self.fetch_page(page=page, page_size=page_size)
                results.extend(data["results"])
                time.sleep(0.1)
            except Exception as e:
                print(f"获取第 {page} 页时出错: {e}")
                continue

        print(f"成功获取 {len(results)} 个物种的数据")
        return results

    def get_species_filename(self, species: str | None) -> str:
        """将物种名转换为URL/文件友好的名字"""
        if not species:
            return "unknown_species"
        return species.strip().replace(" ", "_")

    def get_output_name(self, ibg_id: str | None, species: str | None) -> str:
        """生成输出文件名，优先使用IBG ID"""
        if ibg_id:
            return ibg_id
        return self.get_species_filename(species)

    def get_record_id(self, ibg_id: str | None, species: str | None) -> str:
        """生成用于汇总比对的唯一ID"""
        return self.get_output_name(ibg_id, species)

    def build_resource_url(self, species_name: str, resource_type: str) -> str:
        """根据物种名拼接下载URL"""
        species_filename = self.get_species_filename(species_name)
        suffix = RESOURCE_CONFIG[resource_type]["url_suffix"]
        return f"{BASE_URL}/data/genome/{species_filename}/{species_filename}{suffix}"

    def get_output_path(
        self,
        resource_type: str,
        output_name: str,
    ) -> Path:
        """获取资源输出路径"""
        suffix = RESOURCE_CONFIG[resource_type]["file_suffix"]
        return self.resource_dirs[resource_type] / f"{output_name}{suffix}"

    def extract_basic_info(self, species_data: dict[str, Any]) -> dict[str, Any]:
        """提取物种基本信息"""
        accession = species_data.get("accession")
        data_source = species_data.get("data_source")

        if accession:
            source_info = f"{data_source} ({accession})"
        else:
            source_info = str(data_source) if data_source else "N/A"

        return {
            "ibg_id": species_data.get("ibg_id", "N/A"),
            "species": species_data.get("species", "N/A"),
            "common_name": species_data.get("common_name", "N/A"),
            "order": species_data.get("order", "N/A"),
            "family": species_data.get("family", "N/A"),
            "genus": species_data.get("genus", "N/A"),
            "data_source": source_info,
            "assembly_level": species_data.get("level", "N/A"),
            "genome_size_mb": species_data.get("genome_size", "N/A"),
            "gc_content_percent": species_data.get("gc", "N/A"),
            "n50_kb": species_data.get("n50", "N/A"),
            "sequence_number": species_data.get("seq_num", "N/A"),
            "busco": species_data.get("busco", "N/A"),
            "omark_completeness": species_data.get("omark_completence", "N/A"),
        }

    def save_summary(self, all_species: list[dict[str, Any]]) -> None:
        """保存所有物种的汇总信息"""
        summary_path = self.output_dir / "species_summary.json"
        summary_path.write_text(json.dumps(all_species, indent=2, ensure_ascii=False))
        print(f"汇总信息已保存到: {summary_path}")

    def save_csv(self, basic_info_list: list[dict[str, Any]]) -> Path:
        """保存CSV格式的基本信息"""
        csv_path = self.output_dir / "species_info.csv"
        if basic_info_list:
            keys = basic_info_list[0].keys()
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(basic_info_list)
            print(f"CSV信息已保存到: {csv_path}")
        return csv_path

    def write_log(self, event: str, **fields: Any) -> None:
        """写入可追溯的完整下载日志(JSONL)"""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        with self.download_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def save_download_report(self, rows: list[dict[str, Any]]) -> Path:
        """保存下载结果清单"""
        if not rows:
            return self.download_report_path
        keys = list(rows[0].keys())
        with self.download_report_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
        return self.download_report_path

    def load_species_from_csv(self, csv_path: str) -> list[dict[str, str]]:
        """从CSV加载物种信息（至少需要 ibg_id 和 species）"""
        rows: list[dict[str, str]] = []
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV 文件不存在: {path}")

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV 文件缺少表头")
            if "species" not in reader.fieldnames or "ibg_id" not in reader.fieldnames:
                raise ValueError("CSV 必须包含 ibg_id 和 species 列")

            for row in reader:
                species = (row.get("species") or "").strip()
                ibg_id = (row.get("ibg_id") or "").strip()
                if not species:
                    continue
                rows.append({"species": species, "ibg_id": ibg_id})

        if not rows:
            raise ValueError("CSV 中未找到有效物种记录")
        return rows

    def validate_binary_header(self, path: Path, min_bytes: int) -> tuple[bool, str]:
        """对二进制或压缩文件做基础校验"""
        try:
            size_bytes = path.stat().st_size
        except FileNotFoundError:
            return False, "file_missing"

        if size_bytes < min_bytes:
            return False, f"size<{min_bytes}"

        try:
            header = path.read_bytes()[:2048]
        except OSError as e:
            return False, f"read_error:{type(e).__name__}"

        text_header = header.decode("utf-8", errors="ignore").strip().lower()
        if text_header.startswith("<html") or text_header.startswith("<!doctype") or "<html" in text_header:
            return False, "html_response"
        return True, "ok"

    def validate_gff_file(self, path: Path) -> tuple[bool, str]:
        """二次校验GFF3文件（header/数据行 + 行数 + 大小）"""
        try:
            size_bytes = path.stat().st_size
        except FileNotFoundError:
            return False, "file_missing"

        if size_bytes < self.min_gff_bytes:
            return False, f"size<{self.min_gff_bytes}"

        line_count = 0
        header_checked = False
        header_ok = False
        data_line_count = 0

        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_count += 1
                stripped = line.strip()
                if not header_checked:
                    if not stripped:
                        continue
                    header_checked = True
                    lower = stripped.lower()
                    if lower.startswith("<html") or lower.startswith("<!doctype") or "<html" in lower:
                        return False, "html_response"
                    if stripped.startswith("##gff-version 3"):
                        header_ok = True

                if stripped and not stripped.startswith("#"):
                    cols = stripped.split("\t")
                    if len(cols) >= 9:
                        data_line_count += 1

                if line_count >= self.min_gff_lines and (
                    header_ok or data_line_count >= self.min_gff_lines
                ):
                    return True, "ok"

        if not header_checked:
            return False, "empty_file"
        if line_count < self.min_gff_lines:
            return False, f"lines<{self.min_gff_lines}"
        if not header_ok and data_line_count < self.min_gff_lines:
            return False, "insufficient_data_lines"
        return True, "ok"

    def validate_fasta_file(self, path: Path) -> tuple[bool, str]:
        """校验FASTA文件"""
        try:
            size_bytes = path.stat().st_size
        except FileNotFoundError:
            return False, "file_missing"

        if size_bytes < self.min_sequence_bytes:
            return False, f"size<{self.min_sequence_bytes}"

        header_found = False
        sequence_line_found = False
        line_count = 0

        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_count += 1
                stripped = line.strip()
                if not stripped:
                    continue
                lower = stripped.lower()
                if lower.startswith("<html") or lower.startswith("<!doctype") or "<html" in lower:
                    return False, "html_response"
                if stripped.startswith(">"):
                    header_found = True
                    continue
                if header_found:
                    sequence_line_found = True
                    if line_count >= 2:
                        return True, "ok"

        if not header_found:
            return False, "missing_fasta_header"
        if not sequence_line_found:
            return False, "missing_sequence"
        return True, "ok"

    def validate_downloaded_file(self, resource_type: str, path: Path) -> tuple[bool, str]:
        """按资源类型选择校验逻辑"""
        if resource_type == "gff3":
            return self.validate_gff_file(path)
        if resource_type in {"protein", "cds"}:
            return self.validate_fasta_file(path)
        return self.validate_binary_header(path, self.min_sequence_bytes)

    def build_report_row(
        self,
        resource_type: str,
        species_name: str,
        ibg_id: str | None,
        output_name: str,
        url: str,
        output_path: Path,
        success: bool,
        reason: str,
        attempts: int,
        http_status: int | str,
    ) -> dict[str, Any]:
        """构建下载报告行"""
        record_id = self.get_record_id(ibg_id, species_name)
        size_bytes = output_path.stat().st_size if output_path.exists() else ""
        return {
            "run_id": self.run_id,
            "resource_type": resource_type,
            "resource_label": RESOURCE_CONFIG[resource_type]["label"],
            "ibg_id": ibg_id or "",
            "species": species_name,
            "record_id": record_id,
            "output_name": output_name,
            "status": "success" if success else "failed",
            "reason": reason,
            "attempts": attempts,
            "http_status": http_status,
            "url": url,
            "file_path": str(output_path) if output_path.exists() else "",
            "size_bytes": size_bytes,
        }

    def download_resource(
        self,
        resource_type: str,
        species_name: str,
        ibg_id: str | None,
        index: int,
        total: int,
        retry_count: int = 3,
    ) -> dict[str, Any]:
        """下载单个资源文件（串行）"""
        resource_label = RESOURCE_CONFIG[resource_type]["label"]
        species_filename = self.get_species_filename(species_name)
        output_name = self.get_output_name(ibg_id, species_name)
        record_id = self.get_record_id(ibg_id, species_name)
        url = self.build_resource_url(species_name, resource_type)
        output_path = self.get_output_path(resource_type, output_name)
        display_name = f"{output_name} ({species_name})" if ibg_id else species_name

        print(f"\n[{index}/{total}] 正在下载 {resource_label}: {display_name}")
        print(f"    URL: {url}")

        self.write_log(
            "resource_started",
            resource_type=resource_type,
            species=species_name,
            species_filename=species_filename,
            ibg_id=ibg_id or "",
            record_id=record_id,
            output_name=output_name,
            url=url,
            output_path=str(output_path),
        )

        if output_path.exists():
            is_valid, reason = self.validate_downloaded_file(resource_type, output_path)
            if is_valid:
                size_mb = output_path.stat().st_size / 1024 / 1024
                print(f"    已存在且有效，跳过 ({size_mb:.2f} MB)")
                self.write_log(
                    "resource_skipped_existing",
                    resource_type=resource_type,
                    species=species_name,
                    ibg_id=ibg_id or "",
                    record_id=record_id,
                    output_name=output_name,
                    reason="exists",
                    size_bytes=output_path.stat().st_size,
                    output_path=str(output_path),
                )
                return self.build_report_row(
                    resource_type=resource_type,
                    species_name=species_name,
                    ibg_id=ibg_id,
                    output_name=output_name,
                    url=url,
                    output_path=output_path,
                    success=True,
                    reason="exists",
                    attempts=0,
                    http_status=200,
                )
            print(f"    已存在但校验失败({reason})，重新下载")
            self.write_log(
                "resource_existing_invalid",
                resource_type=resource_type,
                species=species_name,
                ibg_id=ibg_id or "",
                record_id=record_id,
                output_name=output_name,
                reason=reason,
                output_path=str(output_path),
            )
            output_path.unlink(missing_ok=True)

        last_http_status: int | str = ""
        last_reason = "failed"

        for attempt in range(1, retry_count + 1):
            tmp_path = output_path.with_name(output_path.name + ".part")
            tmp_path.unlink(missing_ok=True)
            try:
                print(f"    尝试 {attempt}/{retry_count}...", end=" ", flush=True)
                self.write_log(
                    "resource_attempt_started",
                    resource_type=resource_type,
                    species=species_name,
                    ibg_id=ibg_id or "",
                    record_id=record_id,
                    output_name=output_name,
                    attempt=attempt,
                    url=url,
                )

                bytes_written = 0
                with self.client.stream("GET", url, timeout=180.0) as response:
                    last_http_status = response.status_code
                    if response.status_code == 200:
                        with tmp_path.open("wb") as f:
                            for chunk in response.iter_bytes():
                                if not chunk:
                                    continue
                                f.write(chunk)
                                bytes_written += len(chunk)

                        is_valid, reason = self.validate_downloaded_file(resource_type, tmp_path)
                        if is_valid:
                            tmp_path.replace(output_path)
                            size_mb = bytes_written / 1024 / 1024
                            print(f"成功 ({size_mb:.2f} MB)")
                            self.write_log(
                                "resource_download_succeeded",
                                resource_type=resource_type,
                                species=species_name,
                                ibg_id=ibg_id or "",
                                record_id=record_id,
                                output_name=output_name,
                                attempt=attempt,
                                http_status=response.status_code,
                                size_bytes=bytes_written,
                                output_path=str(output_path),
                            )
                            return self.build_report_row(
                                resource_type=resource_type,
                                species_name=species_name,
                                ibg_id=ibg_id,
                                output_name=output_name,
                                url=url,
                                output_path=output_path,
                                success=True,
                                reason="ok",
                                attempts=attempt,
                                http_status=response.status_code,
                            )

                        tmp_path.unlink(missing_ok=True)
                        last_reason = f"validation_failed:{reason}"
                        print(f"失败 - 校验未通过({reason})")
                        self.write_log(
                            "resource_validation_failed",
                            resource_type=resource_type,
                            species=species_name,
                            ibg_id=ibg_id or "",
                            record_id=record_id,
                            output_name=output_name,
                            attempt=attempt,
                            http_status=response.status_code,
                            size_bytes=bytes_written,
                            reason=reason,
                        )
                        if attempt < retry_count:
                            print("    等待 2 秒后重试...")
                            time.sleep(2)
                            continue
                        break

                    if response.status_code in (429, 503):
                        last_reason = f"http_{response.status_code}_rate_limited"
                        print(f"速率限制 (HTTP {response.status_code})")
                        self.write_log(
                            "resource_rate_limited",
                            resource_type=resource_type,
                            species=species_name,
                            ibg_id=ibg_id or "",
                            record_id=record_id,
                            output_name=output_name,
                            attempt=attempt,
                            http_status=response.status_code,
                        )
                        if attempt < retry_count:
                            wait_time = 3 * attempt
                            print(f"    等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue
                        print(f"失败 - HTTP {response.status_code} (速率限制)")
                        break

                    last_reason = f"http_{response.status_code}"
                    print(f"失败 - HTTP {response.status_code}")
                    self.write_log(
                        "resource_http_failed",
                        resource_type=resource_type,
                        species=species_name,
                        ibg_id=ibg_id or "",
                        record_id=record_id,
                        output_name=output_name,
                        attempt=attempt,
                        http_status=response.status_code,
                    )
                    break

            except Exception as e:
                tmp_path.unlink(missing_ok=True)
                last_reason = f"{type(e).__name__}:{str(e)[:120]}"
                if attempt < retry_count:
                    print(f"异常 - {type(e).__name__}: {str(e)[:50]}")
                    print("    等待 2 秒后重试...")
                    self.write_log(
                        "resource_exception_retry",
                        resource_type=resource_type,
                        species=species_name,
                        ibg_id=ibg_id or "",
                        record_id=record_id,
                        output_name=output_name,
                        attempt=attempt,
                        reason=last_reason,
                    )
                    time.sleep(2)
                    continue
                print(f"失败 - {type(e).__name__}: {str(e)[:50]}")
                self.write_log(
                    "resource_exception_failed",
                    resource_type=resource_type,
                    species=species_name,
                    ibg_id=ibg_id or "",
                    record_id=record_id,
                    output_name=output_name,
                    attempt=attempt,
                    reason=last_reason,
                )
                break

        self.write_log(
            "resource_finished_failed",
            resource_type=resource_type,
            species=species_name,
            ibg_id=ibg_id or "",
            record_id=record_id,
            output_name=output_name,
            http_status=last_http_status,
            reason=last_reason,
            output_path=str(output_path),
        )
        return self.build_report_row(
            resource_type=resource_type,
            species_name=species_name,
            ibg_id=ibg_id,
            output_name=output_name,
            url=url,
            output_path=output_path,
            success=False,
            reason=last_reason,
            attempts=retry_count,
            http_status=last_http_status,
        )

    def collect_file_record_ids(self, resource_type: str) -> list[str]:
        """从磁盘收集某类资源的record_id"""
        suffix = RESOURCE_CONFIG[resource_type]["file_suffix"]
        ids = []
        for path in self.resource_dirs[resource_type].iterdir():
            if path.is_file() and path.name.endswith(suffix):
                ids.append(path.name[: -len(suffix)])
        return sorted(ids)

    def build_download_comparison(
        self,
        species_rows: list[dict[str, Any]],
        report_rows: list[dict[str, Any]],
        resource_types: list[str],
    ) -> dict[str, Any]:
        """生成类似 gff_report_species_comparison.json 的汇总"""
        species_ids = [
            self.get_record_id(row.get("ibg_id"), row.get("species")) for row in species_rows
        ]
        species_counter = Counter(species_ids)
        species_id_set = set(species_ids)

        counts: dict[str, Any] = {
            "species_rows_excluding_header": len(species_rows),
            "species_unique_record_id": len(species_id_set),
            "report_rows_excluding_header": len(report_rows),
        }
        duplicates: dict[str, Any] = {
            "species_duplicate_record_id": sorted(
                record_id for record_id, count in species_counter.items() if count > 1
            )
        }
        missing_or_extra: dict[str, Any] = {}
        resources: dict[str, Any] = {}
        overall_consistent = not duplicates["species_duplicate_record_id"]

        for resource_type in resource_types:
            resource_rows = [
                row for row in report_rows if row["resource_type"] == resource_type
            ]
            report_ids = [row["record_id"] for row in resource_rows]
            success_report_ids = [
                row["record_id"] for row in resource_rows if row["status"] == "success"
            ]
            failed_report_ids = [
                row["record_id"] for row in resource_rows if row["status"] != "success"
            ]
            report_counter = Counter(report_ids)
            file_ids = self.collect_file_record_ids(resource_type)
            report_id_set = set(report_ids)
            success_report_id_set = set(success_report_ids)
            file_id_set = set(file_ids)
            failed_report_id_set = set(failed_report_ids)
            report_duplicates = sorted(
                record_id for record_id, count in report_counter.items() if count > 1
            )
            species_vs_report_match = species_id_set == report_id_set
            species_vs_files_match = species_id_set == file_id_set
            report_vs_files_match = report_id_set == file_id_set
            success_report_vs_files_match = success_report_id_set == file_id_set
            resource_consistent = (
                species_vs_report_match
                and species_vs_files_match
                and report_vs_files_match
                and success_report_vs_files_match
                and not report_duplicates
            )
            overall_consistent = overall_consistent and resource_consistent

            counts[f"{resource_type}_files"] = len(file_ids)
            counts[f"{resource_type}_report_rows_excluding_header"] = len(report_ids)
            counts[f"{resource_type}_report_unique_record_id"] = len(report_id_set)
            counts[f"{resource_type}_report_success_rows"] = len(success_report_ids)
            counts[f"{resource_type}_report_failed_rows"] = len(resource_rows) - len(
                success_report_ids
            )
            duplicates[f"{resource_type}_report_duplicate_record_id"] = report_duplicates

            resource_missing_or_extra = {
                "in_species_not_in_report": sorted(species_id_set - report_id_set),
                "in_report_not_in_species": sorted(report_id_set - species_id_set),
                "in_species_not_in_files": sorted(species_id_set - file_id_set),
                "in_files_not_in_species": sorted(file_id_set - species_id_set),
                "in_report_not_in_files": sorted(report_id_set - file_id_set),
                "in_files_not_in_report": sorted(file_id_set - report_id_set),
                "success_report_not_in_files": sorted(success_report_id_set - file_id_set),
                "in_files_not_in_success_report": sorted(file_id_set - success_report_id_set),
                "failed_in_report": sorted(failed_report_id_set),
            }
            resources[resource_type] = {
                "label": RESOURCE_CONFIG[resource_type]["label"],
                "directory": str(self.resource_dirs[resource_type]),
                "counts": {
                    "species_unique_record_id": len(species_id_set),
                    "report_rows_excluding_header": len(report_ids),
                    "report_success_rows": len(success_report_ids),
                    "report_failed_rows": len(resource_rows) - len(success_report_ids),
                    "report_unique_record_id": len(report_id_set),
                    "files": len(file_ids),
                },
                "duplicates": {
                    "report_duplicate_record_id": report_duplicates,
                },
                "checks": {
                    "species_vs_report_match": species_vs_report_match,
                    "species_vs_files_match": species_vs_files_match,
                    "report_vs_files_match": report_vs_files_match,
                    "success_report_vs_files_match": success_report_vs_files_match,
                    "all_tasks_succeeded": not failed_report_ids
                    and len(resource_rows) == len(species_rows),
                    "all_consistent": resource_consistent,
                },
                "missing_or_extra": resource_missing_or_extra,
            }
            for key, value in resource_missing_or_extra.items():
                missing_or_extra[f"{resource_type}_{key}"] = value

        return {
            "run_id": self.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "resource_types": resource_types,
                "species_list_source": self.species_list_source or "unknown",
                "all_consistent": overall_consistent,
            },
            "files": {
                "species_list_csv": str(self.species_list_path) if self.species_list_path else "",
                "report_csv": str(self.download_report_path),
                "log_jsonl": str(self.download_log_path),
                "comparison_json": str(self.download_comparison_path),
                "resource_dirs": {
                    resource_type: str(self.resource_dirs[resource_type])
                    for resource_type in resource_types
                },
            },
            "species_list": {
                "rows_excluding_header": len(species_rows),
                "unique_record_id": len(species_id_set),
                "duplicate_record_id": duplicates["species_duplicate_record_id"],
            },
            "resources": resources,
            "counts": counts,
            "duplicates": duplicates,
            "missing_or_extra": missing_or_extra,
        }

    def save_download_comparison(
        self,
        species_rows: list[dict[str, Any]],
        report_rows: list[dict[str, Any]],
        resource_types: list[str],
    ) -> Path:
        """保存下载汇总比对JSON"""
        comparison = self.build_download_comparison(
            species_rows=species_rows,
            report_rows=report_rows,
            resource_types=resource_types,
        )
        self.download_comparison_path.write_text(
            json.dumps(comparison, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.download_comparison_path

    def download_all_resources(
        self,
        species_rows: list[dict[str, Any]],
        species_for_download: list[tuple[str, str | None]],
        resource_types: list[str],
    ) -> dict[str, int]:
        """串行下载所有资源文件"""
        total_tasks = len(species_for_download) * len(resource_types)
        print(f"\n开始下载 {len(species_for_download)} 个物种的 {len(resource_types)} 类资源...")
        print("=" * 60)
        print("模式: 串行下载（更稳定）")
        print("资源类型: " + ", ".join(RESOURCE_CONFIG[rt]["label"] for rt in resource_types))
        if self.proxy:
            print(f"使用代理: {self.proxy}")
        print(
            f"校验阈值: gff_min_bytes={self.min_gff_bytes}, "
            f"gff_min_lines={self.min_gff_lines}, sequence_min_bytes={self.min_sequence_bytes}"
        )
        print("=" * 60)

        self.write_log(
            "run_started",
            order=self.order,
            output_dir=str(self.output_dir),
            resource_types=resource_types,
            species_count=len(species_for_download),
            species_list_path=str(self.species_list_path) if self.species_list_path else "",
            resource_dirs={
                resource_type: str(self.resource_dirs[resource_type])
                for resource_type in resource_types
            },
        )

        report_rows: list[dict[str, Any]] = []
        task_index = 0

        for species_name, ibg_id in species_for_download:
            for resource_type in resource_types:
                task_index += 1
                row = self.download_resource(
                    resource_type=resource_type,
                    species_name=species_name,
                    ibg_id=ibg_id,
                    index=task_index,
                    total=total_tasks,
                )
                report_rows.append(row)
                if task_index < total_tasks:
                    time.sleep(0.5)

        report_path = self.save_download_report(report_rows)
        comparison_path = self.save_download_comparison(
            species_rows=species_rows,
            report_rows=report_rows,
            resource_types=resource_types,
        )

        success_rows = [row for row in report_rows if row["status"] == "success"]
        failed_rows = [row for row in report_rows if row["status"] != "success"]
        success_by_resource = Counter(row["resource_type"] for row in success_rows)
        failed_by_resource = Counter(row["resource_type"] for row in failed_rows)
        reason_counter = Counter(row["reason"] for row in failed_rows)

        print("\n" + "=" * 60)
        print("下载完成！")
        print(f"总任务数: {len(report_rows)}")
        print(f"成功: {len(success_rows)}")
        print(f"失败: {len(failed_rows)}")

        for resource_type in resource_types:
            label = RESOURCE_CONFIG[resource_type]["label"]
            print(
                f"  {label}: 成功 {success_by_resource.get(resource_type, 0)} / "
                f"失败 {failed_by_resource.get(resource_type, 0)}"
            )

        if failed_rows:
            print("\n失败原因统计:")
            for reason, count in reason_counter.most_common():
                print(f"  {reason}: {count} 个")

            print("\n失败任务 (前20个):")
            for row in failed_rows[:20]:
                print(
                    f"  - {row['resource_label']} | {row['record_id']} | "
                    f"{row['species']} | {row['reason']}"
                )
            if len(failed_rows) > 20:
                print(f"  ... 还有 {len(failed_rows) - 20} 个")

        print(f"\n下载清单已保存到: {report_path}")
        print(f"下载日志已追加到: {self.download_log_path}")
        print(f"下载汇总已保存到: {comparison_path}")
        for resource_type in resource_types:
            print(
                f"{RESOURCE_CONFIG[resource_type]['label']} 文件目录: "
                f"{self.resource_dirs[resource_type].absolute()}"
            )

        self.write_log(
            "run_completed",
            resource_types=resource_types,
            total_tasks=len(report_rows),
            success=len(success_rows),
            failed=len(failed_rows),
            report_path=str(report_path),
            log_path=str(self.download_log_path),
            comparison_path=str(comparison_path),
        )
        return {"success": len(success_rows), "failed": len(failed_rows)}

    def run(
        self,
        download_gff: bool = True,
        download_protein: bool = True,
        download_cds: bool = True,
        csv_path: str | None = None,
    ) -> None:
        """运行爬虫"""
        if csv_path:
            self.species_list_path = Path(csv_path)
            self.species_list_source = "input_csv"
            species_rows = self.load_species_from_csv(csv_path)
            species_for_download = [
                (row["species"], row.get("ibg_id") or None) for row in species_rows
            ]
            print(f"使用CSV物种列表: {csv_path}")
        else:
            raw_species_list = self.fetch_all_species()
            species_rows = []
            species_for_download: list[tuple[str, str | None]] = []

            for species in raw_species_list:
                info = self.extract_basic_info(species)
                species_rows.append(info)
                if species.get("species"):
                    species_for_download.append(
                        (species["species"], species.get("ibg_id"))
                    )

            self.save_summary(species_rows)
            self.species_list_path = self.save_csv(species_rows)
            self.species_list_source = "fetched_species_info_csv"

        resource_types = []
        if download_gff:
            resource_types.append("gff3")
        if download_protein:
            resource_types.append("protein")
        if download_cds:
            resource_types.append("cds")

        if resource_types:
            print(f"\n有效物种数: {len(species_for_download)}/{len(species_rows)}")
            self.download_all_resources(
                species_rows=species_rows,
                species_for_download=species_for_download,
                resource_types=resource_types,
            )
        else:
            print("未选择任何下载资源，已仅输出物种基础信息。")

    def close(self) -> None:
        """关闭HTTP客户端"""
        self.client.close()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="爬取昆虫基因组数据")
    parser.add_argument(
        "--order",
        default="Lepidoptera",
        help="要爬取的昆虫目(Order)，默认为Lepidoptera（鳞翅目）",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="输出目录，默认为data",
    )
    parser.add_argument(
        "--no-gff",
        action="store_true",
        help="不下载GFF3文件",
    )
    parser.add_argument(
        "--no-protein",
        "--no-genome",
        dest="no_protein",
        action="store_true",
        help="不下载Protein文件（兼容旧参数 --no-genome）",
    )
    parser.add_argument(
        "--no-cds",
        action="store_true",
        help="不下载CDS文件",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="HTTP代理地址，如: http://127.0.0.1:7890 或 socks5://127.0.0.1:1080",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="从CSV读取物种列表（需包含 ibg_id 和 species 列）",
    )
    parser.add_argument(
        "--min-gff-bytes",
        type=int,
        default=10_240,
        help="GFF3文件最小字节数阈值（低于则重试），默认 10240",
    )
    parser.add_argument(
        "--min-gff-lines",
        type=int,
        default=10,
        help="GFF3文件最小行数阈值（低于则重试），默认 10",
    )
    parser.add_argument(
        "--min-sequence-bytes",
        type=int,
        default=1_024,
        help="Protein/CDS 文件最小字节数阈值（低于则重试），默认 1024",
    )

    args = parser.parse_args()

    crawler = InsectGenomeCrawler(
        order=args.order,
        output_dir=args.output_dir,
        proxy=args.proxy,
        min_gff_bytes=args.min_gff_bytes,
        min_gff_lines=args.min_gff_lines,
        min_sequence_bytes=args.min_sequence_bytes,
    )
    try:
        crawler.run(
            download_gff=not args.no_gff,
            download_protein=not args.no_protein,
            download_cds=not args.no_cds,
            csv_path=args.csv,
        )
    finally:
        crawler.close()


if __name__ == "__main__":
    main()