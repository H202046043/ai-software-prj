from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import duckdb

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]


@dataclass(frozen=True)
class WorkParams:
    max_gap_min: int = 120
    first_commit_min: int = 30


DEFAULT_WORK = WorkParams()


def partition_filter(start: date, end: date) -> str:
    by_year: dict[int, list[int]] = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        by_year.setdefault(y, []).append(m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    parts = [f"(year = {yr} AND month IN ({', '.join(map(str, ms))}))" for yr, ms in by_year.items()]
    return "(" + " OR ".join(parts) + ")"


def _where(start: date, end: date) -> tuple[str, dict]:
    clause = f"{partition_filter(start, end)} AND local_date BETWEEN $start AND $end"
    return clause, {"start": start, "end": end}


def _work_cte(start: date, end: date, wp: WorkParams) -> tuple[str, dict]:
    if not 0 < wp.max_gap_min < 24 * 60 or not 0 <= wp.first_commit_min < 24 * 60:
        raise ValueError("max_gap_min 은 1~1439, first_commit_min 은 0~1439 이어야 합니다")
    buf_start, buf_end = start - timedelta(days=1), end + timedelta(days=1)
    sql = f"""
    wc AS (
        SELECT repo, author_email, authored_at,
               lag(authored_at) OVER (PARTITION BY author_email ORDER BY authored_at) AS prev_at
        FROM commits
        WHERE {partition_filter(buf_start, buf_end)} AND local_date BETWEEN $buf_start AND $buf_end AND NOT is_merge
    ), wi AS (
        SELECT repo, authored_at AS end_ts,
               CASE WHEN prev_at IS NULL OR authored_at - prev_at > to_minutes({wp.max_gap_min})
                    THEN authored_at - to_minutes({wp.first_commit_min})
                    ELSE prev_at END AS start_ts
        FROM wc
    ), work AS (
        SELECT repo,
               CAST(slot AS DATE) AS local_date,
               CAST(hour(slot) AS TINYINT) AS local_hour,
               CAST(isodow(slot) - 1 AS TINYINT) AS weekday,
               epoch(least(end_ts, slot + INTERVAL 1 HOUR) - greatest(start_ts, slot)) AS work_sec
        FROM wi, unnest(range(date_trunc('hour', start_ts), end_ts, INTERVAL 1 HOUR)) AS t(slot)
        WHERE end_ts > start_ts AND CAST(slot AS DATE) BETWEEN $start AND $end
    )"""
    return sql, {"buf_start": buf_start, "buf_end": buf_end}


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: dict) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def summary(con, start: date, end: date, wp: WorkParams = DEFAULT_WORK) -> dict:
    w, p = _where(start, end)
    work, wp_params = _work_cte(start, end, wp)
    sql = f"""
    WITH {work}, c AS (
        SELECT count(*) AS commits,
               coalesce(sum(insertions), 0) AS insertions,
               coalesce(sum(deletions), 0) AS deletions,
               count(DISTINCT local_date) AS commit_days,
               count(DISTINCT repo) AS repos
        FROM commits WHERE {w} AND NOT is_merge
    ), a AS (
        SELECT round(coalesce(sum(work_sec), 0) / 3600, 2) AS work_hours,
               count(DISTINCT local_date) AS work_days
        FROM work
    ), top AS (
        SELECT repo FROM work GROUP BY repo ORDER BY sum(work_sec) DESC LIMIT 1
    )
    SELECT c.*, a.*, (SELECT repo FROM top) AS top_project,
           round(a.work_hours / nullif(a.work_days, 0), 2) AS avg_work_hours_per_day,
           round(c.commits / nullif(a.work_hours, 0), 2) AS commits_per_hour
    FROM c, a
    """
    return _rows(con, sql, {**p, **wp_params})[0]


def daily(con, start: date, end: date, wp: WorkParams = DEFAULT_WORK) -> list[dict]:
    w, p = _where(start, end)
    work, wp_params = _work_cte(start, end, wp)
    sql = f"""
    WITH {work}, days AS (
        SELECT CAST(unnest(generate_series(CAST($start AS DATE), CAST($end AS DATE), INTERVAL 1 DAY)) AS DATE) AS d
    ), c AS (
        SELECT local_date AS d, count(*) AS commits, sum(insertions) AS insertions, sum(deletions) AS deletions
        FROM commits WHERE {w} AND NOT is_merge GROUP BY 1
    ), a AS (
        SELECT local_date AS d, sum(work_sec) / 60 AS work_min FROM work GROUP BY 1
    )
    SELECT days.d AS date,
           coalesce(c.commits, 0) AS commits,
           coalesce(c.insertions, 0) AS insertions,
           coalesce(c.deletions, 0) AS deletions,
           round(coalesce(a.work_min, 0), 1) AS work_min
    FROM days LEFT JOIN c USING (d) LEFT JOIN a USING (d)
    ORDER BY date
    """
    return _rows(con, sql, {**p, **wp_params})


