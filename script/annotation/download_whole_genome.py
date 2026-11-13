from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import httpx
from tqdm import tqdm

BASE_URL = "https://www.insect-genome.com"
DETAIL_PAGE_TEMPLATE = f"{BASE_URL}/genome/{{ibg_id}}"
DETAIL_API_TEMPLATE = f"{BASE_URL}/api/genome/genomes/{{ibg_id}}/?format=json"
GENOME_FILE_SUFFIX = ".genome.fa.tar.bz2"
GENOME_URL_TEMPLATE = f"{BASE_URL}/data/genome/{{species_filename}}/{{species_filename}}{GENOME_FILE_SUFFIX}"
IBG_ID_PATTERN = re.compile(r"(IBG_\d+)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="根据 InsectBase genome 详情页 URL 或 IBG ID 下载 whole genome 文件"
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="一个或多个 IBG ID，或 https://www.insect-genome.com/genome/IBG_xxxxx 形式的 URL",
    )
    parser.add_argument(
        "--input-file",
        help="可选，文本文件；每行一个 IBG ID 或详情页 URL，支持空行和 # 注释",
    )
    parser.add_argument(
        "--output-dir",
        default="data/genome_whole",
        help="输出目录，默认 data/genome_whole",
    )
    parser.add_argument(
        "--proxy",
        help="HTTP 或 SOCKS 代理，例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="如果目标文件已存在则覆盖重下",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析物种信息、下载 URL 和输出文件名，不实际下载文件",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="单次请求超时时间（秒），默认 180",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1024 * 1024,
        help="流式下载分块大小（字节），默认 1048576",
    )
    parser.add_argument(
        "--min-bytes",
        type=int,
        default=1024,
        help="下载完成后的最小文件大小校验阈值，默认 1024",
    )
    return parser.parse_args()


def normalize_species_filename(species_name: str) -> str:
    return species_name.strip().replace(" ", "_")


def extract_ibg_id(raw_value: str) -> str:
    match = IBG_ID_PATTERN.search(raw_value.strip())
    if not match:
        raise ValueError(f"无法从输入中解析 IBG ID: {raw_value}")
    return match.group(1).upper()


def load_targets(cli_targets: list[str], input_file: str | None) -> list[str]:
    raw_targets = list(cli_targets)
    if input_file:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"输入文件不存在: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw_targets.append(line)

    if not raw_targets:
        raise ValueError("请至少提供一个 IBG ID、详情页 URL，或使用 --input-file")

    seen: set[str] = set()
    normalized_targets: list[str] = []
    for raw in raw_targets:
        ibg_id = extract_ibg_id(raw)
        if ibg_id in seen:
            continue
        seen.add(ibg_id)
        normalized_targets.append(ibg_id)
    return normalized_targets


def ensure_valid_detail_page(client: httpx.Client, ibg_id: str) -> str:
    url = DETAIL_PAGE_TEMPLATE.format(ibg_id=ibg_id)
    response = client.get(url)
    response.raise_for_status()
    return url


def fetch_species_name(client: httpx.Client, ibg_id: str) -> str:
    response = client.get(DETAIL_API_TEMPLATE.format(ibg_id=ibg_id))
    response.raise_for_status()
    data = response.json()
    species_name = str(data.get("species") or "").strip()
    if not species_name:
        raise ValueError(f"{ibg_id} 的详情接口返回里缺少 species 字段")
    return species_name


def build_genome_url(species_name: str) -> str:
    species_filename = normalize_species_filename(species_name)
    return GENOME_URL_TEMPLATE.format(species_filename=species_filename)


def validate_downloaded_file(path: Path, min_bytes: int) -> tuple[bool, str]:
    if not path.exists():
        return False, "file_missing"

    size_bytes = path.stat().st_size
    if size_bytes < min_bytes:
        return False, f"size<{min_bytes}"

    try:
        header = path.read_bytes()[:2048]
    except OSError as exc:
        return False, f"read_error:{type(exc).__name__}"

    text_header = header.decode("utf-8", errors="ignore").strip().lower()
    if text_header.startswith("<html") or text_header.startswith("<!doctype") or "<html" in text_header:
        return False, "html_response"

    if header[:3] != b"BZh":
        return False, "unexpected_archive_header"

    return True, "ok"


def stream_download(
    client: httpx.Client,
    url: str,
    output_path: Path,
    chunk_size: int,
) -> None:
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    if tmp_path.exists():
        tmp_path.unlink()

    with client.stream("GET", url) as response:
        response.raise_for_status()
        total_bytes = int(response.headers.get("content-length") or 0)
        progress = tqdm(
            total=total_bytes if total_bytes > 0 else None,
            unit="B",
            unit_scale=True,
            desc=output_path.name,
        )
        try:
            with tmp_path.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    progress.update(len(chunk))
        finally:
            progress.close()

    tmp_path.replace(output_path)


def iter_download_jobs(client: httpx.Client, ibg_ids: Iterable[str]) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for ibg_id in ibg_ids:
        detail_page_url = ensure_valid_detail_page(client, ibg_id)
        species_name = fetch_species_name(client, ibg_id)
        species_filename = normalize_species_filename(species_name)
        jobs.append(
            {
                "ibg_id": ibg_id,
                "species_name": species_name,
                "species_filename": species_filename,
                "detail_page_url": detail_page_url,
                "download_url": build_genome_url(species_name),
                "filename": f"{ibg_id}{GENOME_FILE_SUFFIX}",
            }
        )
    return jobs


def main() -> int:
    args = parse_args()

    try:
        ibg_ids = load_targets(args.targets, args.input_file)
    except Exception as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(
        timeout=args.timeout,
        follow_redirects=True,
        proxy=args.proxy if args.proxy else None,
    )

    try:
        jobs = iter_download_jobs(client, ibg_ids)
        print(f"共解析到 {len(jobs)} 个目标")
        for job in jobs:
            print(
                f"- {job['ibg_id']} -> {job['species_name']} -> {job['download_url']} -> {job['filename']}"
            )

        if args.dry_run:
            print("\n已启用 --dry-run，不执行实际下载。")
            return 0

        failed = 0
        for index, job in enumerate(jobs, start=1):
            output_path = output_dir / job["filename"]
            print(f"\n[{index}/{len(jobs)}] {job['ibg_id']} | {job['species_name']}")
            print(f"详情页: {job['detail_page_url']}")
            print(f"下载链接: {job['download_url']}")
            print(f"输出文件: {output_path}")

            if output_path.exists() and not args.overwrite:
                is_valid, reason = validate_downloaded_file(output_path, args.min_bytes)
                if is_valid:
                    print("文件已存在且校验通过，跳过下载。")
                    continue
                print(f"已存在文件校验失败（{reason}），将重新下载。")
                output_path.unlink()

            try:
                stream_download(
                    client=client,
                    url=job["download_url"],
                    output_path=output_path,
                    chunk_size=args.chunk_size,
                )
                is_valid, reason = validate_downloaded_file(output_path, args.min_bytes)
                if not is_valid:
                    output_path.unlink(missing_ok=True)
                    raise RuntimeError(f"下载完成但校验失败: {reason}")
                print("下载成功。")
            except Exception as exc:
                failed += 1
                print(f"下载失败: {exc}", file=sys.stderr)

        if failed:
            print(f"\n完成，但有 {failed} 个目标下载失败。", file=sys.stderr)
            return 1

        print(f"\n全部完成，文件已保存到: {output_dir.resolve()}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())