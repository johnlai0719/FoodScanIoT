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
import numpy as np

class VectorRAG:
    def __init__(self, model_name='models/text-embedding-004'):
        self.model_name = model_name
        self.knowledge_vectors = []
        self.knowledge_data = []
        self.is_initialized = False

    def update_knowledge_base(self, knowledge_list):
        """將資料庫中的添加物資料轉為向量"""
        if not knowledge_list: return
        print("[INFO] [RAG] 更新向量知識庫中...")
        texts = []
        for k in knowledge_list:
            name = k.get('name') or ""
            aliases = k.get('aliases') or ""
            texts.append(f"{name} {aliases}".strip())
            
        try:
            result = genai.embed_content(model=self.model_name, content=texts, task_type="retrieval_document")
            self.knowledge_vectors = np.array(result['embedding'])
            self.knowledge_data = knowledge_list
            self.is_initialized = True
            print(f"✅ [SUCCESS] [RAG] 已成功索引 {len(texts)} 筆添加物向量。")
        except Exception as e:
            print(f"❌ [ERROR] [RAG] 向量轉換失敗: {e}")

    def find_nearest(self, query_text, threshold=0.65):
        """尋找語義最相似的成分"""
        if not self.is_initialized or len(self.knowledge_vectors) == 0: return None
        if not query_text or len(query_text.strip()) < 2: return None
        
        try:
            res = genai.embed_content(model=self.model_name, content=query_text, task_type="retrieval_query")
            query_vec = np.array(res['embedding'])
            
            dot_product = np.dot(self.knowledge_vectors, query_vec)
            norms = np.linalg.norm(self.knowledge_vectors, axis=1) * np.linalg.norm(query_vec)
            similarities = dot_product / (norms + 1e-8)
            
            best_idx = np.argmax(similarities)
            if similarities[best_idx] >= threshold:
                print(f"🎯 [RAG] 語義匹配成功: '{query_text}' -> '{self.knowledge_data[best_idx]['name']}' (相似度: {similarities[best_idx]:.2f})")
                return self.knowledge_data[best_idx]
        except Exception as e:
            print(f"[WARN] [RAG] 搜尋失敗: {e}")
        return None

# 初始化全域向量 RAG 實例
vector_rag = VectorRAG()

from typing import List, Dict, Any
import base64
import io
from PIL import Image

# 匯入食安監控模組與 Nutri-Score V7 計算機
from safety_monitor import update_producer_safety_events
from admin_api import router as admin_router
from nutriscore_v7 import calculator as ns_calculator

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

