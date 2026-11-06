"""환경 변수(.env) 기반 설정."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    storage_backend: str = "s3"  # "s3" | "local"
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "productivity"
    s3_use_ssl: bool = False
    local_data_dir: Path = PROJECT_ROOT / "data"

    timezone: str = "Asia/Seoul"
    git_repo_roots: list[Path] = field(default_factory=list)
    git_author_emails: list[str] = field(default_factory=list)
    # 커밋 기반 작업 시간 추정 파라미터
    work_max_gap_min: int = 120
    work_first_commit_min: int = 30

    # ActivityWatch 연동(선택, 기본 꺼짐)
    enable_activitywatch: bool = False
    activitywatch_url: str = "http://localhost:5600"
    coding_apps: list[str] = field(default_factory=list)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def work_params(self):
        from .analytics.queries import WorkParams

        return WorkParams(self.work_max_gap_min, self.work_first_commit_min)

    @property
    def dataset_root(self) -> str:
        """DuckDB/pyarrow 에서 사용할 데이터셋 루트 URI."""
        if self.storage_backend == "s3":
            return f"s3://{self.s3_bucket}"
        return str(self.local_data_dir)

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
        e = os.environ.get
        local_dir = Path(e("LOCAL_DATA_DIR", "./data")).expanduser()
        if not local_dir.is_absolute():
            local_dir = PROJECT_ROOT / local_dir
        return cls(
            storage_backend=e("STORAGE_BACKEND", "s3").lower(),
            s3_endpoint=e("S3_ENDPOINT", "localhost:9000"),
            s3_access_key=e("MINIO_ROOT_USER", "minioadmin"),
            s3_secret_key=e("MINIO_ROOT_PASSWORD", "minioadmin"),
            s3_bucket=e("S3_BUCKET", "productivity"),
            s3_use_ssl=_bool(e("S3_USE_SSL"), False),
            local_data_dir=local_dir,
            timezone=e("TIMEZONE", "Asia/Seoul"),
            git_repo_roots=[Path(p).expanduser() for p in _list(e("GIT_REPO_ROOTS", "~/IdeaProjects"))],
            git_author_emails=[x.lower() for x in _list(e("GIT_AUTHOR_EMAILS"))],
            work_max_gap_min=int(e("WORK_MAX_GAP_MIN", "120")),
            work_first_commit_min=int(e("WORK_FIRST_COMMIT_MIN", "30")),
            enable_activitywatch=_bool(e("ENABLE_ACTIVITYWATCH"), False),
            activitywatch_url=e("ACTIVITYWATCH_URL", "http://localhost:5600").rstrip("/"),
            coding_apps=[x.lower() for x in _list(e("CODING_APPS"))],
        )
