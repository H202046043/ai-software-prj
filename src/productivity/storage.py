"""Parquet 적재 (MinIO 또는 로컬 디렉터리).

경로: {dataset}/year=YYYY/month=MM/YYYY-MM-DD.parquet
같은 날짜를 다시 쓰면 덮어쓰므로 재수집해도 중복이 생기지 않는다.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs

from .config import Settings
from .schema import SCHEMAS

log = logging.getLogger(__name__)


def get_filesystem(settings: Settings) -> tuple[fs.FileSystem, str]:
    """(파일시스템, 버킷/루트 경로) 반환."""
    if settings.storage_backend == "s3":
        s3 = fs.S3FileSystem(
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            endpoint_override=settings.s3_endpoint,
            scheme="https" if settings.s3_use_ssl else "http",
            region="us-east-1",
            allow_bucket_creation=True,  # ensure_bucket() 에서 버킷 생성
        )
        return s3, settings.s3_bucket
    if settings.storage_backend == "local":
        settings.local_data_dir.mkdir(parents=True, exist_ok=True)
        return fs.LocalFileSystem(), str(settings.local_data_dir)
    raise ValueError(f"알 수 없는 STORAGE_BACKEND: {settings.storage_backend}")


def partition_path(root: str, dataset: str, day: date) -> str:
    return f"{root}/{dataset}/year={day.year:04d}/month={day.month:02d}/{day.isoformat()}.parquet"


def write_daily(settings: Settings, dataset: str, rows: list[dict], days: list[date]) -> dict[date, int]:
    """rows 를 local_date 기준으로 나눠 날짜별 파일로 쓴다.

    days 에 포함됐지만 데이터가 없는 날짜는 기존 파일을 삭제한다(이전 데이터가 남지 않도록).
    빈 파일을 만들지 않는 이유: 장기간 백필 시 작은 파일이 수천 개 생기면
    S3 목록 조회·파일 열기 비용이 커져 DuckDB 쿼리가 느려진다(small files problem).
    """
    schema = SCHEMAS[dataset]
    filesystem, root = get_filesystem(settings)
    by_day: dict[date, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["local_date"]].append(r)

    written: dict[date, int] = {}
    for day in sorted(set(days) | set(by_day)):
        day_rows = by_day.get(day, [])
        path = partition_path(root, dataset, day)
        if not day_rows:
            if filesystem.get_file_info(path).type == fs.FileType.File:
                filesystem.delete_file(path)
            continue
        table = pa.Table.from_pylist(day_rows, schema=schema)
        filesystem.create_dir(path.rsplit("/", 1)[0], recursive=True)
        pq.write_table(table, path, filesystem=filesystem, compression="zstd")
        written[day] = table.num_rows
    log.info("%s: %d개 파일, 총 %d행 적재", dataset, len(written), sum(written.values()))
    return written


def ensure_bucket(settings: Settings) -> bool:
    """버킷(또는 로컬 루트)이 없으면 만든다. 새로 만들었으면 True.

    MinIO 에 연결할 수 없으면 예외가 발생하므로 연결 확인 용도로도 쓴다.
    """
    filesystem, root = get_filesystem(settings)
    if filesystem.get_file_info(root).type != fs.FileType.NotFound:
        return False
    filesystem.create_dir(root)  # S3FileSystem 에서는 버킷 생성
    log.info("버킷/디렉터리 생성: %s", root)
    return True