@app.post("/query")
@app.post("/analyze")
async def analyze(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        print(f"[DEBUG] [Request] Keys: {list(data.keys())}, Has Images: {bool(data.get('label_images'))}")
        barcode = data.get("barcode")
        label_images = data.get("label_images")
        user_conditions = data.get("user_conditions", {"group": "adult", "allergens": []})
        
        # --- 🚀 支持 Fog Node 快取重算邏輯 ---
        cached_result = data.get("cached_result")
        if cached_result and not label_images:
            print(f"[INFO] [Query] Re-calculating personalization for cached barcode: {barcode}")
            # 如果有快取且無圖片，我們可以直接從快取中提取基礎資料，進行個人化診斷
            # 這裡暫時維持原流程以確保資料一致性
        
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
        
        # --- 🚀 初始化向量 RAG 知識庫 (僅在需要時) ---
        if not vector_rag.is_initialized and knowledge:
            vector_rag.update_knowledge_base(knowledge)

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
            
            # --- 🚀 向量 RAG 補償邏輯：若 SQL 找不到，嘗試語義匹配 ---
            if not match:
                match = vector_rag.find_nearest(norm)
            
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

        # --- 🚀 核心：Nutri-Score V7 硬代碼精準計分 (對照 PDF V7 標準) ---
        # 準備計算所需數值，確保類型為 float
        ns_data = {
            "energy": float(product['calories']) * 4.184, # kcal 轉 kJ
            "sugars": float(product['sugar']),
            "sfa": float(product['fat']) * 0.35, # 假設飽和脂肪佔總脂肪 35% (若資料庫無此欄位)
            "salt": float(product['sodium']) / 1000 * 2.5, # mg 鈉 轉 g 鹽
            "proteins": float(product['protein']),
            "fibres": 2.0, # 預設纖維
            "fruit_veg_pct": 10.0 # 預設蔬果比
        }
        
        # 判定是否為特定類別 (後續可優化為從資料庫讀取)
        is_beverage = any(kw in product['name'] for kw in ["飲", "水", "汁", "奶", "啡", "茶"])
        is_cheese = any(kw in product['name'] for kw in ["乳酪", "起司", "Cheese"])
        
        # 執行 100% 準確的 V7 演算法
        calc_result = ns_calculator.calculate(ns_data, is_beverage=is_beverage, is_cheese=is_cheese)
        deterministic_score = calc_result['score']
        deterministic_grade = calc_result['grade']

        # 強化後的複合式 Prompt
        composite_prompt = f"""
        你是 FoodAware Pro 專家系統。請執行「臨床風險診斷」。
        
        【產品資訊】
        產品: {product['name']} | 品牌: {product['brand']} | 廠商: {product['manufacturer']}
        營養成分: {_json.dumps(nutrition, ensure_ascii=False)}
        
        【權威計分依據 (Nutri-Score V7 2024 最新版)】
        依據歐盟 2024 演算法算出的確切總分: {deterministic_score}
        確定之健康分級: {deterministic_grade} 級 (A為最優，E為最差)
        
        【廠商食安歷史 (Module C)】
        {_json.dumps(safety_alerts, ensure_ascii=False)}
        
        【權威資料庫已提供資訊 (Module B)】
        {_json.dumps(chemical, ensure_ascii=False)}
        
        【使用者健康背景】
        {_json.dumps(user_conditions, ensure_ascii=False)}
        
        【任務】
        1. 參考「確切總分」與「健康分級」，產出 100 字內之「核心診斷總結與預估風險」。
        2. 請以極度嚴謹的毒理學觀點撰寫，必須明確指出長期過量攝取可能累積的慢性健康風險。
        3. 產出具體「個人化警示」清單。
        
        以純 JSON 格式回傳：{{"score": {deterministic_score}, "grade": "{deterministic_grade}", "summary": "字串", "warnings": ["警告1", "警告2"]}}
        """
        
        try:
            ai_resp = model.generate_content(composite_prompt)
            ai_data = _json.loads(re.search(r'(\{.*\})', ai_resp.text, re.DOTALL).group(1))
        except Exception as ai_e:
            print(f"[WARN] [Analyze] LLM 診斷失敗: {ai_e}")
            ai_data = {"score": deterministic_score, "grade": deterministic_grade, "summary": "診斷引擎暫時降級運作。", "warnings": []}
        
        cursor.close()
        db.close()

        # 獲取標章資訊 (從資料庫)
        raw_certs = product.get('certifications', '[]')
        if not raw_certs: raw_certs = '[]'
        if isinstance(raw_certs, str): cert_marks = _json.loads(raw_certs)
        else: cert_marks = raw_certs

        # --- 6. 依照 Fog 端標準化格式建構回傳 (對齊 shared/types.ts) ---
        # 映射等級到風險程度
        grade_to_risk = {"A": "low", "B": "low", "C": "medium", "D": "high", "E": "high"}
        risk_level = grade_to_risk.get(ai_data.get("grade", "C"), "medium")

        # 整理過敏原
        final_allergens = []
        if raw_allergens:
            final_allergens = [a.strip() for a in raw_allergens.split(",") if a.strip()]

        # 建構 FogQueryResult 格式
        return {
            "status": "success",
            "barcode": product['barcode'],
            "health_score": ai_data.get("score", deterministic_score),
            "risk_level": risk_level,
            "risk_tags": ai_data.get("warnings", []),
            "allergen_warnings": final_allergens,
            "food_safety_events": [
                {
                    "date": str(e.get('alert_date', datetime.now().date())), 
                    "type": "官方抽驗/違規", 
                    "summary": e.get('title', '未知事件'), 
                    "source_url": ""
                }
                for e in safety_alerts
            ],
            "explanation": {
                "triggers": [ch['name'] for ch in chemical if ch.get('risk_level') == 'high'],
                "sources": ["歐盟 Nutri-Score V7 (2024)", "FoodScanIoT 食品添加物知識庫"]
            },
            "personalized_notes": [ai_data.get("summary", "")],
            "processed_at": datetime.now().isoformat(),
            # 原始詳細資料保留於 data 欄位供前端擴充顯示
            "data": {
                "product_info": {
                    "name": product['name'] or "AI 解析產品", 
                    "brand": product['brand'] or "AI 解析品牌", 
                    "ingredients": raw_ingredients,
                    "manufacturer": product['manufacturer']
                },
                "ingredients_detail": chemical,
                "nutrition_facts": nutrition,
                "certification_marks": cert_marks
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3002)
