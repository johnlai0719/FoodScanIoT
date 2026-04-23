from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
load_dotenv() # 加載 .env 環境變數

import json as _json
import mysql.connector
from datetime import datetime, timedelta
import re
import time
import google.generativeai as genai
from typing import List, Dict, Any
import base64
import io
from PIL import Image

# 匯入食安監控模組
from safety_monitor import update_producer_safety_events
from admin_api import router as admin_router

# 配置 AI
API_KEY = os.getenv("GEMINI_API_KEY")
if API_KEY:
    if API_KEY:
        genai.configure(api_key=API_KEY)
    else:
        print("[WARNING] GEMINI_API_KEY environment variable not detected")
    model = genai.GenerativeModel('gemini-3-flash-preview')

    from fastapi.staticfiles import StaticFiles
    import os

    app = FastAPI(title="FoodAware Cloud Core - Smart Guide Mode")

    # [MOUNT] Static resource directories for product marks and temporary uploads
    # Establish path mappings
    MARKS_DIR = "/home/johnlai/projects/FoodScanIoT/Clients/Mobile_App/Resource/食品標章"
    if os.path.exists(MARKS_DIR):
        app.mount("/marks", StaticFiles(directory=MARKS_DIR), name="marks")

    UPLOADS_DIR = "/home/johnlai/projects/FoodScanIoT/Servers/Cloud/uploads"
    if not os.path.exists(UPLOADS_DIR):
        os.makedirs(UPLOADS_DIR)
    app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

# 掛載管理介面靜態路徑
ADMIN_UI_DIR = os.path.join(os.path.dirname(__file__), "admin_ui")
if not os.path.exists(ADMIN_UI_DIR):
    os.makedirs(ADMIN_UI_DIR)
app.mount("/admin", StaticFiles(directory=ADMIN_UI_DIR), name="admin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "Welcome to FoodAware Cloud Server! API is ready.",
        "version": "1.0.0",
        "endpoints": {
            "analyze": "/analyze (POST)",
            "docs": "/docs"
        }
    }

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "password"),
    "database": os.getenv("DB_NAME", "product_db"),
    "charset": "utf8mb4",
    "use_unicode": True
}

def get_db_conn():
    return mysql.connector.connect(**DB_CONFIG)

def normalize_text(text: str) -> str:
    if not text: return ""
    text = "".join([chr(ord(c) - 0xfee0) if 0xff01 <= ord(c) <= 0xff5e else c for c in text])
    return re.sub(r'\(.*?\)|（.*?）|\s+', '', text)

def normalize_manufacturer_name(name: str) -> str:
    """正規化廠商名稱，移除常見後綴以提高匹配率"""
    if not name: return "未知製造商"
    # 移除「股份有限公司」、「有限公司」、「(股)公司」、「(股)」、「股份公司」等
    name = re.sub(r'(股份)?有限公司', '', name)
    name = re.sub(r'股份公司', '', name)
    name = re.sub(r'\(股\)公司?', '', name)
    name = re.sub(r'（股）公司?', '', name)
    name = re.sub(r'\s+', '', name) # 移除多餘空格
    return name

