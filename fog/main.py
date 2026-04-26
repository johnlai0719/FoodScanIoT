from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import json
import time
import requests
import hmac
import hashlib
import copy
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv

# 加載環境變數
load_dotenv()

def mask_sensitive_data(payload: dict) -> dict:
    """
    執行數據脫敏處理 (Task A)
    移除：device_id, uuid, location
    模糊化：timestamp 截斷至小時精度
    """
    clean_payload = copy.deepcopy(payload)
    
    # 1. 刪除敏感欄位
    clean_payload.pop("device_id", None)
    clean_payload.pop("uuid", None)
    
    # 2. 刪除地理位置資訊
    clean_payload.pop("location", None)
    
    # 3. 時間戳記截斷至小時精度 (格式假設: 2024-01-15T14:25:30 -> 2024-01-15T14:00:00)
    if "timestamp" in clean_payload:
        ts = clean_payload["timestamp"]
        if isinstance(ts, str) and len(ts) >= 13:
            clean_payload["timestamp"] = ts[:13] + ":00:00"
            
    return clean_payload

def sign_request(payload_bytes: bytes) -> str:
    """
    使用 HMAC-SHA256 計算請求簽章 (Task C)
    
    Cloud 端驗證邏輯範例 (Python):
    ---------------------------------------------------------
    # expected_sig = hmac.new(SECRET_KEY, request_body_bytes, hashlib.sha256).hexdigest()
    # if hmac.compare_digest(expected_sig, request.headers.get("X-Fog-Signature")):
    #     print("驗證成功")
    ---------------------------------------------------------
    """
    secret = os.getenv("FOG_SECRET_KEY")
    if not secret:
        raise ValueError("缺少環境變數 FOG_SECRET_KEY，無法進行請求簽章。請確保 .env 中已設定該變數。")
    
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

DB_PATH = "fog_cache.db"
CLOUD_URL = os.getenv("CLOUD_API_URL")
if not CLOUD_URL:
    print("[!] 警告: 未在環境變數或 .env 中偵測到 CLOUD_API_URL！")