def hourly(con, start: date, end: date, wp: WorkParams = DEFAULT_WORK) -> list[dict]:
    w, p = _where(start, end)
    work, wp_params = _work_cte(start, end, wp)
    sql = f"""
    WITH {work}, h AS (SELECT CAST(range AS TINYINT) AS hour FROM range(24)),
    c AS (SELECT local_hour AS hour, count(*) AS commits FROM commits WHERE {w} AND NOT is_merge GROUP BY 1),
    a AS (SELECT local_hour AS hour, sum(work_sec) / 60 AS work_min FROM work GROUP BY 1)
    SELECT h.hour, coalesce(c.commits, 0) AS commits, round(coalesce(a.work_min, 0), 1) AS work_min
    FROM h LEFT JOIN c USING (hour) LEFT JOIN a USING (hour)
    ORDER BY hour
    """
    return _rows(con, sql, {**p, **wp_params})


def weekday_hour_heatmap(con, start: date, end: date, wp: WorkParams = DEFAULT_WORK) -> list[dict]:
    w, p = _where(start, end)
    work, wp_params = _work_cte(start, end, wp)
    sql = f"""
    WITH {work}, grid AS (
        SELECT CAST(wd.range AS TINYINT) AS weekday, CAST(h.range AS TINYINT) AS hour
        FROM range(7) wd, range(24) h
    ),
    c AS (SELECT weekday, local_hour AS hour, count(*) AS commits
          FROM commits WHERE {w} AND NOT is_merge GROUP BY 1, 2),
    a AS (SELECT weekday, local_hour AS hour, sum(work_sec) / 60 AS work_min FROM work GROUP BY 1, 2)
    SELECT grid.weekday, grid.hour,
           coalesce(c.commits, 0) AS commits,
           round(coalesce(a.work_min, 0), 1) AS work_min
    FROM grid LEFT JOIN c USING (weekday, hour) LEFT JOIN a USING (weekday, hour)
    ORDER BY weekday, hour
    """
    return _rows(con, sql, {**p, **wp_params})


def weekday_totals(con, start: date, end: date, wp: WorkParams = DEFAULT_WORK) -> list[dict]:
    rows = weekday_hour_heatmap(con, start, end, wp)
    totals = [{"weekday": i, "label": WEEKDAYS_KO[i], "commits": 0, "work_min": 0.0} for i in range(7)]
    for r in rows:
        t = totals[r["weekday"]]
        t["commits"] += r["commits"]
        t["work_min"] = round(t["work_min"] + r["work_min"], 1)
    return totals


def projects(con, start: date, end: date, wp: WorkParams = DEFAULT_WORK) -> list[dict]:
    w, p = _where(start, end)
    work, wp_params = _work_cte(start, end, wp)
    sql = f"""
    WITH {work}, a AS (
        SELECT repo AS project, sum(work_sec) / 60 AS work_min FROM work GROUP BY 1
    ), c AS (
        SELECT repo AS project, count(*) AS commits, sum(insertions + deletions) AS changed_lines
        FROM commits WHERE {w} AND NOT is_merge GROUP BY 1
    ), j AS (
        SELECT coalesce(a.project, c.project) AS project,
               coalesce(a.work_min, 0) AS work_min,
               coalesce(c.commits, 0) AS commits,
               coalesce(c.changed_lines, 0) AS changed_lines
        FROM a FULL OUTER JOIN c ON a.project = c.project
    )
    SELECT project,
           round(work_min, 1) AS work_min,
           round(100 * work_min / nullif(sum(work_min) OVER (), 0), 1) AS work_share_pct,
           commits,
           round(100 * commits / nullif(sum(commits) OVER (), 0), 1) AS commit_share_pct,
           changed_lines
    FROM j
    ORDER BY work_min DESC, commits DESC
    """
    return _rows(con, sql, {**p, **wp_params})


def week_over_week(con, ref: date, wp: WorkParams = DEFAULT_WORK) -> dict:
    this_start = ref - timedelta(days=ref.weekday())
    prev_start = this_start - timedelta(days=7)
    this = summary(con, this_start, this_start + timedelta(days=6), wp)
    prev = summary(con, prev_start, prev_start + timedelta(days=6), wp)
    metrics = {}
    for key in ("commits", "insertions", "deletions", "work_hours", "commit_days", "work_days"):
        cur, before = float(this[key] or 0), float(prev[key] or 0)
        metrics[key] = {
            "this_week": cur,
            "last_week": before,
            "diff": round(cur - before, 2),
            "change_pct": round(100 * (cur - before) / before, 1) if before else None,
        }
    return {
        "this_week": {"start": this_start, "end": this_start + timedelta(days=6)},
        "last_week": {"start": prev_start, "end": prev_start + timedelta(days=6)},
        "metrics": metrics,
    }


def recent_commits(con, start: date, end: date, limit: int = 20) -> list[dict]:
    w, p = _where(start, end)
    sql = f"""
    SELECT authored_at, repo, message, insertions, deletions
    FROM commits WHERE {w} AND NOT is_merge
    ORDER BY authored_at DESC LIMIT $limit
    """
    return _rows(con, sql, {**p, "limit": limit})
