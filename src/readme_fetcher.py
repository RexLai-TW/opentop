"""從 GitHub repo 抓取 README 內容 (raw markdown), 含快取與 rate limit 偵測。

策略:
  1) 先查 SQLite cache (`Storage.get_cached_readme`), 命中且未過期 (預設 7 天) 直接回傳
  2) 嘗試 https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md
     branch 依序 main → master → HEAD
  3) 若失敗, 退回 https://api.github.com/repos/{owner}/{repo}/readme
  4) 都失敗就回 None

Rate limit 偵測:
  - GitHub API 對匿名請求限制 60 req/hr
  - 偵測 403 + X-RateLimit-Remaining: 0 → 拋 RateLimitError, 包含 reset 時間
  - 偵測 429 (含 Retry-After header) → 拋 RateLimitError
  - 呼叫端應 catch 後等待 reset 或略過該 repo

快取寫入:
  - 成功抓到 → 寫入 cache (source: 'raw_main' | 'api' 等)
  - 抓不到 → 也寫入 cache 但 source='none', 避免短時間內重試同一個失敗 repo
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote

import requests


if TYPE_CHECKING:
    from .storage import Storage


logger = logging.getLogger(__name__)


class RateLimitError(RuntimeError):
    """GitHub API 觸發限流時拋出。"""

    def __init__(self, message: str, reset_at: datetime | None = None, retry_after_sec: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.retry_after_sec = retry_after_sec


# module-level state: tracking in-flight rate limit to avoid hammering after first 403
_last_rate_limit_until: datetime | None = None


def _is_rate_limited_now() -> datetime | None:
    """若前次請求觸發限流, 回傳到解除為止的時間; 否 None。"""
    global _last_rate_limit_until
    if _last_rate_limit_until is None:
        return None
    now = datetime.now(timezone.utc)
    if now >= _last_rate_limit_until:
        _last_rate_limit_until = None
        return None
    return _last_rate_limit_until


def _record_rate_limit(reset_at: datetime | None, retry_after_sec: int | None) -> None:
    global _last_rate_limit_until
    until = reset_at
    if until is None and retry_after_sec:
        until = datetime.now(timezone.utc).timestamp() + retry_after_sec
        until_dt = datetime.fromtimestamp(until, tz=timezone.utc)
        _last_rate_limit_until = until_dt
        return
    if until is not None:
        _last_rate_limit_until = until


def _check_rate_limit_response(r: requests.Response) -> None:
    """若 response 是 403/429 且有 rate limit 訊號, 拋 RateLimitError。"""
    if r.status_code not in (403, 429):
        return
    remaining = r.headers.get("X-RateLimit-Remaining")
    reset_unix = r.headers.get("X-RateLimit-Reset")
    retry_after = r.headers.get("Retry-After")

    reset_at: datetime | None = None
    if reset_unix and reset_unix.isdigit():
        reset_at = datetime.fromtimestamp(int(reset_unix), tz=timezone.utc)
    retry_sec: int | None = None
    if retry_after and retry_after.isdigit():
        retry_sec = int(retry_after)

    is_gh_rate_limit = (
        r.status_code == 429
        or (remaining is not None and remaining == "0")
        or "rate limit" in r.text.lower()
    )
    if not is_gh_rate_limit:
        return  # 403 但非 rate limit (例如私人 repo), 不視為限流

    _record_rate_limit(reset_at, retry_sec)
    raise RateLimitError(
        f"GitHub rate limit hit: status={r.status_code}, remaining={remaining}, reset={reset_at}",
        reset_at=reset_at,
        retry_after_sec=retry_sec,
    )


def _fetch_raw(owner: str, repo: str, headers: dict, timeout_sec: int) -> tuple[str | None, str | None]:
    """嘗試 raw.githubusercontent.com 三種 branch, 回傳 (content, source) 或 (None, None)。"""
    for branch in ("main", "master", "HEAD"):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            r = requests.get(url, headers=headers, timeout=timeout_sec, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.text.strip():
            return r.text, f"raw_{branch.lower()}"
    return None, None


def _fetch_api(owner: str, repo: str, headers: dict, timeout_sec: int) -> str | None:
    """用 GitHub API 抓 README (限流敏感)。"""
    r = requests.get(
        f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/readme",
        headers={**headers, "Accept": "application/vnd.github.raw"},
        timeout=timeout_sec,
    )
    _check_rate_limit_response(r)  # 拋出 RateLimitError
    if r.status_code == 200 and r.text.strip():
        return r.text
    return None


def fetch_readme(
    repo_full_name: str,
    *,
    storage: "Storage | None" = None,
    max_age_days: int = 7,
    timeout_sec: int = 15,
    user_agent: str = "opentop-bot/1.0",
) -> str | None:
    """抓取 README。優先用 storage cache。

    參數:
      storage:           若提供, 啟用 SQLite 快取 (避免重複打 GitHub)
      max_age_days:      cache TTL, 預設 7 天
      timeout_sec:       單次 HTTP 逾時
      user_agent:        User-Agent header

    回傳:
      README 內容 (str) 或 None (抓不到)

    拋出:
      RateLimitError: 觸發 GitHub rate limit, 含 reset 時間
    """
    if "/" not in repo_full_name:
        return None
    owner, repo = repo_full_name.split("/", 1)

    # 1) 查 cache
    if storage is not None:
        cached = storage.get_cached_readme(repo_full_name, max_age_days=max_age_days)
        if cached is not None:
            return cached

    # 2) 若模組層級知道正限流中, 直接跳過以免觸發更嚴重
    if _is_rate_limited_now() is not None:
        until = _is_rate_limited_now()
        raise RateLimitError(
            f"skipped: previous request triggered rate limit, retry after {until}",
            reset_at=until,
        )

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/vnd.github.raw, text/plain, */*",
    }

    content: str | None = None
    source: str = "none"

    try:
        # raw.githubusercontent.com 通常不限流, 先試
        content, source = _fetch_raw(owner, repo, headers, timeout_sec)

        # 退回 API
        if content is None:
            try:
                content = _fetch_api(owner, repo, headers, timeout_sec)
                if content is not None:
                    source = "api"
            except RateLimitError:
                raise  # 拋給 caller 處理
    except requests.RequestException as e:
        logger.warning("network error fetching %s: %s", repo_full_name, e)

    # 3) 寫入 cache (包含失敗情況, 避免 24h 內重試)
    if storage is not None:
        storage.put_cached_readme(repo_full_name, content, source)

    return content
