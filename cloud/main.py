from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import secrets
import os
from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import json as _json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, timezone
import re
import time
from google import genai

import vision_backend
from starlette.concurrency import run_in_threadpool

# 埋點。與 Fog 共用同一份（server/telemetry.py）——兩層各寫一份格式化一定會
# 分岔，族群詞彙、撇號、添加物分母都是這樣來的。
import telemetry as T
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

# 事件去重/合併邏輯已抽出至 module_c/dedup.py(2026-07-24),邏輯逐字未改動。
from module_c.dedup import merge_events


from typing import List, Dict, Any
import base64
import hashlib
import io
from PIL import Image

# 匯入食安監控模組與 Nutri-Score V7 計算機
from module_c.safety_monitor import update_producer_safety_events

# 廠商食安事件功能總開關（2026-07-26 關閉）
#
# 關閉理由：目前呈現的是舊 Tavily 管線寫入 safety_alerts 表的資料，其品質未經
# 驗證——實測可見來源標為「社群討論」且指向 Dcard、以及廠商已公開否認之指控，
# 摘要卻仍以肯定語氣敘述。新的 Grounding 管線（module_c/grounding_discovery.py）
# 雖品質較佳，但同樣因已知限制（來源標題多為網域名、維基語言版本重複計算、
# 部分事件僅以公司總覽頁為佐證）決定暫不上線，見
# 專案管理/Cloud/資料抓取/07-Grounding管線與資料來源查證。
#
# 一個會顯示不精確食安紀錄的功能，成本高於其價值：錯誤指控一家公司的代價，
# 遠高於少一項功能。故在資料品質達到可上線水準前，整條路徑（背景蒐集與前端
# 呈現）一併停用，而非只停其中一半。
#
# 恢復方式：改為 True 即可；若要改接新管線，需將讀取來源從 safety_alerts 表
# 換成 module_c/query.py 的 get_events_payload()。
SAFETY_EVENTS_ENABLED = False
from admin_api import router as admin_router
from module_b.nutriscore_v7 import calculator as ns_calculator
from version import get_commit

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


# ── 延遲埋點 ────────────────────────────────────────────────────────────────
# 用 middleware 而不是在每個 return 前加一行：`/api/analyze` 有七個以上的
# return 點（rejected／not_found／error／快取／正常），漏掉任何一個，
# 最需要量的那幾次（失敗與降階）就剛好沒有數字。
@app.middleware("http")
async def _timing_middleware(request, call_next):
    sw = T.Stopwatch()
    request.state.sw = sw
    request.state.rid = T.safe_request_id(request.headers.get(T.REQUEST_ID_HEADER))
    response = await call_next(request)
    response.headers[T.CLOUD_HEADER] = T.format_timing(**sw.segments())
    if request.state.rid:
        response.headers[T.REQUEST_ID_HEADER] = request.state.rid
    return response


def _mark(request, name, ms):
    """把一段耗時記到本次請求上。沒有 state 時安靜略過——
    埋點壞了不該讓分析失敗。"""
    sw = getattr(getattr(request, "state", None), "sw", None)
    if sw is not None:
        sw._record(name, ms)

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

class SafetyEventRequest(BaseModel):
    producer_id: int
    title: str
    content: str = ""
    alert_date: str = ""
    source_url: str = ""
    source_type: str = "news"
    severity: int = 1

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


# ============================================================
# 廠商食安事件管理 API（管理員專用，需 JWT）
# ============================================================

@app.get("/api/admin/producers")
def admin_list_producers(current_user = Depends(get_current_user)):
    """列出所有廠商，附帶各自的食安事件數量"""
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT p.id, p.name, p.risk_level, p.last_audit_date,
                   COUNT(s.id) AS event_count
            FROM producers p
            LEFT JOIN safety_alerts s ON s.producer_id = p.id
            GROUP BY p.id, p.name, p.risk_level, p.last_audit_date
            ORDER BY event_count DESC, p.name ASC
        """)
        return cur.fetchall()
    finally:
        conn.close()

@app.get("/api/admin/safety-events")
def admin_list_safety_events(producer_id: int = None, current_user = Depends(get_current_user)):
    """列出食安事件，可依 producer_id 篩選，依嚴重度與日期排序"""
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if producer_id is not None:
            cur.execute("""
                SELECT s.id, s.title, s.content, s.alert_date, s.source_url,
                       s.source_type, s.severity, s.producer_id, p.name AS producer_name
                FROM safety_alerts s
                LEFT JOIN producers p ON p.id = s.producer_id
                WHERE s.producer_id = %s
                ORDER BY s.severity DESC, s.alert_date DESC NULLS LAST, s.id DESC
            """, (producer_id,))
        else:
            cur.execute("""
                SELECT s.id, s.title, s.content, s.alert_date, s.source_url,
                       s.source_type, s.severity, s.producer_id, p.name AS producer_name
                FROM safety_alerts s
                LEFT JOIN producers p ON p.id = s.producer_id
                ORDER BY s.severity DESC, s.alert_date DESC NULLS LAST, s.id DESC
            """)
        return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/admin/safety-events")
def admin_create_safety_event(req: SafetyEventRequest, current_user = Depends(get_current_user)):
    """手動新增一筆食安事件"""
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO safety_alerts
                (producer_id, title, content, alert_date, source_url, source_type, severity, keyword_used)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, producer_id, title, content, alert_date, source_url, source_type, severity
        """, (req.producer_id, req.title, req.content, req.alert_date or None,
              req.source_url or None, req.source_type, req.severity, "manual"))
        row = cur.fetchone()
        conn.commit()
        return {"status": "success", "data": row}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.put("/api/admin/safety-events/{event_id}")