async def analyze_image_with_gemini(base64_images: list, barcode: str = "Unknown"):
    try:
        images_to_process = []
        for i, b64 in enumerate(base64_images):
            if "base64," in b64:
                b64 = b64.split("base64,")[1]
            img_data = base64.b64decode(b64)
            
            # --- 🛠️ 儲存圖片至實體路徑 ---
            img_filename = f"scan_{int(time.time())}_{barcode}_{i}.jpg"
            img_path = os.path.join(UPLOADS_DIR, img_filename)
            with open(img_path, "wb") as f:
                f.write(img_data)
            print(f"[INFO] [Storage] Image saved to: /uploads/{img_filename}")
            
            img = Image.open(io.BytesIO(img_data))
            images_to_process.append(img)
            
        prompt = """解析這些食品包裝照片（請綜合正面、成分表、營養標示所有資訊），回傳繁體中文 JSON：
        {
          "name": "產品完整名稱 (若不明確請結合品牌與產品類型描述，例如：XX牌草莓夾心餅乾)", 
          "brand": "品牌", 
          "ingredients_list": ["成分1", "成分2"], 
          "nutrition": {"calories": 0, "protein": 0, "fat": 0, "sugar": 0, "sodium": 0}, 
          "manufacturer": "製造商全名 (請參考包裝標示)", 
          "allergy_warning": "過敏原注意事項文字",
          "certification_marks": ["標章名稱", "例如: TQF, CAS, TAP, 健康食品, 有機農產品"], 
          "ingredient_details": {
            "成分1": {"desc": "50字內專業功能介紹與健康影響說明", "purpose": "技術用途如：抗氧化劑"}, 
            "成分2": {"desc": "...", "purpose": "..."}
          }
        }
        JSON ONLY. No markdown. 數值皆為數字。
        【標章辨識任務】請特別主動搜尋是否有「TQF (優良食品)」、「CAS (優良農產品)」、「TAP (產銷履歷)」、「健康食品 (小綠人)」、「有機農產品」這 5 類標章，若有發現請務必填入 certification_marks。若真的無法辨識產品名，請在 name 填入「無法辨識的產品」。"""
        
        content = [prompt] + images_to_process
        response = model.generate_content(content)
        
        # [DEBUG] 記錄原始回傳，幫助診斷失敗原因
        try:
            print(f"[DEBUG] Gemini Raw Response: {response.text[:200]}...")
        except:
            print("[DEBUG] Gemini Raw Response: <Blocked or Empty>")

        match = re.search(r'(\{.*\})', response.text, re.DOTALL)
        if match:
            return _json.loads(match.group(1))
        return None
    except Exception as e: 
        print(f"[ERROR] Gemini Vision Error: {e}")
        return None

