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
