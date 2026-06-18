"""opentop 單元/整合測試。

涵蓋:
  - config 載入與驗證
  - scraper HTML 解析 (使用本地 fixture, 不打網路)
  - storage 寫入/查詢/保留策略
  - LLM mock provider 摘要
  - site HTML 產生
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import scraper, storage, site  # noqa: E402
from src.llm import LlmClient  # noqa: E402


# --------- scraper HTML fixture ----------
TRENDING_FIXTURE = """<!DOCTYPE html><html><body>
<article class="Box-row">
  <h2><a href="/owner1/repo1"></a></h2>
  <p class="col-9">A great project</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/owner1/repo1/stargazers">1,234</a>
  <a href="/owner1/repo1/forks">56</a>
  <span class="d-inline-block float-sm-right">100 stars today</span>
</article>
<article class="Box-row">
  <h2><a href="/owner2/repo2"></a></h2>
  <p class="col-9">Another repo description</p>
  <span itemprop="programmingLanguage">TypeScript</span>
  <a href="/owner2/repo2/stargazers">500</a>
  <a href="/owner2/repo2/forks">10</a>
  <span class="d-inline-block float-sm-right">50 stars this week</span>
</article>
</body></html>
"""


class TestScraper(unittest.TestCase):
    def test_parse_fixture(self) -> None:
        # 暫時替換 requests.get
        import requests

        class _Resp:
            status_code = 200
            text = TRENDING_FIXTURE

            def raise_for_status(self) -> None:
                return None

        original = requests.get
        requests.get = lambda *a, **kw: _Resp()  # type: ignore[assignment]
        try:
            payload = scraper.fetch_trending(
                since="daily", base_url="https://github.com/trending"
            )
        finally:
            requests.get = original  # type: ignore[assignment]

        self.assertEqual(payload["since"], "daily")
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["repo_full_name"], "owner1/repo1")
        self.assertEqual(payload["items"][0]["language"], "Python")
        self.assertEqual(payload["items"][0]["stars"], 1234)
        self.assertEqual(payload["items"][0]["stars_period"], 100)
        self.assertEqual(payload["items"][0]["rank"], 1)
        self.assertEqual(payload["items"][1]["repo_full_name"], "owner2/repo2")
        self.assertEqual(payload["items"][1]["language"], "TypeScript")

    def test_top_n_enforced(self) -> None:
        with self.assertRaises(ValueError):
            scraper.fetch_trending(since="daily", base_url="x", top_n=10)


class TestStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = storage.Storage(self.tmp.name)

    def tearDown(self) -> None:
        self.store.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def _make_item(self, repo: str, rank: int) -> dict:
        return {
            "rank": rank,
            "repo_full_name": repo,
            "repo_url": f"https://github.com/{repo}",
            "description": f"desc {repo}",
            "language": "Python",
            "stars": 100,
            "forks": 10,
            "stars_period": 5,
            "title_zh": f"{repo} 中文",
            "summary_zh": f"{repo} 中文摘要",
            "tags": ["AI", "工具"],
            "summary_source": "mock",
            "readme_excerpt": "...",
        }

    def test_upsert_and_query(self) -> None:
        items = [self._make_item("o/r", i) for i in range(1, 4)]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snap_id = self.store.upsert_snapshot("daily", now, items)
        self.assertGreater(snap_id, 0)
        snap = self.store.latest_snapshots("daily")[0]
        self.assertEqual(snap["item_count"], 3)
        got = self.store.get_items(snap_id)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]["title_zh"], "o/r 中文")
        self.assertEqual(got[0]["tags"], ["AI", "工具"])

    def test_upsert_idempotent_same_day(self) -> None:
        items = [self._make_item("o/r", 1)]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snap_id_1 = self.store.upsert_snapshot("daily", now, items)
        snap_id_2 = self.store.upsert_snapshot("daily", now, items)
        self.assertEqual(snap_id_1, snap_id_2)
        # list 應只有 1 筆
        self.assertEqual(len(self.store.latest_snapshots("daily", limit=10)), 1)

    def test_retention_daily(self) -> None:
        """45 天前的 snapshot 超出 daily 30 天與 weekly 12 週, 應被清掉。"""
        old_date = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat(timespec="seconds")
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.upsert_snapshot("daily", old_date, [self._make_item("o/old", 1)])
        self.store.upsert_snapshot("daily", today, [self._make_item("o/new", 1)])
        deleted = self.store.enforce_retention(daily_keep_days=30)
        remaining = self.store.latest_snapshots("daily", limit=10)
        repos = [self.store.get_items(s["id"])[0]["repo_full_name"] for s in remaining]
        self.assertIn("o/new", repos)
        self.assertNotIn("o/old", repos)
        self.assertGreater(deleted["weekly_expired"] + deleted.get("monthly_expired", 0) + deleted["daily"], 0)

    def test_retention_keeps_weekly_and_monthly_reps(self) -> None:
        """驗證 weekly 12 週 + monthly 12 月的 snapshot 會被保留 (即使超過 30 天)。"""
        # 模擬 50 個連續日的 snapshot, 跨度 50 天
        today = datetime.now(timezone.utc)
        self.store._conn.execute("DELETE FROM items")
        self.store._conn.execute("DELETE FROM snapshots")
        self.store._conn.commit()
        for d in range(50):
            dt = (today - timedelta(days=d)).isoformat(timespec="seconds")
            self.store.upsert_snapshot("daily", dt, [self._make_item(f"o/d{d}", 1)])
        before = self.store._conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        self.assertEqual(before, 50)
        deleted = self.store.enforce_retention(
            daily_keep_days=30, weekly_keep_weeks=12, monthly_keep_months=12
        )
        # 應保留 30 天 daily + 跨月份/週期更早的代表; 此處 50 天都落在同一個月 (1-2 月視情況)
        # 至少保留 30 筆 (daily 30 天)
        after = self.store._conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        self.assertGreaterEqual(after, 30)
        # weekly 應至少有 7-8 個 ISO week (50 天跨 7-8 週)
        weekly = self.store.list_retained()["weekly"]
        self.assertGreaterEqual(len(weekly), 7)
        self.assertLessEqual(len(weekly), 12)
        monthly = self.store.list_retained()["monthly"]
        self.assertGreaterEqual(len(monthly), 1)
        self.assertLessEqual(len(monthly), 12)


class TestLlm(unittest.TestCase):
    def test_mock_provider(self) -> None:
        client = LlmClient({"provider": "mock", "model": "x"})
        r = client.summarize(repo_full_name="a/b", original_desc="hello", readme="...")
        self.assertEqual(r["source"], "mock")
        self.assertTrue(r["title_zh"])
        self.assertIn("a/b", r["summary_zh"])

    def test_extract_json(self) -> None:
        from src.llm import _extract_json
        self.assertEqual(_extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(_extract_json('```json\n{"a": 2}\n```'), {"a": 2})
        self.assertEqual(_extract_json("prefix {not-json} suffix"), None)
        self.assertIsNone(_extract_json(""))


class TestSite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.store = storage.Storage(self.tmp_db.name)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cfg = {
            "site": {
                "output_dir": self.tmp_dir.name,
                "site_title": "Test",
                "site_subtitle": "subtitle",
                "base_url": "",
                "items_per_page": 15,
            },
            "storage": {"sqlite_path": self.tmp_db.name, "retention": {}},
        }

    def tearDown(self) -> None:
        self.store.close()
        Path(self.tmp_db.name).unlink(missing_ok=True)
        self.tmp_dir.cleanup()

    def _seed(self) -> None:
        items = [
            {
                "rank": i,
                "repo_full_name": f"o/r{i}",
                "repo_url": f"https://github.com/o/r{i}",
                "description": f"desc {i}",
                "language": "Go",
                "stars": 100 + i,
                "forks": 10,
                "stars_period": 5,
                "title_zh": f"r{i} 中文",
                "summary_zh": f"r{i} 摘要",
                "tags": ["tagA"],
                "summary_source": "mock",
                "readme_excerpt": "...",
            }
            for i in range(1, 4)
        ]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.upsert_snapshot("daily", now, items)

    def test_generate_site(self) -> None:
        self._seed()
        out = site.generate_site(self.store, self.cfg)
        # 至少 index / daily / weekly / monthly + 1 snapshot 頁
        self.assertIn("index.html", out)
        self.assertIn("daily.html", out)
        self.assertIn("weekly.html", out)
        self.assertIn("monthly.html", out)
        # index.html 內容含中文摘要
        index_path = Path(self.tmp_dir.name) / "index.html"
        html = index_path.read_text(encoding="utf-8")
        self.assertIn("r1 中文", html)
        # snapshot 頁存在
        date = datetime.now(timezone.utc).date().isoformat()
        snap_path = Path(self.tmp_dir.name) / "snapshots" / date / "daily.html"
        self.assertTrue(snap_path.exists())


class TestConfig(unittest.TestCase):
    def test_missing_file_raises(self) -> None:
        from src.config import load_config
        with self.assertRaises(FileNotFoundError):
            load_config("/tmp/does_not_exist_opentop.yml")

    def test_top_n_must_be_15(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write(
                "github_trending:\n  top_n: 10\n  since_options: [daily]\n"
                "llm:\n  provider: mock\nstorage:\n  sqlite_path: /tmp/x.db\nsite:\n  output_dir: /tmp/x\n"
            )
            path = f.name
        try:
            from src.config import load_config
            with self.assertRaises(ValueError):
                load_config(path)
        finally:
            Path(path).unlink(missing_ok=True)


class TestEnvLoader(unittest.TestCase):
    def test_load_env_file(self) -> None:
        from src.env_loader import load_env_file
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write(
                "# comment\n"
                "FOO=bar\n"
                "export BAZ='qux qux'\n"
                "QUOTED=\"double\"\n"
                "EMPTY=\n"
            )
            path = f.name
        for k in ("FOO", "BAZ", "QUOTED", "EMPTY"):
            os.environ.pop(k, None)
        try:
            loaded = load_env_file(path)
            self.assertEqual(loaded, ["FOO", "BAZ", "QUOTED", "EMPTY"])
            self.assertEqual(os.environ["FOO"], "bar")
            self.assertEqual(os.environ["BAZ"], "qux qux")
            self.assertEqual(os.environ["QUOTED"], "double")
            self.assertEqual(os.environ["EMPTY"], "")
        finally:
            Path(path).unlink(missing_ok=True)
            for k in ("FOO", "BAZ", "QUOTED", "EMPTY"):
                os.environ.pop(k, None)

    def test_does_not_override_existing(self) -> None:
        from src.env_loader import load_env_file
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("EXISTING=fromfile\n")
            path = f.name
        os.environ["EXISTING"] = "fromshell"
        try:
            load_env_file(path)
            self.assertEqual(os.environ["EXISTING"], "fromshell")
        finally:
            Path(path).unlink(missing_ok=True)
            os.environ.pop("EXISTING", None)

    def test_missing_file_is_silent(self) -> None:
        from src.env_loader import load_env_file
        self.assertEqual(load_env_file("/tmp/nonexistent_opentop.env"), [])


class TestReadmeCache(unittest.TestCase):
    def test_cache_hit_and_miss(self) -> None:
        """驗證 storage cache 寫入/讀取邏輯。"""
        from src.storage import Storage
        fd, path = tempfile.mkstemp(suffix='.db')
        import os as _os
        _os.close(fd)
        try:
            store = Storage(path)
            # 1) 沒快取 → None
            self.assertIsNone(store.get_cached_readme('foo/bar'))
            # 2) 寫入 source=none → 也視為 None (避免重試失敗 repo)
            store.put_cached_readme('foo/bar', None, 'none')
            self.assertIsNone(store.get_cached_readme('foo/bar'))
            # 3) 寫入有效 content → 命中
            store.put_cached_readme('foo/bar', '# Hello World', 'raw_main')
            self.assertEqual(store.get_cached_readme('foo/bar'), '# Hello World')
            # 4) 同 repo 再寫入會 upsert
            store.put_cached_readme('foo/bar', '# Updated', 'raw_master')
            self.assertEqual(store.get_cached_readme('foo/bar'), '# Updated')
            store.close()
        finally:
            Path(path).unlink(missing_ok=True)

    def test_cache_expiry(self) -> None:
        """max_age_days=0 應永遠過期。"""
        from src.storage import Storage
        fd, path = tempfile.mkstemp(suffix='.db')
        import os as _os
        _os.close(fd)
        try:
            store = Storage(path)
            store.put_cached_readme('x/y', 'content', 'raw_main')
            self.assertIsNone(store.get_cached_readme('x/y', max_age_days=0))
            self.assertEqual(store.get_cached_readme('x/y', max_age_days=7), 'content')
            store.close()
        finally:
            Path(path).unlink(missing_ok=True)


class TestRateLimit(unittest.TestCase):
    def test_403_remaining_zero_raises(self) -> None:
        from src.readme_fetcher import _check_rate_limit_response
        class R:
            status_code = 403
            headers = {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '9999999999'}
            text = 'rate limit exceeded'
        with self.assertRaises(Exception) as ctx:
            _check_rate_limit_response(R())
        # reset_at 應有值
        self.assertIsNotNone(ctx.exception.reset_at)

    def test_403_non_rate_limit_passes(self) -> None:
        """403 但非 rate limit (例如私人 repo) 不應拋例外。"""
        from src.readme_fetcher import _check_rate_limit_response
        class R:
            status_code = 403
            headers = {}
            text = 'Not Found'
        # 不拋
        _check_rate_limit_response(R())

    def test_429_raises_with_retry_after(self) -> None:
        from src.readme_fetcher import _check_rate_limit_response
        class R:
            status_code = 429
            headers = {'Retry-After': '60'}
            text = 'Too Many Requests'
        with self.assertRaises(Exception) as ctx:
            _check_rate_limit_response(R())
        self.assertEqual(ctx.exception.retry_after_sec, 60)
    def test_basic_conversion(self) -> None:
        from src.llm import s2t
        # "软" → "軟"
        self.assertEqual(s2t('软'), '軟')
        # "网络" → "網絡" (兩個字都替換)
        self.assertEqual(s2t('网络'), '網絡')
        # "数据" → "數據"
        self.assertEqual(s2t('数据'), '數據')
        # 沒命中就原樣
        self.assertEqual(s2t('hello'), 'hello')
        self.assertEqual(s2t(''), '')
        self.assertEqual(s2t(None), None)

    def test_conversion_keys_present(self) -> None:
        """確認字表涵蓋最常見的簡繁對。"""
        from src.llm import _S2T_MAP
        must_have = {
            '软': '軟', '体': '體', '网': '網', '数': '數', '据': '據',
            '视': '視', '图': '圖', '设': '設', '计': '計', '语': '語',
            '应': '應', '开': '開', '发': '發', '类': '類', '码': '碼',
            '链': '鏈', '终': '終', '异': '異', '显': '顯', '脑': '腦',
        }
        for k, v in must_have.items():
            self.assertEqual(_S2T_MAP.get(k), v, f'missing mapping {k} → {v}')


if __name__ == "__main__":
    unittest.main()
