# opentop

<p align="center">
  <img src="https://img.shields.io/badge/opentop-📊_GitHub_Trending_中文速覽-0969da?style=for-the-badge&logo=github&logoColor=white" alt="opentop">
</p>

<p align="center">
  <img src="docs/assets/logo.svg" alt="opentop" width="280" onerror="this.style.display='none'">
</p>

> 每日自動抓取 **GitHub Trending** 每日／每週／每月前 15 名，用 LLM 摘要成繁體中文，產出可部署到 GitHub Pages 的靜態網頁。

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![GitHub Pages](https://img.shields.io/badge/deploy-GitHub%20Pages-blue)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20compatible-orange)

## 為什麼用 opentop？

- **零摩擦**：一行指令抓取 + 摘要 + 部署
- **保留歷史**：SQLite 自動保留 daily 30 天 / weekly 12 週 / monthly 12 月
- **可離線測試**：內建 `mock` provider，不需要 API key 就能跑完整流程
- **無前端依賴**：靜態 HTML + 純 CSS，GitHub Pages 開箱即用

## 功能

- 每日／每週／每月 GitHub Trending 前 15 名自動抓取
- 每個 repo 透過 LLM (OpenAI / 相容端點 / mock) 把 README 摘要成繁體中文
- 翻譯標題與描述為中文，產生標籤 (tags)
- SQLite 儲存歷史榜單 + 自動保留策略
- 靜態 HTML 網站 (index / daily / weekly / monthly + 各 snapshot 詳細頁)
- cron 友善：每日排程、冪等執行、可跳過 LLM 重生頁面

## 保留策略

| 類型 | 保留範圍 | 說明 |
| --- | --- | --- |
| `daily` | 最近 30 天 | 每日一份完整榜單 |
| `weekly` | 最近 12 週 | 從 daily 中每週取最新一筆代表 |
| `monthly` | 最近 12 個月 | 從 daily 中每月取最新一筆代表 |

三者聯集 = 實際保留的 snapshot 集合。超出範圍的 snapshot 連同 items 自動 cascade 刪除。

---

```
opentop/
├── config.example.yml         # 設定檔範本 (複製成 config.yml)
├── requirements.txt
├── README.md
├── src/
│   ├── config.py              # 設定載入與驗證
│   ├── scraper.py             # GitHub Trending 抓取
│   ├── readme_fetcher.py      # README 抓取
│   ├── llm.py                 # LLM 摘要 (OpenAI / 相容 / mock)
│   ├── storage.py             # SQLite 寫入/查詢/保留
│   └── site.py                # 靜態 HTML 產生器
├── scripts/
│   ├── run.py                 # 主排程 (CLI)
│   └── install_cron.sh        # 安裝/移除 cron 輔助
├── tests/
│   └── test_pipeline.py
├── data/                      # SQLite 存放 (預設)
├── docs/                      # 靜態 HTML 輸出 (給 GitHub Pages)
└── logs/                      # cron 執行紀錄
```

## 快速開始 (30 秒體驗)

```bash
# 1. 安裝相依 (會自動建 venv + 複製 config)
./scripts/install_cron.sh setup

# 2. 先用 mock 跑一次完整流程 (不需 API key)
./scripts/install_cron.sh run -- --no-llm

# 3. 打開 docs/index.html 看結果, 或:
python3 -m http.server -d docs 8000
# → http://localhost:8000
```

完成後想用真實 LLM 摘要，把 `config.yml` 裡 `llm.provider: mock` 改成 `openai`，設定 `OPENAI_API_KEY` 環境變數，再跑一次 `./scripts/install_cron.sh run` 即可。

> ⚠️ **安全提醒**：`config.yml` 已加入 `.gitignore`，**不會**被 commit。API key 請用環境變數（推薦），不要直接寫進檔案。

## 安裝

```bash
cd opentop
./scripts/install_cron.sh setup    # 建 venv, 安裝相依
# config.yml 已自動建立, 編輯填入 OPENAI_API_KEY 或保持 mock
```

## 設定 LLM API Key

opentop 從 **環境變數** 讀取 key（推薦做法，不會誤 commit）。`config.yml` 裡的 `llm.api_key_env` 決定讀哪個變數名稱（預設 `OPENCODE_ZEN_API_KEY`）。

### 方法 A：`.env` 檔（推薦）

opentop 啟動時會自動讀取 repo 根目錄的 `.env` 檔，不需手動 export。

```bash
cp .env.example .env
# 編輯 .env 填入真實 key
./scripts/install_cron.sh run -- --since daily
```

`.env` 已在 `.gitignore` 內，不會被 commit。

### 方法 B：shell 環境變數

```bash
# 一次性
export OPENCODE_ZEN_API_KEY="sk-..."

# 永久: 寫進 shell 啟動檔
echo 'export OPENCODE_ZEN_API_KEY="sk-..."' >> ~/.zshrc   # macOS
echo 'export OPENCODE_ZEN_API_KEY="sk-..."' >> ~/.bashrc  # Linux
source ~/.zshrc

# 確認
echo $OPENCODE_ZEN_API_KEY
```

### 方法 C：給 cron 排程用

cron 不會讀 `~/.zshrc`，需要把 env 放在固定位置：

```bash
mkdir -p ~/.config/opentop
cat > ~/.config/opentop/env <<EOF
export OPENCODE_ZEN_API_KEY="sk-..."
EOF
chmod 600 ~/.config/opentop/env
```

`install_cron.sh cron` 會自動讀這個檔。

## 常用指令

# 只抓特定 since (除錯用)
./scripts/install_cron.sh run -- --since daily

# 只跑保留清理
./scripts/install_cron.sh run -- --retention-only

# 跳過抓取, 只重生 HTML
./scripts/install_cron.sh run -- --skip-scrape
```

> `--` 後的參數會原樣傳給 `scripts/run.py`。

## 每日排程

```bash
# 預設每天 07:00 執行
./scripts/install_cron.sh cron

# 或自訂時間 (例: 每天 09:30)
./scripts/install_cron.sh cron 9 30

# 移除
./scripts/install_cron.sh unschedule
```

實作：寫入使用者 crontab：

```
0 7 * * * cd <repo> && <repo>/.venv/bin/python scripts/run.py >> logs/run.log 2>&1
```

## LLM 設定

`config.yml` 的 `llm` 區段，預設使用 OpenCode Zen 的 `deepseek-v4-flash-free` (免費)：

```yaml
llm:
  provider: openai_compatible
  api_key_env: OPENCODE_ZEN_API_KEY
  base_url: https://opencode.ai/zen/v1
  model: deepseek-v4-flash-free
  max_output_tokens: 800
  temperature: 0.2
  timeout_sec: 60
```

Provider 選項：
- **openai**：OpenAI 官方 (`provider: openai`, `base_url: https://api.openai.com/v1`)
- **openai_compatible**：任何 OpenAI Chat Completions 相容端點 (OpenCode Zen、Ollama、llama.cpp、vLLM、OpenRouter)
- **mock**：不打 API，用樣板回傳 (CI / 離線測試)

推薦模型 (OpenCode Zen)：
- `big-pickle` — 免費，stealth model
- `deepseek-v4-flash-free` — 免費，DeepSeek 速度快 (預設)
- `qwen3.5-plus` — $0.20/$1.20 per 1M tokens，中文最強
- `claude-sonnet-4-5` — $3/$15 per 1M，頂級品質

## 部署到 GitHub Pages (推薦流程)

1. 把程式碼推上 GitHub repo
2. 到 repo **Settings → Secrets and variables → Actions** 新增 `OPENCODE_ZEN_API_KEY`
3. 到 **Settings → Pages → Source** 選 `GitHub Actions`
4. GitHub Actions 每天 UTC 23:00 自動跑 (`.github/workflows/daily.yml`)，產出推到 `docs/`
5. 部署網址：`https://<user>.github.io/<repo>/`

> 手動觸發：到 Actions 頁 → Daily Trending Update → Run workflow，可選 `daily/weekly/monthly` 與 `skip_llm`。

> 若用 cron 取代 Actions (本機跑)：見上一段「每日排程」。

## 測試

```bash
.venv/bin/python -m unittest discover -s tests -v
```

涵蓋：config 載入、scraper HTML 解析 (本地 fixture)、SQLite 寫入/查詢、保留策略三聯集邏輯、LLM mock provider、靜態頁面產出。

## 行為細節

- **抓取**：直接打 `https://github.com/trending?since=daily|weekly|monthly`，解析 `article.Box-row`。
- **README 抓取**：依序試 raw.githubusercontent.com (main → master → HEAD) → GitHub API；命中 SQLite `readme_cache` (TTL 7 天) 會直接跳過網路呼叫。
- **Rate limit 偵測**：捕 GitHub API 的 403/429 + `X-RateLimit-Remaining=0`，拋 `RateLimitError` 含 reset 時間。模組級短路避免雪崩；run.py 捕捉後剩餘 repos 改用原始 description 摘要，不中斷整體管線。
- **LLM 摘要**：System prompt 強制繁中（台灣用語清單），單次 prompt 強制 JSON (`response_format: {type: json_object}`)。失敗重試 3 次，降級為 mock。輸出後再用 `s2t()` 字表後處理，補上 prompt 漏字。
- **儲存**：同一日同 since 的 snapshot 用 `upsert` (UNIQUE 約束)，items 整批取代，確保冪等。
- **保留**：以 `snapshot_date` (UTC 日期) 為基準。daily/weekly/monthly 三者聯集，過期 snapshot 連同 items cascade 刪除。
- **HTML**：純 server-side，無 JS；亮暗色隨 `prefers-color-scheme`；連結以相對路徑為主，無外部依賴。

## License

MIT