def admin_update_safety_event(event_id: int, req: SafetyEventRequest, current_user = Depends(get_current_user)):
    """更新食安事件（標題、內容、日期、來源、嚴重度）"""
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id FROM safety_alerts WHERE id = %s", (event_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Safety event not found")
        cur.execute("""
            UPDATE safety_alerts
            SET producer_id = %s, title = %s, content = %s, alert_date = %s,
                source_url = %s, source_type = %s, severity = %s
            WHERE id = %s
            RETURNING id, producer_id, title, content, alert_date, source_url, source_type, severity
        """, (req.producer_id, req.title, req.content, req.alert_date or None,
              req.source_url or None, req.source_type, req.severity, event_id))
        row = cur.fetchone()
        conn.commit()
        return {"status": "success", "data": row}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.delete("/api/admin/safety-events/{event_id}")
def admin_delete_safety_event(event_id: int, current_user = Depends(get_current_user)):
    """刪除一筆食安事件"""
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM safety_alerts WHERE id = %s", (event_id,))
        deleted = cur.rowcount
        conn.commit()
        if deleted == 0:
            raise HTTPException(status_code=404, detail="Safety event not found")
        return {"status": "success", "deleted": deleted}
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

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

# 掛載管理介面靜態路徑
ADMIN_UI_DIR = os.path.join(os.path.dirname(__file__), "admin_ui")
if not os.path.exists(ADMIN_UI_DIR):
    os.makedirs(ADMIN_UI_DIR)
app.mount("/admin", StaticFiles(directory=ADMIN_UI_DIR), name="admin")

# ── /api/analyze 的共享密鑰 ──────────────────────────────────────────────────
# 這個端點會呼叫計費的 Gemini API，而 Cloudflare Tunnel 給的是**公開 HTTPS 網址**
# （Tailscale 是私有網路，只有 tailnet 成員連得到，所以先前沒有這道）。
#
# ⚠ **未設定時放行**，理由是不能讓既有的 Tailscale 佈署一升級就全斷。
#    但「環境變數沒設就靜默走寬鬆路徑」正是本專案踩過九次的坑，所以：
#      · 啟動時印警告
#      · `/health` 明確回報 `auth: "disabled"`
#    切到 Tunnel 之前務必設定——見 `專案管理/工程待辦.md` A4。
#
# ⚠ 這**不是**給 App 直連用的。把密鑰嵌進手機 App 等於公開它；
#    正式路徑是 App → Fog → Cloud，只有 Fog 需要持有密鑰。
API_SHARED_SECRET = os.getenv("API_SHARED_SECRET", "").strip()
API_KEY_HEADER = "X-API-Key"
# 測試者身分標頭。不是憑證——只標記「這批上傳是誰提供的」，
# 讓 scan_uploads 事後分得出哪些樣本來自測試，哪些來自一般使用。
TESTER_ID_HEADER = "X-Tester-Id"
if not API_SHARED_SECRET:
    print("[WARNING] 未設定 API_SHARED_SECRET，/api/analyze 未受保護。"
          "公開端點（Cloudflare Tunnel）上線前必須設定。")


def require_api_key(request: Request):
    """驗證共享密鑰。未設定密鑰時放行（見上方說明）。"""
    if not API_SHARED_SECRET:
        return
    got = request.headers.get(API_KEY_HEADER) or ""
    # compare_digest：避免用字串比較洩漏前綴資訊
    if not secrets.compare_digest(got, API_SHARED_SECRET):
        raise HTTPException(status_code=401, detail="缺少或錯誤的 API 金鑰")


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


@app.get("/health")
def health():
    """健康檢查。供部署腳本與 Fog 的深度檢查（GET /health?deep=1）使用。

    只回報「這一層能不能服務」與必要的組態狀態，不回報任何金鑰內容——
    gemini_key_configured 只說有沒有設定，不透露值。

    資料庫連不上時 status 降為 degraded：Cloud 沒有資料庫就什麼都查不了，
    這與 Fog 的情況不同（Fog 沒有 Cloud 仍能以快取服務）。
    """
    payload = {
        "status": "ok",
        "layer": "cloud",
        "commit": get_commit(),
        "database": "unknown",
        "gemini_key_configured": bool(os.getenv("GEMINI_API_KEY")),
        # 現在實際在用哪個讀取器。看 log 才知道會漏掉重啟後的切換。
        "vision_backend": vision_backend.backend_name(),
        "safety_events_enabled": SAFETY_EVENTS_ENABLED,
        # ⚠ `/api/analyze` 有沒有受保護。未設 API_SHARED_SECRET 時放行，
        #    那在 Tailscale（私有網路）下可以接受，在 Cloudflare Tunnel
        #    （公開 HTTPS）下不行——這個欄位就是要讓它**看得見**，
        #    而不是變成又一個「沒設就靜默走寬鬆路徑」。
        "analyze_auth": "enabled" if API_SHARED_SECRET else "disabled",
    }

    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        payload["database"] = "ok"
    except Exception as e:
        payload["database"] = f"error: {type(e).__name__}"
        payload["status"] = "degraded"

    return payload

