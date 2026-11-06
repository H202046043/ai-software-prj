from __future__ import annotations

import logging
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

RS, US = "\x1e", "\x1f"
PRETTY = f"--pretty=format:{RS}%H{US}%an{US}%ae{US}%aI{US}%P{US}%s"


def discover_repos(roots: list[Path]) -> list[Path]:
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
        log.warning("[%s] git %s 실패: %s", repo.name, args[0], exc.stderr.strip())
        return None


def global_git_email() -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--global", "user.email"], capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()
    except FileNotFoundError:
        return None
    return out.lower() or None


def resolve_author_emails(configured: list[str]) -> list[str]:
    if configured:
        log.info("수집 대상 작성자(.env): %s", ", ".join(configured))
        return configured
    email = global_git_email()
    if email:
        log.info("수집 대상 작성자(git 전역 설정): %s", email)
        return [email]
    log.warning("작성자 이메일을 찾지 못해 모든 작성자의 커밋을 수집합니다")
    return []


def collect_repo(repo: Path, start: date, end: date, tz: ZoneInfo) -> list[dict]:
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
