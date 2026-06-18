"""SQLite 儲存層: 寫入 trending 榜單、查詢歷史、執行保留策略。

Schema:
  snapshots(
    id              INTEGER PK,
    fetched_at      TEXT NOT NULL,        -- ISO8601 UTC
    snapshot_date   TEXT NOT NULL,        -- YYYY-MM-DD (UTC date of fetched_at)
    since           TEXT NOT NULL,        -- daily|weekly|monthly
    item_count      INTEGER NOT NULL
  )
  items(
    id              INTEGER PK,
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    rank            INTEGER NOT NULL,
    repo_full_name  TEXT NOT NULL,
    repo_url        TEXT NOT NULL,
    description_en  TEXT,
    language        TEXT,
    stars           INTEGER,
    forks           INTEGER,
    stars_period    INTEGER,
    title_zh        TEXT,
    summary_zh      TEXT,
    tags_json       TEXT,
    summary_source  TEXT,
    readme_excerpt  TEXT
  )
  indexes: snapshots(since, snapshot_date DESC), items(snapshot_id, rank)

保留策略 (enforce_retention):
  - daily:    保留最近 30 天 (snapshot_date >= today - 29)
  - weekly:   保留最近 12 週 (每週一筆, snapshot_date >= today - 12*7 + 7)
  - monthly:  保留最近 12 個月 (每月一筆, snapshot_date >= today - 12 個月初)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  fetched_at    TEXT NOT NULL,
  snapshot_date TEXT NOT NULL,
  since         TEXT NOT NULL,
  item_count    INTEGER NOT NULL,
  UNIQUE(snapshot_date, since)
);
CREATE TABLE IF NOT EXISTS items (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id    INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  rank           INTEGER NOT NULL,
  repo_full_name TEXT NOT NULL,
  repo_url       TEXT NOT NULL,
  description_en TEXT,
  language       TEXT,
  stars          INTEGER,
  forks          INTEGER,
  stars_period   INTEGER,
  title_zh       TEXT,
  summary_zh     TEXT,
  tags_json      TEXT,
  summary_source TEXT,
  readme_excerpt TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_since_date
  ON snapshots(since, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_items_snapshot_rank
  ON items(snapshot_id, rank);
CREATE TABLE IF NOT EXISTS readme_cache (
  repo_full_name TEXT PRIMARY KEY,
  content        TEXT NOT NULL,
  fetched_at     TEXT NOT NULL,
  source         TEXT NOT NULL         -- 'raw_main' | 'raw_master' | 'raw_head' | 'api' | 'none'
);
"""

