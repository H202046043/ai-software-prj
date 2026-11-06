"""수집 → 정제 → 적재 파이프라인."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .collectors.aw_collector import ActivityWatchClient, ActivityWatchUnavailable, collect_activity_day
from .collectors.git_collector import collect_commits, discover_repos
from .config import Settings
from .schema import ACTIVITY, COMMITS
from .storage import ensure_bucket, write_daily

log = logging.getLogger(__name__)


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def run(settings: Settings, start: date, end: date, *, git: bool = True, activity: bool = True) -> dict:
    if start > end:
        raise ValueError("start 가 end 보다 늦습니다")
    ensure_bucket(settings)
    days = date_range(start, end)
    tz = settings.tz
    summary: dict = {"start": start.isoformat(), "end": end.isoformat()}

    if git:
        commits = collect_commits(settings.git_repo_roots, start, end, tz, settings.git_author_emails)
        summary["commits"] = sum(write_daily(settings, COMMITS, commits, days).values())

    if activity and settings.enable_activitywatch:
        client = ActivityWatchClient(settings.activitywatch_url)
        known = [p.name for p in discover_repos(settings.git_repo_roots)]
        rows: list[dict] = []
        try:
            for d in days:
                rows.extend(collect_activity_day(client, d, tz, settings.coding_apps, known))
        except ActivityWatchUnavailable as exc:
            # 기존 데이터를 빈 파일로 덮어쓰지 않도록 적재하지 않고 넘어간다.
            log.error("%s — 활동 데이터 적재를 건너뜁니다", exc)
            summary["activity"] = "skipped"
        else:
            summary["activity"] = sum(write_daily(settings, ACTIVITY, rows, days).values())

    return summary
