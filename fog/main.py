# Fog Server v1.1
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import json
import time
import requests
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv

# 純轉換函式抽至 transforms.py（2026-08-05），讓契約測試不必安裝 FastAPI 即可載入
from transforms import (mask_sensitive_data, normalize_result,
                        is_cacheable_result, CloudUnavailable)

# 埋點。與 Cloud 共用同一份格式函式（server/telemetry.py）——兩層各寫一份
# 一定會分岔（族群詞彙、撇號、添加物分母都是這樣來的）。
import sys as _sys
from pathlib import Path as _Path
_sys.path.append(str(_Path(__file__).resolve().parents[1] / "server"))
import telemetry as T
from version import get_commit
# Durable offline outbox; OCR engines are no longer loaded by Fog.
from offline_queue import OfflineQueue
from fog_events import emit as emit_fog, result_summary
import threading
from starlette.concurrency import run_in_threadpool

# 加載環境變數
load_dotenv()


# HMAC 請求簽章已被移除

DB_PATH = os.getenv("FOG_DB_PATH", str(_Path(__file__).resolve().with_name("fog_cache.db")))
CLOUD_URL = os.getenv("CLOUD_API_URL")
if not CLOUD_URL:
    print("[!] 警告: 未在環境變數或 .env 中偵測到 CLOUD_API_URL！")

# Cloud 的共享密鑰。Cloudflare Tunnel 把 /api/analyze 變成公開端點，
# 而它會呼叫計費的 Gemini API。兩邊都未設定時行為與先前完全相同。
API_SHARED_SECRET = os.getenv("API_SHARED_SECRET", "").strip()


# Node waits 105s; connect 5s + cloud read 90s leaves time to persist the outbox.
CLOUD_READ_TIMEOUT = float(os.getenv("CLOUD_READ_TIMEOUT", "90"))


def cloud_headers() -> dict:
    """轉發給 Cloud 的標頭。密鑰只存在於 Fog，不會下發到 App。"""
    h = {"Content-Type": "application/json"}
    if API_SHARED_SECRET:
        h["X-API-Key"] = API_SHARED_SECRET
    return h

cloud_online = None
_cloud_retry_after = 0.0
_queue = None


def queue_store():
    global _queue
    if _queue is None or _queue.db_path != str(DB_PATH):
        _queue = OfflineQueue(DB_PATH)
    return _queue


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS cache (barcode TEXT PRIMARY KEY, result_json TEXT NOT NULL, last_updated INTEGER NOT NULL, ttl INTEGER NOT NULL)")
    queue_store()


def get_stale_from_db(barcode):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT result_json,last_updated,ttl FROM cache WHERE barcode=?", (barcode,)).fetchone()
    if row:
        result = json.loads(row[0])
        if is_cacheable_result(result):
            return result, max(0, int(time.time()) - row[1]), int(time.time()) > row[1] + row[2]
    return None, 0, False


def forward_cloud(payload, rid=""):
    global cloud_online, _cloud_retry_after
    if not CLOUD_URL:
        raise CloudUnavailable("尚未設定雲端連線")
    headers = cloud_headers()
    if rid:
        headers[T.REQUEST_ID_HEADER] = rid
    try:
        r = requests.post(CLOUD_URL, data=json.dumps(payload).encode(), headers=headers,
                          timeout=(5.0, CLOUD_READ_TIMEOUT))
        if r.status_code >= 500 or r.status_code in (408, 429):
            raise CloudUnavailable(f"Cloud HTTP {r.status_code}")
        try:
            result = r.json()
        except ValueError:
            if r.status_code >= 400:
                result = {"status": "error", "message": f"Cloud HTTP {r.status_code}"}
            else:
                raise
        if not isinstance(result, dict):
            raise CloudUnavailable("Cloud 回應格式異常")
        cloud_online = True
        _cloud_retry_after = 0
        return r, result
    except (requests.RequestException, ValueError, CloudUnavailable) as exc:
        cloud_online = False
        _cloud_retry_after = time.monotonic() + 5
        raise CloudUnavailable(str(exc)) from exc


def sync_once():
    """Single durable FIFO delivery; called by one background worker."""
    q = queue_store()
    job = q.claim()
    if not job:
        return False
    observation = {'queue_id': job['id'], 'attempt': job['attempts']}
    emit_fog(job['request_id'], 'replay_started', detail=observation)
    try:
        r, result = forward_cloud(job["payload"], job["request_id"])
        if not r.ok:
            q.fail(job, f"Cloud HTTP {r.status_code}")
            emit_fog(job['request_id'], 'replay_failed', 'failed', {**observation, 'http_status': r.status_code})
        elif result.get("status") == "error":
            q.retry(job, result.get("message", "Cloud analysis error"))
            emit_fog(job['request_id'], 'replay_retry', detail=observation)
        else:
            q.complete(job, result)
            emit_fog(job['request_id'], 'replay_completed', 'done', observation, result_summary(result))
    except Exception as exc:
        q.retry(job, exc)
        emit_fog(job['request_id'], 'replay_retry', detail=observation)
    return True