class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---------- writes ----------
    def upsert_snapshot(self, since: str, fetched_at: str, items: list[dict[str, Any]]) -> int:
        """寫入或更新當日該 since 的 snapshot, 並取代其 items。"""
        snap_date = fetched_at[:10]  # YYYY-MM-DD
        cur = self._conn.cursor()
        cur.execute(
            "SELECT id FROM snapshots WHERE snapshot_date = ? AND since = ?",
            (snap_date, since),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO snapshots(fetched_at, snapshot_date, since, item_count) VALUES (?, ?, ?, ?)",
                (fetched_at, snap_date, since, len(items)),
            )
            snap_id = int(cur.lastrowid)
        else:
            snap_id = int(row[0])
            cur.execute(
                "UPDATE snapshots SET fetched_at = ?, item_count = ? WHERE id = ?",
                (fetched_at, len(items), snap_id),
            )
            cur.execute("DELETE FROM items WHERE snapshot_id = ?", (snap_id,))

        for it in items:
            cur.execute(
                """
                INSERT INTO items(
                  snapshot_id, rank, repo_full_name, repo_url,
                  description_en, language, stars, forks, stars_period,
                  title_zh, summary_zh, tags_json, summary_source, readme_excerpt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap_id,
                    int(it["rank"]),
                    it["repo_full_name"],
                    it["repo_url"],
                    it.get("description"),
                    it.get("language"),
                    int(it.get("stars") or 0),
                    int(it.get("forks") or 0),
                    int(it.get("stars_period") or 0),
                    it.get("title_zh"),
                    it.get("summary_zh"),
                    json.dumps(it.get("tags") or [], ensure_ascii=False),
                    it.get("summary_source"),
                    (it.get("readme_excerpt") or "")[:4000],
                ),
            )
        self._conn.commit()
        return snap_id

    # ---------- reads ----------
    def latest_snapshots(self, since: str, limit: int = 1) -> list[dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT id, fetched_at, snapshot_date, since, item_count "
            "FROM snapshots WHERE since = ? ORDER BY snapshot_date DESC LIMIT ?",
            (since, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_items(self, snapshot_id: int) -> list[dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM items WHERE snapshot_id = ? ORDER BY rank ASC",
            (snapshot_id,),
        )
        cols = [c[0] for c in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            try:
                d["tags"] = json.loads(d.pop("tags_json") or "[]")
            except json.JSONDecodeError:
                d["tags"] = []
            out.append(d)
        return out

    # ---------- readme cache ----------
    def get_cached_readme(self, repo_full_name: str, *, max_age_days: int = 7) -> str | None:
        """取得快取的 README, 若超過 max_age_days 或 source='none' 則視為無效回 None。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT content, fetched_at, source FROM readme_cache WHERE repo_full_name = ?",
            (repo_full_name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        if row["source"] == "none":
            return None
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
        except ValueError:
            return None
        age = datetime.now(timezone.utc) - fetched
        if age > timedelta(days=max_age_days):
            return None
        return row["content"]

    def put_cached_readme(
        self, repo_full_name: str, content: str | None, source: str
    ) -> None:
        """寫入快取。content 為 None 表示明確抓不到, 也要記 (避免重試)。"""
        cur = self._conn.cursor()
        content_marker = content if content is not None else ""
        cur.execute(
            """
            INSERT INTO readme_cache(repo_full_name, content, fetched_at, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repo_full_name) DO UPDATE SET
                content = excluded.content,
                fetched_at = excluded.fetched_at,
                source = excluded.source
            """,
            (repo_full_name, content_marker, datetime.now(timezone.utc).isoformat(timespec="seconds"), source),
        )
        self._conn.commit()

    def list_retained(self) -> dict[str, list[dict[str, Any]]]:
        """依保留策略列出 daily/weekly/monthly 各保留哪些 snapshots。"""
        cur = self._conn.cursor()
        today = datetime.now(timezone.utc).date()
        out: dict[str, list[dict[str, Any]]] = {"daily": [], "weekly": [], "monthly": []}

        # daily: 最近 30 天 (含當天)
        since_date = (today - timedelta(days=29)).isoformat()
        cur.execute(
            "SELECT id, fetched_at, snapshot_date, since, item_count "
            "FROM snapshots WHERE since='daily' AND snapshot_date >= ? "
            "ORDER BY snapshot_date DESC",
            (since_date,),
        )
        out["daily"] = [dict(r) for r in cur.fetchall()]

        # weekly: 最近 12 週, 從所有 daily snapshot 中挑每週最新一筆
        weekly_since = (today - timedelta(weeks=11)).isoformat()
        cur.execute(
            "SELECT id, fetched_at, snapshot_date, since, item_count "
            "FROM snapshots WHERE since='daily' AND snapshot_date >= ? "
            "ORDER BY snapshot_date DESC",
            (weekly_since,),
        )
        seen_weeks: set[tuple[int, int]] = set()
        for r in cur.fetchall():
            d = datetime.fromisoformat(r["snapshot_date"]).date()
            wk = d.isocalendar().week
            key = (d.year, wk)
            if key in seen_weeks:
                continue
            seen_weeks.add(key)
            out["weekly"].append(dict(r))
            if len(out["weekly"]) >= 12:
                break

        # monthly: 最近 12 個月, 從 daily snapshot 中挑每月最新一筆
        months_threshold = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        for _ in range(11):
            months_threshold = (months_threshold - timedelta(days=1)).replace(day=1)
        cur.execute(
            "SELECT id, fetched_at, snapshot_date, since, item_count "
            "FROM snapshots WHERE since='daily' AND snapshot_date >= ? "
            "ORDER BY snapshot_date DESC",
            (months_threshold.isoformat(),),
        )
        seen_months: set[tuple[int, int]] = set()
        for r in cur.fetchall():
            d = datetime.fromisoformat(r["snapshot_date"]).date()
            key = (d.year, d.month)
            if key in seen_months:
                continue
            seen_months.add(key)
            out["monthly"].append(dict(r))
            if len(out["monthly"]) >= 12:
                break

        return out

    def enforce_retention(
        self,
        *,
        daily_keep_days: int = 30,
        weekly_keep_weeks: int = 12,
        monthly_keep_months: int = 12,
    ) -> dict[str, int]:
        """刪除超出保留範圍的 snapshots (cascades 刪 items)。

        保留策略: 一個 snapshot 只要落在 daily/weekly/monthly 任一保留集合即保留。
        - daily:   snapshot_date >= today - (daily_keep_days - 1)
        - weekly:  從全部 snapshots 中, 取每個 ISO week 內最新一筆, 最多 N 週
        - monthly: 從全部 snapshots 中, 取每個 calendar month 內最新一筆, 最多 N 月
        """
        cur = self._conn.cursor()
        today = datetime.now(timezone.utc).date()
        deleted = {"daily": 0, "weekly_expired": 0, "monthly_expired": 0}

        # 1) daily 保留集合: 最近 N 天
        daily_threshold = (today - timedelta(days=daily_keep_days - 1)).isoformat()
        cur.execute(
            "SELECT id, snapshot_date FROM snapshots WHERE since='daily' AND snapshot_date >= ? ORDER BY snapshot_date DESC",
            (daily_threshold,),
        )
        daily_rows = cur.fetchall()
        daily_keep: set[int] = {int(r[0]) for r in daily_rows}

        # 2) weekly 保留集合: 從「最近 max(daily_keep_days, weekly_keep_weeks*7+7) 天」的
        #    daily snapshots 中, 每個 ISO week 取最新一筆, 最多 N 週
        cur.execute(
            "SELECT id, snapshot_date FROM snapshots WHERE since='daily' ORDER BY snapshot_date DESC"
        )
        all_rows = cur.fetchall()
        weekly_keep: set[int] = set()
        seen_weeks: set[tuple[int, int]] = set()
        weekly_window_start = (today - timedelta(days=max(daily_keep_days, weekly_keep_weeks * 7 + 7))).isoformat()
        for snap_id, snap_date in all_rows:
            try:
                d = datetime.fromisoformat(snap_date).date()
            except ValueError:
                continue
            if snap_date < weekly_window_start:
                continue
            key = (d.year, d.isocalendar().week)
            if key in seen_weeks:
                continue
            seen_weeks.add(key)
            weekly_keep.add(int(snap_id))
            if len(seen_weeks) >= weekly_keep_weeks:
                break
        monthly_keep: set[int] = set()
        seen_months: set[tuple[int, int]] = set()
        monthly_window_start = (today - timedelta(days=max(daily_keep_days, monthly_keep_months * 31 + 31))).isoformat()
        for snap_id, snap_date in all_rows:
            try:
                d = datetime.fromisoformat(snap_date).date()
            except ValueError:
                continue
            if snap_date < monthly_window_start:
                continue
            key = (d.year, d.month)
            if key in seen_months:
                continue
            seen_months.add(key)
            monthly_keep.add(int(snap_id))
            if len(seen_months) >= monthly_keep_months:
                break

        # 4) 計算應保留 = 三者聯集
        keep_union = daily_keep | weekly_keep | monthly_keep
        cur.execute("SELECT id FROM snapshots WHERE since='daily'")
        all_ids = [int(r[0]) for r in cur.fetchall()]
        expired = [i for i in all_ids if i not in keep_union]
        if expired:
            qmarks = ",".join("?" * len(expired))
            cur.execute(
                f"DELETE FROM snapshots WHERE id IN ({qmarks})", expired
            )
            deleted["weekly_expired"] = cur.rowcount

        self._conn.commit()
        return deleted
