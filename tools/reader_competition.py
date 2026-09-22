#!/usr/bin/env python3
"""競賽版辨識服務：EasyOCR 定位裁切 → Gemini 2.5 Flash 結構化輸出。

為什麼是這個形狀
----------------
Cloud 的 `server/vision_backend.py` 打的是 `POST {READER_URL}/read`，body 是
`{label_images, barcode}`，回傳要與 `analyze_image_with_gemini()` 同形。主線的
vlcrop reader（`reader/service.py`）就是這個介面。這支提供**同一個介面**，
所以切換後端不需要改 Cloud 一行程式。

**刻意聽同一個埠（8180）。** 主線 reader 與這支同時只會有一個在跑——它們搶
同一張 GPU，本來就不該並存。聽同一個埠的好處是 Cloud 連 `READER_URL` 都不用
改：切換只是「換一個行程起來」。切換用 tools/switch_reader.ps1。

⚠ 因此 Cloud 端的 `vision_backend.backend_name()` 會一律回 "vlcrop"，那只是
  「走 reader 這條路」的意思，不代表跑的是哪一個 reader。**要知道實際是誰做的，
  看回傳的 `_meta.reader`**——這支會標自己的名字，主線那支標 vlcrop_hy。

管線
----
1. EasyOCR **只做偵測**（`recognizer=False`），拿文字框。不做辨識：辨識交給
   Gemini，這正是競賽版與主線 vlcrop 的分野。
2. 把所有文字框取外接矩形加 padding 裁成一張（`text_envelope`）。外框佔滿九成
   以上或邊長不足時退回整張——否則「裁切」實際上什麼都沒去掉，還多一層失真。
3. 裁切後的圖交給 Gemini 2.5 Flash，要 `FoodLabel` 結構化輸出。

以上三步的程式碼**原樣沿用** tools/easyocr_union_gemini.py 與 gemini_compare.py，
這支只負責把它們接成服務，不改辨識行為——否則服務跑出來的結果與實驗數據就對不起來。

跑法（要用有 easyocr＋CUDA 的環境）：
    ./.venv-easyocr-cuda-clean/Scripts/python.exe tools/reader_competition.py
"""
import base64
import io as _io
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI, Header, HTTPException
from google import genai
from google.genai import types
import numpy as np
from PIL import Image
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from easyocr_union_gemini import text_envelope  # noqa: E402
from gemini_compare import PROMPT, request as gemini_request  # noqa: E402
from structured_pipeline import FoodLabel  # noqa: E402

READER_NAME = "easyocr_union_gemini (EasyOCR detect → envelope crop → Gemini)"
PORT = int(os.getenv("READER_PORT", "8180"))
MODEL = os.getenv("COMPETITION_GEMINI_MODEL", "gemini-2.5-flash")
# 與 Cloud 共用的密鑰。未設定時放行，與 server/main.py 的 require_api_key 同一個
# 取捨（不能讓既有佈署一升級就全斷），差別只在這裡是被呼叫端。
SECRET = (os.getenv("API_SHARED_SECRET") or "").strip()

# GPU 只有一張，一次只服務一個請求。與主線 reader 的 `_lock` 同一個理由，
# 而且排隊時間要與處理時間分開量——不分開的話排隊會被算成「辨識變慢了」。
_lock = threading.Lock()
_state = {"state": "loading", "detail": "尚未載入 EasyOCR"}
_easyocr_reader = None
_client = None

app = FastAPI(title="competition reader")


class ReadRequest(BaseModel):
    label_images: list[str]
    barcode: str | None = None


def _decode(b64: str) -> Image.Image:
    if "base64," in b64:
        b64 = b64.split("base64,")[1]
    return Image.open(_io.BytesIO(base64.b64decode(b64))).convert("RGB")


