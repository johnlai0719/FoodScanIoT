"""視覺辨識後端的切換點：Gemini 或 vlcrop reader。

2026-09-13 之前 `main.py` 直接呼叫 `analyze_image_with_gemini()`。改用 vlcrop
之後，辨識跑在主機上的 reader 服務（見 `reader/service.py`），Cloud 這邊只
發一個 HTTP 請求。**兩邊回傳同一個形狀**，所以下游（`_is_valid_food_scan`、
`match_ingredients`、`module_d` 組裝）一行都不用改。

切換靠 `VISION_BACKEND`：

    vlcrop（預設）  打 reader，失敗**不回退 Gemini**，理由見下
    gemini          原本的路徑，留著是為了 ABBA 對照與 reader 掛掉時的手動退路

**為什麼失敗不自動回退 Gemini**：兩個讀取器的失效模式相反——Gemini 會憑常識
補完（添加物精確度 40.2%，多判的 43% 是正解與 OCR 都查無此物的），vlcrop 不會。
靜默回退等於「系統偶爾會編造，而且沒人知道是哪幾次」，那違反第 0 條原則
（輸出的每個字都要有影像來源）。reader 掛掉時就讓它掛掉、由 Fog 走降階，
使用者看到的是「暫時降級」而不是一份看起來正常的假結果。
"""
import os

import requests
from starlette.concurrency import run_in_threadpool

BACKEND = os.getenv("VISION_BACKEND", "vlcrop").strip().lower()
READER_URL = os.getenv("READER_URL") or "http://host.docker.internal:8180"
# reader 要跑 PP-OCR ＋ PPStructureV3 ＋ 兩次 VLM，實測穩態 10.3 秒／張。
# 120 秒是上限不是預期值：reader 一次只服務一個請求（GPU 只有一張），
# 同時來兩個就會排隊，而排隊等待也算在這個逾時裡。
# ⚠ 它比 Fog 的讀取逾時（90 秒）長是**刻意的**：真的等超過 90 秒時，
#    該由 Fog 決定降階（它有本機 OCR 可退），不是由 Cloud 先放棄。
READER_TIMEOUT = float(os.getenv("READER_TIMEOUT", "120"))
SECRET = os.getenv("API_SHARED_SECRET", "").strip()


def backend_name() -> str:
    return BACKEND


async def analyze(base64_images: list, barcode: str, gemini_fn):
    """依 VISION_BACKEND 分派。`gemini_fn` 是 main.analyze_image_with_gemini。

    ⚠ **必須 run_in_threadpool。** `requests.post` 是同步的，直接在 async 函式
    裡呼叫會卡住 event loop 整整十幾秒，期間 Cloud **不讀任何新請求的 body**。
    一張圖 base64 後約 656 KB，塞不進 socket 緩衝區，於是 Fog 的上傳卡在
    「連線階段」——而 urllib3 在傳 body 時套用的是**連線逾時（Fog 設 5 秒）**，
    不是 90 秒的讀取逾時。結果是 Cloud 只要在忙，下一個帶圖請求就在 5 秒後
    降階，而不是排隊等候。

    2026-09-13 由 Fog 端的重現實驗定位：先佔住 Cloud，用 `(5, 90)` 會在 5.4 秒
    `Connection aborted` 並降階；放寬到 `(30, 90)` 則排隊、31 秒後成功。
    當天兩次不明降階（都在約 6.7 秒）都是這個。

    **修在 Cloud 而不是放寬 Fog 的連線逾時**：後者要把 30 + 90 + 本機辨識 5 秒
    塞進 Node 的 105 秒，塞不下；而且會把「對端離線」的偵測從 5 秒拖到 30 秒。
    根因是這裡阻塞，就修在這裡。
    """
    if BACKEND == "gemini":
        return await gemini_fn(base64_images, barcode)
    return await run_in_threadpool(_read_via_reader, base64_images, barcode)


def _read_via_reader(base64_images: list, barcode: str):
    headers = {"X-API-Key": SECRET} if SECRET else {}
    r = requests.post(READER_URL.rstrip("/") + "/read",
                      json={"label_images": base64_images, "barcode": barcode},
                      headers=headers, timeout=READER_TIMEOUT)
    if r.status_code != 200:
        # 拋出而不是回 None：回 None 會讓 `_is_valid_food_scan` 判成
        # 「這張不是食品標籤」，把服務故障說成使用者拍錯。
        raise RuntimeError("reader %s: %s" % (r.status_code, r.text[:200]))
    return r.json()
