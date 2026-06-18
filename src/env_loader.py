"""輕量級 .env 載入器: 解析 KEY=VALUE 格式, 注入 os.environ。

行為:
- 讀取 <repo>/.env (若存在)
- 每行格式: KEY=VALUE 或 export KEY=VALUE
- 忽略空行與 # 開頭的註解
- 自動去掉引號 (單/雙)
- 不覆寫已存在的環境變數 (除非 override=True)
- 靜默失敗: 檔案不存在不報錯
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _strip_quotes(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
        return v[1:-1]
    return v


def load_env_file(path: str | os.PathLike[str] | None = None, *, override: bool = False) -> list[str]:
    """載入 .env 檔, 回傳被設定的變數名稱清單。"""
    if path is None:
        path = Path.cwd() / ".env"
    else:
        path = Path(path)
    if not path.exists():
        return []
    loaded: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _LINE_RE.match(line)
            if not m:
                continue
            key, val = m.group(1), _strip_quotes(m.group(2))
            if not override and key in os.environ:
                continue
            os.environ[key] = val
            loaded.append(key)
    return loaded


def load_env(*paths: str | os.PathLike[str], override: bool = False) -> list[str]:
    """依序載入多個 .env 檔, 後者 override 前者。"""
    loaded: list[str] = []
    for p in paths:
        loaded.extend(load_env_file(p, override=override))
    return loaded