def check_cloud_functionality():
    """啟動時測試 Cloud 端是否能接通並正確回應"""
    print(f"[*] 正在探查 Cloud 端功能: {CLOUD_URL}")
    test_payload = {
        "barcode": "STARTUP_PROBE",
        "label_images": [],
        "user_conditions": {"group": "adult", "allergens": []}
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
    # --- 啟動時檢查 ---
    if not check_cloud_functionality():
        print("="*60)
        print("【 啟動中斷 】: Cloud 端功能檢查失敗！")
        print("請檢查網域配置。")
        print("="*60)
        # 引發錯誤會導致啟動停止
        raise RuntimeError("Cloud functionality probe failed.")
    yield
    # --- 關閉時執行 (如有需要) ---

app = FastAPI(title="FoodAware Fog Server - Vision Optimized", lifespan=lifespan)

# 強化 CORS 處理
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

DB_PATH = "fog_cache.db"
CLOUD_URL = os.getenv("CLOUD_API_URL")
if not CLOUD_URL:
    print("[!] 警告: 未在環境變數或 .env 中偵測到 CLOUD_API_URL！")
print(f"[DEBUG] Using CLOUD_URL: {CLOUD_URL}")

def calculate_personalized_score(result: dict, user_conditions: dict):
    """根據使用者條件與 Cloud 提供之風險資料，即時計算個人化評分與扣分明細"""
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
            "summary": "AI 解析完成，正進行個人化評估...",
            "score_breakdown": []
        }
    
    # 如果只有 product_info 但沒填入基本資訊，從平鋪層抓取
    if "product_info" not in target and "name" in result:
        target["product_info"] = {"name": result.get("name"), "brand": result.get("brand", "")}

    # 3. 取得基礎分數
    raw_base = target["final_health_diagnosis"].get("score", 75)
    base_score = max(75, raw_base)
    
    # ... (其餘評分邏輯保持不變)
    
    user_group = user_conditions.get("group", "adult")
    user_allergens = user_conditions.get("allergens", [])
    custom_allergens = user_conditions.get("custom_allergens", [])
    user_health = user_conditions.get("health_conditions", [])
    custom_conditions = user_conditions.get("custom_conditions", [])
    
    # 合併標準與自訂過敏原
    all_user_allergens = user_allergens + custom_allergens

    # 過敏原標籤相容性對照表 (手機傳入標籤 -> 內部比對標籤)
    allergen_map = {
        "milk": "牛奶", "dairy": "牛奶",
        "egg": "蛋", "eggs": "蛋",
        "nuts": "堅果", "nut": "堅果",
        "gluten": "含麩質穀物", "wheat": "含麩質穀物",
        "soy": "大豆", "soybean": "大豆",
        "crustacean": "甲殼類", "shrimp": "甲殼類", "crab": "甲殼類",
        "fish": "魚類", "peanut": "花生", "sesame": "芝麻",
        "sulfite": "亞硫酸鹽", "mango": "芒果"
    }
    
    # 標準化使用者傳入的過敏原
    normalized_user_allergens = []
    for a in all_user_allergens:
        a_lower = a.lower()
        if a_lower in allergen_map:
            normalized_user_allergens.append(allergen_map[a_lower])
        else:
            normalized_user_allergens.append(a)
    
    all_user_allergens = list(set(normalized_user_allergens)) # 去重

    # 過敏原關鍵字擴展對應
    allergen_keywords = {
        "牛奶": ["乳", "奶", "乳清", "酪蛋白", "milk", "dairy", "lactose"],
        "蛋": ["蛋", "卵", "egg"],
        "堅果": ["核桃", "腰果", "杏仁", "夏威夷豆", "榛果", "堅果", "nut"],
        "含麩質穀物": ["麵粉", "小麥", "大麥", "燕麥", "黑麥", "麩質", "gluten", "wheat"],
        "大豆": ["黃豆", "大豆", "卵磷脂", "soy"],
        "甲殼類": ["蝦", "蟹", "龍蝦", "shrimp", "crab"],
        "魚類": ["魚", "明膠", "fish"],
        "花生": ["花生", "落花生", "peanut"],
        "芝麻": ["芝麻", "香油", "sesame"],
        "亞硫酸鹽": ["亞硫酸", "二氧化硫", "漂白劑", "sulfite"],
        "芒果": ["芒果", "mango"]
    }

    # 族群與疾病名稱映射 (統一對應資料庫中的風險標籤)
    tag_map = {
        "pregnant": "孕婦",
        "child": "嬰幼兒",
        "hypertension": "高血壓",
        "diabetes": "糖尿病",
        "heart_disease": "心臟病",
        "kidney_disease": "腎臟病",
        "adult": "成人"
    }
    
    active_tags = []
    if user_group in tag_map: active_tags.append(tag_map[user_group])
    for h in user_health:
        if h in tag_map: active_tags.append(tag_map[h])
        else: active_tags.append(h)
        
    # 加入自訂健康條件/族群標籤
    for c in custom_conditions:
        active_tags.append(c)

    current_score = base_score
    breakdown = []
    
    breakdown.append({
        "reason": "基礎營養與客觀風險分", 
        "description": "基於熱量、糖、鈉等營養標示之基礎評分。",
        "points": base_score, 
        "type": "base"
    })
    
    # 2. 遍歷成分與原始文字，進行個人化加減分
    ingredients = target.get("ingredients_detail", [])
    # 取得產品原始成分文字與過敏原文字
    raw_ingredients_text = target.get("product_info", {}).get("ingredients", "") or ""
    product_allergens_text = target.get("product_info", {}).get("allergens", "") or ""
    
    # 合併所有文字來源，增加比對命中率
    all_source_text = (raw_ingredients_text + product_allergens_text).lower()
    
    matched_allergens = []

    # --- A. 針對詳細成分清單進行比對 ---
    for ing in ingredients:
        ing_name = ing.get("name", "").lower()
        for user_allergen in all_user_allergens:
            keywords = allergen_keywords.get(user_allergen, [user_allergen])
            if any(k in ing_name for k in keywords):
                if user_allergen not in matched_allergens:
                    matched_allergens.append(user_allergen)
                    penalty = -40 
                    current_score += penalty
                    breakdown.append({
                        "reason": f"🚨 重度過敏原警告: {user_allergen}", 
                        "description": f"產品細節成分中包含「{ing_name}」，請絕對避免食用！",
                        "points": penalty, 
                        "type": "allergen"
                    })

    # --- B. 針對原始文字 (大字串) 進行二次檢查 (防止 AI 沒拆解成分) ---
    for user_allergen in all_user_allergens:
        if user_allergen in matched_allergens: continue # 已在 A 步驟抓到則跳過
        
        keywords = allergen_keywords.get(user_allergen, [user_allergen])
        for k in keywords:
            if k in all_source_text:
                matched_allergens.append(user_allergen)
                penalty = -40
                current_score += penalty
                breakdown.append({
                    "reason": f"🚨 文字偵測過敏原: {user_allergen}", 
                    "description": f"產品標示文字中偵測到「{k}」，極可能包含過敏原，請謹慎！",
                    "points": penalty, 
                    "type": "allergen"
                })
                break # 抓到一個關鍵字就夠了
            
        group_risks = ing.get("groupRisks", [])
        for gr in group_risks:
            target_tag = gr.get("group")
            if target_tag in active_tags:
                level = gr.get("riskLevel", 0)
                if level >= 3:
                    penalty_map = {3: -8, 4: -12, 5: -20}
                    penalty = penalty_map.get(level, -5)
                    current_score += penalty
                    
                    impact_desc = gr.get("reason", f"對「{target_tag}」有潛在影響。")
                    if ing.get("caution"):
                        impact_desc = f"{ing.get('caution')} (影響等級: {level})"
                    
                    breakdown.append({
                        "reason": f"{ing_name} ({target_tag}專屬風險)", 
                        "description": impact_desc,
                        "points": penalty, 
                        "type": "group_risk"
                    })

    nutrition = target.get("nutrition_facts", {})
    if ("高血壓" in active_tags or user_group == "hypertension") and nutrition.get("sodium", 0) > 400:
        penalty = -15
        current_score += penalty
        breakdown.append({
            "reason": "高血壓族群高鈉加權扣分", 
            "description": f"此產品鈉含量為 {nutrition.get('sodium')}mg，超過高血壓建議閾值。",
            "points": penalty, 
            "type": "nutrition"
        })
    
    if ("糖尿病" in active_tags or user_group == "diabetes") and nutrition.get("sugar", 0) > 10:
        penalty = -20
        current_score += penalty
        breakdown.append({
            "reason": "糖尿病族群高糖加權扣分", 
            "description": f"此產品含糖量為 {nutrition.get('sugar')}g，對血糖波動影響顯著。",
            "points": penalty, 
            "type": "nutrition"
        })
    
    # 3. 標章加分 (CAS, TAP, TQF, 健康食品, 有機農產品)
    certification_marks = target.get("certification_marks", [])
    mark_points = 0
    if certification_marks:
        for mark in certification_marks:
            # 每個標章加 5 分
            mark_points += 5
            breakdown.append({
                "reason": f"✅ 認證標章加分: {mark}",
                "description": f"產品獲得「{mark}」認證，代表符合國家級或公正單位之食安/品質規範。",
                "points": 5,
                "type": "certification"
            })
        current_score += mark_points

    current_score = max(0, min(100, current_score))
    
    def get_grade(s):
        if s >= 85: return "A"
        if s >= 70: return "B"
        if s >= 55: return "C"
        if s >= 40: return "D"
        return "E"

    new_grade = get_grade(current_score)
    
    dynamic_summary = ""
    critical_risks = [b["reason"] for b in breakdown if b.get("type") in ["allergen", "group_risk"] and b.get("points", 0) <= -8]
    
    if critical_risks:
        dynamic_summary = f"【{tag_map.get(user_group, '使用者')}注意】偵測到高度不相符成分：{', '.join(critical_risks[:2])}。建議諮詢專業醫護建議。"
    elif current_score < 60:
        dynamic_summary = f"【健康警示】根據您的個人設定，此產品評分偏低 ({current_score}分)，請謹慎食用。"
    else:
        dynamic_summary = f"【適宜建議】此產品與您的健康設定相符，評級為 {new_grade}。"

    target["final_health_diagnosis"]["score"] = current_score
    target["final_health_diagnosis"]["grade"] = new_grade
    target["final_health_diagnosis"]["score_breakdown"] = breakdown
    
    # 取得 Cloud 端提供的 AI 摘要 (優先從新格式的 product_info.ai_summary 抓取)
    original_summary = target.get("product_info", {}).get("ai_summary") or target["final_health_diagnosis"].get("summary", "")
    target["final_health_diagnosis"]["summary"] = f"{dynamic_summary}\n\n[AI 深度分析]：{original_summary}"
    
    return result

