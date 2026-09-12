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

BACKEND = os.getenv("VISION_BACKEND", "vlcrop").strip().lower()
READER_URL = os.getenv("READER_URL") or "http://host.docker.internal:8180"
# reader 要跑 PP-OCR ＋ PPStructureV3 ＋ 兩次 VLM，實測中位數約 10 秒，
# 但 8GB 的 4060 在連續請求下會慢很多。120 秒是上限不是預期值。
# ⚠ 這個數字要小於 Fog 的讀取逾時（45 秒）才有意義——目前**不是**，
#    所以實務上是 Fog 先放棄並降階。留 120 是為了讓 reader 端的 log 能
#    記錄完整耗時，不是要 Cloud 真的等那麼久。見工程待辦。
READER_TIMEOUT = float(os.getenv("READER_TIMEOUT", "120"))
SECRET = os.getenv("API_SHARED_SECRET", "").strip()


def backend_name() -> str:
    return BACKEND


async def analyze(base64_images: list, barcode: str, gemini_fn):
    """依 VISION_BACKEND 分派。`gemini_fn` 是 main.analyze_image_with_gemini。"""
    if BACKEND == "gemini":
        return await gemini_fn(base64_images, barcode)
    return _read_via_reader(base64_images, barcode)


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
