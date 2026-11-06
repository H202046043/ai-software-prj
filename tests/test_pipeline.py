from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from productivity import pipeline
from productivity.analytics import queries as q
from productivity.analytics.db import connect
from productivity.api import main as api
from productivity.collectors import aw_collector as aw
from productivity.collectors.git_collector import collect_commits, parse_git_log, resolve_author_emails
from productivity.config import Settings

KST = ZoneInfo("Asia/Seoul")


def git(repo: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    repo = root / "demo_app"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    import os

    def commit(msg: str, when: str, content: str):
        (repo / "a.txt").write_text(content)
        git(repo, "add", ".")
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
               "GIT_AUTHOR_NAME": "me", "GIT_AUTHOR_EMAIL": "me@example.com",
               "GIT_COMMITTER_NAME": "me", "GIT_COMMITTER_EMAIL": "me@example.com"}
        git(repo, "commit", "-q", "-m", msg, env=env)

    commit("first", "2026-11-02T10:15:00+09:00", "1\n2\n")
    commit("second", "2026-11-02T23:50:00+09:00", "1\n2\n3\n")
    commit("third", "2026-11-03T00:45:00+09:00", "1\n")
    commit("old", "2026-10-27T14:00:00+09:00", "x\n")
    (root / "not_a_repo").mkdir()
    subprocess.run(["git", "clone", "-q", str(repo), str(root / "demo_copy")], check=True, capture_output=True)
    return root


def fake_events(day: date) -> list[dict]:
    base = datetime(day.year, day.month, day.day, 10, 0, tzinfo=KST)
    return [
        {"timestamp": base.astimezone(timezone.utc).isoformat(), "duration": 3600,
         "data": {"app": "IntelliJ IDEA", "title": "demo_app – main.py"}},
        {"timestamp": (base + timedelta(hours=1)).astimezone(timezone.utc).isoformat(), "duration": 1800,
         "data": {"app": "Code", "title": "index.ts — web_front"}},
        {"timestamp": (base + timedelta(hours=2)).astimezone(timezone.utc).isoformat(), "duration": 600,
         "data": {"app": "Google Chrome", "title": "docs"}},
        {"timestamp": (base + timedelta(hours=13, minutes=30)).astimezone(timezone.utc).isoformat(), "duration": 3600,
         "data": {"app": "IntelliJ IDEA", "title": "demo_app – late.py"}},
    ]


class FakeAW:
    def buckets(self):
        return {
            "aw-watcher-window_mac": {"type": "currentwindow", "hostname": "mac"},
            "aw-watcher-afk_mac": {"type": "afkstatus", "hostname": "mac"},
        }

    def query(self, timeperiods, query):
        start = datetime.fromisoformat(timeperiods[0].split("/")[0])
        return [fake_events(start.date())]


def set_global_email(tmp_path: Path, monkeypatch, email: str | None) -> None:
    cfg = tmp_path / "gitconfig"
    cfg.write_text(f"[user]\n\temail = {email}\n" if email else "")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))


@pytest.fixture
def settings(tmp_path: Path, repo_root: Path, monkeypatch) -> Settings:
    set_global_email(tmp_path, monkeypatch, "me@example.com")
    monkeypatch.setattr(aw, "ActivityWatchClient", lambda url: FakeAW())
    monkeypatch.setattr(pipeline, "ActivityWatchClient", lambda url: FakeAW())
    return Settings(
        storage_backend="local",
        local_data_dir=tmp_path / "data",
        timezone="Asia/Seoul",
        git_repo_roots=[repo_root],
        coding_apps=["idea", "code"],
    )


def test_parse_git_log_handles_binary_and_merge():
    out = ("\x1eabc\x1fme\x1fMe@X.com\x1f2026-11-02T10:00:00+09:00\x1fp1 p2\x1fmerge it\n"
           "3\t1\ta.py\n-\t-\timg.png\n")
    [r] = parse_git_log(out, "repo", KST)
    assert r["is_merge"] and r["files_changed"] == 2 and r["insertions"] == 3 and r["deletions"] == 1
    assert r["author_email"] == "me@x.com" and r["local_hour"] == 10 and r["weekday"] == 0


def test_collect_commits_uses_local_date(repo_root):
    rows = collect_commits([repo_root], date(2026, 11, 2), date(2026, 11, 2), KST)
    assert sorted(r["message"] for r in rows) == ["first", "second"]
    assert {r["repo"] for r in rows} == {"demo_app"}


def test_collect_commits_author_filter(repo_root):
    rows = collect_commits([repo_root], date(2026, 10, 1), date(2026, 11, 30), KST, ["someone@else.com"])
    assert rows == []
    rows = collect_commits([repo_root], date(2026, 10, 1), date(2026, 11, 30), KST, ["me@example.com"])
    assert len(rows) == 4


def test_resolve_author_emails(tmp_path, monkeypatch):
    set_global_email(tmp_path, monkeypatch, "Me@Example.com")
    assert resolve_author_emails(["school@x.ac.kr"]) == ["school@x.ac.kr"]
    assert resolve_author_emails([]) == ["me@example.com"]
    set_global_email(tmp_path, monkeypatch, None)
    assert resolve_author_emails([]) == []


