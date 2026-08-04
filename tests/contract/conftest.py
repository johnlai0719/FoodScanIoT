"""讓契約測試能以 server/main.py 相同的方式解析 module_a / module_d 等套件。

server/ 內部一律用 `from module_d.response_builder import build_response` 這種
以 server/ 為根的絕對匯入，所以測試必須把 server/ 放進 sys.path，而不是 repo 根。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"
FOG_DIR = REPO_ROOT / "fog"
APP_DIR = REPO_ROOT / "APP"

for _d in (SERVER_DIR, FOG_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

# 註：只 import fog/transforms.py（純標準函式庫）。fog/main.py 需要 FastAPI，
# 契約測試刻意不碰它，才能在 CI 上不裝任何 web 框架就執行。
