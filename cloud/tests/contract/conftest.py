"""讓契約測試能以 server/main.py 相同的方式解析 module_a / module_c / module_d 等套件。

server/ 內部一律用 `from module_d.response_builder import build_response` 這種以 server/
為根的絕對匯入，所以測試必須把 server/ 放進 sys.path，而不是 repo 根。

**名稱衝突（重要）**：fog/ 與 server/ 底下各有一個 main.py。只要兩者都在 sys.path 上，
`import main` 拿到哪一個就取決於順序——而 fog 的健康檢查測試需要 fog 的那個，
server/ 的各模組則需要以 server/ 為根解析。

處理方式：
  - sys.path 以 server/ 優先（讓 models 與各 module_* 正確解析）
  - fog/ 排在其後（供 transforms / version 這類不衝突的名稱使用）
  - fog/main.py 改用 load_fog_main() 以明確路徑載入，完全不經過 `import main`

註：先前 fog/ 底下也有一個 models.py，與 server/models.py 同名而造成第二處衝突。
該檔與 fog/database.py 只有彼此互相 import、無任何實際使用者，已於 2026-08-05 移除
（Fog 的快取實際使用 sqlite3 與 better-sqlite3，不經 SQLAlchemy）。
"""
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO_ROOT / "cloud"
FOG_DIR = REPO_ROOT / "fog"
APP_DIR = REPO_ROOT / "APP"

# 注意順序：後 insert 的會排在前面，故此處讓 server/ 最終位於 fog/ 之前
for _d in (FOG_DIR, SERVER_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def load_fog_main():
    """以明確路徑載入 fog/main.py，避免與 server/main.py 的名稱衝突。

    註冊為 "fog_main" 而非 "main"，使 `import main` 在測試過程中永遠指向 server 端，
    不會因為載入順序不同而得到不一樣的結果。
    """
    if "fog_main" in sys.modules:
        return sys.modules["fog_main"]

    spec = importlib.util.spec_from_file_location("fog_main", FOG_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["fog_main"] = module
    spec.loader.exec_module(module)
    return module
