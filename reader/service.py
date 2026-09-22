"""vlcrop 讀取器服務（:8180）。

Cloud 的 `server/main.py` 原本直接呼叫 Gemini；改用 vlcrop 之後，辨識這一段
跑在**主機**而不是容器裡——paddle、PPStructureV3 與兩個 llama-server 都要
GPU，裝進 python:3.11-slim 既肥又會與主機環境分岔。

    APP → Fog → cloud-server（:8000, docker）
                      │ HTTP
                      ▼
                reader（:8180, 主機）
                      ├─ PP-OCR         in-process
                      ├─ HunyuanOCR     :8177
                      └─ Qwen3.5-2B     :8179

啟動：
    python reader/service.py
    （或 uvicorn reader.service:app --host 0.0.0.0 --port 8180）

前置：HunyuanOCR 與 Qwen 的 llama-server 要先起來，否則 /health 會標成
not ready，/read 直接回 503——**不會靜默回一份空結果**，那是本專案
最常踩的失敗模式。
"""
import os
import sys
import threading
import time

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline  # noqa: E402

app = FastAPI(title="FoodScan vlcrop reader")

# 與 Cloud 共用同一把密鑰。這支雖然只綁本機，但它會吃 GPU 數十秒，
# 沒有驗證等於任何本機程序都能拖垮辨識。未設時放行並在 /health 標明，
# 與 server/main.py 的 require_api_key 同一個慣例。
def _secret():
    """密鑰與 Cloud 共用同一個值，而它寫在 server/.env（Cloud 由 compose 的
    env_file 載入，reader 是主機行程、沒有那一步），所以這裡自己讀一次。
    環境變數優先，讓部署時可以覆寫。"""
    v = os.getenv("API_SHARED_SECRET", "").strip()
    if v:
        return v
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "server", ".env")
    try:
        for line in open(env, encoding="utf-8"):
            if line.startswith("API_SHARED_SECRET="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


SECRET = _secret()

MODEL_SERVERS = {
    "hunyuan": os.getenv("HY_URL") or "http://127.0.0.1:8177/v1/models",
    "qwen": os.getenv("QWEN_URL", "").replace("/chat/completions", "/models")
            or "http://127.0.0.1:8179/v1/models",
}


class ReadRequest(BaseModel):
    label_images: list[str]
    barcode: str = "unknown"


def _probe(url):
    try:
        return requests.get(url, timeout=2).status_code == 200
    except Exception:
        return False


@app.on_event("startup")
def _startup():
    # 背景暖機：載模型要數十秒，不能讓 uvicorn 卡在啟動上——
    # 那樣 /health 也打不到，看起來像整台掛了。
    threading.Thread(target=pipeline.warm_up, daemon=True).start()


@app.get("/health")
def health():
    st = pipeline.status()
    servers = {k: _probe(v) for k, v in MODEL_SERVERS.items()}
    ok = pipeline.is_ready() and all(servers.values())
    return {
        "status": "ok" if ok else "degraded",
        "layer": "reader",
        # 誰在跑。8180 上同時只會有一個 reader，光看埠與行程名（兩邊都是
        # python.exe）分不出是主線還是競賽版，所以要自己報。
        "reader": pipeline.READER_NAME,
        "pipeline": st["state"],
        "detail": st["detail"],
        "model_servers": servers,
        "auth": "enabled" if SECRET else "disabled",
    }


@app.post("/read")
def read(req: ReadRequest, x_api_key: str = Header(default="")):
    if SECRET and x_api_key != SECRET:
        raise HTTPException(status_code=401, detail="bad api key")
    if not pipeline.is_ready():
        # 503 而不是回空結果：讀不出來與「這張圖沒有成分」必須分得開，
        # 否則 Cloud 會把「服務沒起來」寫成「本商品無添加物」。
        raise HTTPException(status_code=503,
                            detail="reader 未就緒：%s" % pipeline.status())
    down = [k for k, v in MODEL_SERVERS.items() if not _probe(v)]
    if down:
        raise HTTPException(status_code=503,
                            detail="模型服務未啟動：%s" % ", ".join(down))
    t0 = time.time()
    try:
        out = pipeline.read(req.label_images, req.barcode)
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail="%s: %s" % (type(e).__name__, e))
    out.setdefault("_meta", {})["elapsed_s"] = round(time.time() - t0, 2)
    return out


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("READER_PORT", "8180")))
