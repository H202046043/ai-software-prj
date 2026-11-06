"""데이터셋 스키마 정의 (Parquet / Hive 파티션: year=YYYY/month=MM).

파일 경로 규칙:
    {root}/{dataset}/year={YYYY}/month={MM}/{YYYY-MM-DD}.parquet

하루 단위 파일로 저장하므로 같은 날짜를 다시 수집하면 해당 파일만 덮어써진다(멱등성).
year/month 는 경로에서 파생되는 파티션 컬럼이라 파일 안에는 저장하지 않는다.
"""
import pyarrow as pa

TS = pa.timestamp("us", tz="UTC")

COMMITS = "commits"
ACTIVITY = "activity"

COMMITS_SCHEMA = pa.schema(
    [
        ("commit_hash", pa.string()),
        ("repo", pa.string()),            # 저장소 디렉터리 이름 = 프로젝트 이름
        ("author_name", pa.string()),
        ("author_email", pa.string()),
        ("authored_at", TS),               # 작성 시각(UTC)
        ("local_date", pa.date32()),       # 설정 타임존 기준 날짜
        ("local_hour", pa.int8()),         # 0~23
        ("weekday", pa.int8()),            # 0=월 ~ 6=일
        ("is_merge", pa.bool_()),
        ("files_changed", pa.int32()),
        ("insertions", pa.int32()),
        ("deletions", pa.int32()),
        ("message", pa.string()),
    ]
)

ACTIVITY_SCHEMA = pa.schema(
    [
        ("event_start", TS),
        ("event_end", TS),
        ("duration_sec", pa.float64()),
        ("app", pa.string()),
        ("title", pa.string()),
        ("project", pa.string()),          # 창 제목에서 추출(없으면 null)
        ("is_coding", pa.bool_()),         # CODING_APPS 에 해당하는 앱인지
        ("hostname", pa.string()),
        ("local_date", pa.date32()),
        ("local_hour", pa.int8()),
        ("weekday", pa.int8()),
    ]
)

SCHEMAS = {COMMITS: COMMITS_SCHEMA, ACTIVITY: ACTIVITY_SCHEMA}