# normalize_text 已抽出至 module_a/ingredient_matching.py(2026-07-24),邏輯逐字未改動。
from module_a.ingredient_matching import normalize_text, match_ingredients
from module_d.response_builder import build_response
from module_b.scoring import InsufficientNutritionData, calculate_nutriscore
from module_b.daily_reference import get_daily_reference_payload
from module_d.diagnosis import generate_ai_diagnosis

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
        # 注意:此處僅解碼供辨識,不存檔。圖片留待通過相關性閘門後才存(見 _save_scan_images)。
        images_to_process = []
        for i, b64 in enumerate(base64_images):
            if "base64," in b64:
                b64 = b64.split("base64,")[1]
            img_data = base64.b64decode(b64)
            img = Image.open(io.BytesIO(img_data))
            images_to_process.append(img)

        prompt = """解析這些食品包裝照片，回傳繁體中文 JSON：
        {
          "is_food_label": true 或 false（這張照片是否為食品包裝或其成分／營養標示）,
          "reject_reason": "若非食品標籤，簡述原因（例如：非食品照片／無成分標示／過於模糊），否則留空",
          "name": "產品完整名稱 (若不明確請結合品牌與產品類型描述，例如：XX牌草莓夾心餅乾)",
          "brand": "品牌", 
          "ingredients_raw": "成分欄位的完整原文，逐字照抄、保留標點與括號，不要拆解或改寫",
          "ingredients_list": ["成分1", "成分2"], 
          "nutrition": {
            "calories": 數字或 null,
            "protein": 數字或 null,
            "fat": 數字或 null,
            "saturated_fat": 數字或 null,
            "trans_fat": 數字或 null,
            "carbohydrates": 數字或 null,
            "sugar": 數字或 null,
            "fiber": 數字或 null,
            "sodium": 數字或 null
          },
          "serving_size": 每一份量的公克或毫升數字（如「每份30公克」填 30），無標示則 null,
          "servings_per_container": 每包裝含幾份的數字（如「本包裝含2份」填 2），無標示則 null,
          "serving_description": "每一份量欄位的原始文字（如「每份30公克(約10片)」），無則 null",
          "nutrition_raw": "營養標示整個表格的逐字原文（含每份/每100g兩欄的所有列與數值），照抄不整理",
          "nutrition_other": {"營養素名稱(照標示，如 鈣/鐵/維生素C/膽固醇/單元不飽和脂肪)": {"per_100": 數字或 null, "per_serving": 數字或 null}},
          "nutrition_per_serving": {
            "calories": 數字或 null,
            "protein": 數字或 null,
            "fat": 數字或 null,
            "saturated_fat": 數字或 null,
            "trans_fat": 數字或 null,
            "carbohydrates": 數字或 null,
            "sugar": 數字或 null,
            "fiber": 數字或 null,
            "sodium": 數字或 null
          }, 
          "manufacturer": "製造商全名 (請參考包裝標示)", 
          "allergy_warning": "過敏原注意事項文字",
          "certification_marks": ["標章名稱", "例如: TQF, CAS, TAP, 健康食品, 有機農產品"]
        }

        【相關性判斷 - 最優先】
        請先判斷照片是否為食品包裝，或其成分／營養標示。
        若不是（例如人物、自拍、風景、動物、無關物品，或完全看不到成分與營養標示），
        請將 is_food_label 設為 false、reject_reason 填寫原因，其餘欄位一律留空或空陣列。
        切勿為非食品照片捏造成分、營養數值或產品名稱。
        若確為食品標籤，is_food_label 設為 true 並正常解析。

        【成分原文保留規則】
        ingredients_raw 請逐字照抄包裝上「成分」欄位的整段文字，包含括號內的補充
        說明（如「麥芽糊精(玉米來源)」）、連接標點與排列順序，不要拆解、不要改寫、
        不要省略。ingredients_list 則是把同一段文字拆成個別成分名稱後的結果。
        兩者必須來自同一段標示；若成分欄位無法辨識，ingredients_raw 留空字串。

        【營養標示讀取規則 - 極重要】
        台灣營養標示通常同時或擇一列出「每份」與「每100公克/毫升」兩欄。請**分別**填入：
          nutrition            → 「每100公克/毫升」那一欄的值。
          nutrition_per_serving → 「每份（每一份量）」那一欄的值。
        **兩欄都照抄標示上的原始數字，不要自行換算、不要互相推導。** 標示上只有其中
        一欄時，另一欄整組填 null（後端會處理，不需你換算——換算易出錯）。
        serving_size 填「每一份量」的公克/毫升數（如「每份 30 公克」→ 30）；
        servings_per_container 填「本包裝含 N 份」的 N。這兩者若無標示則填 null。
        calories = 大卡(kcal)數字，例如標示 39.2大卡 → 填 39.2。
        **標示上沒有的欄位請填 null，不要填 0，也不要自行推估。**
        0 代表「標示為零」，null 代表「標示上沒有這一項」，兩者意義完全不同。
        依我國營養標示規定，熱量、蛋白質、脂肪、飽和脂肪、反式脂肪、碳水化合物、
        糖、鈉為強制標示項目，通常都找得到；膳食纖維為自願標示，可能沒有。
        **上列九項以外，只要出現在營養標示表格內的任何營養素（如鈣、鐵、鉀、
        維生素、膽固醇、單元／多元不飽和脂肪、糖醇、乳糖等），一律收進 nutrition_other**，
        鍵用標示上的名稱，值填每100g與每份兩欄的數字。
        另外 nutrition_raw 請把整個營養標示表格逐字照抄（含標題列、每份/每100g欄與所有數值），
        作為完整存底——結構化欄位若漏抓，仍可由原文回溯。

        【標章辨識任務】
        請主動辨識所有標章 logo，尤其是：
        TQF（優良食品驗證，盾牌形金色logo）、CAS（優良農產品）、TAP（產銷履歷）、
        健康食品（小綠人）、有機農產品。若同一款標章出現多個編號，仍只回傳一個 "TQF"。

        JSON ONLY. No markdown. 數值皆為數字。成分只需回傳 ingredients_raw 與 ingredients_list；成分是否為添加物由後端依食藥署正面表列判定，不需在此分類。"""
        
        content = [prompt] + images_to_process
        response = _genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=content,
            config={"thinking_config": {"thinking_budget": 0}}
        )
        
        # [DEBUG] 記錄原始回傳，幫助診斷失敗原因
        try:
            print(f"[DEBUG] Gemini Raw Response: {response.text[:200]}...")
        except Exception:
            # 裸 except 會連 KeyboardInterrupt/SystemExit 一起吞掉，
            # 而這裡只是想擋 response.text 在被 safety filter 擋下時的取值失敗。
            print("[DEBUG] Gemini Raw Response: <Blocked or Empty>")

        match = re.search(r'(\{.*\})', response.text, re.DOTALL)
        if match:
            return _json.loads(match.group(1))
        return None
    except Exception as e: 
        print(f"[ERROR] Gemini Vision Error: {e}")
        return None