def test_pipeline_uses_global_email_only_when_env_empty(settings, tmp_path, monkeypatch):
    set_global_email(tmp_path, monkeypatch, "other@example.com")
    s = pipeline.run(settings, date(2026, 11, 2), date(2026, 11, 3), activity=False)
    assert s["authors"] == ["other@example.com"] and s["commits"] == 0
    s = pipeline.run(replace(settings, git_author_emails=["me@example.com"]),
                     date(2026, 11, 2), date(2026, 11, 3), activity=False)
    assert s["authors"] == ["me@example.com"] and s["commits"] == 3


def test_extract_project():
    assert aw.extract_project("IntelliJ IDEA", "my-proj [~/x] – a.kt", []) == "my-proj"
    assert aw.extract_project("Code", "a.ts — web — Visual Studio Code", []) == "web"
    assert aw.extract_project("Google Chrome", "github.com/me/demo_app", ["demo_app"]) == "demo_app"
    assert aw.extract_project("Google Chrome", "demo_application", ["demo_app"]) is None


def test_full_pipeline_and_queries(settings):
    result = pipeline.run(settings, date(2026, 10, 26), date(2026, 11, 8))
    assert result["commits"] == 4
    assert "activity" not in result

    files = sorted((settings.local_data_dir / "commits").rglob("*.parquet"))
    assert len(files) == 3
    assert files[0].parent.name == "month=10" and files[-1].parent.name == "month=11"

    pipeline.run(settings, date(2026, 11, 2), date(2026, 11, 3))
    con = connect(settings)
    assert con.execute("SELECT DISTINCT typeof(year), typeof(month) FROM commits").fetchall() == [("BIGINT", "BIGINT")]

    s = q.summary(con, date(2026, 11, 2), date(2026, 11, 8))
    assert s["commits"] == 3
    assert s["work_hours"] == pytest.approx(round(115 / 60, 2))
    assert s["work_days"] == 2 and s["top_project"] == "demo_app"

    daily = {r["date"]: r["work_min"] for r in q.daily(con, date(2026, 11, 2), date(2026, 11, 3))}
    assert daily == {date(2026, 11, 2): 70.0, date(2026, 11, 3): 45.0}

    hourly = {r["hour"]: r for r in q.hourly(con, date(2026, 11, 2), date(2026, 11, 2))}
    assert hourly[10]["commits"] == 1 and hourly[23]["commits"] == 1
    assert (hourly[9]["work_min"], hourly[10]["work_min"], hourly[23]["work_min"]) == (15.0, 15.0, 40.0)

    assert q.summary(con, date(2026, 11, 3), date(2026, 11, 3))["work_hours"] == pytest.approx(0.75)
    tight = q.WorkParams(max_gap_min=30, first_commit_min=30)
    assert q.summary(con, date(2026, 11, 3), date(2026, 11, 3), tight)["work_hours"] == pytest.approx(0.5)

    projects = {r["project"]: r for r in q.projects(con, date(2026, 11, 2), date(2026, 11, 8))}
    assert projects["demo_app"]["commits"] == 3 and projects["demo_app"]["work_share_pct"] == 100.0

    wow = q.week_over_week(con, date(2026, 11, 5))
    assert wow["metrics"]["commits"]["this_week"] == 3
    assert wow["metrics"]["commits"]["last_week"] == 1
    assert wow["metrics"]["commits"]["change_pct"] == 200.0
    assert wow["metrics"]["work_hours"]["last_week"] == 0.5

    assert q.partition_filter(date(2026, 12, 30), date(2027, 1, 2)) == \
        "((year = 2026 AND month IN (12)) OR (year = 2027 AND month IN (1)))"


def test_activitywatch_optional_and_down_does_not_overwrite(settings, monkeypatch):
    settings = replace(settings, enable_activitywatch=True)
    result = pipeline.run(settings, date(2026, 11, 2), date(2026, 11, 2))
    assert result["activity"] == 4

    class Down:
        def buckets(self):
            raise aw.ActivityWatchUnavailable("down")

    monkeypatch.setattr(pipeline, "ActivityWatchClient", lambda url: Down())
    result = pipeline.run(settings, date(2026, 11, 2), date(2026, 11, 2))
    assert result["activity"] == "skipped"
    con = connect(settings)
    assert con.execute("SELECT sum(duration_sec) FILTER (WHERE is_coding) FROM activity").fetchall()[0][0] == 7200


def test_empty_storage_queries_work(settings):
    con = connect(settings)
    assert q.summary(con, date(2026, 11, 1), date(2026, 11, 7))["commits"] == 0
    assert len(q.daily(con, date(2026, 11, 1), date(2026, 11, 7))) == 7


def test_api(settings):
    pipeline.run(settings, date(2026, 11, 2), date(2026, 11, 8))
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    client = TestClient(api.app)
    try:
        r = client.get("/api/summary", params={"start": "2026-11-02", "end": "2026-11-08"})
        assert r.status_code == 200 and r.json()["commits"] == 3
        assert r.json()["work_hours"] == pytest.approx(round(115 / 60, 2))
        for path in ["daily", "hourly", "weekday", "heatmap", "projects", "commits"]:
            assert client.get(f"/api/{path}", params={"start": "2026-11-02", "end": "2026-11-08"}).status_code == 200
        assert client.get("/api/week-over-week", params={"ref": "2026-11-05"}).status_code == 200
        assert client.get("/api/config").json()["work_max_gap_min"] == 120
        assert client.get("/api/daily", params={"start": "2026-11-08", "end": "2026-11-02"}).status_code == 400
        assert client.get("/").status_code == 200
    finally:
        api.app.dependency_overrides.clear()
