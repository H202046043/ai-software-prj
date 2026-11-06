from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..analytics import queries as q
from ..analytics.db import connect
from ..config import Settings

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="개인 업무 생산성 분석 API", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_settings() -> Settings:
    return Settings.from_env()


def get_con(settings: Settings = Depends(get_settings)) -> Iterator:
    con = connect(settings)
    try:
        yield con
    finally:
        con.close()


def period(
    start: date | None = Query(None, description="시작일(YYYY-MM-DD), 기본: 종료일 29일 전"),
    end: date | None = Query(None, description="종료일(YYYY-MM-DD), 기본: 오늘"),
    settings: Settings = Depends(get_settings),
) -> tuple[date, date]:
    end = end or datetime.now(settings.tz).date()
    start = start or end - timedelta(days=29)
    if start > end:
        raise HTTPException(400, "start 는 end 보다 이전이어야 합니다")
    if (end - start).days > 366 * 3:
        raise HTTPException(400, "조회 기간은 최대 3년입니다")
    return start, end


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config", summary="작업 시간 추정 파라미터")
def api_config(settings: Settings = Depends(get_settings)):
    return {
        "work_max_gap_min": settings.work_max_gap_min,
        "work_first_commit_min": settings.work_first_commit_min,
        "timezone": settings.timezone,
    }


@app.get("/api/summary", summary="기간 요약(커밋 수, 추정 작업 시간 등)")
def api_summary(p=Depends(period), con=Depends(get_con), settings: Settings = Depends(get_settings)):
    return {"start": p[0], "end": p[1], **q.summary(con, *p, settings.work_params)}


@app.get("/api/daily", summary="일별 커밋 수·추정 작업 시간")
def api_daily(p=Depends(period), con=Depends(get_con), settings: Settings = Depends(get_settings)):
    return q.daily(con, *p, settings.work_params)


@app.get("/api/hourly", summary="시간대별(0~23시) 커밋 수·추정 작업 시간")
def api_hourly(p=Depends(period), con=Depends(get_con), settings: Settings = Depends(get_settings)):
    return q.hourly(con, *p, settings.work_params)


@app.get("/api/weekday", summary="요일별 합계")
def api_weekday(p=Depends(period), con=Depends(get_con), settings: Settings = Depends(get_settings)):
    return q.weekday_totals(con, *p, settings.work_params)


@app.get("/api/heatmap", summary="요일 x 시간대 히트맵")
def api_heatmap(p=Depends(period), con=Depends(get_con), settings: Settings = Depends(get_settings)):
    return q.weekday_hour_heatmap(con, *p, settings.work_params)


@app.get("/api/projects", summary="프로젝트별 비중")
def api_projects(p=Depends(period), con=Depends(get_con), settings: Settings = Depends(get_settings)):
    return q.projects(con, *p, settings.work_params)


@app.get("/api/commits", summary="최근 커밋")
def api_commits(p=Depends(period), limit: int = Query(20, ge=1, le=200), con=Depends(get_con)):
    return q.recent_commits(con, *p, limit=limit)


@app.get("/api/week-over-week", summary="이번 주 vs 지난 주 증감")
def api_wow(
    ref: date | None = Query(None, description="기준일(해당 주 월~일), 기본: 오늘"),
    settings: Settings = Depends(get_settings),
    con=Depends(get_con),
):
    return q.week_over_week(con, ref or datetime.now(settings.tz).date(), settings.work_params)
