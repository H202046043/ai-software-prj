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
    if settings.storage_backend == "s3":
        s3 = fs.S3FileSystem(
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            endpoint_override=settings.s3_endpoint,
            scheme="https" if settings.s3_use_ssl else "http",
            region="us-east-1",
            allow_bucket_creation=True,
        )
        return s3, settings.s3_bucket
    if settings.storage_backend == "local":
        settings.local_data_dir.mkdir(parents=True, exist_ok=True)
        return fs.LocalFileSystem(), str(settings.local_data_dir)
    raise ValueError(f"알 수 없는 STORAGE_BACKEND: {settings.storage_backend}")


def partition_path(root: str, dataset: str, day: date) -> str:
    return f"{root}/{dataset}/year={day.year:04d}/month={day.month:02d}/{day.isoformat()}.parquet"


def write_daily(settings: Settings, dataset: str, rows: list[dict], days: list[date]) -> dict[date, int]:
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
    filesystem, root = get_filesystem(settings)
    if filesystem.get_file_info(root).type != fs.FileType.NotFound:
        return False
    filesystem.create_dir(root)
    log.info("버킷/디렉터리 생성: %s", root)
    return True
