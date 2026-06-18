# Changelog

All notable changes to opentop will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-18

### Added
- 每日／每週／每月 GitHub Trending 前 15 名自動抓取
- LLM 摘要 README 為繁體中文 + 翻譯標題／描述 + 標籤生成
- 多 provider 支援：OpenAI 官方、OpenAI 相容端點（OpenCode Zen / Ollama / llama.cpp / vLLM / OpenRouter）、mock
- SQLite 儲存歷史榜單 + 自動保留策略（daily 30 天 / weekly 12 週 / monthly 12 月）
- 靜態 HTML 網站（index / daily / weekly / monthly + 各 snapshot 詳細頁），GitHub Pages 開箱即用
- README 快取層（TTL 7 天），避免重複打 GitHub API
- GitHub Rate Limit 偵測（403/429 + `X-RateLimit-Remaining=0`）含 reset 時間感知；觸發時剩餘 repos 改用原始 description 摘要，管線不中斷
- 簡轉繁後處理（s2t 字表），確保 LLM 偶爾漏字也能被補正
- 系統 prompt 強制繁體中文（台灣用語清單 + 簡繁對照）
- `.env` 自動載入（不需手動 export），含不覆寫既有 shell 環境變數的安全設計
- LLM 失敗重試 3 次（含指數退避）後降級為 mock，管線不中斷
- LLM JSON 解析失敗時降級回原文 + `summary_source: fallback`
- 同日同 since 的 snapshot 用 `upsert`，確保冪等可重跑
- 一鍵安裝／執行／排程輔助腳本 `scripts/install_cron.sh`
- GitHub Actions 自動排程（每天 UTC 23:00 = 台灣 07:00）含手動觸發與 since 選項
- GitHub Actions 自動部署到 GitHub Pages（upload artifact + deploy-pages）
- opentop 品牌 logo（橫幅 + 正方形 SVG，內嵌於 site.py）
- 21 個單元測試（config / scraper / storage / retention / LLM / site / env / rate limit / s2t）
- 完整 README 含安裝、設定、部署、行為細節文件

### Security
- `config.yml` 與 `.env` 自動 `.gitignore`，API key 永不進版控
- 推薦使用環境變數（不要直接寫 key 在 config）
- Cron 排程支援獨立的 `~/.config/opentop/env` 檔（與 `~/.zshrc` 分離）
- 支援 GitHub Actions repository secrets

[1.0.0]: https://github.com/<user>/opentop/releases/tag/v1.0.0
