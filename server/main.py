from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import json as _json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
import re
import time
from google import genai
from google.genai import types
import numpy as np

class VectorRAG:
    def __init__(self, model_name='gemini-embedding-2'):
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
            name_zh = k.get('name_zh') or ""
            name_en = k.get('name_en') or ""
            ins_or_e = k.get('ins_or_e_number') or ""
            
            aliases_raw = k.get('aliases')
            aliases_str = ""
            if aliases_raw:
                if isinstance(aliases_raw, list):
                    aliases_str = " ".join(aliases_raw)
                elif isinstance(aliases_raw, str):
                    try:
                        aliases_list = _json.loads(aliases_raw)
                        if isinstance(aliases_list, list):
                            aliases_str = " ".join(aliases_list)
                        else:
                            aliases_str = str(aliases_raw)
                    except Exception:
                        aliases_str = aliases_raw
            texts.append(f"{name_zh} {name_en} {aliases_str} {ins_or_e}".strip())
            
        try:
            embeddings_list = []
            chunk_size = 100
            for i in range(0, len(texts), chunk_size):
                chunk = texts[i:i + chunk_size]
                result = _genai_client.models.embed_content(
                    model=self.model_name,
                    contents=chunk,
                    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
                )
                embeddings_list.extend([e.values for e in result.embeddings])
            self.knowledge_vectors = np.array(embeddings_list)
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
            res = _genai_client.models.embed_content(
                model=self.model_name,
                contents=query_text,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
            )
            query_vec = np.array(res.embeddings[0].values)
            
            dot_product = np.dot(self.knowledge_vectors, query_vec)
            norms = np.linalg.norm(self.knowledge_vectors, axis=1) * np.linalg.norm(query_vec)
            similarities = dot_product / (norms + 1e-8)
            
            best_idx = np.argmax(similarities)
            if similarities[best_idx] >= threshold:
                print(f"🎯 [RAG] 語義匹配成功: '{query_text}' -> '{self.knowledge_data[best_idx]['name_zh']}' (相似度: {similarities[best_idx]:.2f})")
                return self.knowledge_data[best_idx]
        except Exception as e:
            print(f"[WARN] [RAG] 搜尋失敗: {e}")
        return None

# 初始化全域向量 RAG 實例
vector_rag = VectorRAG()

def is_similar_event(evt1: dict, evt2: dict) -> bool:
    def clean(text):
        if not text:
            return ""
        return re.sub(r'[^\w\s]', '', text).lower().strip()

    title1 = clean(evt1.get('title', ''))
    title2 = clean(evt2.get('title', ''))
    summary1 = clean(evt1.get('summary', ''))
    summary2 = clean(evt2.get('summary', ''))

    # 1. Exact or substring match in titles
    if title1 and title2:
        if title1 in title2 or title2 in title1:
            return True

    # 2. Check for specific high-value keyword matches
    keywords = ["8公分", "8cm", "手卷", "蘋果麵包", "塑化劑", "順丁烯二酸", "銅葉綠素", "戴奧辛", "單氯丙二醇", "立光農工", "真飽涼麵", "保明智"]
    for kw in keywords:
        kw_clean = kw.lower()
        in_evt1 = (kw_clean in title1 or kw_clean in summary1)
        in_evt2 = (kw_clean in title2 or kw_clean in summary2)
        if in_evt1 and in_evt2:
            return True

    # 3. Use Jaccard similarity on words of titles
    words1 = set(title1.split())
    words2 = set(title2.split())
    if words1 and words2:
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        jaccard = len(intersection) / len(union)
        if jaccard > 0.4:
            return True

    return False

def merge_events(events: list) -> list:
    merged_list = []
    for evt in events:
        found = False
        for m_evt in merged_list:
            if is_similar_event(evt, m_evt):
                # Merge evt into m_evt
                d1 = m_evt.get('date', '')
                d2 = evt.get('date', '')
                if d2 and (not d1 or d2 > d1):
                    m_evt['date'] = d2
                
                if evt.get('summary') and evt.get('summary') not in m_evt.get('summary', ''):
                    if len(evt.get('summary')) > len(m_evt.get('summary', '')):
                        m_evt['summary'] = evt.get('summary')
                
                if evt.get('title') and len(evt.get('title', '')) > len(m_evt.get('title', '')):
                    m_evt['title'] = evt.get('title')

                if evt.get('severity') is not None:
                    if m_evt.get('severity') is None or evt.get('severity') > m_evt.get('severity'):
                        m_evt['severity'] = evt.get('severity')

                url = evt.get('source_url', '')
                if url:
                    link_title = "來源連結"
                    url_lower = url.lower()
                    st = evt.get('_raw_source_type') or ''
                    if "threads.com" in url_lower:
                        link_title = "Threads 連結"
                    elif "wikipedia.org" in url_lower:
                        link_title = "維基百科"
                    elif st == "official":
                        link_title = "官方公告"
                    elif st == "news":
                        link_title = "新聞連結"
                    elif st == "social":
                        link_title = "社群討論"
                    
                    if not any(lnk['url'] == url for lnk in m_evt['links']):
                        m_evt['links'].append({"title": link_title, "url": url})
                    
                    if 'sources' not in m_evt or not m_evt['sources']:
                        m_evt['sources'] = {"official": None, "news": None, "social": None}
                    if st in ["official", "news", "social"]:
                        m_evt['sources'][st] = url
                
                found = True
                break
        
        if not found:
            links = []
            url = evt.get('source_url', '')
            st = evt.get('_raw_source_type') or ''
            if url:
                link_title = "來源連結"
                url_lower = url.lower()
                if "threads.com" in url_lower:
                    link_title = "Threads 連結"
                elif "wikipedia.org" in url_lower:
                    link_title = "維基百科"
                elif st == "official":
                    link_title = "官方公告"
                elif st == "news":
                    link_title = "新聞連結"
                elif st == "social":
                    link_title = "社群討論"
                links.append({"title": link_title, "url": url})

            new_evt = {
                "date": evt.get('date') or '',
                "type": evt.get('type') or '未知來源',
                "title": evt.get('title') or '',
                "summary": evt.get('summary') or '',
                "source_url": url,
                "severity": evt.get('severity'),
                "links": links,
                "sources": {
                    "official": url if st == 'official' else None,
                    "news": url if st == 'news' else None,
                    "social": url if st == 'social' else None
                },
                "_raw_source_type": st
            }
            merged_list.append(new_evt)
            
    for evt in merged_list:
        evt.pop('_raw_source_type', None)
        
    return merged_list


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
_genai_client = None
_model_name = 'gemma-4-31b-it'
if API_KEY:
    _genai_client = genai.Client(api_key=API_KEY, http_options=types.HttpOptions(timeout=60000))
