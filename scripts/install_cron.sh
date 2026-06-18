#!/usr/bin/env bash
# opentop 安裝/啟動輔助腳本
#
# 子命令:
#   setup   - 建立 venv, 安裝相依套件
#   run     - 執行一次 (同步)
#   cron    - 安裝每日 cron (預設每天 07:00, 用 crontab 寫入)
#   unschedule - 移除 cron

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${ROOT}/.venv"
PY="${VENV}/bin/python"

cmd="${1:-setup}"

case "$cmd" in
  setup)
    if [[ ! -d "$VENV" ]]; then
      python3 -m venv "$VENV"
    fi
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
    if [[ ! -f config.yml ]]; then
      cp config.example.yml config.yml
      echo "已建立 config.yml, 請編輯填入 LLM API key"
    fi
    mkdir -p data docs logs
    echo "setup 完成。"
    echo "編輯 config.yml 填入 API key, 然後執行: $0 run"
    ;;
  run)
    "$PY" scripts/run.py "$@"
    ;;
  cron)
    hour="${2:-7}"
    minute="${3:-0}"
    # 從 ~/.config/opentop/env 讀取環境變數 (若存在)
    env_prefix=""
    env_file="$HOME/.config/opentop/env"
    if [[ -f "$env_file" ]]; then
      env_prefix="set -a; . $env_file; set +a; "
    fi
    ( crontab -l 2>/dev/null | grep -v 'opentop/scripts/run.py' || true
      echo "$minute $hour * * * cd $ROOT && $env_prefix$PY scripts/run.py >> logs/run.log 2>&1" ) | crontab -
    echo "已安裝每日 $hour:$minute 排程"
    echo "  (環境變數讀取: $env_file)"
    ;;
  unschedule)
    ( crontab -l 2>/dev/null | grep -v 'opentop/scripts/run.py' || true ) | crontab -
    echo "已移除 cron"
    ;;
  *)
    echo "usage: $0 {setup|run|cron [hour] [minute]|unschedule}"
    exit 1
    ;;
esac
