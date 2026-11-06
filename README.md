# DuckDB와 MinIO 기반 개인 업무 생산성 분석 시스템

Git 커밋 로그를 수집해서 자체 구축한 MinIO(S3 호환 오브젝트 스토리지)에
Hive 파티션 구조(연/월)의 Parquet 파일로 적재하고, DuckDB로 분석한 결과를 FastAPI와 웹 대시보드로 보여준다.
작업 시간은 커밋 간격으로 추정한다(아래 "작업 시간 추정" 참고).

```
 ┌──────────────┐   ┌─────────────────────────────┐
 │ git log      │   │ ActivityWatch (선택, 기본 꺼짐) │
 └──────┬───────┘   └──────────────┬──────────────┘
        └──── Python 수집·정제 (Cron 매일 00:10) ────┐
                                                     ▼
        MinIO  s3://productivity/{commits|activity}/year=YYYY/month=MM/YYYY-MM-DD.parquet
                                                     │  httpfs 로 직접 조회
                                                     ▼
                     DuckDB (인메모리, 분석 쿼리 세트 + 커밋 기반 작업 시간 추정)
                                                     │
                                   FastAPI REST API  →  웹 대시보드 (Chart.js)
```

## 디렉터리 구조

| 경로 | 역할 |
|---|---|
| `docker-compose.yml`, `docker/minio/Dockerfile` | MinIO 서버 (공식 소스로 직접 빌드) |
| `src/productivity/collectors/git_collector.py` | Git 커밋 수집·정제 |
| `src/productivity/collectors/aw_collector.py` | (선택) ActivityWatch 활동 시간 수집 |
| `src/productivity/schema.py` | Parquet 스키마 / 파티션 규칙 |
| `src/productivity/storage.py` | MinIO(또는 로컬)에 일 단위 Parquet 적재 |
| `src/productivity/pipeline.py` | 수집 → 적재 파이프라인 |
| `src/productivity/analytics/` | DuckDB 연결, 분석 쿼리, 작업 시간 추정 |
| `src/productivity/api/` | FastAPI 엔드포인트 + 대시보드 정적 파일 |
| `scripts/install_cron.sh` | Cron 자동 수집 등록 |
| `docs/schema.md` | 스키마 설계서 |

## 실행 방법

사전 준비: [uv](https://docs.astral.sh/uv/), Docker

```bash
cp .env.example .env          # 저장소 경로, 작성자 이메일 등 수정
uv sync
docker compose up -d --build  # MinIO: API :9000, 콘솔 http://localhost:9001 (minioadmin/minioadmin)
uv run productivity init      # 버킷 생성 (collect 첫 실행 때도 자동 생성)

uv run productivity collect --start 2026-01-01 --end today   # 과거 데이터 백필
uv run productivity collect                                  # 어제 하루 수집 (Cron 기본 동작)
uv run productivity query summary --days 30                  # 터미널에서 분석 결과 확인
uv run productivity serve                                    # http://localhost:8000 대시보드, /docs API 문서
./scripts/install_cron.sh                                    # 매일 00:10 자동 수집 등록
```

같은 날짜를 다시 수집하면 해당 날짜 파일만 덮어써지므로 몇 번을 다시 실행해도 데이터가 중복되지 않는다.

## 작업 시간 추정

별도의 감시 프로그램 없이 커밋 기록만으로 작업 시간을 추정한다(git-hours 방식).

1. 같은 작성자의 커밋을 저장소 구분 없이 시간순으로 정렬한다.
   그래서 여러 저장소를 오가며 작업해도 같은 시간이 두 번 집계되지 않는다.
2. 직전 커밋과의 간격이 `WORK_MAX_GAP_MIN`(기본 120분) 이하이면 그 간격 전체를 작업 시간으로 본다.
3. 간격이 더 길면 새 세션의 시작으로 보고, 커밋 전 `WORK_FIRST_COMMIT_MIN`(기본 30분)을 작업 시간으로 본다.
4. 각 작업 구간은 그 커밋의 저장소에 귀속되고, 로컬 시각 기준 1시간 단위로 잘라서 시간대·요일·날짜별로 나눠 담는다.

한계: 커밋하지 않은 작업(코드 읽기, 디버깅, 결국 버린 코드)은 잡히지 않는다.
하루에 한 번 몰아서 커밋하는 습관이면 작업 시간이 적게 추정된다.

실제 활동 시간도 함께 수집하려면 [ActivityWatch](https://activitywatch.net/)를 설치하고
`.env`에서 `ENABLE_ACTIVITYWATCH=true`로 바꾼다. 수집 데이터는 `activity` 데이터셋에 적재된다.

## API

| 엔드포인트 | 내용 |
|---|---|
| `GET /api/summary` | 기간 요약: 커밋 수, 변경 라인, 추정 작업 시간, 시간당 커밋, 최다 작업 프로젝트 |
| `GET /api/daily` | 일별 커밋 수 / 추정 작업 시간 |
| `GET /api/hourly` | 시간대별(0~23시) |
| `GET /api/weekday` | 요일별 합계 |
| `GET /api/heatmap` | 요일 × 시간대 |
| `GET /api/projects` | 프로젝트별 작업 시간·커밋 비중 |
| `GET /api/commits` | 최근 커밋 |
| `GET /api/week-over-week?ref=YYYY-MM-DD` | 이번 주 vs 지난 주 증감률 |
| `GET /api/config` | 작업 시간 추정 파라미터 |

공통 쿼리 파라미터: `start`, `end` (YYYY-MM-DD, 기본값은 최근 30일)

## 테스트

```bash
uv run pytest
```

로컬 디렉터리 저장소를 써서 수집, 적재, 작업 시간 추정(경계 처리 포함), API까지 전체 흐름을 검증한다.
