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

# 加載環境變數
load_dotenv()

def mask_sensitive_data(payload: dict) -> dict:
    """
    執行數據脫敏處理 (Task A)
    移除：device_id, uuid, location, user_conditions
    註：個人化已移至 App 端，正常情況下 App 不再送出 user_conditions；
        此處的移除保留為深度防禦，以防舊版 App 仍送出。
    模糊化：timestamp 截斷至小時精度
    """
    clean_payload = copy.deepcopy(payload)

    # 1. 刪除敏感欄位
    clean_payload.pop("device_id", None)
    clean_payload.pop("uuid", None)

    # 2. 刪除地理位置資訊
    clean_payload.pop("location", None)

    # 3. 刪除使用者健康背景（過敏原、慢性病等），不轉發至 Cloud
    #    個人化已移至 App 端（2026-08-04），Fog 本身不再讀取此欄位
    clean_payload.pop("user_conditions", None)

    # 4. 時間戳記截斷至小時精度 (格式假設: 2024-01-15T14:25:30 -> 2024-01-15T14:00:00)
    if "timestamp" in clean_payload:
        ts = clean_payload["timestamp"]
        if isinstance(ts, str) and len(ts) >= 13:
            clean_payload["timestamp"] = ts[:13] + ":00:00"

    return clean_payload

# HMAC 請求簽章已被移除

DB_PATH = "fog_cache.db"
CLOUD_URL = os.getenv("CLOUD_API_URL")
if not CLOUD_URL:
    print("[!] 警告: 未在環境變數或 .env 中偵測到 CLOUD_API_URL！")

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
    cloud_online = check_cloud_functionality()
    if not cloud_online:
        print("="*60)
        print("[WARN] Cloud 不可用，以離線快取模式啟動")
        print("[WARN] 僅能服務已快取條碼，新條碼查詢將回傳離線錯誤")
        print("="*60)
    yield
    # --- 關閉時執行 (如有需要) ---

app = FastAPI(title="FoodAware Fog Server - Vision Optimized", lifespan=lifespan)

def normalize_result(result: dict):
    """將 Cloud 回應正規化為 App 期望的格式（與使用者無關）。

    個人化比對（過敏原、族群添加物風險、慢性病營養閾值）已於 2026-08-04 移至 App 端，
    使用者健康背景不再離開裝置。本函式只保留與使用者無關的格式處理：
      1. 格式解包（Cloud 的 {status, data:{...}} 或扁平格式 → 統一結構）
      2. final_health_diagnosis 骨架注入
      3. 客觀 Nutri-Score 分數/等級提升至頂層
      4. overall_summary / additives_summary / safety_events_summary 雙向映射（App 直接讀這三個）
    """
    print(f"[DEBUG] Processing result from Cloud. Keys: {list(result.keys())}")

    # --- 1. 格式標準化 (Unwrapping) ---
    target = {}
    if result.get("status") == "success" and "data" in result:
        target = result["data"]
    elif "product_info" in result or "final_health_diagnosis" in result:
        target = result
    else:
        # 如果是扁平化格式 (直接有 name 或 ingredients)
        if any(k in result for k in ["name", "ingredients", "ingredients_list", "nutrition"]):
            print("[INFO] Flat format detected, wrapping into product_info")
            target = {
                "product_info": {
                    "name": result.get("name") or result.get("product_name", "未知產品"),
                    "brand": result.get("brand", ""),
                    "ingredients": ",".join(result.get("ingredients_list", [])) if isinstance(result.get("ingredients_list"), list) else result.get("ingredients", ""),
                    "allergens": result.get("allergens", "")
                },
                "nutrition_facts": result.get("nutrition") or result.get("nutrition_facts", {}),
                "ingredients_detail": result.get("ingredients_detail", [])
            }
        else:
            target = result

    # --- 2. 確保診斷區塊存在 ---
    if "final_health_diagnosis" not in target:
        target["final_health_diagnosis"] = {
            "score": 75,
            "grade": "B",
            "summary": "AI 解析完成。",
            "score_breakdown": []
        }

    # 如果只有 product_info 但沒填入基本資訊，從平鋪層抓取
    if "product_info" not in target and "name" in result:
        target["product_info"] = {"name": result.get("name"), "brand": result.get("brand", "")}

    # --- 3. 取得並回填 Cloud 端計算之客觀 Nutri-Score 分數與等級 ---
    objective_score = result.get("health_score") or target["final_health_diagnosis"].get("score", 75)
    objective_grade = result.get("risk_level") or target["final_health_diagnosis"].get("grade") or result.get("grade") or "C"

    result["health_score"] = objective_score
    result["risk_level"] = objective_grade
    target["final_health_diagnosis"]["score"] = objective_score
    target["final_health_diagnosis"]["grade"] = objective_grade

    # --- 4. 雙向相容映射：確保三個 summary 在 result 最外層 ---
    if "overall_summary" not in result or not result["overall_summary"]:
        explanation = result.get("explanation")
        if isinstance(explanation, dict):
            result["overall_summary"] = explanation.get("overall") or explanation.get("overall_summary")

    if "additives_summary" not in result or not result["additives_summary"]:
        explanation = result.get("explanation")
        if isinstance(explanation, dict):
            result["additives_summary"] = explanation.get("additives") or explanation.get("additives_summary")

    if "safety_events_summary" not in result or not result["safety_events_summary"]:
        notes = result.get("personalized_notes")
        if isinstance(notes, list) and len(notes) > 0:
            result["safety_events_summary"] = "\n".join(notes)
        elif isinstance(notes, str):
            result["safety_events_summary"] = notes
        else:
            explanation = result.get("explanation")
            if isinstance(explanation, dict):
                result["safety_events_summary"] = explanation.get("safety") or explanation.get("safety_events_summary")

    # 同步複製到 target / final_health_diagnosis 以免其他位置需要
    if "overall_summary" in result and isinstance(target.get("final_health_diagnosis"), dict):
        target["final_health_diagnosis"]["overall_summary"] = result["overall_summary"]
    if "additives_summary" in result and isinstance(target.get("final_health_diagnosis"), dict):
        target["final_health_diagnosis"]["additives_summary"] = result["additives_summary"]
    if "safety_events_summary" in result and isinstance(target.get("final_health_diagnosis"), dict):
        target["final_health_diagnosis"]["safety_events_summary"] = result["safety_events_summary"]

    return result

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
                    headers={"Content-Type": "application/json"},
                    timeout=90.0
                )
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
                headers={"Content-Type": "application/json"},
                timeout=50.0
            )
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
