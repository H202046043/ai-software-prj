"""Git 커밋 로그 수집기.

`git log --all --numstat` 결과를 파싱해 커밋 단위 레코드로 정제한다.
- 여러 브랜치에 같은 커밋이 있어도 해시 기준으로 1건만 남긴다.
- 날짜 필터는 작성 시각(author date)의 로컬 날짜 기준이다.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

RS, US = "\x1e", "\x1f"  # 레코드/필드 구분자
PRETTY = f"--pretty=format:{RS}%H{US}%an{US}%ae{US}%aI{US}%P{US}%s"


def discover_repos(roots: list[Path]) -> list[Path]:
    """root 자체 또는 하위 1단계 디렉터리 중 git 저장소를 찾는다."""
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            log.warning("저장소 루트가 존재하지 않음: %s", root)
            continue
        candidates = [root] + sorted(p for p in root.iterdir() if p.is_dir())
        for c in candidates:
            if (c / ".git").exists() and c not in found:
                found.append(c)
    return found


def parse_git_log(output: str, repo: str, tz: ZoneInfo) -> list[dict]:
    records: list[dict] = []
    for chunk in output.split(RS):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        header, *stat_lines = chunk.split("\n")
        parts = header.split(US)
        if len(parts) != 6:
            log.warning("[%s] 파싱 불가 라인 건너뜀: %r", repo, header[:80])
            continue
        commit_hash, name, email, authored_iso, parents, subject = parts

        files = ins = dels = 0
        for line in stat_lines:
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            files += 1
            # 바이너리 파일은 "-" 로 표시됨
            ins += int(cols[0]) if cols[0].isdigit() else 0
            dels += int(cols[1]) if cols[1].isdigit() else 0

        authored = datetime.fromisoformat(authored_iso)
        local = authored.astimezone(tz)
        records.append(
            {
                "commit_hash": commit_hash,
                "repo": repo,
                "author_name": name,
                "author_email": email.lower(),
                "authored_at": authored.astimezone(timezone.utc),
                "local_date": local.date(),
                "local_hour": local.hour,
                "weekday": local.weekday(),
                "is_merge": len(parents.split()) > 1,
                "files_changed": files,
                "insertions": ins,
                "deletions": dels,
                "message": subject,
            }
        )
    return records


def _git(repo: Path, args: list[str], stdin: str | None = None) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True,
            check=True, encoding="utf-8", errors="replace",
        ).stdout
    except subprocess.CalledProcessError as exc:
        # 커밋이 하나도 없는 저장소 등
        log.warning("[%s] git %s 실패: %s", repo.name, args[0], exc.stderr.strip())
        return None


def collect_repo(repo: Path, start: date, end: date, tz: ZoneInfo) -> list[dict]:
    """2단계 수집.

    git log --since 는 탐색 중 기준보다 오래된 커밋을 만나면 거기서 멈추므로
    (브랜치 끝 커밋 날짜가 과거로 조작되었거나 rebase 된 경우 누락 발생) 사용하지 않는다.
    1) 전체 커밋의 해시·작성일만 가볍게 읽어 작성일(로컬) 기준으로 거르고
    2) 걸러진 커밋만 --numstat 으로 상세 조회한다.
    """
    index = _git(repo, ["log", "--all", f"--pretty=format:%H{US}%aI"])
    if not index:
        return []
    hashes = []
    for line in index.splitlines():
        h, _, iso = line.partition(US)
        if iso and start <= datetime.fromisoformat(iso).astimezone(tz).date() <= end:
            hashes.append(h)
    if not hashes:
        return []
    out = _git(repo, ["log", "--no-walk=unsorted", "--stdin", "--numstat", "--no-renames", PRETTY],
               stdin="\n".join(hashes) + "\n")
    return parse_git_log(out or "", repo.name, tz)


def collect_commits(
    roots: list[Path], start: date, end: date, tz: ZoneInfo, author_emails: list[str] | None = None
) -> list[dict]:
    """여러 저장소의 커밋을 모은다.

    포크·복사본처럼 히스토리를 공유하는 저장소에서는 같은 커밋(해시)이 여러 번 나오므로
    해시 기준으로 1건만 남긴다. 귀속 저장소는 탐색 순서(경로 이름순)에서 먼저 나온 쪽이다.
    """
    seen: set[str] = set()
    shared = 0
    result: list[dict] = []
    for repo in discover_repos(roots):
        for rec in collect_repo(repo, start, end, tz):
            if author_emails and rec["author_email"] not in author_emails:
                continue
            if rec["commit_hash"] in seen:
                shared += 1
                continue
            seen.add(rec["commit_hash"])
            result.append(rec)
    if shared:
        log.info("저장소 간 공유 커밋 %d건 중복 제외", shared)
    log.info("커밋 %d건 수집 (%s ~ %s)", len(result), start, end)
    return result
