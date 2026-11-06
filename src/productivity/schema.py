import pyarrow as pa

TS = pa.timestamp("us", tz="UTC")

COMMITS = "commits"
ACTIVITY = "activity"

COMMITS_SCHEMA = pa.schema(
    [
        ("commit_hash", pa.string()),
        ("repo", pa.string()),
        ("author_name", pa.string()),
        ("author_email", pa.string()),
        ("authored_at", TS),
        ("local_date", pa.date32()),
        ("local_hour", pa.int8()),
        ("weekday", pa.int8()),
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
        ("project", pa.string()),
        ("is_coding", pa.bool_()),
        ("hostname", pa.string()),
        ("local_date", pa.date32()),
        ("local_hour", pa.int8()),
        ("weekday", pa.int8()),
    ]
)

SCHEMAS = {COMMITS: COMMITS_SCHEMA, ACTIVITY: ACTIVITY_SCHEMA}
