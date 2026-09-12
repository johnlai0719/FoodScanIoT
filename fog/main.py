# Fog Server v1.1
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import json
import time
import requests
import copy
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv

# 純轉換函式抽至 transforms.py（2026-08-05），讓契約測試不必安裝 FastAPI 即可載入
from transforms import (mask_sensitive_data, normalize_result,
                        should_degrade, CloudUnavailable)
from version import get_commit
# local_ocr 在模組層只用標準函式庫，OCR 等依賴延遲到 warm_up() 才載入——CI 會載入本檔
import local_ocr
import threading
from starlette.concurrency import run_in_threadpool

# 加載環境變數
load_dotenv()


# HMAC 請求簽章已被移除

DB_PATH = "fog_cache.db"
CLOUD_URL = os.getenv("CLOUD_API_URL")
if not CLOUD_URL:
    print("[!] 警告: 未在環境變數或 .env 中偵測到 CLOUD_API_URL！")

# Cloud 的共享密鑰。Cloudflare Tunnel 把 /api/analyze 變成公開端點，
# 而它會呼叫計費的 Gemini API。兩邊都未設定時行為與先前完全相同。
API_SHARED_SECRET = os.getenv("API_SHARED_SECRET", "").strip()


# 讀取逾時。Cloudflare 的 524 是 **100 秒硬上限**（免費方案改不了），
# 所以不能設得比它更長——否則判斷權落到 Cloudflare 手上，而 524 比自己的
# 逾時更難診斷。Node 層 60 秒放棄，連線 5 + 讀取 45 = 50 秒，
# 留 10 秒給本機降階跑完並回傳。
# 2026-09-13：45 → 90。A5 當初從 90 降到 45 的理由是「Cloudflare 會即時回
# 502/530，不必再等」，那在 Gemini 時代成立（Cloud 端 13～16 秒）。改用 vlcrop
# 之後 Cloud 端自己就要 55 秒／張（實測穩態，reader 跑 PP-OCR ＋ PPStructureV3
# ＋ 兩次 VLM），45 秒等於**每一次都降階**、vlcrop 永遠用不到。
# 90 而不是更大，是因為 Cloudflare 的 524 是 100 秒硬上限，超過就不是我們能
# 控制的了。⚠ 這代表**多張圖片仍會超時**（2 張約 110 秒）——見工程待辦。
CLOUD_READ_TIMEOUT = float(os.getenv("CLOUD_READ_TIMEOUT", "90"))


def cloud_headers() -> dict:
    """轉發給 Cloud 的標頭。密鑰只存在於 Fog，不會下發到 App。"""
    h = {"Content-Type": "application/json"}
    if API_SHARED_SECRET:
        h["X-API-Key"] = API_SHARED_SECRET
    return h

cloud_online = True

