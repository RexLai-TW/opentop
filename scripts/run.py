#!/usr/bin/env python3
"""opentop 每日排程主程式。

流程:
  1) 讀取 config.yml
  2) 抓取 daily/weekly/monthly 各前 15 名
  3) 對每個 repo 抓 README, 透過 LLM 產生中文摘要
  4) 寫入 SQLite (snapshots + items)
  5) 依保留策略清掉過期資料
  6) 重新產生靜態 HTML 到 docs/

用法:
  python scripts/run.py
  python scripts/run.py --config config.yml --no-llm   # 跳過 LLM (只存原文, 後續批次補)
  python scripts/run.py --since daily weekly           # 只抓特定 since
  python scripts/run.py --skip-scrape                 # 只重生 HTML (除錯用)
  python scripts/run.py --retention-only               # 只跑保留清理
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

# 讓 `python scripts/run.py` 也能 import src.*
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 自動載入 repo 根目錄的 .env (若存在), 不覆寫 shell 既有變數
from src.env_loader import load_env_file  # noqa: E402
load_env_file(_ROOT / ".env")

from src.config import load_config  # noqa: E402
from src.scraper import fetch_all  # noqa: E402
from src.storage import Storage  # noqa: E402
from src.llm import LlmClient  # noqa: E402
from src.readme_fetcher import fetch_readme  # noqa: E402
from src.site import generate_site  # noqa: E402


def _enrich_with_llm(
    items: list[dict[str, Any]],
    *,
    client: LlmClient,
    skip_llm: bool,
    storage: Any = None,
    readme_excerpt_chars: int = 1200,
) -> list[dict[str, Any]]:
    """對 items 加上 title_zh / summary_zh / tags / summary_source。

    觸發 RateLimitError 時: 剩餘 repos 全部降級, 不中斷整體管線。
    """
    from src.readme_fetcher import RateLimitError  # local import 避免循環

    if skip_llm:
        for it in items:
            it["title_zh"] = it["repo_full_name"]
            it["summary_zh"] = (it.get("description") or "").strip() or "(略)"
            it["tags"] = []
            it["summary_source"] = "skipped"
            it["readme_excerpt"] = ""
        return items

    total = len(items)
    for idx, it in enumerate(items, start=1):
        print(f"  [{idx}/{total}] summarize {it['repo_full_name']}", flush=True)
        try:
            readme = fetch_readme(it["repo_full_name"], storage=storage)
        except RateLimitError as e:
            until = e.reset_at.isoformat() if e.reset_at else "unknown"
            print(f"  ! GitHub rate limit hit (reset at {until}); "
                  f"剩餘 {total - idx} 個 repos 將跳過 README 抓取")
            for it2 in items[idx:]:
                result = client.summarize(
                    repo_full_name=it2["repo_full_name"],
                    original_desc=it2.get("description"),
                    readme=None,
                )
                it2["title_zh"] = result.get("title_zh")
                it2["summary_zh"] = result.get("summary_zh")
                it2["tags"] = result.get("tags") or []
                it2["summary_source"] = result.get("source")
                it2["readme_excerpt"] = ""
            return items
        excerpt = (readme or "")[:readme_excerpt_chars]
        result = client.summarize(
            repo_full_name=it["repo_full_name"],
            original_desc=it.get("description"),
            readme=readme,
        )
        it["title_zh"] = result.get("title_zh")
        it["summary_zh"] = result.get("summary_zh")
        it["tags"] = result.get("tags") or []
        it["summary_source"] = result.get("source")
        it["readme_excerpt"] = excerpt
        time.sleep(0.5)  # 禮貌節流
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="opentop 每日排程")
    ap.add_argument("--config", default="config.yml", help="config 檔路徑")
    ap.add_argument(
        "--since",
        nargs="+",
        choices=["daily", "weekly", "monthly"],
        help="只抓特定 since (預設全抓)",
    )
    ap.add_argument("--no-llm", action="store_true", help="跳過 LLM 摘要 (僅存原文)")
    ap.add_argument("--skip-scrape", action="store_true", help="跳過抓取, 只重生 HTML")
    ap.add_argument("--retention-only", action="store_true", help="只跑保留清理")
    ap.add_argument("--skip-site", action="store_true", help="跳過靜態頁面生成")
    args = ap.parse_args()

    cfg = load_config(args.config)
    gt = cfg["github_trending"]
    llm_cfg = cfg["llm"]
    storage_cfg = cfg["storage"]
    since_options = args.since or gt.get("since_options", ["daily", "weekly", "monthly"])
    top_n = int(gt.get("top_n", 15))
    assert top_n == 15, "top_n 必須為 15"

    storage = Storage(storage_cfg["sqlite_path"])

    try:
        if args.retention_only:
            deleted = storage.enforce_retention(**storage_cfg.get("retention", {}))
            print(f"retention: {deleted}")
            return 0

        if not args.skip_scrape:
            print(f"== scrape == since={since_options} top_n={top_n}")
            raw = fetch_all(
                since_options=since_options,
                base_url=gt["base_url"],
                spoken_languages=gt.get("spoken_languages") or None,
                top_n=top_n,
                timeout_sec=int(gt.get("request_timeout_sec", 30)),
                user_agent=gt.get("user_agent", "opentop-bot/1.0"),
            )
            client = LlmClient(llm_cfg)
            for since, payload in raw.items():
                items = payload["items"]
                print(f"== LLM enrich since={since} ({len(items)} repos) ==")
                items = _enrich_with_llm(items, client=client, skip_llm=args.no_llm, storage=storage)
                snap_id = storage.upsert_snapshot(since, payload["fetched_at"], items)
                print(f"  snapshot #{snap_id} {since} {payload['fetched_at']} stored.")
        else:
            print("--skip-scrape: 不抓取, 直接重用 SQLite 既有資料")

        # 保留策略
        deleted = storage.enforce_retention(**storage_cfg.get("retention", {}))
        print(f"== retention == deleted={deleted}")

        if not args.skip_site:
            print("== generate static site ==")
            generated = generate_site(storage, cfg)
            for p in sorted(generated):
                print(f"  wrote {p}")

        print("done.")
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