def queue_worker(stop):
    while not stop.is_set():
        try:
            delivered = sync_once()
        except Exception as exc:
            print(f"[QUEUE] Worker error: {type(exc).__name__}")
            delivered = False
        stop.wait(0.1 if delivered else 2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = threading.Event()
    worker = threading.Thread(target=queue_worker, args=(stop,), name="cloud-outbox", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        await run_in_threadpool(worker.join, 2)

app = FastAPI(title="FoodAware Fog Server - Vision Optimized", lifespan=lifespan)


# ── 延遲埋點 ────────────────────────────────────────────────────────────────
# middleware 而不是在每個 return 前加一行：/query 有多個 return（快取命中、
# 降階、STALE、錯誤），漏掉任何一個，最需要量的那幾次剛好就沒有數字。
@app.middleware("http")
async def _timing_middleware(request, call_next):
    sw = T.Stopwatch()
    request.state.sw = sw
    response = await call_next(request)
    response.headers[T.FOG_PY_HEADER] = T.format_timing(**sw.segments())
    return response


@app.get("/health")
def health(deep: bool = False):
    """健康檢查。部署腳本用它確認服務重啟後有沒有活過來。

    設計原則：**Cloud 不可用不會讓 status 變成非 ok**。Fog 的存在理由之一就是
    Cloud 斷線時仍能以快取服務，若把 Cloud 的狀態算進自身健康度，會導致 Cloud
    離線時無法部署 Fog——那與離線降級的設計主張直接矛盾。下游狀態只作為資訊回報。

    預設只做本機檢查（快取 DB 可否讀取），成本極低。
    `?deep=1` 才會實際連線 Cloud，供人工診斷用。
    """
    payload = {
        "status": "ok",
        "layer": "fog-python",
        "commit": get_commit(),
        "cache_db": "unknown",
        "cache_entries": None,
    }

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cache")
        payload["cache_entries"] = cur.fetchone()[0]
        conn.close()
        payload["cache_db"] = "ok"
    except Exception as e:
        # 快取讀不到 = 這一層真的無法正常服務，才降級 status
        payload["cache_db"] = f"error: {e}"
        payload["status"] = "degraded"

    payload["cloud_url_configured"] = bool(CLOUD_URL)
    # 本機降階是附加能力，停用時 status 仍是 ok——理由與 Cloud 不可用相同（見 docstring）
    payload["local_ocr"] = {"state": "disabled", "detail": "Fog 使用快取與離線佇列"}
    payload["cloud_online"] = cloud_online
    try:
        payload["queue"] = queue_store().stats()
    except Exception:
        payload["queue"] = {"error": "unavailable"}
        payload["status"] = "degraded"

    if deep:
        if not CLOUD_URL:
            payload["downstream"] = {"cloud": "not_configured"}
        else:
            try:
                r = requests.get(CLOUD_URL.replace("/api/analyze", "/health"), timeout=3.0)
                payload["downstream"] = {"cloud": "ok" if r.ok else f"http_{r.status_code}"}
            except Exception as e:
                payload["downstream"] = {"cloud": f"unreachable: {type(e).__name__}"}

    return payload


@app.get("/queue/{job_id}")
def get_queue_job(job_id: str):
    job = queue_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到此排隊任務")
    if "result" in job:
        job["result"] = normalize_result(job["result"])
    return job


@app.post("/query")
async def query(request: Request, response: Response):
    sw = request.state.sw
    rid = T.safe_request_id(request.headers.get(T.REQUEST_ID_HEADER))
    if rid:
        response.headers[T.REQUEST_ID_HEADER] = rid
    data = await request.json()
    if not isinstance(data, dict) or not isinstance(data.get("barcode"), str):
        raise HTTPException(status_code=400, detail="需要提供條碼")
    barcode = data["barcode"]
    scenario = request.headers.get('X-Demo-Scenario')
    emit_fog(rid, 'received', payload={'image_count': len(data.get('label_images') or []),
             **({'demo_scenario': scenario} if scenario in ('cloud', 'miss', 'cache', 'queue') else {})})
    # Historical privacy opt-out must never become an implicit queued upload.
    if request.headers.get("X-Local-Only") == "1":
        response.status_code = 409
        return {"status": "error", "message": "Fog 已改為快取與雲端佇列管理，不再提供本機 OCR；此請求未上傳、未排隊。"}
    cached_from_node = data.get("cached_result")
    if isinstance(cached_from_node, dict):
        return normalize_result(cached_from_node)
    init_db()
    if request.headers.get("X-Bypass-Cache") != "1" and barcode != "TEST":
        cached, age, stale = get_stale_from_db(barcode)
        if cached:
            emit_fog(rid, 'cache_hit', detail={'cache_status': 'HIT-STALE' if stale else 'HIT'})
            response.headers["X-Cache"] = "HIT-STALE" if stale else "HIT"
            result = normalize_result(cached)
            result["cached"] = True
            if stale:
                result["_cache_age_seconds"] = age
                result["_warning"] = "此為本地舊快取，尚未取得最新資料"
            emit_fog(rid, 'cache_returned', 'done', {'cache_status': 'HIT-STALE' if stale else 'HIT'},
                     {**result_summary(result), 'cached': True})
            return result
    bypass = request.headers.get('X-Bypass-Cache') == '1'
    emit_fog(rid, 'cache_bypass' if bypass else 'cache_excluded' if barcode == 'TEST' else 'cache_miss',
             detail={'cache_status': 'BYPASS' if bypass else 'EXCLUDED' if barcode == 'TEST' else 'MISS'})
    # Persist only the same sanitized analysis payload that Cloud receives.
    payload = mask_sensitive_data(data)
    payload.pop("cached_result", None)
    def queued_response(job_id):
        original_rid = queue_store().get(job_id)['request_id']
        emit_fog(rid, 'queued', detail={'queue_id': job_id, 'cache_status': 'QUEUED'},
                 payload={'canonical_request_id': original_rid} if original_rid and original_rid != rid else {})
        response.status_code = 202
        response.headers["X-Cache"] = "QUEUED"
        return {"status": "queued", "queued": True, "queue_id": job_id,
                "request_id": queue_store().get(job_id)["request_id"],
                "message": "目前無法連線雲端或任務尚在排隊。資料已暫存在 Fog，連線恢復後會依序送出分析。",
                "barcode": barcode, "cached": False,
                "status_url": f"/queue/{job_id}"}
    active = queue_store().find_active(payload)
    if active:
        return queued_response(active)
    try:
        if time.monotonic() < _cloud_retry_after:
            raise CloudUnavailable("雲端連線暫時不可用")
        with sw.lap("upstream"):
            emit_fog(rid, 'cloud_forwarded')
            r, result = await run_in_threadpool(forward_cloud, payload, rid)
        ct = r.headers.get(T.CLOUD_HEADER)
        if ct:
            response.headers[T.CLOUD_HEADER] = ct
        if not r.ok:
            emit_fog(rid, 'unavailable', 'failed', {'http_status': r.status_code})
            response.status_code = r.status_code
            return {"status": "error", "message": result.get("message", f"Cloud HTTP {r.status_code}")}
        if is_cacheable_result(result) and barcode != "TEST":
            with sqlite3.connect(DB_PATH) as db:
                db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
                           (barcode, json.dumps(result), int(time.time()), 86400))
        response.headers["X-Cache"] = "MISS"
        emit_fog(rid, 'cloud_returned', 'done', {'http_status': r.status_code}, result_summary(result))
        return normalize_result(result)
    except CloudUnavailable:
        emit_fog(rid, 'cloud_unavailable')
        try:
            job_id = queue_store().enqueue(payload, rid)
        except Exception:
            emit_fog(rid, 'storage_failed', 'failed')
            response.status_code = 503
            return {"status": "error", "message": "目前無法連線雲端，且本地暫存失敗。請保留照片並稍後重試。", "queued": False}
        return queued_response(job_id)

@app.delete("/cache")
@app.post("/cache")
@app.delete("/cache/clear")
@app.post("/cache/clear")
async def clear_cache():
    """清除所有暫存資料庫內容"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cache")
        conn.commit()
        conn.close()
        print("[SUCCESS] [Fog] Full cache cleared via API")
        return {"status": "success", "message": "Fog cache cleared successfully"}
    except Exception as e:
        print(f"[ERROR] Cache clear failed: {e}")
        return {"status": "error", "message": str(e)}

@app.delete("/cache/{barcode}")
async def clear_specific_cache(barcode: str):
    """清除特定條碼的暫存"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cache WHERE barcode = ?", (barcode,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"[SUCCESS] [Fog] Cache for {barcode} cleared via API")
        return {"status": "success", "message": f"Cache for {barcode} cleared", "deleted": deleted}
    except Exception as e:
        print(f"[ERROR] Cache clear failed for {barcode}: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("FOG_PORT", 3002)))
