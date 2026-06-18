"""LLM 摘要模組: 摘要 README + 翻譯標題/描述為中文。

支援 provider:
  - openai             → 走官方 OpenAI Chat Completions (gpt-4o-mini 等)
  - openai_compatible  → 走任何 OpenAI 相容端點 (含本地 llama.cpp / ollama 等)
  - mock               → 不打 API, 直接用樣板回傳 (供離線測試 / CI)

每個 repo 一次呼叫 LLM, prompt 要求 JSON 輸出:
  { "title_zh": "...", "summary_zh": "...", "tags": ["...", "..."] }

實作策略:
  - 重試 + 指數退避, 失敗 3 次後降級到 mock (確保管線不會因 LLM 暫時不可用而中斷)
  - 嚴格解析 JSON, 解析失敗時以原文 + 截斷描述回填
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests


# 簡轉繁常用字表 (key 簡體, value 繁體)。涵蓋 LLM 摘要最常見的簡繁混用。
# 這層是 prompt 強制後的保險: 即便模型偶爾漏字, 後處理仍能轉。
_S2T_MAP: dict[str, str] = {
    "软": "軟", "体": "體", "术": "術", "业": "業", "务": "務", "会": "會",
    "系": "系", "统": "統", "网": "網", "络": "絡", "数": "數", "据": "據",
    "视": "視", "频": "頻", "图": "圖", "程": "程", "序": "序", "线": "線",
    "结": "結", "构": "構", "处": "處", "理": "理", "应": "應", "用": "用",
    "开": "開", "发": "發", "环": "環", "境": "境", "设": "設", "计": "計",
    "语": "語", "言": "言", "类": "類", "别": "別", "项": "項", "参": "參",
    "测": "測", "试": "試", "码": "碼", "链": "鏈", "接": "接", "终": "終",
    "端": "端", "连": "連", "异": "異", "复": "複", "杂": "雜", "显": "顯",
    "示": "示", "脑": "腦", "习": "習", "创": "創", "建": "建", "动": "動",
    "态": "態", "个": "個", "们": "們", "从": "從", "议": "議", "记": "記",
    "录": "錄", "载": "載", "输": "輸", "户": "戶", "权": "權", "限": "限",
    "认": "認", "证": "證", "书": "書", "写": "寫", "读": "讀", "听": "聽",
    "说": "說", "话": "話", "调": "調", "变": "變", "换": "換", "长": "長",
    "宽": "寬", "颜": "顏", "页": "頁", "响": "響", "办": "辦", "型": "型",
    "符": "符", "号": "號", "标": "標", "单": "單", "据": "據", "库": "庫",
    "格": "格", "列": "列", "行": "行", "过": "過", "滤": "濾", "存": "存",
    "取": "取", "查": "查", "询": "詢", "口": "口", "请": "請", "求": "求",
    "补": "補", "警": "警", "报": "報", "错": "錯", "误": "誤", "异": "異",
    "常": "常", "崩": "崩", "溃": "潰", "风": "風", "险": "險", "优": "優",
    "备": "備", "份": "份", "热": "熱", "门": "門", "导": "導", "航": "航",
    "盘": "盤", "压": "壓", "缩": "縮", "解": "解", "场": "場", "景": "景",
    "验": "驗",
}
_S2T_TRANS = str.maketrans(_S2T_MAP)


def s2t(text: str) -> str:
    """簡轉繁 (簡易字表版本)。不會處理所有簡繁差異, 但能補上 prompt 漏字的情況。"""
    if not text:
        return text
    return text.translate(_S2T_TRANS)


SYSTEM_PROMPT = (
    "你是一位熟悉開源專案的技術編輯，協助把英文 GitHub 專案摘要給台灣繁體中文讀者。"
    "【語言強制規則】你必須使用繁體中文 (Traditional Chinese), 使用台灣常用詞彙, 禁止使用簡體字。"
    "常見簡轉繁對照: 软體→軟體, 信息→資訊, 默认→預設, 文件→文件, 网络→網路, "
    "数据→資料, 视频→影片, 视频→影片, 音频→音訊, 图像→影像, 程序→程式, 线程→執行緒, "
    "异→異, 复→複, 项→項, 设→設, 测→測, 验→驗, 码→碼, 链→鏈, 终→終, 应→應, 显→顯, "
    "脑→腦, 习→習, 创→創, 动→動, 类→類, 语→語, 进→進, 内→內, 序→順序, 输→輸, 入→入。"
    "請閱讀 README 內容, 並輸出一個 JSON 物件, 不得有任何其他文字。"
    "JSON 結構: "
    '{"title_zh": "繁體中文標題 (一句, 不超過 30 字, 必須用繁體)", '
    '"summary_zh": "繁體中文摘要 (3-5 句, 涵蓋用途/特色/適合誰用, 必須用繁體)", '
    '"tags": ["繁體中文標籤 1", "繁體中文標籤 2", "繁體中文標籤 3"]}'
)

def _build_user_prompt(repo_full_name: str, original_desc: str | None, readme: str | None) -> str:
    head = f"Repository: {repo_full_name}\n"
    if original_desc:
        head += f"Original description: {original_desc}\n"
    body = readme.strip() if readme else "(README 內容為空或抓取失敗)"
    # 限制長度, 避免 token 爆炸
    body = body[:8000]
    return head + "\n---README---\n" + body


def _mock_summary(repo_full_name: str, original_desc: str | None) -> dict[str, str | list[str]]:
    desc = (original_desc or "").strip()
    summary = (
        f"(離線模式) 這是 {repo_full_name} 的本地預設摘要。"
        + (f" 原始描述: {desc[:200]}" if desc else "")
    )
    return {
        "title_zh": f"{repo_full_name} 中文速覽",
        "summary_zh": summary,
        "tags": ["GitHub", "趨勢"],
    }


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # 移除常見的 markdown 圍欄
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


class LlmClient:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.provider = cfg.get("provider", "mock")
        self.api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        self.base_url = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.model = cfg.get("model", "gpt-4o-mini")
        self.max_output_tokens = int(cfg.get("max_output_tokens", 600))
        self.temperature = float(cfg.get("temperature", 0.2))
        self.timeout_sec = int(cfg.get("timeout_sec", 60))

    def _api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def _chat(self, messages: list[dict[str, str]]) -> str:
        if self.provider == "mock":
            raise RuntimeError("mock provider does not call API")
        key = self._api_key()
        if not key:
            raise RuntimeError(
                f"缺少 API key: 請設定環境變數 {self.api_key_env}"
            )
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout_sec,
                )
                if r.status_code >= 500 or r.status_code == 429:
                    raise RuntimeError(f"upstream {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM call failed after 3 attempts: {last_err}")

    def summarize(
        self,
        *,
        repo_full_name: str,
        original_desc: str | None,
        readme: str | None,
    ) -> dict[str, Any]:
        """呼叫 LLM 產生中文摘要; 失敗時降級回 mock。

        回傳 dict 含:
          title_zh, summary_zh, tags(list[str]), source (llm|mock)
        """
        if self.provider == "mock":
            mock = _mock_summary(repo_full_name, original_desc)
            mock["source"] = "mock"
            return mock

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(repo_full_name, original_desc, readme)},
        ]
        try:
            content = self._chat(messages)
        except Exception as e:  # noqa: BLE001
            print(f"  ! LLM 失敗, 降級 mock: {e}")
            mock = _mock_summary(repo_full_name, original_desc)
            mock["source"] = "mock"
            return mock
        parsed = _extract_json(content) or {}
        # 強制後處理: 即便 prompt 漏字, 簡轉繁能補上
        title_zh = s2t((parsed.get("title_zh") or "").strip()) or f"{repo_full_name} 中文速覽"
        summary_zh = s2t((parsed.get("summary_zh") or "").strip())
        tags_raw = parsed.get("tags") or []
        if isinstance(tags_raw, str):
            tags_raw = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif not isinstance(tags_raw, list):
            tags_raw = []
        tags = [s2t(str(t)) for t in tags_raw[:5]]

        if not summary_zh:
            # 解析失敗或內容缺漏, 用原文降級
            summary_zh = (original_desc or "").strip() or f"{repo_full_name} (無可用摘要)"
            return {
                "title_zh": title_zh,
                "summary_zh": summary_zh,
                "tags": tags,
                "source": "fallback",
            }
        return {
            "title_zh": title_zh,
            "summary_zh": summary_zh,
            "tags": tags,
            "source": "llm",
        }
