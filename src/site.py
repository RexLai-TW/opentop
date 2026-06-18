"""靜態 HTML 頁面產生器 (GitHub Pages friendly)。

產出結構:
  docs/
    index.html            -- 最新一日 daily + 各 since 入口
    daily.html            -- 最近 30 天每日列表 (點進去看詳情)
    weekly.html           -- 最近 12 週列表
    monthly.html          -- 最近 12 月列表
    snapshots/
      <YYYY-MM-DD>/<since>.html   -- 單一 snapshot 詳細頁
    assets/
      site.css             -- 樣式 (內嵌 base64 也行, 這裡分檔方便部署後維護)

所有連結以相對路徑為主, base_url 設定後會在 <head> 加 canonical 與 og:url。
無 JS, 純 server-side 渲染, GitHub Pages 開箱即用。
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import Storage


BASE_CSS = """
:root {
  --bg: #fafafa;
  --fg: #1f2328;
  --muted: #57606a;
  --accent: #0969da;
  --border: #d0d7de;
  --card: #ffffff;
  --tag-bg: #ddf4ff;
  --tag-fg: #0969da;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --fg: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --border: #30363d;
    --card: #161b22;
    --tag-bg: #1f6feb33;
    --tag-fg: #79c0ff;
  }
}
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", "PingFang TC", "Microsoft JhengHei", sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 0; line-height: 1.6; }
.container { max-width: 980px; margin: 0 auto; padding: 24px 20px; }
.site-header { margin-bottom: 16px; }
.site-header .logo { height: 48px; width: auto; display: block; }
nav.bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 24px; }
nav.bar a { padding: 6px 12px; border: 1px solid var(--border); border-radius: 999px;
            color: var(--fg); text-decoration: none; background: var(--card); }
nav.bar a:hover { border-color: var(--accent); color: var(--accent); }
.cards { display: grid; gap: 16px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
        padding: 16px 18px; }
.card h2 { margin: 0 0 4px; font-size: 1.15rem; }
.card h2 a { color: var(--accent); text-decoration: none; }
.card .meta { color: var(--muted); font-size: 0.9rem; margin-bottom: 8px; }
.card p { margin: 8px 0; }
.card .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.card .tag { background: var(--tag-bg); color: var(--tag-fg); padding: 2px 8px;
             border-radius: 999px; font-size: 0.78rem; }
.list { list-style: none; padding: 0; margin: 0; }
.list li { padding: 10px 0; border-bottom: 1px solid var(--border); display: flex;
           justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.list li:last-child { border-bottom: none; }
.list a { color: var(--accent); text-decoration: none; }
.list .date { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 0.9rem; }
.section-title { margin: 32px 0 12px; font-size: 1.3rem; border-bottom: 1px solid var(--border);
                 padding-bottom: 6px; }
footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border);
         color: var(--muted); font-size: 0.85rem; }
"""

BASE_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60" role="img" aria-label="opentop">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0969da"/>
      <stop offset="1" stop-color="#1f6feb"/>
    </linearGradient>
  </defs>
  <rect x="2" y="6" width="48" height="48" rx="10" fill="url(#g)"/>
  <path d="M10 42 L20 30 L28 36 L42 18" fill="none" stroke="white" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M34 18 L42 18 L42 26" fill="none" stroke="white" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="60" y="40" font-family="-apple-system, 'Segoe UI', sans-serif" font-size="28" font-weight="700" fill="currentColor" letter-spacing="-0.5">opentop</text>
</svg>
"""

BASE_LOGO_SQUARE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="opentop">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0969da"/>
      <stop offset="1" stop-color="#1f6feb"/>
    </linearGradient>
  </defs>
  <rect x="4" y="4" width="92" height="92" rx="20" fill="url(#g)"/>
  <path d="M20 76 L40 50 L52 62 L80 30" fill="none" stroke="white" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M62 30 L80 30 L80 48" fill="none" stroke="white" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def _esc(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s))


def _url(base: str, rel: str) -> str:
    if not base:
        return rel
    return base.rstrip("/") + "/" + rel.lstrip("/")


def _layout(title: str, subtitle: str, base_url: str, body: str) -> str:
    canonical = _url(base_url, "index.html") if base_url else ""
    logo_url = _url(base_url, "assets/logo.svg")
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<link rel="icon" type="image/svg+xml" href="{_esc(logo_url)}">
<link rel="stylesheet" href="assets/site.css">
{f'<link rel="canonical" href="{_esc(canonical)}">' if canonical else ''}
<meta name="description" content="{_esc(subtitle)}">
</head>
<body>
<div class="container">
<header class="site-header">
  <img src="{_esc(logo_url)}" alt="opentop" class="logo">
</header>
{body}
<footer>
  由 opentop 自動產生 · 資料來源 GitHub Trending · 摘要由 LLM 生成
