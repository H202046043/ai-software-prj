from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

JETBRAINS_SEP = " – "
VSCODE_SEP = " — "
VSCODE_APPS = ("code", "cursor", "visual studio code")


class ActivityWatchUnavailable(RuntimeError):
    pass


class ActivityWatchClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str):
        try:
            r = requests.get(f"{self.base_url}{path}", timeout=self.timeout)
        except requests.ConnectionError as exc:
            raise ActivityWatchUnavailable(f"ActivityWatch 서버에 연결할 수 없음: {self.base_url}") from exc
        r.raise_for_status()
        return r.json()

    def buckets(self) -> dict:
        return self._get("/api/0/buckets/")

    def query(self, timeperiods: list[str], query: list[str]) -> list:
        try:
            r = requests.post(
                f"{self.base_url}/api/0/query/",
                json={"timeperiods": timeperiods, "query": query},
                timeout=self.timeout,
            )
        except requests.ConnectionError as exc:
            raise ActivityWatchUnavailable(f"ActivityWatch 서버에 연결할 수 없음: {self.base_url}") from exc
        r.raise_for_status()
        return r.json()


def find_host_buckets(buckets: dict) -> list[tuple[str, str, str]]:
    by_host: dict[str, dict[str, str]] = {}
    for bid, meta in buckets.items():
        host = meta.get("hostname", "unknown")
        if meta.get("type") == "currentwindow":
            by_host.setdefault(host, {})["window"] = bid
        elif meta.get("type") == "afkstatus":
            by_host.setdefault(host, {})["afk"] = bid
    result = []
    for host, b in by_host.items():
        if "window" in b and "afk" in b:
            result.append((host, b["window"], b["afk"]))
        else:
            log.warning("호스트 %s 에 window/afk 버킷이 모두 있지 않아 건너뜀: %s", host, b)
    return result


def build_query(window_bucket: str, afk_bucket: str) -> list[str]:
    return [
        f"afk = flood(query_bucket({json.dumps(afk_bucket)}));",
        f"win = flood(query_bucket({json.dumps(window_bucket)}));",
        'win = filter_period_intersect(win, filter_keyvals(afk, "status", ["not-afk"]));',
        "RETURN = win;",
    ]


def is_coding_app(app: str, coding_apps: list[str]) -> bool:
    a = app.lower()
    return any(token in a for token in coding_apps)


def extract_project(app: str, title: str, known_projects: list[str]) -> str | None:
    if not title:
        return None
    for name in sorted(known_projects, key=len, reverse=True):
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", title):
            return name

    a = app.lower()
    if JETBRAINS_SEP in title and not any(v in a for v in VSCODE_APPS):
        head = title.split(JETBRAINS_SEP)[0]
        return re.sub(r"\s*\[.*?\]\s*$", "", head).strip() or None
    if VSCODE_SEP in title and any(v in a for v in VSCODE_APPS):
        parts = [p.strip() for p in title.split(VSCODE_SEP)]
        parts = [p for p in parts if p.lower() not in ("visual studio code", "cursor")]
        if len(parts) >= 2:
            return parts[-1] or None
    return None


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def normalize_events(
    events: list[dict], day: date, tz: ZoneInfo, hostname: str,
    coding_apps: list[str], known_projects: list[str],
) -> list[dict]:
    day_start, day_end = _day_bounds(day, tz)
    rows = []
    for ev in events:
        start = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
        end = start + timedelta(seconds=float(ev.get("duration", 0)))
        start, end = max(start, day_start), min(end, day_end)
        dur = (end - start).total_seconds()
        if dur <= 0:
            continue
        data = ev.get("data", {})
        app = data.get("app", "") or ""
        title = data.get("title", "") or ""
        local = start.astimezone(tz)
        rows.append(
            {
                "event_start": start.astimezone(timezone.utc),
                "event_end": end.astimezone(timezone.utc),
                "duration_sec": dur,
                "app": app,
                "title": title,
                "project": extract_project(app, title, known_projects),
                "is_coding": is_coding_app(app, coding_apps),
                "hostname": hostname,
                "local_date": local.date(),
                "local_hour": local.hour,
                "weekday": local.weekday(),
            }
        )
    return rows


def collect_activity_day(
    client: ActivityWatchClient, day: date, tz: ZoneInfo,
    coding_apps: list[str], known_projects: list[str],
) -> list[dict]:
    hosts = find_host_buckets(client.buckets())
    if not hosts:
        log.warning("ActivityWatch 에 window/afk 버킷이 없음 (watcher 실행 여부 확인)")
        return []
    day_start, day_end = _day_bounds(day, tz)
    period = f"{day_start.isoformat()}/{day_end.isoformat()}"
    rows: list[dict] = []
    for host, win_b, afk_b in hosts:
        result = client.query([period], build_query(win_b, afk_b))
        events = result[0] if result else []
        rows.extend(normalize_events(events, day, tz, host, coding_apps, known_projects))
    log.info("%s: 활동 이벤트 %d건 (코딩 %.1f분)", day, len(rows),
             sum(r["duration_sec"] for r in rows if r["is_coding"]) / 60)
    return rows