@app.post("/analyze")
async def analyze(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        print(f"[DEBUG] [Request] Keys: {list(data.keys())}, Has Images: {bool(data.get('label_images'))}")
        barcode = data.get("barcode")
        label_images = data.get("label_images")
        user_conditions = data.get("user_conditions", {"group": "adult", "allergens": []})
        
        product = None
        # 1. 如果有圖片，不論是否有條碼，直接啟動視覺同步
        if label_images and len(label_images) > 0:
            print(f"[INFO] [Analyze] Starting AI vision analysis for barcode: {barcode or 'NEW'}...")
            vision_data = await analyze_image_with_gemini(label_images, barcode or "NEW")
            
            # --- 核心連線邏輯：在分析完畢後才建立連線，防止超時 ---
            db = get_db_conn()
            cursor = db.cursor(dictionary=True)
            
            if vision_data is not None:
                try:
                    # 即使 vision_data 是空字典 {}，也要繼續處理
                    target_barcode = barcode or f"IMG_{int(time.time())}"
                    
                    # 偵錯：印出 AI 回傳的欄位
                    print(f"[DEBUG] [Vision] Fields received: {list(vision_data.keys())}")
                    
                    # 智能名稱處理：若 AI 沒給名字，試著從品牌或預設值補完
                    v_name = vision_data.get('name') or vision_data.get('brand') or '待確認產品'
                    v_brand = vision_data.get('brand') or '未知品牌'
                    
                    raw_mfg_name = vision_data.get('manufacturer', '未知製造商') or '未知製造商'
                    mfg_name = normalize_manufacturer_name(raw_mfg_name)
                    
                    # --- 核心邏輯：廠商匹配與關聯 (加入防呆) ---
                    try:
                        cursor.execute("SELECT id FROM producers WHERE name = %s", (mfg_name,))
                        prod_row = cursor.fetchone()
                        if not prod_row:
                            cursor.execute("INSERT INTO producers (name, risk_level) VALUES (%s, %s)", (mfg_name, "Low"))
                            db.commit()
                            prod_id = cursor.lastrowid
                        else:
                            prod_id = prod_row['id']
                    except Exception as mfg_e:
                        print(f"[WARN] [DB] Producer lookup failed: {mfg_e}. Using default ID 1.")
                        prod_id = 1 # 降級使用手動建立的備胎廠商

                    ing_json = _json.dumps(vision_data.get('ingredients_list', []), ensure_ascii=False)
                    cert_json = _json.dumps(vision_data.get('certification_marks', []), ensure_ascii=False)
                    n = vision_data.get('nutrition', {})
                    # 🛠️ 關鍵修復：確保 allergens 也是標準 JSON 字串 (MySQL JSON 欄位必須)
                    allergy_text = _json.dumps(vision_data.get('allergy_warning', ''), ensure_ascii=False)
                    
                    # 執行持久化儲存
                    try:
                        sql = """
                            INSERT INTO products (
                                barcode, name, brand, producer_id, manufacturer, 
                                ingredients_list, certifications, calories, protein, fat, sugar, sodium, allergens
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE 
                            name=VALUES(name), brand=VALUES(brand), producer_id=VALUES(producer_id),
                            manufacturer=VALUES(manufacturer), ingredients_list=VALUES(ingredients_list), 
                            certifications=VALUES(certifications),
                            calories=VALUES(calories), protein=VALUES(protein), fat=VALUES(fat), 
                            sugar=VALUES(sugar), sodium=VALUES(sodium), allergens=VALUES(allergens)
                        """
                        params = (target_barcode, v_name, v_brand, 
                              prod_id, mfg_name, ing_json, cert_json,
                              n.get('calories', 0), n.get('protein', 0), n.get('fat', 0), 
                              n.get('sugar', 0), n.get('sodium', 0), allergy_text)
                        
                        cursor.execute(sql, params)
                        db.commit()
                        print(f"✅ [SUCCESS] [DB] Saved to MySQL: {v_name} ({target_barcode})")
                    except Exception as db_e:
                        import traceback
                        error_msg = traceback.format_exc()
                        print(f"❌ [CRITICAL ERROR] [DB] FAILED TO SAVE PRODUCT: {db_e}\n{error_msg}")
                        db.rollback()
                except Exception as logic_e:
                    import traceback
                    print(f"[CRITICAL ERROR] [Logic] Processing failed: {logic_e}")
                    traceback.print_exc()
                
                # 重新抓取完整資料
                cursor.execute("SELECT * FROM products WHERE barcode = %s", (target_barcode,))
                product = cursor.fetchone()
                
                # [CORE FIX] 如果資料庫抓不到（可能寫入延遲），直接用剛剛 AI 解析的資料構建臨時 product
                if not product and vision_data:
                    print(f"[WARN] [Analyze] Database lookup delayed for {target_barcode}, using vision_data as fallback.")
                    product = {
                        "barcode": target_barcode,
                        "name": v_name,
                        "brand": v_brand,
                        "producer_id": prod_id,
                        "manufacturer": mfg_name,
                        "ingredients_list": ing_json,
                        "calories": n.get('calories', 0),
                        "protein": n.get('protein', 0),
                        "fat": n.get('fat', 0),
                        "sugar": n.get('sugar', 0),
                        "sodium": n.get('sodium', 0),
                        "allergens": allergy_text
                    }
                
                if product:
                    print(f"[SUCCESS] [Analyze] Vision analysis successful: {product['name']}")
            else:
                print("[WARN] [Analyze] Gemini Vision returned None, possibly API error or blocked content.")

        # 2. 如果沒圖片，或剛才視覺分析失敗，則嘗試讀取資料庫
        if not product and barcode:
            # 核心修正：避免 TEST 條碼在分析失敗時回傳舊的錯誤紀錄
            if barcode == "TEST":
                 return {"status": "error", "message": "測試模式分析失敗：AI 視覺辨識無回傳結果。請確保拍攝清晰且包含產品名稱、成分與營養標示。"}
            
            # --- 🛠️ 重要修復：確保在純條碼查詢模式下也能建立資料庫連線 ---
            db = get_db_conn()
            cursor = db.cursor(dictionary=True)
            
            cursor.execute("SELECT * FROM products WHERE barcode = %s", (barcode,))
            product = cursor.fetchone()

        # 3. 如果依然沒資料，回傳「引導狀態」
        if not product:
            return {
                "status": "not_found",
                "barcode": barcode,
                "message": "資料庫查無此條碼紀錄，請點擊下方「上傳照片」按鈕，讓 AI 視覺大腦為您即時解析包裝！"
            }

        # --- 4. 抓取廠商食安警訊 ---
        safety_alerts = []
        if product.get('producer_id'):
            cursor.execute("SELECT title, content, alert_date FROM safety_alerts WHERE producer_id = %s", (product['producer_id'],))
            safety_alerts = cursor.fetchall()

        # --- 5. 正常分析流程 (添加物比對) ---
        ing_list = product.get('ingredients_list', '[]')
        if isinstance(ing_list, str): ing_list = _json.loads(ing_list)
        
        # 獲取 AI 產出的臨時描述 (用於資料庫缺失時)
        ai_ing_descs = vision_data.get('ingredient_descriptions', {}) if 'vision_data' in locals() else {}

        # 抓取完整的添加物知識庫資訊
        cursor.execute("SELECT id, name, aliases, food_tech_purpose, adi, medical_caution, iarc_class, description, risks FROM additives")
        knowledge = cursor.fetchall()
        
        basic, chemical = [], []
        CHEM_CHARS = ["酯", "鈉", "鉀", "酸", "磷", "聚", "精", "醇", "膠", "素"]
        
        # 準備資料庫更新清單 (智能補完)
        db_updates = []

        for ing in ing_list:
            if not ing: continue
            norm = normalize_text(ing)
            # 精確比對或別名比對 (強化防呆：過濾 None 欄位)
            match = next((a for a in knowledge if 
                        (a['name'] and a['name'] in norm) or 
                        (a['aliases'] and norm and norm in (a['aliases'] or '[]'))
                        ), None)
            
            # 獲取 AI 產出的專業描述 (從 vision_data 或 ingredient_details 獲取)
            ai_info = {}
            if 'vision_data' in locals():
                ai_info = vision_data.get('ingredient_details', {}).get(ing, {})
            
            ai_desc = ai_info.get('desc')
            ai_purpose = ai_info.get('purpose')

            # 決策邏輯：1. 優先使用資料庫 -> 2. 資料庫沒有或沒描述則記錄待更新 -> 3. 使用 AI 產出的描述
            if match and match['description']:
                final_desc = match['description']
                final_purpose = match['food_tech_purpose']
            elif ai_desc:
                final_desc = ai_desc
                final_purpose = ai_purpose
                # 如果資料庫已有此項但沒描述，或根本沒有此項，則加入更新清單
                db_updates.append({
                    "name": ing,
                    "description": ai_desc,
                    "purpose": ai_purpose,
                    "exists": bool(match),
                    "id": match['id'] if match else None
                })
            elif match:
                final_desc = f"此成分為{match['food_tech_purpose'] or '食品添加物'}，建議依個人體質適量攝取。"
                final_purpose = match['food_tech_purpose']
            else:
                final_desc = "一般成分，目前無特定風險紀錄。"
                final_purpose = None

            if match or (ai_desc and any(c in norm for c in CHEM_CHARS)):
                # 處理化學食品添加物之中英文名稱
                display_name = ing
                if match and match.get('aliases'):
                    try:
                        aliases_list = _json.loads(match['aliases'])
                        en_name = next((a for a in aliases_list if re.match(r'^[A-Za-z0-9\s\-\(\)\.\,\']+$', a)), None)
                        if en_name:
                            display_name = f"{en_name.strip()} ({ing})"
                    except Exception:
                        pass

                # 解析 risks 欄位
                risks_raw = match.get('risks') if match else '[]'
                group_risks = []
                # ... (後續解析邏輯)
                
                chemical.append({
                    "name": display_name,
                    "isAdditive": True,
                    "officialName": match['name'] if match else ing,
                    "purpose": final_purpose or "食品添加物",
                    "adiValue": match['adi'] if match else "見標示",
                    "caution": match['medical_caution'] if match else "無",
                    "iarcRating": match['iarc_class'] if match else "未分類",
                    "description": final_desc,
                    "groupRisks": group_risks,
                    "risk_level": "medium" if match or ai_desc else "low"
                })
            elif any(c in norm for c in CHEM_CHARS) and not any(b in norm for b in ["麵粉", "生乳"]):
                chemical.append({"name": ing, "isAdditive": True, "purpose": "特徵化學成分", "risk_level": "low", "description": final_desc})
            else:
                basic.append(ing)

        # 執行資料庫智能補完 (暫時註解以確保穩定性)
        # if db_updates:
        #     print(f"[INFO] 發現 {len(db_updates)} 個缺失添加物，準備智能補齊...")
        #     for up in db_updates:
        #         try:
        #             if up['exists']:
        #                 cursor.execute(
        #                     "UPDATE additives SET description = %s, food_tech_purpose = %s WHERE id = %s",
        #                     (up['description'], up['purpose'], up['id'])
        #                 )
        #             else:
        #                 cursor.execute(
        #                     "INSERT INTO additives (name, description, food_tech_purpose) VALUES (%s, %s, %s)",
        #                     (up['name'], up['description'], up['purpose'])
        #                 )
        #             db.commit()
        #         except Exception as e:
        #             print(f"[WARN] 智能補齊添加物失敗: {up['name']}, Error: {e}")
        #             db.rollback()

        nutrition = {
            "calories": product['calories'], 
            "protein": product['protein'], 
            "fat": product['fat'], 
            "sugar": product['sugar'], 
            "sodium": product['sodium'],
            "carbohydrates": product.get('carbohydrates', 0)
        }

        # 強化後的複合式 Prompt
        composite_prompt = f"""
        你是 FoodAware Pro 專家系統。請執行「臨床風險診斷」。
        
        【產品資訊】
        產品: {product['name']} | 品牌: {product['brand']} | 廠商: {product['manufacturer']}
        營養成分: {_json.dumps(nutrition, ensure_ascii=False)}
        
        【廠商食安歷史 (Module C)】
        {_json.dumps(safety_alerts, ensure_ascii=False)}
        
        【權威資料庫已提供資訊 (Module B)】
        {_json.dumps(chemical, ensure_ascii=False)}
        
        【使用者健康背景】
        {_json.dumps(user_conditions, ensure_ascii=False)}
        
        【任務】
        1. 評定總分 (0-100) 與 等級 (A-E)。
        2. 產出 100 字內之「核心診斷總結與預估風險」: 請以極度嚴謹的毒理學觀點撰寫商品總介。必須明確指出短期食用無明顯危害，但長期過量攝取可能累積的慢性健康風險（即「吃了沒事，但吃久了會有事」的潛在後果）。嚴禁單純回覆「可正常食用」或淡化風險。
        3. 產出具體「個人化警示」清單。
        4. 針對「權威資料庫已提供資訊」中的每個成分名稱(name)，產出對應的英文翻譯。
        
        以純 JSON 格式回傳：{{"score": 數字, "grade": "A-E", "summary": "字串", "warnings": ["警告1", "警告2"], "translated_chemicals": {{"中文名1": "英文名1 (中文名1)", "中文名2": "英文名2 (中文名2)"}}}}
        """
        
        try:
            ai_resp = model.generate_content(composite_prompt)
            ai_data = _json.loads(re.search(r'(\{.*\})', ai_resp.text, re.DOTALL).group(1))
            
            # --- 6. 套用增強型外文翻譯 ---
            translations = ai_data.get("translated_chemicals", {})
            for ch in chemical:
                original_name = ch["name"]
                if original_name in translations:
                    ch["name"] = translations[original_name]
                    
        except Exception as ai_e:
            print(f"[WARN] [Analyze] LLM 診斷失敗: {ai_e}")
            ai_data = {"score": 60, "grade": "C", "summary": "診斷引擎暫時降級運作。", "warnings": []}
        
        cursor.close()
        db.close()

        # 獲取標章資訊 (從資料庫)
        raw_certs = product.get('certifications', '[]')
        if not raw_certs: raw_certs = '[]'
        if isinstance(raw_certs, str): cert_marks = _json.loads(raw_certs)
        else: cert_marks = raw_certs

        # --- 6. 依照 Fog 端標準化格式建構回傳 ---
        # 處理原始成分字串 (用於 Fog 端檢索)
        raw_ingredients = ", ".join(ing_list) if isinstance(ing_list, list) else str(ing_list)
        
        # 處理過敏原文字 (從 JSON 轉回純文字)
        raw_allergens = product.get('allergens', "")
        try:
            parsed_allergens = _json.loads(raw_allergens)
            if isinstance(parsed_allergens, list):
                raw_allergens = ", ".join(parsed_allergens)
            else:
                raw_allergens = str(parsed_allergens)
        except:
            pass

        return {
            "status": "success",
            "barcode": product['barcode'],
            "data": {
                "product_info": {
                    "name": product['name'] or "AI 解析產品", 
                    "brand": product['brand'] or "AI 解析品牌", 
                    "ingredients": raw_ingredients,
                    "allergens": raw_allergens or "無特定紀錄",
                    "ai_summary": product.get('ai_summary', "無特定產品解說。")
                },
                "ingredients_detail": chemical, # 已包含 name, groupRisks, caution
                "nutrition_facts": nutrition,   # 已包含純數字之 calories, sugar, sodium, protein, fat
                "certification_marks": cert_marks,
                "manufacturer_alerts": safety_alerts,
                "final_health_diagnosis": {
                    "grade": ai_data.get("grade", "C"),
                    "score": ai_data.get("score", 60),
                    "summary": ai_data.get("summary", ""),
                    "warnings": ai_data.get("warnings", []),
                    "transparencyScore": 85 if safety_alerts else 70
                }
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3002)