</footer>
</div>
</body>
</html>
"""


def _nav(active: str, base_url: str) -> str:
    items = [
        ("index.html", "首頁"),
        ("daily.html", "每日 (30 天)"),
        ("weekly.html", "每週 (12 週)"),
        ("monthly.html", "每月 (12 月)"),
    ]
    links = []
    for href, label in items:
        cls = ' class="active"' if href == active else ""
        links.append(f'<a href="{_url(base_url, href)}"{cls}>{label}</a>')
    return '<nav class="bar">' + "".join(links) + "</nav>"


def _render_items(items: list[dict[str, Any]], snapshot_relpath: str) -> str:
    parts: list[str] = []
    for it in items:
        tags_html = "".join(
            f'<span class="tag">{_esc(t)}</span>' for t in (it.get("tags") or [])
        )
        title_zh = it.get("title_zh") or it["repo_full_name"]
        summary_zh = it.get("summary_zh") or ""
        lang = it.get("language") or ""
        stars = it.get("stars") or 0
        period = it.get("stars_period") or 0
        meta_bits = []
        if lang:
            meta_bits.append(f"<span>{_esc(lang)}</span>")
        if stars:
            meta_bits.append(f"<span>★ {_esc(stars)}</span>")
        if period:
            meta_bits.append(f"<span>+{_esc(period)} 本期</span>")
        meta_html = " · ".join(meta_bits)
        sep = " · "
        meta_line = (
            f'<div class="meta">{meta_html}{sep}'
            f'<a href="{_esc(it["repo_url"])}">{_esc(it["repo_full_name"])}</a></div>'
        )
        parts.append(
            "<article class=\"card\">\n"
            f'  <h2><a href="{_esc(it["repo_url"])}" target="_blank" rel="noopener">{_esc(title_zh)}</a></h2>\n'
            f"  {meta_line}\n"
            f"  <p>{_esc(summary_zh)}</p>\n"
            f'  <div class="tags">{tags_html}</div>\n'
            "</article>\n"
        )
    return "".join(parts)


def generate_site(storage: Storage, cfg: dict[str, Any]) -> dict[str, str]:
    """產生所有靜態頁面; 回傳 {相對路徑: 內容}。"""
    site = cfg["site"]
    output_dir = Path(site["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "assets").mkdir(exist_ok=True)
    (output_dir / "snapshots").mkdir(exist_ok=True)

    base_url = site.get("base_url", "") or ""
    title = site.get("site_title", "GitHub 趨勢中文速覽")
    subtitle = site.get("site_subtitle", "")

    # 寫入 css 與 logo
    (output_dir / "assets" / "site.css").write_text(BASE_CSS, encoding="utf-8")
    (output_dir / "assets" / "logo.svg").write_text(BASE_LOGO, encoding="utf-8")
    (output_dir / "assets" / "logo-square.svg").write_text(BASE_LOGO_SQUARE, encoding="utf-8")
    generated: dict[str, str] = {}

    # 取得所有保留的 snapshots
    retained = storage.list_retained()
    # --- snapshot 詳細頁 ---
    for since in ("daily", "weekly", "monthly"):
        for snap in retained[since]:
            items = storage.get_items(snap["id"])
            date = snap["snapshot_date"]
            page_title = f"{title} · {since} · {date}"
            body = _nav("index.html" if since == "daily" else f"{since}.html", base_url)
            body += f"<h1>{_esc(since)} · {_esc(date)}</h1>"
            body += f"<div class='subtitle'>抓取於 {_esc(snap['fetched_at'])} · 共 {_esc(snap['item_count'])} 個 repo</div>"
            body += '<div class="cards">' + _render_items(items, "") + "</div>"
            path = f"snapshots/{date}/{since}.html"
            (output_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (output_dir / path).write_text(_layout(page_title, subtitle, base_url, body), encoding="utf-8")
            generated[path] = path

    # --- index.html ---
    daily_snap = retained["daily"][0] if retained["daily"] else None
    body = _nav("index.html", base_url)
    body += f"<h1>{_esc(title)}</h1><div class='subtitle'>{_esc(subtitle)}</div>"

    if daily_snap:
        items = storage.get_items(daily_snap["id"])
        body += f"<h2 class='section-title'>最新每日榜單 · {_esc(daily_snap['snapshot_date'])}</h2>"
        body += '<div class="cards">' + _render_items(items, "") + "</div>"
    else:
        body += "<p>尚無資料。請先執行 <code>python -m opentop.scripts.run</code>。</p>"

    # 入口
    body += '<h2 class="section-title">瀏覽歷史</h2><ul class="list">'
    for since, label in [("daily", "每日 (近 30 天)"), ("weekly", "每週 (近 12 週)"), ("monthly", "每月 (近 12 月)")]:
        body += f'<li><a href="{_url(base_url, f"{since}.html")}">{_esc(label)}</a></li>'
    body += "</ul>"
    generated["index.html"] = "index.html"
    (output_dir / "index.html").write_text(_layout(title, subtitle, base_url, body), encoding="utf-8")

    # --- daily.html ---
    body = _nav("daily.html", base_url)
    body += f"<h1>每日榜單 · 近 30 天</h1>"
    body += '<ul class="list">'
    for snap in retained["daily"]:
        href = _url(base_url, f"snapshots/{snap['snapshot_date']}/daily.html")
        body += (
            f'<li><a href="{href}">{_esc(snap["snapshot_date"])}</a>'
            f'<span class="date">{_esc(snap["item_count"])} repos · fetched {_esc(snap["fetched_at"])}</span></li>'
        )
    body += "</ul>"
    (output_dir / "daily.html").write_text(_layout(f"{title} · 每日", subtitle, base_url, body), encoding="utf-8")
    generated["daily.html"] = "daily.html"

    # --- weekly.html / monthly.html ---
    for since, limit_label in (("weekly", "12 週"), ("monthly", "12 月")):
        body = _nav(f"{since}.html", base_url)
        body += f"<h1>{'每週' if since == 'weekly' else '每月'}榜單 · 近 {limit_label}</h1>"
        body += '<ul class="list">'
        for snap in retained[since]:
            href = _url(base_url, f"snapshots/{snap['snapshot_date']}/{since}.html")
            body += (
                f'<li><a href="{href}">{_esc(snap["snapshot_date"])}</a>'
                f'<span class="date">{_esc(snap["item_count"])} repos</span></li>'
            )
        body += "</ul>"
        out_path = f"{since}.html"
        (output_dir / out_path).write_text(
            _layout(f"{title} · {since}", subtitle, base_url, body), encoding="utf-8"
        )
        generated[out_path] = out_path

    return generated
