"""設定載入：讀取 config.yml，提供型別安全的 dict 介面。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("config.yml")


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"config file not found: {p}. 複製 config.example.yml 為 {p} 後填入 API key。"
        )
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    _validate(cfg)
    return cfg


def _validate(cfg: dict[str, Any]) -> None:
    """最低限度的設定檢查；缺值時丟出明確錯誤訊息。"""
    gt = cfg.get("github_trending")
    if not isinstance(gt, dict):
        raise ValueError("github_trending section missing in config")
    if int(gt.get("top_n", 15)) != 15:
        # 任務規格鎖定前 15 名，這裡擋下避免後續歧異
        raise ValueError("github_trending.top_n 必須為 15")
    since = gt.get("since_options", ["daily", "weekly", "monthly"])
    for s in since:
        if s not in {"daily", "weekly", "monthly"}:
            raise ValueError(f"不支援的 since 選項: {s}")
    llm = cfg.get("llm")
    if not isinstance(llm, dict):
        raise ValueError("llm section missing in config")
    if llm.get("provider") not in {"openai", "openai_compatible", "mock"}:
        raise ValueError(f"不支援的 llm.provider: {llm.get('provider')}")
    storage = cfg.get("storage")
    if not isinstance(storage, dict):
        raise ValueError("storage section missing in config")
    site = cfg.get("site")
    if not isinstance(site, dict):
        raise ValueError("site section missing in config")