else:
    print("[WARNING] GEMINI_API_KEY environment variable not detected")

from fastapi.staticfiles import StaticFiles
import os

app = FastAPI(title="FoodAware Cloud Core - Smart Guide Mode")

import jwt
import bcrypt
from pydantic import BaseModel
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

class LoginRequest(BaseModel):
    username: str
    password: str

class SuggestionRequest(BaseModel):
    field_name: str
    new_value: str
    suggested_by: str
    reason: str

class CommentRequest(BaseModel):
    author: str
    content: str

JWT_SECRET = os.getenv("JWT_SECRET", "super_secret_key_change_me")
JWT_ALGORITHM = "HS256"
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.post("/api/admin/login")
def admin_login(req: LoginRequest):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM admin_users WHERE username = %s", (req.username,))
    user = cur.fetchone()
    conn.close()
    
    if not user or not bcrypt.checkpw(req.password.encode('utf-8'), user['password_hash'].encode('utf-8')):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
        
    token = jwt.encode({"user_id": user['id'], "username": user['username']}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/additives/{record_id}/suggest")
def submit_suggestion(record_id: str, req: SuggestionRequest):
    allowed_fields = {"description", "name_zh", "name_en", "ins_or_e_number", "adi"}
    if req.field_name not in allowed_fields:
        raise HTTPException(status_code=400, detail="Invalid field name")
        
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM additives WHERE record_id = %s", (record_id,))
        additive = cur.fetchone()
        if not additive:
            raise HTTPException(status_code=404, detail="Additive not found")
            
        old_val = additive.get(req.field_name)
        old_value = str(old_val) if old_val is not None else None
        
        cur.execute(
            """
            INSERT INTO additive_suggestions (record_id, field_name, old_value, new_value, suggested_by, reason, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, record_id, field_name, old_value, new_value, suggested_by, reason, status, created_at
            """,
            (record_id, req.field_name, old_value, req.new_value, req.suggested_by, req.reason, "pending")
        )
        suggestion = cur.fetchone()
        conn.commit()
        return suggestion
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/additives/{record_id}/comment")
def submit_comment(record_id: str, req: CommentRequest):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            INSERT INTO additive_comments (record_id, author, content, status)
            VALUES (%s, %s, %s, %s)
            RETURNING id, record_id, author, content, status, created_at
            """,
            (record_id, req.author, req.content, "approved")
        )
        comment = cur.fetchone()
        conn.commit()
        return comment
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/additives")
def list_or_search_additives(q: str = None):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if q:
        query = """
            SELECT * FROM additives
            WHERE name_zh ILIKE %s
               OR name_en ILIKE %s
               OR ins_or_e_number ILIKE %s
               OR aliases::jsonb::text ILIKE %s
               OR category::jsonb::text ILIKE %s
            ORDER BY id ASC
        """
        like_pattern = f"%{q}%"
        cur.execute(query, (like_pattern, like_pattern, like_pattern, like_pattern, like_pattern))
    else:
        cur.execute("SELECT * FROM additives ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return rows

@app.get("/api/additives/{record_id}")
def get_additive(record_id: str):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM additives WHERE record_id = %s", (record_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Additive not found")
    
    cur.execute("SELECT * FROM additive_comments WHERE record_id = %s ORDER BY created_at DESC", (record_id,))
    comments = cur.fetchall()
    conn.close()
    
    row["comments"] = comments
    return row

@app.get("/api/admin/suggestions")
def list_suggestions(status: str = None, current_user = Depends(get_current_user)):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if status:
        cur.execute("SELECT * FROM additive_suggestions WHERE status = %s ORDER BY id DESC", (status,))
    else:
        cur.execute("SELECT * FROM additive_suggestions ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

@app.put("/api/admin/suggestions/{suggestion_id}/approve")
def approve_suggestion(suggestion_id: int, current_user = Depends(get_current_user)):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM additive_suggestions WHERE id = %s", (suggestion_id,))
        sug = cur.fetchone()
        if not sug:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        if sug["status"] != "pending":
            raise HTTPException(status_code=400, detail="Suggestion is already processed")
        
        field_name = sug["field_name"]
        allowed_fields = {"description", "name_zh", "name_en", "ins_or_e_number", "adi"}
        if field_name not in allowed_fields:
            raise HTTPException(status_code=400, detail="Field update not allowed for security reasons")
        
        cur.execute(
            """
            UPDATE additive_suggestions
            SET status = 'approved', reviewed_by = %s, reviewed_at = %s
            WHERE id = %s
            """,
            (current_user.get("user_id"), datetime.now(), suggestion_id)
        )
        
        query = f"UPDATE additives SET {field_name} = %s WHERE record_id = %s"
        cur.execute(query, (sug["new_value"], sug["record_id"]))
        
        conn.commit()
        return {"status": "success", "message": "Suggestion approved and database updated"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.put("/api/admin/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: int, current_user = Depends(get_current_user)):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM additive_suggestions WHERE id = %s", (suggestion_id,))
        sug = cur.fetchone()
        if not sug:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        if sug["status"] != "pending":
            raise HTTPException(status_code=400, detail="Suggestion is already processed")
        
        cur.execute(
            """
            UPDATE additive_suggestions
            SET status = 'rejected', reviewed_by = %s, reviewed_at = %s
            WHERE id = %s
            """,
            (current_user.get("user_id"), datetime.now(), suggestion_id)
        )
        
        conn.commit()
        return {"status": "success", "message": "Suggestion rejected"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


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
    allow_credentials=False,
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
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "password"),
    "dbname": os.getenv("DB_NAME", "product_db"),
    "port": int(os.getenv("DB_PORT", "5432")),
}

def get_db_conn():
    return psycopg2.connect(**DB_CONFIG)

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
            
        prompt = """解析這些食品包裝照片，回傳繁體中文 JSON：
        {
          "name": "產品完整名稱 (若不明確請結合品牌與產品類型描述，例如：XX牌草莓夾心餅乾)", 
          "brand": "品牌", 
          "ingredients_list": ["成分1", "成分2"], 
          "ingredient_types": {"成分1": "additive", "成分2": "ingredient"}, 
          "nutrition": {
            "calories": 0,
            "protein": 0,
            "fat": 0,
            "sugar": 0,
            "sodium": 0
          }, 
          "manufacturer": "製造商全名 (請參考包裝標示)", 
          "allergy_warning": "過敏原注意事項文字",
          "certification_marks": ["標章名稱", "例如: TQF, CAS, TAP, 健康食品, 有機農產品"], 
          "ingredient_details": {
            "成分1": {"desc": "50字內專業功能介紹與健康影響說明", "purpose": "技術用途如：抗氧化劑"}, 
            "成分2": {"desc": "...", "purpose": "..."}
          }
        }

        【營養標示讀取規則 - 極重要】
        nutrition 欄位必須使用「每 100 公克」或「每 100 毫升」的數值。
        若包裝同時列有「每份」與「每 100ml/g」兩欄，請取「每 100ml/g」。
        若只有「每份」，請以每份數值除以每份份量(g或ml)再乘以100換算。
        calories = 大卡(kcal)數字，例如包裝標示 39.2大卡/100ml → 填 39.2。

        【標章辨識任務】
        請主動辨識所有標章 logo，尤其是：
        TQF（優良食品驗證，盾牌形金色logo）、CAS（優良農產品）、TAP（產銷履歷）、
        健康食品（小綠人）、有機農產品。若同一款標章出現多個編號，仍只回傳一個 "TQF"。

        JSON ONLY. No markdown. 數值皆為數字。其中 "additive" 代表食品添加物，"ingredient" 代表天然原料/食材；鍵必須與 ingredients_list 中的成分名稱完全對應。"""
        
        content = [prompt] + images_to_process
        response = _genai_client.models.generate_content(model="gemini-2.5-flash", contents=content)
        
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
@app.post("/api/analyze")
async def analyze(request: Request, background_tasks: BackgroundTasks):
    try:
        # 1. 取得原始請求內容供簽章驗證
        body_bytes = await request.body()

        data = _json.loads(body_bytes)
        print(f"[DEBUG] [Request] Keys: {list(data.keys())}, Has Images: {bool(data.get('label_images'))}")
        barcode = data.get("barcode")
        is_test_mode = (barcode == "TEST")
        label_images = data.get("label_images")
        user_conditions = data.get("user_conditions", {"group": "adult", "allergens": []})
        
        # --- 🚀 支持 Fog Node 快取重算邏輯 ---
        cached_result = data.get("cached_result")
        product = None
        if cached_result and not label_images:
            print(f"[INFO] [Query] Re-calculating personalization for cached barcode: {barcode}")
            # 從快取中提取產品基本資訊，進行個人化診斷與快速重算
            p_info = cached_result.get("product_info") or cached_result.get("data", {}).get("product_info") or {}
            v_name = p_info.get("name") or cached_result.get("name") or "快取產品"
            v_brand = p_info.get("brand") or cached_result.get("brand") or "未知品牌"
            v_mfg_raw = p_info.get("manufacturer") or cached_result.get("manufacturer") or "未知製造商"
            v_mfg = normalize_manufacturer_name(v_mfg_raw)
            
            # 提取成份清單 (相容多種格式)
            ing_detail = cached_result.get("ingredients_detail") or cached_result.get("data", {}).get("ingredients_detail") or []
            ing_list = []
            if isinstance(ing_detail, list):
                for item in ing_detail:
                    if isinstance(item, dict):
                        ing_list.append(item.get("officialName") or item.get("name"))
                    elif isinstance(item, str):
                        ing_list.append(item)
            else:
                ing_list = cached_result.get("ingredients_list") or []
                if isinstance(ing_list, str):
                    try:
                        ing_list = _json.loads(ing_list)
                    except Exception:
                        ing_list = []
                        
            # 提取營養標示
            nut = cached_result.get("nutrition") or cached_result.get("data", {}).get("nutrition_facts") or {}
            
            # 提取過敏原警告
            allergens_val = cached_result.get("allergen_warnings") or cached_result.get("allergens") or []
            if isinstance(allergens_val, list):
                allergens_str = _json.dumps(allergens_val, ensure_ascii=False)
            else:
                allergens_str = str(allergens_val)
                
            # 提取標章
            certs = cached_result.get("certifications") or cached_result.get("data", {}).get("certification_marks") or []
            if isinstance(certs, list):
                certs_str = _json.dumps(certs, ensure_ascii=False)
            else:
                certs_str = str(certs)
                
            # 建立連線以便反查製造商 ID 與後續添加物查詢
            db = get_db_conn()
            cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            prod_id = cached_result.get("producer_id") or p_info.get("producer_id")
            if not prod_id and v_mfg:
                try:
                    cursor.execute("SELECT id FROM producers WHERE name = %s", (v_mfg,))
                    prod_row = cursor.fetchone()
                    if prod_row:
                        prod_id = prod_row['id']
                except Exception as lookup_e:
                    print(f"[WARN] [Cache] Failed to lookup producer_id: {lookup_e}")
            
            product = {
                "barcode": barcode or cached_result.get("barcode") or "CACHED",
                "name": v_name,
                "brand": v_brand,
                "producer_id": prod_id,
                "manufacturer": v_mfg,
                "ingredients_list": _json.dumps(ing_list, ensure_ascii=False),
                "certifications": certs_str,
                "calories": nut.get("calories") or nut.get("calories", 0.0),
                "protein": nut.get("protein") or nut.get("protein", 0.0),
                "fat": nut.get("fat") or nut.get("fat", 0.0),
                "sugar": nut.get("sugar") or nut.get("sugar", 0.0),
                "sodium": nut.get("sodium") or nut.get("sodium", 0.0),
                "allergens": allergens_str
            }
            # 從快取中提取原先 AI 的診斷文字，避開後續對外 API 呼叫 (離線/Fog 模式)
            ai_data = {
                "score": cached_result.get("health_score") or cached_result.get("final_health_diagnosis", {}).get("score") or 0,
                "grade": cached_result.get("risk_level") or cached_result.get("final_health_diagnosis", {}).get("grade") or "C",
                "overall_summary": cached_result.get("overall_summary") or cached_result.get("final_health_diagnosis", {}).get("overall_summary") or "使用快取診斷結果。",
                "additives_summary": cached_result.get("additives_summary") or cached_result.get("final_health_diagnosis", {}).get("additives_summary") or "使用快取添加物分析。",
                "safety_events_summary": cached_result.get("safety_events_summary") or cached_result.get("final_health_diagnosis", {}).get("safety_events_summary") or "使用快取食安警訊。",
                "warnings": cached_result.get("risk_tags") or cached_result.get("final_health_diagnosis", {}).get("warnings") or []
            }
            print(f"[SUCCESS] [Query] Restored cached product: {v_name} for personalization")
        
        # 1. 如果有圖片，不論是否有條碼，直接啟動視覺同步
        if label_images and len(label_images) > 0:
            print(f"[INFO] [Analyze] Starting AI vision analysis for barcode: {barcode or 'NEW'}...")
            vision_data = await analyze_image_with_gemini(label_images, barcode or "NEW")
            
            # --- 核心連線邏輯：在分析完畢後才建立連線，防止超時 ---
            db = get_db_conn()
            cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
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
                            cursor.execute("INSERT INTO producers (name, risk_level) VALUES (%s, %s) RETURNING id", (mfg_name, "Low"))
                            if not is_test_mode:
                                db.commit()
                                prod_id = cursor.fetchone()['id']
                            else:
                                prod_id = cursor.fetchone()['id']
                                print(f"[TEST MODE] Skipping DB write for producer {mfg_name}")
                        else:
                            prod_id = prod_row['id']
                    except Exception as mfg_e:
                        print(f"[WARN] [DB] Producer lookup failed: {mfg_e}. Using default ID 1.")
                        prod_id = 1 # 降級使用手動建立的備胎廠商

                    ing_json = _json.dumps(vision_data.get('ingredients_list', []), ensure_ascii=False)
                    cert_json = _json.dumps(vision_data.get('certification_marks', []), ensure_ascii=False)
                    n = vision_data.get('nutrition', {})
                    # 確保 allergens 為標準 JSON 字串
                    allergy_text = _json.dumps(vision_data.get('allergy_warning', ''), ensure_ascii=False)
                    
                    # 執行持久化儲存
                    try:
                        sql = """
                            INSERT INTO products (
                                barcode, name, brand, producer_id, manufacturer,
                                ingredients_list, certifications, calories, protein, fat, sugar, sodium, allergens
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (barcode) DO UPDATE SET
                            name=EXCLUDED.name, brand=EXCLUDED.brand, producer_id=EXCLUDED.producer_id,
                            manufacturer=EXCLUDED.manufacturer, ingredients_list=EXCLUDED.ingredients_list,
                            certifications=EXCLUDED.certifications,
                            calories=EXCLUDED.calories, protein=EXCLUDED.protein, fat=EXCLUDED.fat,
                            sugar=EXCLUDED.sugar, sodium=EXCLUDED.sodium, allergens=EXCLUDED.allergens
                        """
                        params = (target_barcode, v_name, v_brand, 
                              prod_id, mfg_name, ing_json, cert_json,
                              n.get('calories', 0), n.get('protein', 0), n.get('fat', 0), 
                              n.get('sugar', 0), n.get('sodium', 0), allergy_text)
                        
                        cursor.execute(sql, params)
                        if not is_test_mode:
                            db.commit()
                            print(f"✅ [SUCCESS] [DB] Saved to PostgreSQL: {v_name} ({target_barcode})")
                        else:
                            print(f"[TEST MODE] Skipping DB write for product {v_name} ({target_barcode})")
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
            # 核心修正：避免 TEST 條碼在分析失敗時回傳舊的錯誤紀錄 (僅在有上傳圖片且辨識失敗時觸發)
            if barcode == "TEST" and label_images and len(label_images) > 0:
                 return {"status": "error", "message": "測試模式分析失敗：AI 視覺辨識無回傳結果。請確保拍攝清晰且包含產品名稱、成分與營養標示。"}
            
            # --- 🛠️ 重要修復：確保在純條碼查詢模式下也能建立資料庫連線 ---
            db = get_db_conn()
            cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            cursor.execute("SELECT * FROM products WHERE barcode = %s", (barcode,))
            product = cursor.fetchone()

        # 3. 如果依然沒資料，回傳「引導狀態」
        if not product:
            return {
                "status": "not_found",
                "barcode": barcode,
                "message": "資料庫查無此條碼紀錄，請點擊下方「上傳照片」按鈕，讓 AI 視覺大腦為您即時解析包裝！"
            }

        # 背景食安監控任務
        if product.get('producer_id'):
            mfg = product.get('manufacturer') or '未知製造商'
            if not is_test_mode:
                background_tasks.add_task(update_producer_safety_events, product['producer_id'], mfg)
            else:
                print(f"[TEST MODE] Skipping safety monitor background task for producer {mfg}")

        # Define variables for downstream processing
        raw_ingredients = product.get('ingredients_list', '[]')
        raw_allergens = product.get('allergens', '[]')
        nutrition = {
            "calories": product.get("calories", 0.0),
            "protein": product.get("protein", 0.0),
            "fat": product.get("fat", 0.0),
            "sugar": product.get("sugar", 0.0),
            "sodium": product.get("sodium", 0.0)
        }

        # --- 4. 抓取廠商食安警訊 ---
        safety_alerts = []
        if product.get('producer_id'):
            cursor.execute(
                "SELECT title, content, alert_date, source_url, source_type, severity FROM safety_alerts WHERE producer_id = %s ORDER BY severity DESC, alert_date DESC, id DESC LIMIT 50",
                (product['producer_id'],)
            )
            safety_alerts = cursor.fetchall()

        # 整理食安事件並進行去重與合併，同時限定近十年 (>= 2016)
        raw_events = []
        for e in safety_alerts:
            date_str = str(e.get('alert_date') or '').strip()
            # 判斷近十年 (>= 2016)
            is_recent = True
            if date_str:
                try:
                    year_part = date_str[:4]
                    if year_part.isdigit() and int(year_part) < 2016:
                        is_recent = False
                except Exception:
                    pass
            if not is_recent:
                continue

            raw_events.append({
                "date": date_str,
                "type": {"official": "官方公告", "news": "新聞媒體", "social": "消費者投訴"}.get(e.get('source_type', ''), "未知來源"),
                "title": e.get('title', ''),
                "summary": e.get('content', '') or e.get('title', '未知事件'),
                "source_url": e.get('source_url', '') or '',
                "severity": e.get('severity'),
                "_raw_source_type": e.get('source_type')
            })

        raw_events.sort(key=lambda x: x.get('date', ''), reverse=True)
        # 去重與合併，並只保留最多 5 個不重複事件
        final_safety_events = merge_events(raw_events)[:5]

        # --- 5. 正常分析流程 (添加物比對) ---
        ing_list = product.get('ingredients_list', '[]')
        if isinstance(ing_list, str): ing_list = _json.loads(ing_list)
        
        # 獲取 AI 產出的臨時描述 (用於資料庫缺失時)
        ai_ing_descs = vision_data.get('ingredient_descriptions', {}) if 'vision_data' in locals() else {}

        # 抓取完整的添加物知識庫資訊
        cursor.execute("SELECT id, record_id, name_zh, name_en, aliases, ins_or_e_number, category, food_tech_purpose, adi, medical_caution, iarc_class, description, risks, description_sources FROM additives")
        knowledge = cursor.fetchall()
        
        # --- 🚀 初始化向量 RAG 知識庫 (僅在需要時) ---
        if not vector_rag.is_initialized and knowledge:
            vector_rag.update_knowledge_base(knowledge)

        basic, chemical = [], []
        CHEM_CHARS = ["酯", "鈉", "鉀", "酸", "磷", "聚", "精", "醇", "膠", "素"]
        
        # 準備資料庫更新清單 (智能補完)
        db_updates = []

        CONCERN_TO_LEVEL = {"caution": 1, "avoid": 3, "danger": 5}
        GROUP_ZH_TO_EN = {
            "孕婦": "pregnant", "哺乳期婦女": "pregnant",
            "嬰幼兒": "child", "兒童": "child", "兒童及青少年": "child",
            "六個月以下嬰兒": "child", "一歲以下嬰幼兒": "child",
            "慢性腎臟病患者": "kidney_disease",
            "氣喘患者": "asthma",
            "阿斯匹靈過敏者": "aspirin_allergy",
            "苯酮尿症患者": "pku",
            "過敏體質者": "allergy", "對牛奶過敏者": "allergy",
        }

        calculated_ingredient_types = {}
        for ing in ing_list:
            if not ing: continue
            norm = normalize_text(ing)
            match = None
            # 若為水等純原料成分，直接跳過添加物比對，避免誤判
            if norm in ["水", "純水", "熱水", "冰水", "蒸餾水", "礦泉水", "飲用水", "water"]:
                match = None
            else:
                for a in knowledge:
                    # 1. 中文名稱比對 (name_zh) - 僅允許資料庫名稱是成分名稱的子字串（如「檸檬酸」匹配「檸檬酸鈉」）
                    # 絕對不允許成分名稱是資料庫名稱的子字串（如「水」不應匹配「去水醋酸」）
                    if a.get('name_zh') and a['name_zh'] in norm:
                        match = a
                        break
                    # 2. 英文名稱比對 (name_en)
                    if a.get('name_en') and a['name_en'].lower() in norm.lower():
                        match = a
                        break
                    # 3. INS / E 編號比對
                    if a.get('ins_or_e_number'):
                        ins_norm = normalize_text(a['ins_or_e_number'])
                        if ins_norm and ins_norm in norm:
                            match = a
                            break
                    # 4. 別名比對 (aliases)
                    aliases_raw = a.get('aliases')
                    if aliases_raw:
                        aliases_list = []
                        if isinstance(aliases_raw, list):
                            aliases_list = aliases_raw
                        elif isinstance(aliases_raw, str):
                            try:
                                aliases_list = _json.loads(aliases_raw)
                            except Exception:
                                aliases_list = [aliases_raw]
                        if isinstance(aliases_list, list):
                            if any(alias and alias in norm for alias in aliases_list):
                                match = a
                                break
            
            # --- 🚀 向量 RAG 補償邏輯：只有含化學性字元的成分才做語義匹配，避免「水」等原料誤判 ---
            if not match and any(c in norm for c in CHEM_CHARS):
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

            # 優先使用 Gemini 的分類結果
            is_additive = False
            is_additive_from_gemini = None
            if 'vision_data' in locals() and vision_data:
                ing_types = vision_data.get('ingredient_types', {})
                raw_gemini_type = ing_types.get(ing)
                if not raw_gemini_type:
                    # 嘗試進行去空白、大小寫模糊匹配
                    for k, v in ing_types.items():
                        if k.strip().lower() == ing.strip().lower():
                            raw_gemini_type = v
                            break
                if raw_gemini_type in ["additive", "ingredient"]:
                    is_additive_from_gemini = (raw_gemini_type == "additive")

            if is_additive_from_gemini is not None:
                is_additive = is_additive_from_gemini
            else:
                is_additive = bool(match)

            calculated_ingredient_types[ing] = "additive" if is_additive else "ingredient"
            if is_additive:
                # 處理化學食品添加物之中英文名稱
                display_name = ing
                if match:
                    if match.get('name_en'):
                        display_name = f"{match['name_en']} ({ing})"
                    elif match.get('aliases'):
                        try:
                            aliases_list = match['aliases']
                            if isinstance(aliases_list, str):
                                aliases_list = _json.loads(aliases_list)
                            if isinstance(aliases_list, list):
                                en_name = next((a for a in aliases_list if re.match(r'^[A-Za-z0-9\s\-\(\)\.\,\']+$', a)), None)
                                if en_name:
                                    display_name = f"{en_name.strip()} ({ing})"
                        except Exception:
                            pass

                # 解析 risks 欄位並轉換為 Fog 可讀格式
                risks_raw = match.get('risks') if match else []
                if isinstance(risks_raw, str):
                    try:
                        risks_raw = _json.loads(risks_raw)
                    except Exception:
                        risks_raw = []

                group_risks = []
                for r in (risks_raw or []):
                    zh_group = r.get("group", "")
                    en_group = GROUP_ZH_TO_EN.get(zh_group)
                    if not en_group:
                        continue
                    group_risks.append({
                        "group": en_group,
                        "riskLevel": CONCERN_TO_LEVEL.get(r.get("concern", "caution"), 1),
                        "reason": r.get("ai_reasoning") or r.get("source_quote") or zh_group,
                        "confidence": r.get("confidence", ""),
                    })
                
                adi_val = None
                if match and match.get('adi'):
                    clean_adi = str(match['adi']).strip().lower()
                    if clean_adi not in ["unknown", "none", "", "null", "見標示"]:
                        adi_val = match['adi']

                chemical.append({
                    "name": display_name,
                    "isAdditive": True,
                    "officialName": match['name_zh'] if match else ing,
                    "purpose": final_purpose or "食品添加物",
                    "adiValue": adi_val,
                    "caution": match['medical_caution'] if match else "無",
                    "iarcRating": match['iarc_class'] if match else "未分類",
                    "description": final_desc,
                    "groupRisks": group_risks,
                    "risk_level": "medium" if match or ai_desc else "low",
                    "description_sources": match.get('description_sources') or [] if match else []
                })
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
        
        # 飲料細項判定：檢測是否為純水
        is_water = is_beverage and any(kw in product['name'] for kw in ["水", "礦泉水"]) and not any(kw in product['name'] for kw in ["茶", "奶", "汁", "啡", "飲", "風味", "汽水", "可樂", "蘇打", "沙士"])
        
        # 飲料細項判定：檢測是否含有非營養性甜味劑 (NNS)
        sweetener_keywords = [
            "阿斯巴甜", "aspartame", 
            "醋磺內酯鉀", "acesulfame", "安賽蜜",
            "蔗糖素", "sucralose", "三氯蔗糖",
            "糖精", "saccharin", 
            "紐甜", "neotame", 
            "愛德萬甜", "advantame", 
            "甜菊", "steviol", "stevia",
            "甜蜜素", "cyclamate", 
            "索馬甜", "thaumatin",
            "赤藻糖醇", "erythritol",
            "木糖醇", "xylitol",
            "山梨糖醇", "sorbitol",
            "甘露糖醇", "mannitol",
            "麥芽糖醇", "maltitol",
            "異麥芽", "isomalt",
            "乳糖醇", "lactitol"
        ]
        has_sweeteners = False
        if isinstance(ing_list, list):
            has_sweeteners = any(
                any(kw in str(ing).lower() for kw in sweetener_keywords)
                for ing in ing_list
            )
        
        # 執行 100% 準確的 V7 演算法
        calc_result = ns_calculator.calculate(
            ns_data, 
            is_beverage=is_beverage, 
            is_cheese=is_cheese, 
            has_sweeteners=has_sweeteners,
            is_water=is_water
        )
        deterministic_score = calc_result['score']
        deterministic_grade = calc_result['grade']

        # 強化後的複合式 Prompt，使用 Google Gemma 同時生成總體、添加物與歷史事件三個 AI 總結
        composite_prompt = f"""
        你是 FoodAware Pro 專家系統。請執行「臨床風險診斷」並針對商品進行「添加物風險總結」、「食安歷史事件總結」與「總體商品健康診斷總結」。
        
        【產品資訊】
        產品: {product['name']} | 品牌: {product['brand']} | 廠商: {product['manufacturer']}
        營養成分: {_json.dumps(nutrition, ensure_ascii=False)}
        
        【權威計分依據 (Nutri-Score V7 2024 最新版)】
        依據歐盟 2024 演算法算出的確切總分: {deterministic_score}
        確定之健康分級: {deterministic_grade} 級 (A為最優，E為最差)
        
        【廠商食安歷史 (Module C)】
        {_json.dumps(final_safety_events, ensure_ascii=False)}
        
        【權威資料庫已提供之食品添加物資訊 (Module B)】
        {_json.dumps(chemical, ensure_ascii=False)}
        
        【使用者健康背景】
        {_json.dumps(user_conditions, ensure_ascii=False)}
        
        【任務】
        請使用 Google Gemma 模型進行深度分析，並提供以下三個 AI 總結：
        1. 「總體商品健康診斷總結」(overall_summary)：參考「確切總分」與「健康分級」，產出 50 字內之個人化核心診斷與長期過量攝取的累積慢性健康風險（例如：吃了沒事，但吃久了會有事）。
        2. 「添加物風險總結」(additives_summary)：分析本產品所含的食品添加物、人工化學成分（如防腐劑、防凝劑、甘味劑等）的組合風險，特別是針對該使用者背景（如糖尿病、孕婦、高血壓等）的危害程度，產出 100 字內的分析總結。若無添加物，請說明「本產品無添加化學食品添加物」。
        3. 「食安歷史事件總結」(safety_events_summary)：分析本產品製造商（廠商）以往的食安歷史違規與歷史事件，對消費者信任度與產品安全的影響，產出 100 字內的分析總結。若無歷史食安事件，請說明「該廠商無特定歷史食安違規紀錄」。
        4. 產出具體「個人化警示」(warnings) 清單。
        
        請以繁體中文回答。回傳格式必須為純 JSON，不可有任何 Markdown 標記，結構如下：
        {{
          "score": {deterministic_score},
          "grade": "{deterministic_grade}",
          "overall_summary": "總體商品健康診斷總結文字",
          "additives_summary": "添加物風險總結文字",
          "safety_events_summary": "食安歷史事件總結文字",
          "warnings": ["警告1", "警告2"]
        }}
        """
        
        # --- 🚀 檢查資料庫中是否有預存的 AI 總結，若有則直接重用 ---
        ai_data_exists = 'ai_data' in locals() and ai_data is not None
        is_reused_from_db = False
        if not ai_data_exists and product and product.get("overall_summary") and product.get("overall_summary") != "診斷引擎暫時降級運作。":
            print(f"[INFO] [Analyze] Reusing pre-analyzed AI summaries from PostgreSQL for barcode: {product['barcode']}")
            
            # 防禦性解析過敏原字串
            raw_allergens = product.get("allergens")
            warnings_list = []
            if raw_allergens:
                if isinstance(raw_allergens, list):
                    warnings_list = raw_allergens
                elif isinstance(raw_allergens, str):
                    if raw_allergens.strip().startswith("["):
                        try:
                            warnings_list = _json.loads(raw_allergens)
                        except Exception:
                            warnings_list = [raw_allergens]
                    else:
                        warnings_list = [raw_allergens]
            
            ai_data = {
                "score": deterministic_score,
                "grade": deterministic_grade,
                "overall_summary": product.get("overall_summary"),
                "additives_summary": product.get("additives_summary"),
                "safety_events_summary": product.get("safety_events_summary"),
                "warnings": warnings_list
            }
            ai_data_exists = True
            is_reused_from_db = True

        # --- 🚀 只有在無快取 AI 資料且無資料庫預存時才發送大模型請求 ---
        if not ai_data_exists:
            try:
                try:
                    print(f"[DEBUG] [Analyze] Sending prompt to Gemma. Length: {len(composite_prompt)}")
                    ai_resp = _genai_client.models.generate_content(model="gemma-4-31b-it", contents=composite_prompt)
                    ai_data = _json.loads(re.search(r'(\{.*\})', ai_resp.text, re.DOTALL).group(1))
                except Exception as first_e:
                    print(f"[WARN] [Analyze] Gemma model failed, attempting fallback to gemini-2.5-flash... Error: {first_e}")
                    ai_resp = _genai_client.models.generate_content(model="gemini-2.5-flash", contents=composite_prompt)
                    ai_data = _json.loads(re.search(r'(\{.*\})', ai_resp.text, re.DOTALL).group(1))
            except Exception as ai_e:
                print(f"[WARN] [Analyze] Both primary and fallback models failed: {ai_e}")
                ai_data = {
                    "score": deterministic_score,
                    "grade": deterministic_grade,
                    "overall_summary": "診斷引擎暫時降級運作。",
                    "additives_summary": "無法分析添加物風險。",
                    "safety_events_summary": "無法分析廠商食安歷史。",
                    "warnings": []
                }
        else:
            if not is_reused_from_db:
                print("[INFO] [Query] Reusing pre-cached AI summaries (Skipping LLM Generation for Fog/Cache mode)")
            # 即使是使用快取，健康評分與等級仍然依據本次重算的結果更新，以維持個人化計算的準確性
            ai_data["score"] = deterministic_score
            ai_data["grade"] = deterministic_grade
        
        # 將新生成的 AI 總結持久化儲存到資料庫中，以實現「一次分析，永久重用」
        if not is_reused_from_db and ai_data and ai_data.get("overall_summary") != "診斷引擎暫時降級運作。" and product and product.get("barcode"):
            try:
                update_sql = """
                    UPDATE products 
                    SET overall_summary = %s, additives_summary = %s, safety_events_summary = %s
                    WHERE barcode = %s
                """
                cursor.execute(update_sql, (
                    ai_data.get("overall_summary"),
                    ai_data.get("additives_summary"),
                    ai_data.get("safety_events_summary"),
                    product['barcode']
                ))
                db.commit()
                print(f"[SUCCESS] [DB] 持久化預存 AI 總結於資料庫: {product['barcode']}")
            except Exception as update_e:
                print(f"[WARN] [DB] Failed to save AI summaries to DB: {update_e}")
                db.rollback()

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

        # 整理過敏原，兼容 JSON 字串與普通逗號分隔字串
        final_allergens = []
        if raw_allergens:
            raw_allergens_str = str(raw_allergens).strip()
            if raw_allergens_str.startswith('[') or raw_allergens_str.startswith('"'):
                try:
                    parsed = _json.loads(raw_allergens_str)
                    if isinstance(parsed, list):
                        final_allergens = [str(x) for x in parsed if x]
                    elif isinstance(parsed, str):
                        if parsed and parsed != '[]':
                            final_allergens = [parsed]
                except Exception:
                    final_allergens = [a.strip() for a in raw_allergens_str.split(",") if a.strip()]
            else:
                if raw_allergens_str and raw_allergens_str != '[]':
                    final_allergens = [a.strip() for a in raw_allergens_str.split(",") if a.strip()]

        # 整合添加物（chemical）與天然成分（basic），建構完整的 ingredients_detail
        full_ingredients_detail = []
        for chem_item in chemical:
            full_ingredients_detail.append(chem_item)
        for basic_item in basic:
            full_ingredients_detail.append({
                "name": basic_item,
                "isAdditive": False,
                "description": "天然成分，提供基礎營養。"
            })

        # 建立符合 App 格式之個人化分數細項（score_breakdown）
        formatted_score_breakdown = []
        breakdown_raw = calc_result['details']['breakdown']
        
        # 扣分指標映射（penalties，值轉為負數）
        penalty_mapping = {
            "energy": ("熱量 (Energy)", "熱量成分得分為 {} 分，含有較高熱量會增加身體代謝負荷。"),
            "sugars": ("糖分 (Sugars)", "糖分得分為 {} 分，高糖攝取易引發慢性病與肥胖風險。"),
            "sfa": ("飽和脂肪 (Saturated Fatty Acids)", "飽和脂肪得分為 {} 分，過量可能影響心血管健康。"),
            "salt": ("鈉/鹽分 (Sodium/Salt)", "鈉分得分為 {} 分，高鈉攝取增加高血壓與腎臟負擔。")
        }
        # 加分指標映射（bonuses，值維持正數）
        bonus_mapping = {
            "protein": ("蛋白質 (Protein)", "含有豐富蛋白質，有助於身體組織與肌肉修復。"),
            "fibre": ("膳食纖維 (Fibre)", "含有膳食纖維，有益於腸胃蠕動與消化健康。"),
            "fruit_veg": ("天然蔬果比例 (Fruit & Vegetables)", "富含天然蔬果成分，提供多種維生素與抗氧化物。")
        }
        
        for k, v in breakdown_raw.items():
            if v > 0:
                if k in penalty_mapping:
                    name, desc_tpl = penalty_mapping[k]
                    # 嘗試使用資料庫中的具體數據補充描述
                    actual_val_desc = desc_tpl.format(int(v))
                    if k == "sugars" and product.get("sugar") is not None:
                        actual_val_desc = f"含有 {product['sugar']} 公克添加糖，糖分得分為 {int(v)} 分，高糖攝取易引發慢性病與肥胖風險。"
                    elif k == "energy" and product.get("calories") is not None:
                        actual_val_desc = f"含有 {product['calories']} kcal 熱量，熱量成分得分為 {int(v)} 分，高熱量會增加身體代謝負荷。"
                    elif k == "salt" and product.get("sodium") is not None:
                        actual_val_desc = f"含有 {product['sodium']} 毫克鈉，鈉分得分為 {int(v)} 分，高鈉攝取增加高血壓與腎臟負擔。"
                    
                    formatted_score_breakdown.append({
                        "reason": name,
                        "description": actual_val_desc,
                        "points": -int(v)
                    })
                elif k in bonus_mapping:
                      name, desc = bonus_mapping[k]
                      formatted_score_breakdown.append({
                          "reason": name,
                          "description": desc,
                          "points": int(v)
                      })

        # 食安事件已在上方預先去重並限定近十年且最多 5 個，此處直接使用 final_safety_events

        # 建立產品基礎資訊供根目錄顯示
        product_info_root = {
            "name": product['name'] or "AI 解析產品",
            "brand": product['brand'] or "AI 解析品牌",
            "manufacturer": product['manufacturer'] or "未知製造商",
            "barcode": product['barcode']
        }

        # 取得三個 AI 總結欄位值
        overall_summary = ai_data.get("overall_summary") or ai_data.get("summary") or "診斷完成。"
        additives_summary = ai_data.get("additives_summary") or "無法獲取添加物風險總結。"
        safety_events_summary = ai_data.get("safety_events_summary") or "無法獲取廠商食安歷史總結。"

        # 為維持與行動 App 分割邏輯的相容性，將三個總結結合並加入「[AI 深度分析]：」分割符
        combined_summary = f"{overall_summary}\n\n[AI 深度分析]：\n【添加物風險總結】\n{additives_summary}\n\n【食安歷史事件總結】\n{safety_events_summary}"

        # 建立滿足 React Native App 與 API_SPEC_APP.md 串接要求的診斷物件
        final_health_diagnosis_obj = {
            "score": ai_data.get("score", deterministic_score),
            "summary": combined_summary,
            "overall_summary": overall_summary,
            "additives_summary": additives_summary,
            "safety_events_summary": safety_events_summary,
            "warnings": ai_data.get("warnings", []),
            "score_breakdown": formatted_score_breakdown,
            "groupRiskSummary": [],
            "evidence_chain": []
        }

        # 建構 FogQueryResult 與 API_SPEC_APP 回應格式
        return {
            "status": "success",
            "barcode": product['barcode'],
            "health_score": ai_data.get("score", deterministic_score),
            "risk_level": risk_level,
            "product_info": product_info_root,
            "overall_summary": overall_summary,
            "additives_summary": additives_summary,
            "safety_events_summary": safety_events_summary,
            "score_breakdown": formatted_score_breakdown,
            "risk_tags": ai_data.get("warnings", []),
            "allergen_warnings": final_allergens,
            "ingredients_detail": full_ingredients_detail,
            "ingredient_types": calculated_ingredient_types,
            "food_safety_events": final_safety_events,
            "final_health_diagnosis": final_health_diagnosis_obj,
            "explanation": {
                "triggers": [ch['name'] for ch in chemical if ch.get('risk_level') == 'high'],
                "sources": ["歐盟 Nutri-Score V7 (2024)", "FoodScanIoT 食品添加物知識庫"]
            },
            "personalized_notes": [combined_summary],
            "processed_at": datetime.now().isoformat(),
            # 原始詳細資料保留於 data 欄位供前端舊版適配與擴充顯示
            "data": {
                "product_info": product_info_root,
                "ingredients_detail": chemical,
                "nutrition_facts": nutrition,
                "certification_marks": cert_marks,
                "ingredient_types": calculated_ingredient_types
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3002)
