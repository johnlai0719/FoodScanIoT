#!/bin/bash
# 確保在正確的目錄
cd "$(dirname "$0")"
# 執行 Python 伺服器
python3 -m uvicorn main:app --host 0.0.0.0 --port 3002