def _save_scan_images(base64_images, barcode):
    """將上傳圖片存至 uploads/，回傳每張的 path、sha256 與位元組數。

    2026-09-21 起**不論有沒有通過相關性閘門都呼叫**。先前只在通過後才存，
    於是被判定為「非食品標籤」「無實質內容」的照片全部丟掉——而那正是最該
    收集的一批：能通過閘門的照片代表管線已經讀得動它了，讀不動的才有改進空間。

    佔硬碟的疑慮改用別的方式處理（uploads/ 已掛成 bind mount，容量看得到也
    清得掉），不該用「丟掉證據」來換。
    """
    saved = []
    for i, b64 in enumerate(base64_images or []):
        try:
            if "base64," in b64:
                b64 = b64.split("base64,")[1]
            raw = base64.b64decode(b64)
            # sha256 取**解碼後的原始位元組**，與 manifest_tool.py 同一套識別方式；
            # 取 base64 字串的雜湊會因為換行或 padding 差異而對不起來。
            digest = hashlib.sha256(raw).hexdigest()
            fn = f"scan_{int(time.time())}_{barcode}_{i}.jpg"
            with open(os.path.join(UPLOADS_DIR, fn), "wb") as f:
                f.write(raw)
            saved.append({
                "path": f"/uploads/{fn}",
                "sha256": digest,
                "size_bytes": len(raw),
                "image_index": i,
            })
            print(f"[INFO] [Storage] Image saved to: /uploads/{fn} ({len(raw)} bytes)")
        except Exception as e:
            print(f"[WARN] [Storage] 圖片存檔失敗: {e}")
    return saved


def _reader_id(vision_data) -> str:
    """實際做辨識的那一個的識別碼。

    `vision_backend.backend_name()` 只會回 "vlcrop"／"gemini"，而 "vlcrop" 的
    意思是「走 reader 這條路」——8180 上可能是主線的 vlcrop_hy，也可能是競賽版的
    easyocr_union_gemini（同時只有一個在跑，見 feat/adi-multimodal-compliance
    的 tools/switch_reader.ps1）。只記 "vlcrop" 的話，兩個後端產生的樣本在
    scan_uploads 裡分不出來，換後端前後的比較就做不了。

    reader 報的是 "vlcrop_hy (PP-OCR→HunyuanOCR→Qwen)" 這種「識別碼＋人看的說明」，
    取括號前那段即可：scan_uploads.vision_backend 是 String(32)，完整字串 34 字
    塞不進去，而括號裡的說明本來就不該當識別碼用。
    """
    reader = ((vision_data or {}).get("_meta") or {}).get("reader")
    name = str(reader).split(" (")[0].strip() if reader else vision_backend.backend_name()
    return name[:32]


