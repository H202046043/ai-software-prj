"""명령행 진입점.

    productivity init                            # MinIO 버킷 생성
    productivity collect                         # 어제 하루 수집·적재 (Cron 용 기본값)
    productivity collect --date today
    productivity collect --start 2026-10-01 --end 2026-11-06   # 기간 백필
    productivity collect --no-activity           # Git 만
    productivity query summary --days 30         # 분석 결과를 터미널에 출력
    productivity serve                           # API + 대시보드 (http://localhost:8000)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta

from .config import Settings


def _parse_day(value: str, today: date) -> date:
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    return date.fromisoformat(value)


def cmd_collect(args, settings: Settings) -> int:
    from . import pipeline

    today = datetime.now(settings.tz).date()
    if args.start or args.end:
        start = _parse_day(args.start or args.end, today)
        end = _parse_day(args.end or args.start, today)
    else:
        start = end = _parse_day(args.date, today)
    result = pipeline.run(settings, start, end, git=not args.no_git, activity=not args.no_activity)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_init(args, settings: Settings) -> int:
    from .storage import ensure_bucket

    created = ensure_bucket(settings)
    print(f"{settings.dataset_root}: {'생성됨' if created else '이미 존재'}")
    return 0


def cmd_query(args, settings: Settings) -> int:
    from .analytics import queries as q
    from .analytics.db import connect

    end = datetime.now(settings.tz).date()
    start = end - timedelta(days=args.days - 1)
    con = connect(settings)
    wp = settings.work_params
    funcs = {
        "summary": lambda: q.summary(con, start, end, wp),
        "daily": lambda: q.daily(con, start, end, wp),
        "hourly": lambda: q.hourly(con, start, end, wp),
        "weekday": lambda: q.weekday_totals(con, start, end, wp),
        "projects": lambda: q.projects(con, start, end, wp),
        "wow": lambda: q.week_over_week(con, end, wp),
    }
    print(json.dumps(funcs[args.name](), ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    uvicorn.run("productivity.api.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="productivity", description="개인 업무 생산성 분석 시스템")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="MinIO 버킷(또는 로컬 디렉터리) 생성")
    i.set_defaults(func=cmd_init)

    c = sub.add_parser("collect", help="Git 커밋(및 선택 시 ActivityWatch) 수집 후 MinIO 적재")
    c.add_argument("--date", default="yesterday", help="YYYY-MM-DD | today | yesterday (기본)")
    c.add_argument("--start", help="백필 시작일")
    c.add_argument("--end", help="백필 종료일")
    c.add_argument("--no-git", action="store_true")
    c.add_argument("--no-activity", action="store_true", help="ENABLE_ACTIVITYWATCH=true 여도 활동 수집 생략")
    c.set_defaults(func=cmd_collect)

    qp = sub.add_parser("query", help="분석 쿼리 결과 출력")
    qp.add_argument("name", choices=["summary", "daily", "hourly", "weekday", "projects", "wow"])
    qp.add_argument("--days", type=int, default=30)
    qp.set_defaults(func=cmd_query)

    s = sub.add_parser("serve", help="FastAPI 서버 + 대시보드 실행")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args, Settings.from_env())
    except Exception as exc:  # Cron 로그에 원인이 남도록
        logging.getLogger("productivity").exception("실패: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