@app.post("/query")
async def query(request: Request, response: Response):
    try:
        data = await request.json()
        barcode = data.get("barcode")
        label_images = data.get("label_images")
        user_conditions = data.get("user_conditions", {"group": "adult", "allergens": []})
        
        # --- 🟢 簡化版: 單行專業日誌 ---
        img_info = f"YES({len(label_images)})" if label_images else "NO"
        grp = user_conditions.get('group', 'N/A')
        print(f"[REQ] {barcode} | Grp: {grp} | Img: {img_info} | {time.strftime('%H:%M:%S')}")

        # --- 新增: 如果 Node.js 已經帶了快取結果過來，直接計算並回傳 ---
        cached_from_node = data.get("cached_result")
        if cached_from_node:
            print(f"[CACHE] {barcode} -> Re-calculating personalization")
            return calculate_personalized_score(cached_from_node, user_conditions)
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
                # 執行脫敏與簽章 (Task A & C)
                masked_data = mask_sensitive_data(data)
                masked_data_bytes = json.dumps(masked_data).encode()
                signature = sign_request(masked_data_bytes)
                
                cloud_resp = requests.post(
                    CLOUD_URL, 
                    data=masked_data_bytes, 
                    headers={"Content-Type": "application/json", "X-Fog-Signature": signature},
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
                return calculate_personalized_score(result, user_conditions)
            except Exception as e:
                conn.close()
                print(f"[ERROR] Cloud Forwarding Error: {barcode} -> {str(e)}")
                raise HTTPException(status_code=504, detail=f"Cloud Forwarding Error: {str(e)}")

        # 🛠️ 如果是測試模式且沒照片，直接從快取回傳上次的成功結果 (取代 Cloud 查詢)
        if is_test and test_cache_row:
            print(f"[CACHE] {barcode} -> Returning cached result")
            cached_result = json.loads(test_cache_row[0])
            conn.close()
            return calculate_personalized_score(cached_result, user_conditions)

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
                return calculate_personalized_score(cached_result, user_conditions)

        # 3. 快取未命中 (或是快取中是 not_found)
        print(f"[MISS] {barcode} -> Forwarding to Cloud")
        try:
            # 執行脫敏與簽章 (Task A & C)
            masked_data = mask_sensitive_data(data)
            masked_data_bytes = json.dumps(masked_data).encode()
            signature = sign_request(masked_data_bytes)
            
            cloud_resp = requests.post(
                CLOUD_URL, 
                data=masked_data_bytes, 
                headers={"Content-Type": "application/json", "X-Fog-Signature": signature},
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
                return calculate_personalized_score(result, user_conditions)
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
            return {"status": "error", "message": f"Cloud Forwarding Error: {str(e)}"}

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
        "label_images": [],
        "user_conditions": {"group": "adult", "allergens": []}
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
