"""DuckDB 연결 및 데이터셋 뷰 구성.

MinIO 의 Parquet 파일을 httpfs 로 직접 읽는다(별도 적재/복사 없음).
- commits, activity 두 개의 뷰를 만들며 year/month 는 Hive 파티션 컬럼이다.
- 아직 파일이 없는 데이터셋은 같은 스키마의 빈 테이블로 대체해 쿼리가 실패하지 않게 한다.
"""
from __future__ import annotations

import duckdb
import pyarrow as pa

from ..config import Settings
from ..schema import SCHEMAS


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def connect(settings: Settings) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(f"SET TimeZone = {_sql_str(settings.timezone)}")
    if settings.storage_backend == "s3":
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(
            f"""
            CREATE OR REPLACE SECRET minio (
                TYPE s3,
                KEY_ID {_sql_str(settings.s3_access_key)},
                SECRET {_sql_str(settings.s3_secret_key)},
                ENDPOINT {_sql_str(settings.s3_endpoint)},
                URL_STYLE 'path',
                USE_SSL {str(settings.s3_use_ssl).lower()},
                REGION 'us-east-1'
            )
            """
        )
    for name, schema in SCHEMAS.items():
        pattern = f"{settings.dataset_root}/{name}/year=*/month=*/*.parquet"
        has_files = con.execute(f"SELECT count(*) FROM glob({_sql_str(pattern)})").fetchall()[0][0] > 0
        if has_files:
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet({_sql_str(pattern)}, "
                # month=06 처럼 0 이 붙은 값은 자동 감지 시 VARCHAR 가 되므로 타입을 명시한다.
                "hive_partitioning = true, hive_types = {'year': BIGINT, 'month': BIGINT}, "
                "union_by_name = true)"
            )
        else:
            empty = pa.Table.from_pylist(
                [], schema=schema.append(pa.field("year", pa.int64())).append(pa.field("month", pa.int64()))
            )
            con.register(f"_{name}_empty", empty)
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM _{name}_empty")
    return con
