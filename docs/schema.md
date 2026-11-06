# 스키마 설계서

## 1. 저장 구조 (Hive 파티션)

```
s3://productivity/
├── commits/
│   └── year=2026/
│       ├── month=10/2026-10-01.parquet … 2026-10-31.parquet
│       └── month=11/2026-11-01.parquet …
└── activity/
    └── year=2026/month=11/2026-11-01.parquet …
```

- **파티션 키**: `year`, `month`. 파일 안에는 저장하지 않고 경로에서 파생된다
  (DuckDB `hive_partitioning = true`).
- **파일 단위**: 데이터가 있는 날마다 파일 1개. 같은 날짜를 다시 수집하면 그 날짜 파일만 덮어쓰고, 0건이 된 날의 파일은 지우기 때문에 멱등성이 보장된다.
- **포맷**: Parquet, zstd 압축
- **날짜 기준**: 모든 `local_date`, `local_hour`, `weekday`는 `TIMEZONE`(기본 Asia/Seoul) 기준이다.
  원본 시각은 UTC로 저장한다.
- **파티션 프루닝**: 분석 쿼리는 조회 기간에 걸치는 `year`/`month` 조건을 함께 걸어서
  필요한 파티션의 파일만 읽는다.

## 2. commits

| 컬럼 | 타입 | 설명 |
|---|---|---|
| commit_hash | VARCHAR | 커밋 해시 |
| repo | VARCHAR | 저장소 디렉터리 이름 (= 프로젝트 이름) |
| author_name | VARCHAR | 작성자 |
| author_email | VARCHAR | 작성자 이메일 (소문자) |
| authored_at | TIMESTAMPTZ | 작성 시각 (UTC) |
| local_date | DATE | 작성 날짜 (로컬) |
| local_hour | TINYINT | 작성 시 (0~23) |
| weekday | TINYINT | 요일 (0=월 ~ 6=일) |
| is_merge | BOOLEAN | 부모 커밋이 2개 이상이면 머지 커밋 |
| files_changed | INTEGER | 변경 파일 수 |
| insertions | INTEGER | 추가 라인 (바이너리 제외) |
| deletions | INTEGER | 삭제 라인 (바이너리 제외) |
| message | VARCHAR | 커밋 제목 |

정제 규칙
- `--all`로 모든 브랜치를 읽고, (저장소, 해시) 기준으로 중복을 없앤다.
- 날짜 필터는 커밋 날짜가 아니라 **작성일(author date)** 기준이다.
  rebase나 cherry-pick을 해도 실제로 작업한 시각이 유지되기 때문이다.
- `GIT_AUTHOR_EMAILS`를 지정하면 본인 커밋만 남긴다.

## 3. 작업 시간 (파생 데이터, 별도 저장 없음)

작업 시간은 `commits`에서 조회할 때마다 DuckDB가 계산한다(`analytics/queries.py`의 `_work_cte`).
그래서 추정 파라미터를 바꾸면 다시 적재하지 않아도 바로 반영된다.

| 단계 | 내용 |
|---|---|
| 정렬 | 머지 커밋을 뺀 커밋을 작성자별로 `authored_at` 순서로 정렬한다 (저장소 구분 없음) |
| 구간 | 직전 커밋과 간격이 `WORK_MAX_GAP_MIN` 이하이면 `[직전 커밋, 현재 커밋]`, 아니면 `[현재 - WORK_FIRST_COMMIT_MIN, 현재]` |
| 귀속 | 구간 전체를 현재 커밋의 `repo`(프로젝트)에 귀속한다 |
| 분할 | 로컬 시각 1시간 단위로 잘라서 `(repo, local_date, local_hour, weekday, work_sec)` 행을 만든다 |
| 경계 | 조회 기간 [start, end]에 대해 커밋은 start−1일 ~ end+1일 범위를 읽는다. 기간 앞쪽 세션 판단과, 다음 날 새벽 커밋이 end 쪽으로 넘어오는 몫을 정확히 계산하기 위해서다 |

## 4. activity (선택: `ENABLE_ACTIVITYWATCH=true`일 때만 수집)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| event_start / event_end | TIMESTAMPTZ | 이벤트 구간 (UTC) |
| duration_sec | DOUBLE | 지속 시간(초) |
| app | VARCHAR | 활성 앱 이름 |
| title | VARCHAR | 창 제목 |
| project | VARCHAR | 창 제목에서 추출한 프로젝트 (없으면 NULL) |
| is_coding | BOOLEAN | `CODING_APPS`에 해당하는 앱인지 |
| hostname | VARCHAR | 수집한 기기 |
| local_date / local_hour / weekday | DATE / TINYINT / TINYINT | 이벤트 시작 기준 로컬 날짜·시·요일 |

정제 규칙
- ActivityWatch Query API의 `filter_period_intersect`로 **AFK가 아닌 구간과 겹치는 창 이벤트만** 가져온다.
- 날짜 경계(로컬 자정)에서 이벤트를 잘라서 하루 단위 파일에 정확히 나눠 담는다.
- 프로젝트 추출 순서
  1. 창 제목에 Git 저장소 이름이 단어 단위로 들어 있으면 그 이름을 쓴다 (커밋 데이터와 조인할 수 있음)
  2. JetBrains IDE 형식 `project – file`
  3. VS Code / Cursor 형식 `file — project`
