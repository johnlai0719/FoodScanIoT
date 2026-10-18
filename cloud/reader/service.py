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
    python cloud/reader/service.py
    （或 uvicorn cloud.reader.service:app --host 0.0.0.0 --port 8180）

預設由 reader 啟動並管理 HunyuanOCR 與 Qwen，請先停止舊 start_models.py。
READER_MANAGE_MODELS=0 才使用外部啟動器；詳見 cloud/reader/README.md。
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
from input_limits import InputRejected
from model_manager import ModelManager, ModelUnavailable
from request_limits import RequestLimits

app = FastAPI(title="FoodScan vlcrop reader")
app.add_middleware(RequestLimits,
                   max_bytes=int(os.getenv("READER_MAX_BODY_BYTES", str(16 * 1024**2))),
                   capacity=int(os.getenv("READER_REQUEST_CAPACITY", "2")))
MANAGE_MODELS = os.getenv("READER_MANAGE_MODELS", "1") not in ("0", "false")
manager = ModelManager() if MANAGE_MODELS else None

# 與 Cloud 共用同一把密鑰。這支雖然只綁本機，但它會吃 GPU 數十秒，
# 沒有驗證等於任何本機程序都能拖垮辨識。未設時放行並在 /health 標明，
# 與 server/main.py 的 require_api_key 同一個慣例。
def _secret():
    """密鑰與 Cloud 共用同一個值，而它寫在 cloud/.env（Cloud 由 compose 的
    env_file 載入，reader 是主機行程、沒有那一步），所以這裡自己讀一次。
    環境變數優先，讓部署時可以覆寫。"""
    v = os.getenv("API_SHARED_SECRET", "").strip()
    if v:
        return v
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", ".env")
    try:
        for line in open(env, encoding="utf-8"):
            if line.startswith("API_SHARED_SECRET="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


SECRET = _secret()

MODEL_SERVERS = {
    "hunyuan": (os.getenv("HY_URL") or "http://127.0.0.1:8177/v1/models").replace("/chat/completions", "/models"),
    "qwen": (os.getenv("LF_URL") or "http://127.0.0.1:8179/v1/models").replace("/chat/completions", "/models"),
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
    def warm():
        try:
            if manager is not None:
                from urllib.parse import urlparse
                for env, port in (("HY_URL", 8177), ("QWEN_URL", 8179), ("LF_URL", 8179)):
                    value = os.getenv(env)
                    if value:
                        parsed = urlparse(value)
                        if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1') or parsed.port != port:
                            raise ModelUnavailable("自訂模型端點請設定 READER_MANAGE_MODELS=0")
                pipeline.set_model_manager(manager)
                manager.ensure_ready()
            pipeline.warm_up()
        except Exception as exc:
            pipeline._state.update(state="failed", detail=str(exc))
        finally:
            if manager is not None and not pipeline.is_ready():
                manager.close()
    threading.Thread(target=warm, daemon=True).start()


@app.on_event("shutdown")
def _shutdown():
    if manager is not None:
        manager.close()


@app.get("/health")
def health():
    st = pipeline.status()
    servers = {k: _probe(v) for k, v in MODEL_SERVERS.items()}
    managed = manager.status() if manager is not None else {"state": "external"}
    transitioning = managed['state'] in ('paused', 'pausing', 'loading') and pipeline.is_ready()
    managed_ok = manager is None or managed['state'] in ('ready', 'paused', 'pausing', 'loading')
    ok = pipeline.is_ready() and managed_ok and (all(servers.values()) or transitioning)
    return {
        "status": "ok" if ok else "degraded",
        "layer": "reader",
        # 誰在跑。8180 上同時只會有一個 reader，光看埠與行程名（兩邊都是
        # python.exe）分不出是主線還是競賽版，所以要自己報。
        "reader": pipeline.READER_NAME,
        "pipeline": st["state"],
        "detail": st["detail"],
        "model_servers": servers,
        "model_management": managed,
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
    down = [k for k, v in MODEL_SERVERS.items() if manager is None and not _probe(v)]
    if down:
        raise HTTPException(status_code=503,
                            detail="模型服務未啟動：%s" % ", ".join(down))
    t0 = time.time()
    try:
        out = pipeline.read(req.label_images, req.barcode)
    except InputRejected as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except ModelUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e), headers={"Retry-After": "3"}) from e
    except pipeline.ReaderInferenceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail="%s: %s" % (type(e).__name__, e))
    out.setdefault("_meta", {})["elapsed_s"] = round(time.time() - t0, 2)
    return out


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("READER_PORT", "8180")))