def get_stale_from_db(barcode: str) -> tuple[dict | None, int]:
    """查詢快取，忽略 TTL，回傳 (資料, 快取年齡秒數)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT result_json, last_updated FROM cache WHERE barcode = ?", (barcode,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return json.loads(row[0]), int(time.time()) - row[1]
    except Exception:
        pass
    return None, 0

def check_cloud_functionality():
    """啟動時測試 Cloud 端是否能接通並正確回應"""
    print(f"[*] 正在探查 Cloud 端功能: {CLOUD_URL}")
    test_payload = {
        "barcode": "STARTUP_PROBE",
        "label_images": []
    }
    try:
        # 設定較短的 timeout，避免啟動掛起太久 (15秒)
        response = requests.post(CLOUD_URL, json=test_payload, timeout=15.0)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "success":
                print("[+] Cloud 功能探查成功！API 運作正常。")
                return True
            else:
                print(f"[-] Cloud 回傳失敗狀態: {result.get('message', '未知錯誤')}")
                return True 
        else:
            print(f"[-] Cloud 端回應異常狀態碼: {response.status_code}")
            return False
    except Exception as e:
        print(f"[!] Cloud 功能探查發生錯誤: {str(e)}")
        return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    global cloud_online
    # 背景暖機：載入模型要數秒（第一次還要下載），不能擋住啟動——
    # deploy-fog.yml 重啟後只等 5 秒就打 /health。
    threading.Thread(target=local_ocr.warm_up, name="local-ocr-warmup", daemon=True).start()
    cloud_online = check_cloud_functionality()
    if not cloud_online:
        print("="*60)
        print("[WARN] Cloud 不可用，以離線快取模式啟動")
        print("[WARN] 僅能服務已快取條碼，新條碼查詢將回傳離線錯誤")
        print("="*60)
    yield
    # --- 關閉時執行 (如有需要) ---

app = FastAPI(title="FoodAware Fog Server - Vision Optimized", lifespan=lifespan)


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
    payload["local_ocr"] = local_ocr.status()

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


@app.post("/query")
async def query(request: Request, response: Response):
    try:
        data = await request.json()
        barcode = data.get("barcode")
        label_images = data.get("label_images")
        
        # --- 🟢 簡化版: 單行專業日誌 ---
        img_info = f"YES({len(label_images)})" if label_images else "NO"
        print(f"[REQ] {barcode} | Img: {img_info} | {time.strftime('%H:%M:%S')}")

        # --- 如果 Node.js 已經帶了快取結果過來，正規化後直接回傳 ---
        cached_from_node = data.get("cached_result")
        if cached_from_node:
            print(f"[CACHE] {barcode} -> Normalizing cached result")
            return normalize_result(cached_from_node)
        # 🛠️ 優化測試模式：如果有圖片或者是測試模式但快取沒資料，才去 Cloud
        is_test = (barcode == "TEST")
        has_images = (label_images and len(label_images) > 0)
        
        # 先查快取是否有 TEST 紀錄
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT result_json FROM cache WHERE barcode = 'TEST'")
        test_cache_row = cursor.fetchone()
        
        # 決定是否要去 Cloud
        if has_images or (is_test and not test_cache_row):
            action = "Image detected" if has_images else "First TEST probe"
            print(f"[CLOUD] {barcode} -> {action}, Forwarding...")
            try:
                # 執行脫敏 (Task A)
                masked_data = mask_sensitive_data(data)
                masked_data_bytes = json.dumps(masked_data).encode()
                
                cloud_resp = requests.post(
                    CLOUD_URL, 
                    data=masked_data_bytes, 
                    headers=cloud_headers(),
                    # 連線 5 秒、讀取 45 秒分開算：Cloud 對端離線時 TCP 連線會一直卡住，
                    # 合在一起就要等滿讀取逾時，而 Node 層 60 秒就放棄，
                    # 本機降階的結果送不到 App。
                    #
                    # ⚠ 讀取逾時 2026-09-12 由 90 秒縮為 45 秒，兩個理由：
                    #   1. **Cloudflare 的 524 是 100 秒硬上限，免費方案改不了。**
                    #      等到 90 秒才放棄，等於把判斷權交給 Cloudflare，
                    #      而它回的 524 會比我們自己的逾時更難診斷。
                    #   2. 90 秒原本是為了「對端離線時 TCP 卡住」，而隧道在前面時
                    #      那個情境消失了——Cloudflare 會即時回 502／530。
                    # Node 層 60 秒放棄，45 + 5 = 50 秒留了 10 秒讓降階跑完並回傳。
                    timeout=(5.0, CLOUD_READ_TIMEOUT)
                )
                # ⚠ 不能只靠 `.json()` 解析失敗來察覺 Cloud 掛了。
                #    Cloudflare 的錯誤頁是 HTML，解析確實會拋例外；但它在某些
                #    設定下會回 JSON，那時就會被當成一次成功的分析往下走。
                #    判定收斂到 transforms.should_degrade()，見該函式的說明。
                if should_degrade(status_code=cloud_resp.status_code):
                    raise CloudUnavailable(f"上游回 {cloud_resp.status_code}")
                result = cloud_resp.json()
                
                # 修復：存入正確的 barcode，而不是固定為 "TEST"
                cache_key = barcode if barcode else "TEST"
                
                if result.get("status") == "success" or "final_health_diagnosis" in result or "product_info" in result:
                    # 重要：存入快取前，先保留一份原始結果，不要存入已經算過個人化分數的版本
                    cursor.execute("INSERT OR REPLACE INTO cache (barcode, result_json, last_updated, ttl) VALUES (?, ?, ?, ?)",
                                   (cache_key, json.dumps(result), int(time.time()), 86400))
                    conn.commit()
                    print(f"[SUCCESS] {cache_key} cached")

                conn.close()
                # 僅在回傳給 APP 的那一刻才計算個人化分數
                return normalize_result(result)
            except Exception as e:
                conn.close()
                print(f"[ERROR] Cloud Forwarding Error: {barcode} -> {str(e)}")
                stale, age = get_stale_from_db(barcode)
                if stale:
                    print(f"[STALE] {barcode} -> Serving stale cache (age: {age}s)")
                    response.headers["X-Cache"] = "STALE"
                    result = normalize_result(stale)
                    result["_offline_mode"] = True
                    result["_cache_age_seconds"] = age
                    return result
                # 本機降階：Cloud 連不上、也沒有這個條碼的快取時，以本機 OCR 回部分結果。
                # 格式見 transforms.build_degraded_local_response()。不寫入快取，也不可經過
                # normalize_result()——它會替沒有分數的結果補上預設 75 分。
                if has_images and local_ocr.is_ready():
                    print(f"[LOCAL] {barcode} -> Cloud 不可用且無快取，改用本機 OCR")
                    try:
                        return await run_in_threadpool(local_ocr.analyze, label_images, barcode)
                    except Exception as le:
                        print(f"[ERROR] Local OCR Error: {barcode} -> {le}")
                raise HTTPException(status_code=504, detail=f"Cloud 不可用且無快取資料: {str(e)}")

        # 🛠️ 如果是測試模式且沒照片，直接從快取回傳上次的成功結果 (取代 Cloud 查詢)
        if is_test and test_cache_row:
            print(f"[CACHE] {barcode} -> Returning cached result")
            cached_result = json.loads(test_cache_row[0])
            conn.close()
            return normalize_result(cached_result)

        # 2. 一般條碼查詢 (走 SQLite 快取)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT result_json, last_updated, ttl FROM cache WHERE barcode = ?", (barcode,))
        row = cursor.fetchone()
        
        now = int(time.time())
        if row:
            res_json, last_updated, ttl = row
            # 🛠️ 修復：只有 status 為 success 的才算命中快取，如果是 not_found 則重新轉發給 Cloud 嘗試獲取最新持久化結果
            cached_result = json.loads(res_json)
            if now <= last_updated + ttl and cached_result.get("status") == "success":
                response.headers["X-Cache"] = "HIT"
                conn.close()
                return normalize_result(cached_result)

        # 3. 快取未命中 (或是快取中是 not_found)
        print(f"[MISS] {barcode} -> Forwarding to Cloud")
        try:
            # 執行脫敏 (Task A)
            masked_data = mask_sensitive_data(data)
            masked_data_bytes = json.dumps(masked_data).encode()
            
            cloud_resp = requests.post(
                CLOUD_URL, 
                data=masked_data_bytes, 
                headers=cloud_headers(),
                # 無圖路徑：同樣分開連線與讀取，理由見帶圖那條的註解
                # （Cloudflare 524 是 100 秒硬上限）。
                timeout=(5.0, CLOUD_READ_TIMEOUT)
            )
            # 對端明確表示不可用時，交給下面的 except 走陳舊快取那條退路，
            # 而不是把 5xx 當成「請求有問題」直接回錯誤。
            if should_degrade(status_code=cloud_resp.status_code):
                raise CloudUnavailable(f"上游回 {cloud_resp.status_code}")
            if cloud_resp.status_code == 200:
                result = cloud_resp.json()
                # 🛠️ 修正：只有當 Cloud 回傳 status 為 success 時才寫入快取
                if result.get("status") == "success":
                    cursor.execute("INSERT OR REPLACE INTO cache (barcode, result_json, last_updated, ttl) VALUES (?, ?, ?, ?)",
                                   (barcode, json.dumps(result), now, 3600))
                    conn.commit()
                    print(f"[SUCCESS] {barcode} cached")
                else:
                    # 如果是 error 或 not_found，我們「不存入長期快取」，讓下次測試能再次嘗試
                    print(f"[INFO] Cloud: {barcode} -> {result.get('status')}")
                    
                response.headers["X-Cache"] = "MISS"
                conn.close()
                return normalize_result(result)
            else:
                conn.close()
                return {
                    "status": "error", 
                    "message": f"Cloud Server Error (Status: {cloud_resp.status_code})",
                    "detail": "Cloud server received the request but returned an error. Check Cloud logs."
                }
        except Exception as e:
            if 'conn' in locals(): conn.close()
            print(f"[ERROR] Cloud Forwarding Error: {barcode} -> {str(e)}")
            stale, age = get_stale_from_db(barcode)
            if stale:
                print(f"[STALE] {barcode} -> Serving stale cache (age: {age}s)")
                response.headers["X-Cache"] = "STALE"
                result = normalize_result(stale)
                result["_offline_mode"] = True
                result["_cache_age_seconds"] = age
                return result
            return {"status": "error", "message": "Cloud 不可用且無快取資料", "offline": True}

    except Exception as e:
        print(f"[ERROR] Fog Error: {e}")
        return {"status": "error", "message": str(e)}

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

def check_cloud_functionality():
    """啟動時測試 Cloud 端是否能接通並正確回應"""
    print(f"[*] 正在探查 Cloud 端功能: {CLOUD_URL}")
    test_payload = {
        "barcode": "STARTUP_PROBE",
        "label_images": []
    }
    try:
        # 設定較短的 timeout，避免啟動掛起太久 (15秒)
        response = requests.post(CLOUD_URL, json=test_payload, timeout=15.0)
        
        if response.status_code == 200:
            result = response.json()
            # 只要 Cloud 有回應 (代表 API 是通的)，就視為成功
            if result.get("status") == "success":
                print("[+] Cloud 功能探查成功！API 運作正常。")
            else:
                print(f"[!] Cloud 已連通，但回傳業務異常 (正常現象): {result.get('message', '查無條碼')}")
            
            # 只要能走到這步 (status_code 200)，就代表網域正確且服務有開
            return True 
        else:
            print(f"[-] Cloud 端回應異常狀態碼: {response.status_code}")
            return False
    except Exception as e:
        print(f"[!] Cloud 功能探查發生錯誤: {str(e)}")
        return False

if __name__ == "__main__":
    # --- 啟動前功能探查 ---
    import sys
    if not check_cloud_functionality():
        print("="*60)
        print("【 啟動中斷 】: Cloud 端功能檢查失敗！")
        print("這可能是因為：")
        print("1. Cloud 端網域 (CLOUD_API_URL) 已失效。")
        print("2. Cloud 端服務尚未啟動或發生內部錯誤。")
        print("3. 網路連線逾時。")
        print("="*60)
        sys.exit(1)

    import uvicorn
    # 優先讀取環境變數 FOG_PORT，預設為 3002
    port = int(os.getenv("FOG_PORT", 3002))
    print(f"[*] FoodAware Fog Server 正在啟動，埠號: {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
