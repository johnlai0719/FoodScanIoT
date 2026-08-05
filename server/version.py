"""回報目前執行中的程式版本（git commit）。

用途：健康檢查端點回報 commit，就能當場確認 Pi 上跑的是不是剛推上去的那一版——
「部署成功」與「部署的是正確版本」是兩件事，只看部署腳本有沒有報錯無法區分。

取得順序：
  1. 環境變數 APP_COMMIT（CI 部署時可注入，最可靠）
  2. git rev-parse（Pi 上是 git pull 部署的，通常可用）
  3. "unknown"（例如容器內沒有 .git 時；刻意不猜，寧可誠實回報不知道）
"""
import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def get_commit() -> str:
    """回傳短版 commit hash。結果快取，健康檢查會被頻繁呼叫。"""
    env = os.getenv("APP_COMMIT")
    if env:
        return env.strip()

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass

    return "unknown"