def _record_scan_uploads(cursor, saved, *, tester_id, barcode, request_id,
                         vision_backend, gate_passed, gate_reason, recognized):
    """把存下來的圖片寫進 scan_uploads。

    **絕不讓這裡的失敗影響分析結果。** 這是資料蒐集，不是使用者要的東西；
    寫不進去就記一行警告，照樣把分析結果回給使用者。

    barcode 只在使用者真的掃了條碼時才填。`IMG_<timestamp>` 那種合成值不寫進
    來——它是 products 的主鍵需要才造的，寫進這裡會讓「有沒有條碼」查不出來。
    """
    if not saved:
        return
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    real_barcode = barcode if barcode and barcode not in ("NEW", "TEST", "") else None
    payload = _json.dumps(recognized, ensure_ascii=False) if recognized else None
    for item in saved:
        try:
            cursor.execute(
                """
                INSERT INTO scan_uploads
                    (created_at, tester_id, barcode, image_index, stored_path,
                     sha256, size_bytes, request_id, vision_backend,
                     gate_passed, gate_reason, recognized)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (created_at, tester_id, real_barcode, item["image_index"],
                 item["path"], item["sha256"], item["size_bytes"], request_id,
                 vision_backend, gate_passed, gate_reason, payload),
            )
        except Exception as e:
            print(f"[WARN] [ScanUpload] 紀錄寫入失敗（不影響分析結果）: {e}")


def _is_valid_food_scan(vision_data) -> tuple:
    """相關性／品質閘門:判斷視覺辨識結果是否值得寫入共享資料庫。
    回傳 (是否通過, 原因)。非食品標籤或無實質內容者一律不寫入,避免污染。"""
    if not vision_data or not isinstance(vision_data, dict):
        return False, "無法辨識內容"
    # 第一關:視覺模型相關性旗標(明確判定為非食品標籤時直接擋)
    if vision_data.get("is_food_label") is False:
        return False, vision_data.get("reject_reason") or "非食品標籤"

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    # 第二關:確定性訊號檢查 —— 至少要有成分、營養數值、或有意義的品名其一
    ingredients = vision_data.get("ingredients_list") or []
    has_ingredients = isinstance(ingredients, list) and any(str(x).strip() for x in ingredients)
    nutrition = vision_data.get("nutrition") or {}
    has_nutrition = any(_num(nutrition.get(k)) > 0 for k in ("calories", "protein", "fat", "sugar", "sodium"))
    name = (vision_data.get("name") or "").strip()
    has_name = bool(name) and name not in ("待確認產品", "未知品牌", "未知產品")

    if has_ingredients or has_nutrition or has_name:
        return True, ""
    return False, "照片中未偵測到成分或營養標示"


# ⚠ **三條路由共用同一個處理函式，所以金鑰必須掛在每一條上。**
#
# 2026-09-14 發現：A4 的共享密鑰原本只掛在 /api/analyze，而 /query 與
# /analyze 是同一個 handler 的別名——任何人打那兩條就完全繞過驗證，
# 而它們會呼叫付費的 reader 與 Gemini。A4 的整個理由（Cloudflare Tunnel
# 把端點變成公開 HTTPS）在那兩條上等於不存在。
#
# 保留別名是為了相容舊版 App（歷史上打過 /query）。要移除得先確認沒有
# 任何在外的版本還在用——現行 App 與 Fog 都打 /api/analyze。
@app.post("/query", dependencies=[Depends(require_api_key)])
@app.post("/analyze", dependencies=[Depends(require_api_key)])
@app.post("/api/analyze", dependencies=[Depends(require_api_key)])
async def analyze(request: Request, background_tasks: BackgroundTasks):
    try:
        # 1. 取得原始請求內容供簽章驗證
        body_bytes = await request.body()

        data = _json.loads(body_bytes)
        print(f"[DEBUG] [Request] Keys: {list(data.keys())}, Has Images: {bool(data.get('label_images'))}")
        barcode = data.get("barcode")
        is_test_mode = (barcode == "TEST")
        # 測試者模式。用標頭而不是條碼值：條碼是「掃到了什麼」，身分是「誰在傳」，
        # 兩件事擠進同一個欄位就沒辦法同時表達「我是測試者」和「這罐的條碼是 X」。
        #
        # ⚠ 與 is_test_mode 的差別：TEST 條碼的用意是「不要污染資料庫」，測試者
        #   模式的用意是「把樣本留下來」。兩者都跳過 products／producers 的寫入，
        #   但測試者的上傳一定會進 scan_uploads，TEST 條碼的不保證。
        #
        # 不驗證這個值——它不是憑證，只是來源標籤。要防冒用得靠 API 金鑰，
        # 那一層已經在 require_api_key 擋過了。
        tester_id = (request.headers.get(TESTER_ID_HEADER) or "").strip()[:64] or None
        if tester_id:
            print(f"[INFO] [Tester] 測試者上傳：{tester_id}（不寫入 products／producers）")
        # 共享資料庫的寫入閘。測試者與 TEST 條碼都不寫——辨識結果會隨讀取器版本
        # 變動，寫進 products 之後就分不出哪些是實驗殘留。
        skip_shared_writes = is_test_mode or bool(tester_id)
        label_images = data.get("label_images")
        user_conditions = data.get("user_conditions", {"group": "adult", "allergens": []})
        
        # --- 🚀 支持 Fog Node 快取重算邏輯 ---
        cached_result = data.get("cached_result")
        product = None
        vision_data = None  # 明確初始化,取代原本以 locals() 判斷是否存在的隱式狀態
        ai_data = None  # 明確初始化,取代原本以 locals() 判斷是否存在的隱式狀態
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
                # 成分原文要一起帶進來(2026-07-30):比對已改以原文為來源,這條快取
                # 路徑不帶的話會退回模型整理的清單,複合食品內部的添加物又會消失。
                "ingredients_raw": cached_result.get("ingredients_raw"),
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
                "warnings": cached_result.get("risk_tags") or cached_result.get("final_health_diagnosis", {}).get("warnings") or []
            }
            print(f"[SUCCESS] [Query] Restored cached product: {v_name} for personalization")
        
        # 1. 如果有圖片，不論是否有條碼，直接啟動視覺同步
        if label_images and len(label_images) > 0:
            print(f"[INFO] [Analyze] Starting AI vision analysis for barcode: {barcode or 'NEW'}...")
            _t_vision_start = time.time()
            # 2026-09-13：辨識後端可切換（VISION_BACKEND，預設 vlcrop）。
            # vlcrop 走主機上的 reader 服務，Gemini 留作 ABBA 對照與手動退路。
            # 失敗刻意不回退 Gemini——理由見 vision_backend.py 檔頭。
            vision_data = await vision_backend.analyze(
                label_images, barcode or "NEW", analyze_image_with_gemini)
            _t_vision_end = time.time()
            print(f"[PERF] Vision[{vision_backend.backend_name()}]: {(_t_vision_end - _t_vision_start)*1000:.0f}ms")
            _mark(request, "vision", (_t_vision_end - _t_vision_start) * 1000)
            # reader 回的逐段秒數一起帶上來：沒有它就只知道「視覺花了 10 秒」，
            # 不知道是 PP-OCR、HunyuanOCR 還是營養表在花。
            for _k, _v in ((vision_data or {}).get("_meta", {})
                           .get("stage_secs", {}) or {}).items():
                _mark(request, "r_" + _k, float(_v) * 1000)
            # 主機狀態一起帶上來。2026-09-14 為了「同一張圖為何一次 6.8 秒、
            # 一次 11.6 秒」查了半小時，最後排除不掉也再現不了——缺的不是
            # 「哪一段慢」（那已經有了），是**當下主機是什麼狀況**。
            # ⚠ 這幾個不是耗時，單位是 MiB 與 %。`_mark` 的欄位名以 h_ 起頭
            #   就是為了跟毫秒的欄位分開，讀 header 的人不會誤當成時間。
            _host = (vision_data or {}).get("_meta", {}).get("host_before") or {}
            for _k in ("vram_free_mb", "gpu_util_pct", "cpu_pct"):
                if _host.get(_k) is not None:
                    _mark(request, "h_" + _k, float(_host[_k]))
            
            # --- 核心連線邏輯：在分析完畢後才建立連線，防止超時 ---
            db = get_db_conn()
            cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # --- 相關性／品質閘門：非食品標籤或無實質內容者，不寫入共享資料庫 ---
            scan_ok, scan_reason = _is_valid_food_scan(vision_data)

            target_barcode = barcode or f"IMG_{int(time.time())}"

            # --- 樣本蒐集：**只收測試者的照片** ---------------------------------
            # 一般使用者的照片一張都不留。這與本專案既有的界線一致：個人化只在
            # App 端本地跑、健康背景不送往後端、Fog 還有 mask_sensitive_data()
            # 作為深度防禦。在那個脈絡下「所有人的照片一律留存」是不相稱的。
            #
            # 測試者是自己人，知道自己在提供資料，所以收；而蒐集的目的（知道管線
            # 讀不動什麼）靠測試者的樣本就達得到，不需要動到一般使用者。
            #
            # ⚠ 2026-09-21 這裡一度是無條件執行。改成有條件之後，「沒收到樣本」
            #   最可能的原因是 App 沒送 X-Tester-Id（.env 的 EXPO_PUBLIC_TESTER_ID
            #   沒填，或走 Fog 時哪一層沒轉發），而不是後端壞了。
            #
            # 存圖與建檔刻意排在相關性閘門**之前**：閘門沒過的照片正是最該收的
            # 一批——能通過閘門的代表管線已經讀得動它了。
            if tester_id:
                _saved = _save_scan_images(label_images, target_barcode)
                _record_scan_uploads(
                    cursor, _saved,
                    tester_id=tester_id,
                    barcode=barcode,
                    request_id=T.safe_request_id(request.headers.get(T.REQUEST_ID_HEADER)),
                    # 記**實際做辨識的那一個**，不是 VISION_BACKEND。
                    # backend_name() 只會回 "vlcrop"／"gemini"，而 "vlcrop" 的意思
                    # 是「走 reader 這條路」——8180 上可能是主線的 vlcrop_hy，也可能
                    # 是競賽版的 easyocr_union_gemini（同時只有一個在跑，見該分支的
                    # tools/switch_reader.ps1）。只記 "vlcrop" 的話，兩個後端產生的
                    # 樣本在資料庫裡分不出來，換後端前後的比較就做不了。
                    vision_backend=_reader_id(vision_data),
                    gate_passed=scan_ok,
                    gate_reason=None if scan_ok else scan_reason,
                    recognized=vision_data,
                )
                # 紀錄自己 commit：後面的流程可能因為閘門未過而提早 return，
                # 那時這些 INSERT 還在交易裡，會跟著被丟掉。
                try:
                    db.commit()
                except Exception as _e:
                    print(f"[WARN] [ScanUpload] commit 失敗（不影響分析結果）: {_e}")

            if not scan_ok:
                print(f"[GATE] 上傳圖片未通過相關性/品質閘門：{scan_reason}，不寫入 DB")
                if not barcode or barcode in ("NEW", "TEST", ""):
                    return {
                        "status": "rejected",
                        "message": f"無法從照片辨識出食品成分標示（{scan_reason}）。請對準產品的成分表與營養標示，重新拍攝清晰照片。"
                    }
                # 有真實條碼可回退：略過視覺寫入，改走條碼查詢
                vision_data = None

            if vision_data is not None:
                try:
                    # 即使 vision_data 是空字典 {}，也要繼續處理

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
                            if not skip_shared_writes:
                                db.commit()
                                prod_id = cursor.fetchone()['id']
                            else:
                                prod_id = cursor.fetchone()['id']
                                print(f"[SKIP-SHARED] 不寫入 producers：{mfg_name}"
                                      f"（tester={tester_id or '-'}, test_barcode={is_test_mode}）")
                        else:
                            prod_id = prod_row['id']
                    except Exception as mfg_e:
                        print(f"[WARN] [DB] Producer lookup failed: {mfg_e}. Using default ID 1.")
                        prod_id = 1 # 降級使用手動建立的備胎廠商

                    ing_json = _json.dumps(vision_data.get('ingredients_list', []), ensure_ascii=False)
                    # 標示原文(2026-07-26)。拆解後的名稱陣列會遺失括號內的補充說明
                    # (如「麥芽糊精(玉米來源)」拆完只剩「麥芽糊精」),原文留存才可回溯。
                    ing_raw = (vision_data.get('ingredients_raw') or '').strip() or None
                    cert_json = _json.dumps(vision_data.get('certification_marks', []), ensure_ascii=False)
                    n = vision_data.get('nutrition', {})
                    # 營養標示的完整存底（2026-07-26）：核心九項存於 products 各欄，
                    # 以下打包「每份核心值、九項以外的其他營養素、整塊原文、份量描述」，
                    # 一併存入 other_nutrition。設計同 ingredients_raw：結構化供計算，
                    # 原文供回溯，避免因固定欄位只列九項而漏記標示上的其他營養素。
                    _other_nut = {
                        "per_serving": vision_data.get('nutrition_per_serving') or None,
                        "other": vision_data.get('nutrition_other') or None,
                        "raw": (vision_data.get('nutrition_raw') or '').strip() or None,
                        "serving_desc": (vision_data.get('serving_description') or '').strip() or None,
                    }
                    other_nut_json = (_json.dumps(_other_nut, ensure_ascii=False)
                                      if any(_other_nut.values()) else None)
                    # 確保 allergens 為標準 JSON 字串
                    allergy_text = _json.dumps(vision_data.get('allergy_warning', ''), ensure_ascii=False)
                    
                    # 執行持久化儲存
                    try:
                        sql = """
                            INSERT INTO products (
                                barcode, name, brand, producer_id, manufacturer,
                                ingredients_list, ingredients_raw, certifications,
                                calories, protein, fat, saturated_fat, carbohydrates,
                                sugar, fiber, sodium, allergens,
                                serving_size, servings_per_container, other_nutrition
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (barcode) DO UPDATE SET
                            name=EXCLUDED.name, brand=EXCLUDED.brand, producer_id=EXCLUDED.producer_id,
                            manufacturer=EXCLUDED.manufacturer, ingredients_list=EXCLUDED.ingredients_list,
                            -- 原文僅在本次確實辨識到時才覆蓋，避免既有原文被空值洗掉
                            ingredients_raw=COALESCE(EXCLUDED.ingredients_raw, products.ingredients_raw),
                            certifications=EXCLUDED.certifications,
                            calories=EXCLUDED.calories, protein=EXCLUDED.protein, fat=EXCLUDED.fat,
                            -- 新增欄位以 COALESCE 保護：本次辨識不到時不覆蓋既有值
                            saturated_fat=COALESCE(EXCLUDED.saturated_fat, products.saturated_fat),
                            carbohydrates=COALESCE(EXCLUDED.carbohydrates, products.carbohydrates),
                            fiber=COALESCE(EXCLUDED.fiber, products.fiber),
                            sugar=EXCLUDED.sugar, sodium=EXCLUDED.sodium, allergens=EXCLUDED.allergens,
                            serving_size=COALESCE(EXCLUDED.serving_size, products.serving_size),
                            servings_per_container=COALESCE(EXCLUDED.servings_per_container, products.servings_per_container),
                            other_nutrition=COALESCE(EXCLUDED.other_nutrition, products.other_nutrition)
                        """
                        params = (target_barcode, v_name, v_brand, 
                              prod_id, mfg_name, ing_json, ing_raw, cert_json,
                              n.get('calories', 0), n.get('protein', 0), n.get('fat', 0),
                              # 缺漏一律存 None 而非 0：0 代表「標示為零」，
                              # None 代表「標示上沒有」，計分時的處理必須不同。
                              n.get('saturated_fat'), n.get('carbohydrates'),
                              n.get('sugar', 0), n.get('fiber'), n.get('sodium', 0), allergy_text,
                              # 每份份量、份數，以及每份營養（存於 other_nutrition，
                              # 供每日參考值改用「每份」為基準；缺則 None，不換算）
                              vision_data.get('serving_size'),
                              vision_data.get('servings_per_container'),
                              other_nut_json)
                        
                        cursor.execute(sql, params)
                        if not skip_shared_writes:
                            db.commit()
                            print(f"✅ [SUCCESS] [DB] Saved to PostgreSQL: {v_name} ({target_barcode})")
                        else:
                            print(f"[SKIP-SHARED] 不寫入 products：{v_name}（{target_barcode}）"
                                  f"（tester={tester_id or '-'}, test_barcode={is_test_mode}）")
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
                        "ingredients_raw": ing_raw,
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

        # 背景食安監控任務（2026-07-26 停用，見下方 SAFETY_EVENTS_ENABLED 說明）。
        # 舊的 Tavily 監控每次掃描都會觸發、往 safety_alerts 表寫入並消耗 API，
        # 但其產出已決定不對使用者呈現，繼續執行只是持續累積不會被使用的資料。
        if SAFETY_EVENTS_ENABLED and product.get('producer_id'):
            mfg = product.get('manufacturer') or '未知製造商'
            if not skip_shared_writes:
                background_tasks.add_task(update_producer_safety_events, product['producer_id'], mfg)
            else:
                print(f"[SKIP-SHARED] 不觸發食安監控背景任務：{mfg}"
                      f"（tester={tester_id or '-'}, test_barcode={is_test_mode}）")

        # Define variables for downstream processing
        raw_ingredients = product.get('ingredients_list', '[]')
        raw_allergens = product.get('allergens', '[]')
        nutrition = {
            "calories": product.get("calories", 0.0),
            "protein": product.get("protein", 0.0),
            "fat": product.get("fat", 0.0),
            # 2026-09-13 新增。寫入端一直有存（上面的 UPSERT 有 saturated_fat 欄），
            # 讀取端漏了它，所以高血脂的閾值示警做不了——CLAUDE.md 記過這個缺口。
            # ⚠ 這一欄**很常是 None**（自願標示，不是法定必標），下游務必分辨
            #   「沒有標示」與「含量為零」。不可在這裡補 0。
            "saturated_fat": product.get("saturated_fat", 0.0),
            "sugar": product.get("sugar", 0.0),
            "sodium": product.get("sodium", 0.0)
        }

        # --- 4. 抓取廠商食安警訊 ---
        safety_alerts = []
        if SAFETY_EVENTS_ENABLED and product.get('producer_id'):
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
        merged = merge_events(raw_events)
        # 重大事件（severity=3）全部保留，其餘最多 5 筆，皆以近期優先
        critical = [e for e in merged if e.get('severity') == 3]
        others   = [e for e in merged if e.get('severity') != 3][:5]
        final_safety_events = critical + others

        # --- 5. 正常分析流程 (添加物比對) ---
        # 成分比對邏輯已抽出至 module_a/ingredient_matching.py(2026-07-24)。
        # 2026-07-30:改以標示原文為比對來源。本次辨識的原文優先(vision_data),
        # 沒有(掃條碼命中既有商品)則用資料庫裡存的那份;兩者皆無才退回模型清單。
        _ing_raw = (vision_data or {}).get('ingredients_raw') or product.get('ingredients_raw')
        _match_result = match_ingredients(
            product.get('ingredients_list', '[]'), vision_data, cursor, vector_rag,
            ingredients_raw=_ing_raw
        )
        ing_list = _match_result["ing_list"]
        basic = _match_result["basic"]
        basic_detail = _match_result["basic_detail"]
        chemical = _match_result["chemical"]
        calculated_ingredient_types = _match_result["calculated_ingredient_types"]

        # --- 🚀 核心：Nutri-Score V7 硬代碼精準計分 (對照 PDF V7 標準) ---
        # 計分邏輯已抽出至 module_b/scoring.py(2026-07-24),邏輯逐字未改動。
        _ns_result = calculate_nutriscore(product, ing_list)
        calc_result = _ns_result["calc_result"]
        deterministic_score = _ns_result["deterministic_score"]
        deterministic_grade = _ns_result["deterministic_grade"]
        # 哪幾項計分輸入為推估（標示未提供）。呈現端須據此加註，
        # 否則使用者會以為等級完全依實際標示算出。
        ns_estimated = _ns_result.get("estimated_inputs", [])

        # 每日參考值百分比(2026-07-26)。只陳述「佔一天建議量的幾 %」,不做風險判斷;
        # 分母依使用者族群改用附表一對應欄位,見 module_b/daily_reference.py。
        daily_reference = get_daily_reference_payload(product, user_conditions)

        # LLM 摘要生成 + 持久化已抽出至 module_d/diagnosis.py(2026-07-24),邏輯逐字未改動。
        # ⚠ **threadpool 不是可有可無。** 這支裡面是同步的
        # `genai_client.models.generate_content`（Gemma 摘要，實測 2.3 秒，
        # fallback 路徑更久）加上同步的 DB 寫入。在 async handler 裡直接呼叫
        # 會卡住 event loop，期間 Cloud 不讀任何新請求的 body，下一個帶圖請求
        # 就在 Fog 的 5 秒連線逾時處降階——與 vision_backend 那次是同一個
        # bug class（2026-09-13 由 Fog 端重現實驗定位）。
        _t_diag = time.time()
        _diag_result = await run_in_threadpool(
            generate_ai_diagnosis,
            product, chemical, final_safety_events, user_conditions, nutrition,
            deterministic_score, deterministic_grade, ai_data, raw_allergens,
            cursor, db
        )
        _mark(request, "diagnosis", (time.time() - _t_diag) * 1000)
        ai_data = _diag_result["ai_data"]
        raw_allergens = _diag_result["raw_allergens"]

        # --- 6. 建構回傳格式 ---
        # 形狀的權威來源是 module_d/response_builder.py，並由 tests/contract/ 凍結；
        # 原註解指向的 shared/types.ts 已於 2026-08-05 移除（只有 fog 在用，已搬入 fog/types.ts）。
        # 回應組裝邏輯已抽出至 module_d/response_builder.py(2026-07-24),邏輯逐字未改動。
        return build_response(
            product, ai_data, calc_result, deterministic_score,
            chemical, basic, calculated_ingredient_types,
            final_safety_events, raw_allergens, nutrition, daily_reference,
            basic_detail, ns_estimated
        )
    except InsufficientNutritionData as e:
        # 營養標示沒讀齊，無法產出等級。**不給分數**——熱量、糖、鈉都是扣分項，
        # 缺值當 0 會讓分數偏樂觀，那是食安上最不該錯的方向。
        # 回傳沒有 health_score 的錯誤形狀，App 據此顯示 message
        # （判準見 CLAUDE.md：判定是否有效看有沒有 health_score）。
        _ZH = {"calories": "熱量", "sugar": "糖", "sodium": "鈉",
               "fat": "脂肪", "saturated_fat": "飽和脂肪", "protein": "蛋白質"}
        miss = "、".join(_ZH.get(k, k) for k in e.missing)
        print("[WARN] [Analyze] 營養標示缺漏，不計分：%s" % miss)
        return {"status": "error",
                "message": "營養標示未辨識完整（缺少 %s），無法計算健康評分。"
                           "請對準營養標示區域重新拍攝。" % miss}
    except Exception as e:
        import traceback
        traceback.print_exc()
        # ⚠ 不可把例外訊息原樣回給使用者。2026-09-14 實測，一個 TypeError
        # 讓 App 顯示「float() argument must be a string or a real number,
        # not 'NoneType'」——使用者無從理解，也無從處理。
        # 細節留在伺服器日誌裡（上面的 traceback），對外只說發生了什麼層級的事。
        print("[ERROR] [Analyze] 未預期的例外：%r" % (e,))
        return {"status": "error",
                "message": "分析過程發生未預期的錯誤，請稍後再試。"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("CLOUD_PORT", 3003))
    uvicorn.run(app, host="0.0.0.0", port=port)
