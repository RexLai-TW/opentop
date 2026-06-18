"""GitHub Trending 抓取：daily / weekly / monthly 各前 15 名。

資料來源: https://github.com/trending
- URL 參數: since=daily|weekly|monthly, spoken_language_code=xx
- 回傳結構為公開 HTML 頁面，可直接解析 <article class="Box-row">。

回傳結構:
    {
      "fetched_at": ISO8601 字串,
      "since": "daily" | "weekly" | "monthly",
      "items": [
         {
           "rank": int,                       # 1-based
           "repo_full_name": "owner/repo",    # 對應 https://github.com/owner/repo
           "repo_url": "https://github.com/owner/repo",
           "description": str | None,         # 原始英文描述
           "language": str | None,            # 主要語言
           "stars": int,                      # 該 repo 累積 stars
           "forks": int,
           "stars_period": int,               # 該時段新增 stars
        }, ...
      ]
    }
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


@dataclass
class TrendingItem:
    rank: int
    repo_full_name: str
    repo_url: str
    description: str | None
    language: str | None
    stars: int
    forks: int
    stars_period: int


_NUM_RE = re.compile(r"[\d,]+")


def _to_int(s: str | None) -> int:
    if not s:
        return 0
    m = _NUM_RE.search(s.replace("\u202f", "").replace("\xa0", " "))
    if not m:
        return 0
    return int(m.group(0).replace(",", ""))


def fetch_trending(
    *,
    since: str,
    base_url: str,
    spoken_languages: list[str] | None = None,
    top_n: int = 15,
    timeout_sec: int = 30,
    user_agent: str = "opentop-bot/1.0",
) -> dict[str, Any]:
    """抓取指定 since 的 trending 前 top_n 名。"""
    if since not in {"daily", "weekly", "monthly"}:
        raise ValueError(f"invalid since: {since}")
    if top_n != 15:
        raise ValueError("top_n 必須為 15 (規格鎖定)")

    params: dict[str, str] = {"since": since}
    if spoken_languages:
        # GitHub 接受多值相同 key，這裡簡化為單一 (取第一個)；多語可用迴圈
        params["spoken_language_code"] = spoken_languages[0]

    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
    }
    resp = requests.get(base_url, params=params, headers=headers, timeout=timeout_sec)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.select("article.Box-row")
    items: list[TrendingItem] = []
    for idx, art in enumerate(articles[:top_n], start=1):
        # repo 標題: <h2> <a href="/owner/repo"> ... </a> </h2>
        h2 = art.select_one("h2 a")
        if h2 is None:
            continue
        href = h2.get("href", "").strip()
        if not href:
            continue
        owner_repo = href.lstrip("/")
        # base_url 可能含 path (e.g. /trending); 用 scheme+netloc 重建 repo_url
        parsed_base = urlparse(base_url)
        repo_url = f"{parsed_base.scheme}://{parsed_base.netloc}/{owner_repo}"

        # 描述: <p class="col-9 ...">
        desc_p = art.select_one("p")
        description = desc_p.get_text(" ", strip=True) if desc_p else None

        # 語言: <span itemprop="programmingLanguage">
        lang_el = art.select_one("span[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else None

        # 實際 GitHub Trending HTML 結構 (2026):
        #   <a href="/owner/repo/stargazers">5,900</a>     # 累積 stars (文字是純數字)
        #   <a href="/owner/repo/forks">505</a>            # forks
        #   <span class="d-inline-block float-sm-right">371 stars today</span>
        stars = 0
        forks = 0
        stars_period = 0
        owner_prefix = "/" + owner_repo + "/"
        for a in art.select(f"a[href^='{owner_prefix}']"):
            href_a = a.get("href", "")
            text = a.get_text(" ", strip=True)
            n = _to_int(text)
            if href_a.endswith("/stargazers"):
                stars = n
            elif href_a.endswith("/forks"):
                forks = n
        period_el = art.select_one("span.d-inline-block.float-sm-right")
        if period_el:
            stars_period = _to_int(period_el.get_text(" ", strip=True))
        items.append(
            TrendingItem(
                rank=idx,
                repo_full_name=owner_repo,
                repo_url=repo_url,
                description=description,
                language=language,
                stars=stars,
                forks=forks,
                stars_period=stars_period,
            )
        )

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": since,
        "items": [asdict(it) for it in items],
    }


def fetch_all(
    *,
    since_options: list[str],
    base_url: str,
    spoken_languages: list[str] | None,
    top_n: int,
    timeout_sec: int,
    user_agent: str,
) -> dict[str, dict[str, Any]]:
    """一次抓取多種 since，回傳 {since: payload}。"""
    out: dict[str, dict[str, Any]] = {}
    for s in since_options:
        out[s] = fetch_trending(
            since=s,
            base_url=base_url,
            spoken_languages=spoken_languages,
            top_n=top_n,
            timeout_sec=timeout_sec,
            user_agent=user_agent,
        )
    return out