def warm_up():
    """載入 EasyOCR 與 Gemini client。失敗時把原因寫進 _state，不靜默。"""
    global _easyocr_reader, _client
    try:
        import easyocr
        import torch

        use_gpu = torch.cuda.is_available()
        # recognizer=False：只載偵測模型。載辨識模型會多吃 VRAM 而這條路不用它。
        _easyocr_reader = easyocr.Reader(
            ["ch_tra", "en"], gpu=use_gpu, recognizer=False,
            model_storage_directory=os.getenv("EASYOCR_MODULE_PATH") or None,
        )
        secret = (dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
                  or os.getenv("GEMINI_API_KEY"))
        if not secret:
            raise RuntimeError("GEMINI_API_KEY 未設定（.env 或環境變數）")
        _client = genai.Client(
            api_key=secret,
            http_options=types.HttpOptions(
                timeout=120000, retry_options=types.HttpRetryOptions(attempts=1)),
        )
        _state.update(state="ready",
                      detail="EasyOCR gpu=%s, model=%s" % (use_gpu, MODEL))
    except Exception as e:
        _state.update(state="failed", detail="%s: %s" % (type(e).__name__, e))


@app.on_event("startup")
def _startup():
    # 背景載入：EasyOCR 首次載模型要數十秒，擋著啟動會讓 compose 的健康檢查誤判。
    threading.Thread(target=warm_up, name="competition-warmup", daemon=True).start()


@app.get("/health")
def health():
    return {
        "status": "ok" if _state["state"] == "ready" else "degraded",
        "layer": "reader",
        "reader": READER_NAME,
        "pipeline": _state["state"],
        "detail": _state["detail"],
        "model": MODEL,
        "auth": "enabled" if SECRET else "disabled",
    }


@app.post("/read")
def read(req: ReadRequest, x_api_key: str = Header(default="")):
    if SECRET and x_api_key != SECRET:
        raise HTTPException(status_code=401, detail="bad api key")
    if _state["state"] != "ready":
        # 503 而不是回空結果。理由同主線 reader：讀不出來與「這張圖沒有成分」
        # 必須分得開，否則 Cloud 會把「服務沒起來」寫成「本商品無添加物」。
        raise HTTPException(status_code=503,
                            detail="competition reader 未就緒：%s" % _state["detail"])
    if not req.label_images:
        raise HTTPException(status_code=400, detail="沒有圖片")

    cid = "comp_" + uuid.uuid4().hex[:12]
    t_queue = time.time()
    with _lock:
        stages = {"queue": round(time.time() - t_queue, 2)}
        try:
            images = [_decode(b) for b in req.label_images]

            t = time.time()
            crops, notes = [], []
            for im in images:
                # detect() 只吃 file path／bytes／numpy array，不吃 PIL Image。
                horizontal, free = _easyocr_reader.detect(np.array(im))
                # detect() 回傳 (horizontal, free)，各自是 [[...]] 包一層，
                # 取第 0 個才是這張圖的框。兩種格式不同：
                #   horizontal  [x_min, x_max, y_min, y_max]，注意**不是** x,y,x,y
                #   free        四個點的 (4,2) 陣列
                # text_envelope 吃的是點的清單，所以水平框要展成四個角。
                #
                # ⚠ 用陣列維度判斷，不要用 isinstance(v, (int, float))：值的型別是
                #   np.int32，isinstance 會是 False，於是水平框被當成多邊形，
                #   text_envelope 對純量取 p[0] 就 IndexError。
                boxes = []
                for group in (horizontal, free):
                    for b in (group[0] if group else []):
                        if b is None:
                            continue
                        arr = np.asarray(b, dtype=float)
                        if arr.ndim == 1 and arr.size == 4:
                            x1, x2, y1, y2 = arr.tolist()
                            boxes.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
                        elif arr.ndim == 2 and arr.shape[-1] == 2:
                            boxes.append(arr.tolist())
                rect, note = text_envelope(boxes, im.size)
                crops.append(im.crop(rect))
                if note:
                    notes.append(note)
            stages["easyocr_detect"] = round(time.time() - t, 2)

            t = time.time()
            parsed, meta = gemini_request(_client, MODEL, PROMPT, crops, FoodLabel)
            stages["gemini"] = round(time.time() - t, 2)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500,
                                detail="%s: %s" % (type(e).__name__, e))

    out = dict(parsed)
    out["_meta"] = {
        "reader": READER_NAME,
        "case_id": cid,
        "barcode": req.barcode,
        "stage_secs": stages,
        "elapsed_s": round(sum(stages.values()), 2),
        "model_version": meta.get("model_version"),
        "usage": meta.get("usage"),
        # 裁切退回整張的原因（no_boxes／invalid_envelope／little_background_removed）。
        # 留著才看得出「這次其實沒裁到」，否則裁切失效會被當成辨識變差。
        "envelope_notes": notes,
    }
    return out


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
