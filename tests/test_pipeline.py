"""수집 → 적재 → 분석 → API 전 구간 테스트 (로컬 저장소 백엔드 + 가짜 ActivityWatch)."""
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
from productivity.collectors.git_collector import collect_commits, parse_git_log
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

    commit("first", "2026-11-02T10:15:00+09:00", "1\n2\n")        # 월 10시
    commit("second", "2026-11-02T23:50:00+09:00", "1\n2\n3\n")    # 월 23시
    commit("third", "2026-11-03T00:45:00+09:00", "1\n")           # 화 0시 (UTC 로는 11/2), 직전과 55분 간격
    commit("old", "2026-10-27T14:00:00+09:00", "x\n")             # 지난 주 월
    (root / "not_a_repo").mkdir()
    # 히스토리를 공유하는 복사본 저장소: 같은 커밋이 두 번 집계되면 안 됨
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
        # 자정을 넘는 이벤트: 해당 날짜 몫(30분)만 남아야 함
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


@pytest.fixture
def settings(tmp_path: Path, repo_root: Path, monkeypatch) -> Settings:
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
    # 복사본 저장소(demo_copy)의 같은 커밋은 제외되고, 이름순으로 먼저인 demo_app 에 귀속
    assert {r["repo"] for r in rows} == {"demo_app"}


def test_collect_commits_author_filter(repo_root):
    rows = collect_commits([repo_root], date(2026, 10, 1), date(2026, 11, 30), KST, ["someone@else.com"])
    assert rows == []
    rows = collect_commits([repo_root], date(2026, 10, 1), date(2026, 11, 30), KST, ["me@example.com"])
    assert len(rows) == 4


def test_extract_project():
    assert aw.extract_project("IntelliJ IDEA", "my-proj [~/x] – a.kt", []) == "my-proj"
    assert aw.extract_project("Code", "a.ts — web — Visual Studio Code", []) == "web"
    assert aw.extract_project("Google Chrome", "github.com/me/demo_app", ["demo_app"]) == "demo_app"
    assert aw.extract_project("Google Chrome", "demo_application", ["demo_app"]) is None


def test_full_pipeline_and_queries(settings):
    result = pipeline.run(settings, date(2026, 10, 26), date(2026, 11, 8))
    assert result["commits"] == 4
    assert "activity" not in result  # ActivityWatch 는 기본 비활성

    files = sorted((settings.local_data_dir / "commits").rglob("*.parquet"))
    assert len(files) == 3  # 커밋이 있는 날(10/27, 11/2, 11/3)만 파일 생성
    assert files[0].parent.name == "month=10" and files[-1].parent.name == "month=11"

    # 재실행해도 중복 없음(멱등)
    pipeline.run(settings, date(2026, 11, 2), date(2026, 11, 3))
    con = connect(settings)
    # 파티션 컬럼은 정수여야 프루닝 조건(month IN (...))이 정확히 걸린다 (month=0X 문자열 감지 방지)
    assert con.execute("SELECT DISTINCT typeof(year), typeof(month) FROM commits").fetchall() == [("BIGINT", "BIGINT")]

    # 작업 시간 추정 (최대 간격 120분, 세션 첫 커밋 30분)
    #  11/2 10:15  직전(10/27)과 간격 큼 → 09:45~10:15 (9시 15분 + 10시 15분)
    #  11/2 23:50  간격 큼               → 23:20~23:50 (23시 30분)
    #  11/3 00:45  직전과 55분           → 23:50~00:45 (11/2 23시 10분 + 11/3 0시 45분)
    s = q.summary(con, date(2026, 11, 2), date(2026, 11, 8))
    assert s["commits"] == 3
    assert s["work_hours"] == pytest.approx(round(115 / 60, 2))
    assert s["work_days"] == 2 and s["top_project"] == "demo_app"

    daily = {r["date"]: r["work_min"] for r in q.daily(con, date(2026, 11, 2), date(2026, 11, 3))}
    assert daily == {date(2026, 11, 2): 70.0, date(2026, 11, 3): 45.0}

    hourly = {r["hour"]: r for r in q.hourly(con, date(2026, 11, 2), date(2026, 11, 2))}
    assert hourly[10]["commits"] == 1 and hourly[23]["commits"] == 1
    assert (hourly[9]["work_min"], hourly[10]["work_min"], hourly[23]["work_min"]) == (15.0, 15.0, 40.0)

    # 기간 시작일 하나만 조회해도 전날 커밋을 보고 세션을 이어 붙인다 (경계 처리)
    assert q.summary(con, date(2026, 11, 3), date(2026, 11, 3))["work_hours"] == pytest.approx(0.75)
    # 파라미터 변경: 최대 간격 30분이면 00:45 커밋은 새 세션 → 00:15~00:45
    tight = q.WorkParams(max_gap_min=30, first_commit_min=30)
    assert q.summary(con, date(2026, 11, 3), date(2026, 11, 3), tight)["work_hours"] == pytest.approx(0.5)

    projects = {r["project"]: r for r in q.projects(con, date(2026, 11, 2), date(2026, 11, 8))}
    assert projects["demo_app"]["commits"] == 3 and projects["demo_app"]["work_share_pct"] == 100.0

    wow = q.week_over_week(con, date(2026, 11, 5))
    assert wow["metrics"]["commits"]["this_week"] == 3
    assert wow["metrics"]["commits"]["last_week"] == 1
    assert wow["metrics"]["commits"]["change_pct"] == 200.0
    assert wow["metrics"]["work_hours"]["last_week"] == 0.5

    # 월 경계를 걸치는 기간 (파티션 프루닝 조건)
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
    # 기존 활동 데이터(코딩 1h + 0.5h + 자정 전 30분 = 2h)가 보존됨
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
