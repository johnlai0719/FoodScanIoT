import streamlit as st
import pandas as pd
import psycopg2
import json
import os
import altair as alt

# 設定頁面配置
st.set_page_config(
    page_title="FoodScanIoT 添加物與產品審核工作台",
    page_icon="🧬",
    layout="wide"
)

# 本地審查與對照檔案路徑
HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT_REVIEWS_FILE  = os.path.join(HERE, "02_identity_verification", "product_reviews.json")
CURATED_ADDITIVES_FILE = os.path.join(HERE, "02_identity_verification", "curated_additives.json")
AUDIT_CSV     = os.path.join(HERE, "02_identity_verification", "identity_audit.csv")
JECFA_CSV     = os.path.join(HERE, "01_data_sources", "harvested_links", "jecfa_source_map.csv")
OFT_CSV       = os.path.join(HERE, "01_data_sources", "harvested_links", "openfoodtox_matches.csv")
TFDA_PDF_CSV  = os.path.join(HERE, "01_data_sources", "harvested_links", "tfda_pdf_map.csv")
ALIASES_JSON  = os.path.join(HERE, "01_data_sources", "harvested_links", "tfda_aliases_map.json")

os.makedirs(os.path.dirname(PRODUCT_REVIEWS_FILE), exist_ok=True)

# ── 閾值定義 ─────────────────────────────────────────────
OFT_APPROVED_SIM = 0.80
OFT_PENDING_SIM  = 0.60   # FIX #7：三層閾值（原為二層）

# ── Lucide SVG ────────────────────────────────────────────
ICON_LAYERS   = """<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-10 9h18Z"/><path d="m22 17-10 9-10-9"/><path d="m2 10 10 9 10-9"/></svg>"""
ICON_PACKAGE  = """<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>"""
ICON_FLASK    = """<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2v8L4.72 20.55a1 1 0 0 0 .9 1.45h12.76a1 1 0 0 0 .9-1.45L14 10V2Z"/><path d="M8.5 2h7"/><path d="M7 16h10"/></svg>"""
ICON_DATABASE = """<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5c0 1.66 4 3 9 3s9-1.34 9-3s-4-3-9-3s-9 1.34-9 3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"/></svg>"""
ICON_CHART    = """<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>"""

# ── 輔助函式 ──────────────────────────────────────────────
def render_app_title():
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:12px;margin-bottom:25px;">
            <div style="color:#2e7d32;display:flex;align-items:center;">{ICON_LAYERS}</div>
            <h1 style="margin:0;font-size:2.2rem;font-weight:700;color:#1b5e20;">
                FoodScanIoT 添加物與產品審核工作台</h1></div>""",
        unsafe_allow_html=True)

def render_page_header(icon_svg, title_text):
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:15px;margin-top:10px;">
            <div style="color:#388e3c;display:flex;align-items:center;">{icon_svg}</div>
            <h2 style="margin:0;font-size:1.6rem;font-weight:600;color:#2e7d32;">{title_text}</h2></div>""",
        unsafe_allow_html=True)

def safe_str(val, default="（無）"):
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except Exception:
        pass
    s = str(val).strip()
    if s.lower() in ['nan', 'none', '']:
        return default
    if s.endswith('.0') and s[:-2].lstrip('-').isdigit():
        s = s[:-2]
    return s

def safe_col(series_row, col, default=None):
    """FIX #3：安全讀取 pandas Series 欄位，避免 .get() 在部分版本失效"""
    try:
        val = series_row[col]
        return default if pd.isna(val) else val
    except (KeyError, TypeError):
        return default

# ── FIX #1：每次建立短壽命連線，不快取 connection 物件 ────
def make_db_conn():
    try:
        return psycopg2.connect(
            host="localhost", port="5432",
            database="product_db", user="postgres", password=os.getenv("DB_PASSWORD", "")
        )
    except Exception as e:
        st.error(f"無法連線至 PostgreSQL：{e}")
        return None

# ── FIX #4：CSV 加 cache，30 秒 TTL 兼顧效能與即時性 ──────
@st.cache_data(ttl=30)
def load_audit_data():
    if not os.path.exists(AUDIT_CSV):
        return pd.DataFrame()
    return pd.read_csv(AUDIT_CSV)

@st.cache_data(ttl=30)
def load_jecfa_data():
    if not os.path.exists(JECFA_CSV):
        return pd.DataFrame()
    return pd.read_csv(JECFA_CSV)

@st.cache_data(ttl=30)
def load_oft_data():
    if not os.path.exists(OFT_CSV):
        return pd.DataFrame()
    return pd.read_csv(OFT_CSV)

@st.cache_data(ttl=60)
def load_tfda_pdf_data():
    if not os.path.exists(TFDA_PDF_CSV):
        return pd.DataFrame()
    return pd.read_csv(TFDA_PDF_CSV)

def load_products():
    conn = make_db_conn()          # FIX #1：每次新連線
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("SELECT barcode, name, ingredients_raw, ingredients_list FROM products ORDER BY name;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        products = []
        for barcode, name, raw_text, ing_list in rows:
            additives = []
            if ing_list:
                if isinstance(ing_list, list):
                    additives = ing_list
                elif isinstance(ing_list, str):
                    try:
                        additives = json.loads(ing_list)
                    except Exception:
                        additives = [ing_list]
            products.append({
                "barcode": barcode, "name": name,
                "ingredients_raw": raw_text or "（無原始 OCR 內容）",
                "additives": additives
            })
        return products
    except Exception as e:
        st.error(f"讀取產品資料失敗：{e}")
        try:
            conn.close()
        except Exception:
            pass
        return []

def load_json_store(filepath):
    """保留給 product_reviews.json 使用（產品視角審核）"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_json_store(filepath, data):
    """保留給 product_reviews.json 使用（產品視角審核）"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── 新版：從 PostgreSQL staging.additives_curated 讀取審核資料 ──
@st.cache_data(ttl=15)
def load_curated_from_db():
    """
    從 staging.additives_curated 讀取所有人工審核紀錄，
    回傳 dict：{record_id: {...欄位...}}
    """
    conn = make_db_conn()
    if not conn:
        return {}
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT record_id, selected_cas, curated_ins,
                   curated_adi_value, curated_adi_unit,
                   curated_iarc_class, curated_status,
                   cas_overridden, ins_overridden,
                   jecfa_url_overridden, adi_overridden,
                   reviewed_by, reviewed_at, reviewer_notes,
                   updated_at
            FROM staging.additives_curated
            ORDER BY record_id;
        """)
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = {}
        for row in rows:
            d = dict(zip(cols, row))
            rid = d.pop('record_id')
            # 將 None 轉為空字串，Timestamp 轉為字串
            for k, v in d.items():
                if v is None:
                    d[k] = ""
                elif hasattr(v, 'strftime'):
                    d[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            result[rid] = d
        return result
    except Exception as e:
        st.error(f"讀取 staging.additives_curated 失敗：{e}")
        try:
            conn.close()
        except Exception:
            pass
        return {}

def save_curated_to_db(rid, data, old_status=""):
    """
    將單筆審核結果寫入 staging.additives_curated（UPSERT），
    並同步寫一筆操作日誌至 staging.review_log。
    """
    conn = make_db_conn()
    if not conn:
        st.error("無法連線至資料庫，儲存失敗。")
        return False
    try:
        cur = conn.cursor()

        # UPSERT 主表
        cur.execute("""
            INSERT INTO staging.additives_curated (
                record_id, selected_cas, curated_ins,
                curated_adi_value, curated_adi_unit,
                curated_iarc_class, curated_status,
                cas_overridden, ins_overridden,
                jecfa_url_overridden, adi_overridden,
                reviewed_by, reviewed_at, reviewer_notes,
                updated_at
            ) VALUES (
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, NOW(), %s,
                NOW()
            )
            ON CONFLICT (record_id) DO UPDATE SET
                selected_cas         = EXCLUDED.selected_cas,
                curated_ins          = EXCLUDED.curated_ins,
                curated_adi_value    = EXCLUDED.curated_adi_value,
                curated_adi_unit     = EXCLUDED.curated_adi_unit,
                curated_iarc_class   = EXCLUDED.curated_iarc_class,
                curated_status       = EXCLUDED.curated_status,
                cas_overridden       = EXCLUDED.cas_overridden,
                ins_overridden       = EXCLUDED.ins_overridden,
                jecfa_url_overridden = EXCLUDED.jecfa_url_overridden,
                adi_overridden       = EXCLUDED.adi_overridden,
                reviewed_by          = EXCLUDED.reviewed_by,
                reviewed_at          = NOW(),
                reviewer_notes       = EXCLUDED.reviewer_notes,
                updated_at           = NOW();
        """, (
            rid,
            data.get('selected_cas') or None,
            data.get('curated_ins') or None,
            data.get('curated_adi_value') or None,
            data.get('curated_adi_unit') or None,
            data.get('curated_iarc_class') or None,
            data.get('curated_status'),
            bool(data.get('cas_overridden')),
            bool(data.get('ins_overridden')),
            bool(data.get('jecfa_url_overridden')),
            bool(data.get('adi_overridden')),
            data.get('reviewed_by') or None,
            data.get('reviewer_notes') or None,
        ))

        # 寫操作日誌
        action = 'CREATED' if not old_status else 'UPDATED'
        if data.get('curated_status') == 'APPROVED':
            action = 'APPROVED'
        elif data.get('curated_status') == 'REJECTED':
            action = 'REJECTED'

        cur.execute("""
            INSERT INTO staging.review_log
                (record_id, reviewed_by, action, old_status, new_status, reviewer_notes)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (
            rid,
            data.get('reviewed_by') or None,
            action,
            old_status or None,
            data.get('curated_status'),
            data.get('reviewer_notes') or None,
        ))

        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        conn.rollback()
        st.error(f"儲存至 staging.additives_curated 失敗：{e}")
        try:
            conn.close()
        except Exception:
            pass
        return False

@st.cache_data(ttl=30)
def load_experiment_source_urls(record_id=None):
    """從 experiment.additive_source_urls 讀取多 URL 實驗資料"""
    conn = make_db_conn()
    if not conn:
        return pd.DataFrame()
    try:
        cur = conn.cursor()
        if record_id:
            cur.execute("""
                SELECT record_id, source_name, doc_type, url,
                       matched_title, similarity, status, fetched_at, is_curated
                FROM experiment.additive_source_urls
                WHERE record_id = %s
                ORDER BY similarity DESC NULLS LAST;
            """, (record_id,))
        else:
            cur.execute("""
                SELECT record_id, source_name, doc_type, url,
                       matched_title, similarity, status, fetched_at, is_curated
                FROM experiment.additive_source_urls
                ORDER BY record_id, similarity DESC NULLS LAST;
            """)
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return pd.DataFrame()

@st.cache_data(ttl=30)
def load_jecfa_details(record_id):
    """從 experiment.jecfa_details 讀取 JECFA 萃取出的結構化資料"""
    conn = make_db_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ins_number, adi_value, functional_class, latest_evaluation, comments, specifications
            FROM experiment.jecfa_details
            WHERE record_id = %s;
        """, (record_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return {
                'ins_number': row[0],
                'adi_value': row[1],
                'functional_class': row[2],
                'latest_evaluation': row[3],
                'comments': row[4],
                'specifications': row[5]
            }
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
    return None

# ══════════════════════════════════════════════════════════
# 應用介面
# ══════════════════════════════════════════════════════════
render_app_title()

# ── 審核者身份（Session 層級，填一次全程有效）───────────────
st.sidebar.markdown("---")
st.sidebar.markdown("#### 👤 審核者身份")
reviewer_id = st.sidebar.text_input(
    "審核者代號（填寫後全程有效）",
    value=st.session_state.get("reviewer_id", ""),
    placeholder="例：A01、ChemReviewer-B",
    key="reviewer_id_input"
)
if reviewer_id:
    st.session_state["reviewer_id"] = reviewer_id
    st.sidebar.success(f"✅ 目前審核者：{reviewer_id}")
else:
    st.sidebar.warning("⚠️ 請填寫審核者代號後再進行標註")

st.sidebar.markdown("---")
page = st.sidebar.radio(
    "選擇工作台視角",
    ["Product Review (產品視角)",
     "Additive Validation (核心審核 - 主戰場)",
     "Data Sources (資料來源特徵解析)",
     "Statistics (數據統計)"]
)

# ──────────────────────────────────────────────────────────
# 1. Product Review
# ──────────────────────────────────────────────────────────
if page == "Product Review (產品視角)":
    render_page_header(ICON_PACKAGE, "Product Review (產品視角)")
    st.caption("此處僅做產品包裝 OCR 原始成分與 AI 抽出添加物的一致性檢驗，不做詳細的化學屬性審核。")

    products = load_products()
    product_reviews = load_json_store(PRODUCT_REVIEWS_FILE)

    if not products:
        st.info("資料庫中目前沒有產品資料。")
    else:
        p_options = {f"{p['name']} ({p['barcode']})": idx for idx, p in enumerate(products)}
        selected_p_label = st.selectbox("選擇要審核的產品：", list(p_options.keys()))
        selected_p = products[p_options[selected_p_label]]
        prev_review = product_reviews.get(selected_p['barcode'], {"correct": True, "missing": ""})

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### OCR 原始成分標籤")
            st.info(selected_p['ingredients_raw'])
        with col2:
            st.markdown("#### 提取出的添加物列表")
            if selected_p['additives']:
                for a in selected_p['additives']:
                    st.write(f"- `{a}`")
            else:
                st.warning("（此產品未提取出任何添加物）")

        st.markdown("---")
        st.markdown("#### 快速標籤審核")
        with st.form("product_review_form"):
            correct = st.checkbox("AI 提取是否完全正確？(No Errors)", value=prev_review.get("correct", True))
            missing = st.text_input("是否有漏掉的添加物？(如有，以逗號分隔)", value=prev_review.get("missing", ""))
            if st.form_submit_button("儲存產品審查結果"):
                product_reviews[selected_p['barcode']] = {
                    "product_name": selected_p['name'], "correct": correct,
                    "missing": missing, "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                save_json_store(PRODUCT_REVIEWS_FILE, product_reviews)
                st.success("產品審查結果已安全存檔！")

# ──────────────────────────────────────────────────────────
# 2. Additive Validation（主戰場）
# ──────────────────────────────────────────────────────────
elif page == "Additive Validation (核心審核 - 主戰場)":
    render_page_header(ICON_FLASK, "Additive Validation (核心審核 - 主戰場)")
    st.caption("此處進行化學實體（Entity）的核心審核，包含英文名與 CAS 號之比對與消歧。")

    df         = load_audit_data()
    df_jecfa   = load_jecfa_data()
    df_oft     = load_oft_data()
    aliases_map       = load_json_store(ALIASES_JSON)
    curated_additives = load_curated_from_db()   # 改為從 staging DB 讀取

    if df.empty:
        st.info("暫無審核數據，請先運行 step2a_verify_pubchem.py。")
    else:
        status_filter = st.selectbox(
            "過濾審核狀態 (Status Filter)：",
            ["ALL", "PENDING_REVIEW", "APPROVED", "NO_CAS", "NO_PUBCHEM_RECORD", "HAS_IARC"]
        )
        if status_filter == "ALL":
            filtered_df = df
        elif status_filter == "HAS_IARC":
            filtered_df = df[df['record_id'].apply(lambda x: 
                curated_additives.get(x, {}).get('curated_iarc_class', '') not in ('', 'None')
            )]
        else:
            # 優先使用資料庫中的狀態，否則 fallback 使用 CSV 狀態
            filtered_df = df[df['record_id'].apply(lambda x: 
                curated_additives.get(x, {}).get('curated_status', df[df['record_id'] == x]['status'].values[0] if x in df['record_id'].values else 'UNKNOWN') == status_filter
            )]

        if filtered_df.empty:
            st.info(f"查無狀態為 '{status_filter}' 的添加物。")
        else:
            # FIX #9：選單前綴加入狀態 emoji
            def _row_emoji(row):
                cur = curated_additives.get(row['record_id'], {})
                eff_status = cur.get("curated_status", row['status'])
                if eff_status == "APPROVED":
                    return "✅"
                elif eff_status in ("PENDING_REVIEW", "NO_CAS"):
                    return "⚠️"
                else:
                    return "❌"

            def _row_label(r):
                rid = r['record_id']
                cur = curated_additives.get(rid, {})
                iarc = cur.get("curated_iarc_class", "")
                iarc_suffix = f" [IARC: {iarc}]" if iarc and iarc != "None" else ""
                emoji = _row_emoji(r)
                return f"{emoji} {rid} | {r['name_zh']} ({safe_str(r['name_en'])}){iarc_suffix}"

            add_options = {
                _row_label(r): idx
                for idx, r in filtered_df.iterrows()
            }
            selected_add_label = st.selectbox("選擇要審查的添加物：", list(add_options.keys()))
            selected_row = filtered_df.loc[add_options[selected_add_label]]

            rid = selected_row['record_id']
            zh  = selected_row['name_zh']
            en  = selected_row['name_en']
            prev_curation = curated_additives.get(rid, {})

            # ── 修正：為了防止 LLM 數據污染，凡是別名匹配 (local_alias_match) 者，在 UI 與資料庫中一律視為未對照
            raw_pc_status = selected_row['status']
            pc_method = selected_row.get('match_method', '')
            if pc_method == 'local_alias_match':
                raw_pc_status = 'NO_PUBCHEM_RECORD'
                pc_status = 'NO_PUBCHEM_RECORD'
            else:
                pc_status = raw_pc_status

            # ── 三方核對狀態卡片 ────────────────────────────────────
            st.markdown("#### 📋 三方核對檢驗狀態 (Three-Way Validation Checklist)")

            # A. PubChem
            if pc_status == 'APPROVED':
                pc_html = "<span style='color:#2e7d32;font-weight:bold;'>✔️ PASSED (自動通過)</span>"
                pc_desc = "已成功鎖定單一可靠的 CAS 號碼。"
            elif pc_status == 'PENDING_REVIEW':
                pc_html = "<span style='color:#f57c00;font-weight:bold;'>⚠️ PENDING (待確認)</span>"
                pc_desc = "英文拼寫有差異，或存在多個 CAS 候選號碼。"
            else:
                pc_html = "<span style='color:#c62828;font-weight:bold;'>❌ FAILED (未通過)</span>"
                pc_desc = "PubChem 中無此化學品紀錄，或無關聯 CAS。"

            # B. JECFA — 修正：直接讀取新版 experiment.additive_source_urls 資料庫，避免使用舊 CSV
            exp_urls = load_experiment_source_urls(record_id=rid)
            jecfa_db_filt = exp_urls[exp_urls['source_name'] == 'JECFA'] if not exp_urls.empty else pd.DataFrame()
            
            jc_status_val = 'NO_MATCH'
            jc_url_val    = ''
            jc_title_val  = ''
            jc_sim_val    = 0.0
            jc_cand_url   = ''
            jc_cand_title = ''
            
            if jecfa_db_filt.empty:
                jc_html = "<span style='color:#c62828;font-weight:bold;'>❌ UNMATCHED (未對照)</span>"
                jc_desc = "JECFA 文獻庫中查無此添加物對照記錄。"
            else:
                # 取得相似度最高的第一筆（已經依據 similarity 降序排列）
                best_match = jecfa_db_filt.iloc[0]
                jc_status_val = best_match.get('status', 'NO_MATCH')
                jc_title_val  = safe_str(best_match.get('matched_title', ''))
                jc_sim_val    = float(best_match.get('similarity', 0.0) or 0.0)
                jc_url_val    = safe_str(best_match.get('url', ''), '')
                
                # 如果是 Pending 狀態，將最高相似度的那一筆作為候選顯示
                if jc_status_val == 'PENDING_REVIEW':
                    jc_cand_url   = jc_url_val
                    jc_cand_title = jc_title_val

                if jc_status_val == 'APPROVED':
                    jc_html = "<span style='color:#2e7d32;font-weight:bold;'>✔️ PASSED (自動通過)</span>"
                    jc_desc = f"成功定位文獻：'{jc_title_val}' (相似度 {jc_sim_val:.2f})"
                elif jc_status_val == 'PENDING_REVIEW':
                    jc_html = "<span style='color:#f57c00;font-weight:bold;'>⚠️ PENDING (待確認)</span>"
                    if jc_cand_url:
                        jc_desc = f"候選文獻：<a href='{jc_cand_url}' target='_blank'>{jc_cand_title}</a> (相似度 {jc_sim_val:.2f})"
                    else:
                        jc_desc = "文獻比對未達標，無推薦候選，需人工手動搜尋。"
                else:
                    jc_html = "<span style='color:#c62828;font-weight:bold;'>❌ FAILED (未通過)</span>"
                    jc_desc = f"相似度 {jc_sim_val:.2f} 過低，已被過濾。"

                # 讀取並顯示 JECFA 萃取細節
                jc_details = load_jecfa_details(rid)
                if jc_details:
                    jc_desc += f"<br><br><b>💡 JECFA 萃取結論：</b><br>• INS 編號: <code>{jc_details['ins_number']}</code><br>• ADI 限值: <code>{jc_details['adi_value']}</code> (年份: {jc_details['latest_evaluation']})<br>• 備註: <small style='color:#555;'>{jc_details['comments']}</small>"

            # C. OpenFoodTox — FIX #7：三層閾值
            oft_df_filt = df_oft[df_oft['record_id'] == rid] if not df_oft.empty else pd.DataFrame()
            oft_status_val  = 'UNMATCHED'
            oft_adi_val     = ''
            oft_adi_unit_val = 'mg/kg bw/day'
            if oft_df_filt.empty:
                oft_html = "<span style='color:#c62828;font-weight:bold;'>❌ UNMATCHED (未對照)</span>"
                oft_desc = "本機尚未對照到 OpenFoodTox (EFSA) 毒理限值紀錄。"
            else:
                r0 = oft_df_filt.iloc[0]
                oft_sim          = float(safe_col(r0, 'similarity_score', 0.0) or 0.0)
                oft_cas          = safe_str(safe_col(r0, 'efsa_cas', ''))
                oft_adi_val      = safe_str(safe_col(r0, 'efsa_adi_value', ''), '')
                oft_adi_unit_val = safe_str(safe_col(r0, 'efsa_adi_unit', 'mg/kg bw/day'), 'mg/kg bw/day')

                if oft_sim >= OFT_APPROVED_SIM:
                    oft_status_val = 'APPROVED'
                    oft_html = "<span style='color:#2e7d32;font-weight:bold;'>✔️ PASSED (自動通過)</span>"
                    oft_desc = f"成功匹配歐盟毒理限值 (CAS: {oft_cas}，相似度 {oft_sim:.2f})"
                elif oft_sim >= OFT_PENDING_SIM:
                    oft_status_val = 'PENDING_REVIEW'
                    oft_html = "<span style='color:#f57c00;font-weight:bold;'>⚠️ PENDING (待確認)</span>"
                    oft_desc = f"對照相似度 {oft_sim:.2f}（0.60~0.79），需人工核對。"
                else:
                    oft_status_val = 'FAILED'
                    oft_html = "<span style='color:#c62828;font-weight:bold;'>❌ FAILED (錯配風險)</span>"
                    oft_desc = f"相似度 {oft_sim:.2f} 過低，可能為錯誤對照，不應採用。"

            # 渲染三欄卡片
            chk_col1, chk_col2, chk_col3 = st.columns(3)
            for col_widget, label, html_str, desc_str, url_str in [
                (chk_col1, "檢查點 1: PubChem 化學身份",       pc_html,  pc_desc,  ''),
                (chk_col2, "檢查點 2: JECFA 官方安全文獻",     jc_html,  jc_desc,  jc_url_val),
                (chk_col3, "檢查點 3: OpenFoodTox (EFSA) 毒理", oft_html, oft_desc, ''),
            ]:
                link_html = f'<a href="{url_str}" target="_blank">📄 評估文獻連結</a>' if url_str else desc_str
                with col_widget:
                    st.markdown(
                        f"""<div style="border:1px solid #e0e0e0;border-radius:8px;padding:12px;background:#fafafa;">
                            <div style="font-size:0.9rem;color:#666;">{label}</div>
                            <div style="font-size:1.1rem;margin-top:5px;">{html_str}</div>
                            <div style="font-size:0.8rem;color:#555;margin-top:5px;height:35px;
                                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                                {link_html if url_str else desc_str}</div></div>""",
                        unsafe_allow_html=True)

            st.markdown("---")

            # ── 左右佈局 ────────────────────────────────────────────
            col1, col2 = st.columns([3, 2])

            with col1:
                # 讀取資料庫最終 Curated 的 ADI 限值
                cur_adi_val = prev_curation.get("curated_adi_value", "")
                cur_adi_unit = prev_curation.get("curated_adi_unit", "")
                
                if cur_adi_val != "":
                    adi_display = f"0 - {cur_adi_val} {cur_adi_unit}"
                elif cur_adi_unit != "":
                    # 針對 NOT SPECIFIED 或 ACCEPTABLE
                    adi_display = cur_adi_unit
                else:
                    adi_display = "（無安全限值 / 免設定 ADI）"

                # 💡 從資料庫中拆分 JECFA 簡介與專論網址
                jc_eval_url = "（無對照簡介網址）"
                jc_mono_url = "（無對照專論網址）"
                
                if not jecfa_db_filt.empty:
                    # 💡 嚴格限制：僅顯示已通過 APPROVED 核准的對照網址，防止低相似度的待審/錯配文獻被自動填入
                    eval_df = jecfa_db_filt[(jecfa_db_filt['doc_type'] == 'JECFA/EVALUATION_SUMMARY') & (jecfa_db_filt['status'] == 'APPROVED')]
                    if not eval_df.empty:
                        jc_eval_url = safe_str(eval_df.iloc[0].get('url', ''), '（無對照簡介網址）')
                        
                    mono_df = jecfa_db_filt[(jecfa_db_filt['doc_type'] == 'JECFA/TOXICOLOGY_MONOGRAPH') & (jecfa_db_filt['status'] == 'APPROVED')]
                    if not mono_df.empty:
                        jc_mono_url = safe_str(mono_df.iloc[0].get('url', ''), '（無對照專論網址）')

                details_df = pd.DataFrame({
                    "屬性 (Attribute)": [
                        "品名 (中文)", 
                        "品名 (英文)", 
                        "INS/E 編號", 
                        "JECFA 評估簡介網址",  # 💡 拆分 1
                        "JECFA 毒理專論網址",  # 💡 拆分 2
                        "系統 ADI 限值"
                    ],
                    "對照值 (Value)": [
                        safe_str(zh), safe_str(en),
                        safe_str(selected_row['ins_or_e_number']),
                        jc_eval_url,
                        jc_mono_url,
                        adi_display
                    ]
                })
                st.table(details_df)

                st.markdown("##### PubChem 官方資訊")
                if pc_method == 'local_alias_match':
                    st.write("- **官方英文名 (Title)**: `（防污染已屏蔽別名對照）`")
                    st.write("- **英文相似度分數**: `0.0000`")
                    st.write("- **匹配管道 (Method)**: `no_match (防污染已屏蔽)`")
                    st.write("- **匹配品名**: `（防污染已屏蔽別名對照）`")
                    st.write("- **PubChem 提取 CAS**: `（無）` (候補: `（無）`)")
                else:
                    st.write(f"- **官方英文名 (Title)**: `{safe_str(selected_row['pubchem_title'])}`")
                    st.write(f"- **英文相似度分數**: `{selected_row['similarity_score']:.4f}`")
                    st.write(f"- **匹配管道 (Method)**: `{safe_str(selected_row['match_method'])}`")
                    st.write(f"- **匹配品名**: `{safe_str(selected_row['matched_name'])}`")
                    st.write(f"- **PubChem 提取 CAS**: `{safe_str(selected_row['selected_cas'])}` (候補: `{safe_str(selected_row['cas_candidates'])}`)")

                if pc_method == 'local_alias_match':
                    st.write("- **Gemini 別名候補**: `（此品項已列入下一階段 LLM 補強計畫）`")
                else:
                    add_aliases = aliases_map.get(rid, [])
                    alias_text = ', '.join(add_aliases) if add_aliases else "（此品項尚未跑別名預生成任務）"
                    st.write(f"- **Gemini 別名候補**: `{alias_text}`")

                notes_text = safe_str(selected_row['notes'])
                if "衝突" in notes_text:
                    notes_text = "需要人工審查：化學拼寫有差異，或尋獲多個 CAS 候選號碼。"
                if raw_pc_status == 'APPROVED':
                    st.success(f"✔️ {notes_text}")
                else:
                    st.warning(f"⚠️ {notes_text}")

            with col2:
                st.markdown("#### 人工審定決策面")

                # 動態審核指引
                guideline = ""
                if raw_pc_status == 'PENDING_REVIEW':
                    if "多個" in notes_text:
                        guideline = "💡 **[CAS多重候補]** 請參照 TFDA 規格 PDF，從下方選單選取最符合的 CAS 號（優先選無水物）。"
                    elif "未發現" in notes_text:
                        guideline = "💡 **[缺少 CAS 號]** PubChem 同義詞中缺乏 CAS，請手動從外部資料庫檢索後填入下方。"
                    else:
                        guideline = "💡 **[名稱相似度偏低]** 請核對兩邊名稱是否等價，若是請將狀態改為 APPROVED。"
                elif raw_pc_status == 'NO_PUBCHEM_RECORD':
                    guideline = "💡 **[PubChem 查無紀錄]** 請確認中英文名稱是否正確，或手動填入 CAS 號。"
                elif jc_status_val == 'PENDING_REVIEW':
                    guideline = "💡 **[JECFA 文獻待確認]** 請點擊連結確認是否為該品類的合併評估報告（Group Evaluation）。"
                elif oft_status_val == 'PENDING_REVIEW':
                    guideline = "💡 **[OpenFoodTox 待確認]** 歐盟名稱與本機有差異，請核對 ADI 是否適用此品項。"
                elif oft_status_val == 'FAILED':
                    guideline = "💡 **[OpenFoodTox 錯配警告]** 相似度過低，此 EFSA 紀錄可能是錯誤對照，ADI 請勿採用。"

                if guideline:
                    st.info(guideline)

                # ── FIX #2：baseline_cas 從 cas_candidates 第一個值取得 ──
                candidates_raw = selected_row['cas_candidates']
                candidates = (
                    [c.strip() for c in str(candidates_raw).split('|')
                     if c.strip() and c.strip().lower() != 'nan']
                    if pd.notna(candidates_raw) else []
                )
                baseline_cas = candidates[0] if candidates else ""

                # 在表單外部預處理 JECFA 推薦網址的採納（解決 st.button 不能在 st.form 內部的限制）
                default_jc_url = prev_curation.get("curated_jecfa_url", jc_url_val)
                if f"{rid}_adopted_jc_url" in st.session_state:
                    default_jc_url = st.session_state[f"{rid}_adopted_jc_url"]

                if jc_status_val == 'PENDING_REVIEW' and jc_cand_url and not default_jc_url:
                    st.markdown("##### 💡 JECFA 推薦候選")
                    st.caption(f"[{jc_cand_title}]({jc_cand_url}) (相似度 {jc_sim_val:.2f})")
                    if st.button("采納系統推薦候選", key=f"{rid}_adopt_jc"):
                        st.session_state[f"{rid}_adopted_jc_url"] = jc_cand_url
                        st.rerun()

                # ── FIX #8：所有 widget 加唯一 key（rid 前綴）────────────
                with st.form(f"form_{rid}"):
                    st.markdown("##### 0. INS/E 編號標註")
                    default_ins = prev_curation.get("curated_ins", safe_str(selected_row['ins_or_e_number'], ""))
                    curated_ins = st.text_input("手動審定 INS/E 編號：",
                                                value=default_ins, key=f"{rid}_ins")

                    st.markdown("##### 1. CAS 號標註 (CAS Registry)")
                    selected_cas = ""
                    if candidates:
                        cas_options = candidates + ["自訂手動輸入"]
                        prev_cas = prev_curation.get("selected_cas", "")
                        default_idx = candidates.index(prev_cas) if prev_cas in candidates else 0
                        cas_selection = st.selectbox("選擇採納的 CAS 號：",
                                                     cas_options, index=default_idx,
                                                     key=f"{rid}_cas_sel")
                        if cas_selection == "自訂手動輸入":
                            selected_cas = st.text_input("自訂輸入 CAS 號：",
                                                         value=prev_curation.get("selected_cas", ""),
                                                         key=f"{rid}_cas_txt")
                        else:
                            selected_cas = cas_selection
                    else:
                        selected_cas = st.text_input("自訂輸入 CAS 號：",
                                                     value=prev_curation.get("selected_cas", ""),
                                                     key=f"{rid}_cas_txt2")

                    st.markdown("##### 2. JECFA 文獻標註")
                    curated_jecfa_url = st.text_input("手動審定 JECFA 報告網址：",
                                                      value=default_jc_url, key=f"{rid}_jcurl")

                    st.markdown("##### 3. OpenFoodTox 毒理限制標註 (EFSA ADI)")
                    default_adi_val  = prev_curation.get("curated_adi_value", oft_adi_val)
                    default_adi_unit = prev_curation.get("curated_adi_unit", oft_adi_unit_val)
                    ca1, ca2 = st.columns(2)
                    with ca1:
                        curated_adi_value = st.text_input("手動審定 ADI 數值：",
                                                          value=default_adi_val, key=f"{rid}_adiv")
                    with ca2:
                        curated_adi_unit = st.text_input("手動審定 ADI 單位：",
                                                         value=default_adi_unit, key=f"{rid}_adiu")

                    st.markdown("##### 4. IARC 致癌風險標註")
                    iarc_options = ["None", "Group 1", "Group 2A", "Group 2B", "Group 3"]
                    prev_iarc = prev_curation.get("curated_iarc_class", "None")
                    default_iarc_idx = iarc_options.index(prev_iarc) if prev_iarc in iarc_options else 0
                    curated_iarc_class = st.selectbox("手動審定 IARC 致癌分類：",
                                                      iarc_options, index=default_iarc_idx,
                                                      key=f"{rid}_iarc")
                    curated_iarc_url = st.text_input("手動審定 IARC 報告網址：",
                                                     value=prev_curation.get("curated_iarc_url", ""),
                                                     key=f"{rid}_iarcurl")

                    st.markdown("##### 5. 審定決策與日誌")
                    status_options = ["APPROVED", "PENDING_REVIEW", "REJECTED"]
                    prev_cstatus = prev_curation.get("curated_status", raw_pc_status)
                    default_si = status_options.index(prev_cstatus) if prev_cstatus in status_options else 0
                    curated_status = st.selectbox("修改審核狀態：",
                                                  status_options, index=default_si,
                                                  key=f"{rid}_status")
                    reviewer_notes = st.text_area("審核備註與修改理由：",
                                                  value=prev_curation.get("reviewer_notes", ""),
                                                  key=f"{rid}_notes")

                    # 顯示目前審核者身份（唯讀提示）
                    current_reviewer = st.session_state.get("reviewer_id", "")
                    if current_reviewer:
                        st.caption(f"📝 本次標註將以審核者 **{current_reviewer}** 的身份記錄")
                    else:
                        st.warning("⚠️ 尚未填寫審核者代號，請至左側側邊欄填寫後再提交")

                    submit_disabled = not bool(current_reviewer)
                    if st.form_submit_button("提交並儲存審核標註",
                                             disabled=submit_disabled):
                        baseline_ins    = safe_str(selected_row['ins_or_e_number'], "")
                        baseline_jc_url = jc_url_val
                        baseline_adi    = oft_adi_val
                        now_str = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

                        save_data = {
                            "selected_cas":       selected_cas,
                            "curated_ins":        curated_ins,
                            "curated_adi_value":  curated_adi_value,
                            "curated_adi_unit":   curated_adi_unit,
                            "curated_iarc_class": curated_iarc_class,
                            "curated_status":     curated_status,
                            "reviewer_notes":     reviewer_notes,
                            "reviewed_by":        current_reviewer,
                            "cas_overridden":      selected_cas       != baseline_cas,
                            "ins_overridden":      curated_ins        != baseline_ins,
                            "jecfa_url_overridden": curated_jecfa_url != baseline_jc_url,
                            "adi_overridden":      curated_adi_value  != baseline_adi,
                        }
                        old_st = prev_curation.get("curated_status", "")
                        ok = save_curated_to_db(rid, save_data, old_status=old_st)
                        if ok:
                            st.success(f"✅ [{current_reviewer}] 審定標註已寫入 staging.additives_curated！")
                            st.cache_data.clear()

            # ── 🧪 Experiment 多 URL 展示 ──────
            st.markdown("#### 🧪 Experiment：多文件來源（experiment.additive_source_urls）")
            exp_urls = load_experiment_source_urls(record_id=rid)
            if not exp_urls.empty:
                st.caption("以下為新版多 URL 爬蟲的實驗結果，尚未升格至 staging。")
                st.dataframe(
                    exp_urls[['doc_type','url','matched_title','similarity','status','is_curated']],
                    use_container_width=True
                )
                
                # ── 📄 JECFA 原始毒理文獻文本預覽 ──────
                import glob
                archive_dir = os.path.join(HERE, '03_llm_ingestion', 'source_archive')
                pattern = os.path.join(archive_dir, f"{rid}__*.txt")
                txt_files = sorted(glob.glob(pattern))
                
                # 取得目前已核准 (APPROVED) 的 URL 清單
                approved_urls = set(exp_urls[exp_urls['status'] == 'APPROVED']['url'].tolist())
                
                if txt_files:
                    # 篩選出只包含 APPROVED URL 的檔案
                    valid_txt_files = []
                    for path in txt_files:
                        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = [f.readline().strip() for _ in range(5)]
                        url_header = ""
                        for line in lines:
                            if line.startswith("# url:"):
                                url_header = line.replace("# url:", "").strip()
                                break
                        if url_header in approved_urls:
                            valid_txt_files.append((path, url_header))
                            
                    if valid_txt_files:
                        st.markdown("##### 📄 原始毒理報告文本預覽 (僅展示已核准文獻)")
                        for path, url_header in valid_txt_files:
                            fn = os.path.basename(path)
                            with st.expander(f"📖 {fn} ({url_header})"):
                                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read()
                                st.text_area("文本內容：", value=content, height=300, key=f"txt_{fn}_{rid}")
            else:
                st.info("🧪 此品項暫無實驗性多文獻資料（僅前 50 筆已生成）。若需生成，請執行 step1_multi_jecfa_harvest.py。")

# ──────────────────────────────────────────────────────────
# 3. Data Sources
# ──────────────────────────────────────────────────────────
elif page == "Data Sources (資料來源特徵解析)":
    render_page_header(ICON_DATABASE, "Data Sources (資料來源特徵解析)")
    st.caption("展示每個整合資料來源（Data Source）如何運作、能萃取出哪些有用的結構化特徵。")

    source_choice = st.selectbox(
        "選擇要檢視的資料來源：",
        ["PubChem (NIH 美國國家生物技術資訊中心)",
         "JECFA (FAO/WHO 聯合食品添加物專家委員會)",
         "OpenFoodTox (EFSA 歐洲食品安全局毒理資料庫)",
         "TFDA (台灣衛福部食藥署官方標準)"]
    )

    col_l, col_r = st.columns([3, 2])

    if "PubChem" in source_choice:
        with col_l:
            st.markdown("### 🧬 PubChem 化學身份庫")
            st.markdown("""
* **對接機制**：經由 `PUG REST API` 進行線上品名及 CAS 號檢索。
* **核心定位**：純粹作為「化學名 ➔ CAS 號碼」轉換工具，不儲存 CID。
* **萃取特徵（結構化）**：
  1. **CAS Number**: 自同義詞中以正則 `r'^\\d{{2,7}}-\\d{{2}}-\\d$'` 提取。
  2. **Preferred Title**: 用於計算英文相似度。
""")
            st.table(pd.DataFrame({
                "萃取特徵": ["CAS Number", "Preferred Title"],
                "資料類型": ["String (Regex)", "String"],
                "用途": ["多國法規毒理對照核心 Key", "名稱相似度安全核對"]
            }))
        with col_r:
            st.markdown("##### 📄 真實解析樣本")
            st.json({"record_id": "ADD-0009", "name_zh": "苯甲酸鈉", "name_en": "Sodium Benzoate",
                     "pubchem_title": "Sodium benzoate", "match_method": "direct_name_match",
                     "cas_candidates": ["532-32-1", "25665-42-9"]})

    elif "JECFA" in source_choice:
        with col_l:
            st.markdown("### 🌐 JECFA 國際評估文獻")
            st.markdown("""
* **對接機制**：經由 `Inchem Solr API` 三階段漏斗檢索（品名 → INS → CAS）。
* **萃取特徵（結構化）**：
  1. **JECFA Monograph URL**: 官方安全性評估文獻連結。
  2. **Document Title**: JECFA 文獻標題。
  3. **Similarity Score**: 字元相似度，用於三段式審計判定。
* **非結構化**：ADI 限值結論、臨床毒理評估、化學純度規格。
""")
            st.table(pd.DataFrame({
                "萃取特徵": ["Monograph URL", "Title", "Similarity Score"],
                "資料類型": ["URL", "String", "Float 0.00~1.00"],
                "用途": ["官方安全文獻佐證", "核對文獻名稱", "防範錯配（Salt vs Acid）"]
            }))
        with col_r:
            st.markdown("##### 📄 真實解析樣本")
            df_jc = load_jecfa_data()
            if not df_jc.empty:
                sample_row = df_jc.dropna(subset=['jecfa_url']).head(1)
                if not sample_row.empty:
                    r = sample_row.iloc[0]
                    st.json({
                        "record_id": safe_str(r['record_id']),
                        "name_zh": safe_str(r['name_zh']),
                        "name_en": safe_str(r['name_en']),
                        "jecfa_url": safe_str(r['jecfa_url']),
                        "matched_title": safe_str(r['matched_title']),
                        "similarity_score": float(r['similarity_score']),
                        "status": safe_str(r['status'])
                    })
            else:
                st.info("無本地 JECFA CSV 資料檔。")

    elif "OpenFoodTox" in source_choice:
        with col_l:
            st.markdown("""### 🇪🇺 OpenFoodTox 歐盟毒理庫
* **對接機制**：本地 `pandas` 解析 EFSA 官方 XLSX 開源數據。
* **萃取特徵（結構化）**：
  1. **EFSA CAS / E-Number**: 歐盟登記之 CAS 號與 E 編號（等價於 INS 編號）。
  2. **ADI Value / Unit**: 每日允許攝入量數值與單位。
  3. **No Allocated Flag**: 是否「無須設定 ADI」的法規指標。""")
            st.table(pd.DataFrame({
                "萃取特徵": ["EFSA CAS / E-Number", "ADI Value & Unit", "No Allocated Flag"],
                "資料類型": ["String", "Float & String", "Boolean"],
                "用途": ["雙向 CAS/INS 核對消歧", "每日審核警告基礎", "判定無毒無害豁免品項"]
            }))
        with col_r:
            st.markdown("##### 📄 真實解析樣本")
            df_o = load_oft_data()
            if not df_o.empty:
                r = df_o.iloc[0]
                st.json({"efsa_name": safe_str(r['efsa_name']), "efsa_cas": safe_str(r['efsa_cas']),
                         "efsa_ec": safe_str(r['efsa_ec']), "efsa_adi_value": safe_str(r['efsa_adi_value']),
                         "efsa_adi_unit": safe_str(r['efsa_adi_unit']),
                         "similarity_score": round(float(r['similarity_score']), 4)})
            else:
                st.info("無本地 OpenFoodTox CSV 資料檔。")

    elif "TFDA" in source_choice:
        with col_l:
            st.markdown("### 🇹🇼 TFDA 衛福部食藥署法規標準")
            st.markdown("""
* **對接機制**：`urllib` 爬蟲以明細頁 ID 全量輪詢抓取。
* **萃取特徵（結構化）**：
  1. **Chinese / English Name**: 台灣官方公告法定名稱。
  2. **INS / E-Number**: 聯合國食品法典編號。
  3. **PDF Status**: `DOWNLOADED` / `NO_PDF_SPEC`。
* **非結構化**：使用標準、純度規格 PDF（可作為 RAG 知識庫）。
""")
            st.table(pd.DataFrame({
                "萃取特徵": ["Chinese/English Name", "INS/E Number", "PDF Path"],
                "資料類型": ["String", "String", "Local Path"],
                "用途": ["建立資料庫根實體", "國際身分對照", "本地法規規格佐證"]
            }))
        with col_r:
            st.markdown("##### 📄 真實解析樣本")
            df_t = load_tfda_pdf_data()
            if not df_t.empty:
                r = df_t.iloc[0]
                st.json({"id": int(r['id']), "name_zh": safe_str(r['name_zh']),
                         "name_en": safe_str(r['name_en']), "pdf_filename": safe_str(r['pdf_filename']),
                         "status": safe_str(r['status'])})
            else:
                st.info("無本地 TFDA PDF 對照資料檔。")

# ──────────────────────────────────────────────────────────
# 4. Statistics
# ──────────────────────────────────────────────────────────
elif page == "Statistics (數據統計)":
    render_page_header(ICON_CHART, "Statistics (數據統計)")
    st.caption("全量資料庫清洗成效之客觀量化指標與跨資料庫（PubChem / JECFA / OpenFoodTox）狀態統計。")

    df_pubchem = load_audit_data()
    df_jecfa   = load_jecfa_data()
    df_oft     = load_oft_data()

    # FIX #6：從資料庫直接 COUNT(*)，確保分母永遠正確（不受 CSV 進度影響）
    total_additives = 804  # fallback
    try:
        _conn = make_db_conn()
        if _conn:
            _cur = _conn.cursor()
            _cur.execute("SELECT COUNT(*) FROM additives;")
            total_additives = _cur.fetchone()[0]
            _cur.close()
            _conn.close()
    except Exception:
        pass

    jecfa_matched_count   = len(df_jecfa[df_jecfa['jecfa_url'].notna() & (df_jecfa['jecfa_url'] != '')]) if not df_jecfa.empty else 0
    oft_matched_count     = len(df_oft) if not df_oft.empty else 0
    pubchem_approved_count = len(df_pubchem[df_pubchem['status'] == 'APPROVED']) if not df_pubchem.empty else 0
    curated_data          = load_json_store(CURATED_ADDITIVES_FILE)
    curated_approved      = sum(1 for v in curated_data.values() if v.get("curated_status") == "APPROVED")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("添加物總量", f"{total_additives} 筆")
    col2.metric("PubChem 自動核准", f"{(pubchem_approved_count/total_additives)*100:.1f}%", f"{pubchem_approved_count} 筆")
    col3.metric("JECFA 文獻覆蓋率", f"{(jecfa_matched_count/total_additives)*100:.1f}%", f"{jecfa_matched_count} 筆")
    col4.metric("OpenFoodTox 對照率", f"{(oft_matched_count/total_additives)*100:.1f}%", f"{oft_matched_count} 筆")
    col5.metric("人工審定 APPROVED", f"{curated_approved} 筆")   # 新增：人工審定數量

    st.markdown("---")

    sec_pc, sec_jc, sec_oft, sec_adi, sec_audit = st.tabs(
        ["🧬 PubChem 驗證現況", "🌐 JECFA 文獻地圖現況",
         "🇪🇺 OpenFoodTox 歐盟對照現況", "🧪 ADI 毒理限值統計", "📝 人工審定進度"])

    with sec_pc:
        st.subheader("🧬 PubChem 身份校驗數據")
        if df_pubchem.empty:
            st.info("無 PubChem 對照數據。")
        else:
            stats = df_pubchem['status'].value_counts()
            col_l, col_r = st.columns(2)
            with col_l:
                st.markdown("##### 驗證狀態分佈")
                st.dataframe(pd.DataFrame({
                    "狀態": stats.index,
                    "數量": stats.values,
                    "比例": [f"{(v/total_additives)*100:.2f}%" for v in stats.values]
                }))
            with col_r:
                methods = df_pubchem['match_method'].value_counts()
                st.markdown("##### 匹配管道統計")
                method_mapping = {
                    "direct_name_match": "Direct Name",
                    "local_alias_match": "Local Alias",
                    "gemini_alias_match": "Gemini Alias",
                    "no_match": "No Match"
                }
                mapped_index = [method_mapping.get(x, x) for x in methods.index]
                methods_df = pd.DataFrame({
                    "匹配管道": mapped_index,
                    "數量": methods.values
                })
                chart_methods = alt.Chart(methods_df).mark_bar().encode(
                    x=alt.X("匹配管道:N", axis=alt.Axis(labelAngle=0, title="匹配管道")),
                    y=alt.Y("數量:Q", axis=alt.Axis(title="數量"))
                ).properties(height=300)
                st.altair_chart(chart_methods, use_container_width=True)

    with sec_jc:
        st.subheader("🌐 JECFA 官方直接檢索數據")
        if df_jecfa.empty:
            st.info("無 JECFA 對照數據。")
        else:
            jecfa_status = df_jecfa['status'].value_counts()
            col_l, col_r = st.columns(2)
            with col_l:
                st.dataframe(pd.DataFrame({
                    "狀態": jecfa_status.index,
                    "數量": jecfa_status.values,
                    "比例": [f"{(v/total_additives)*100:.2f}%" for v in jecfa_status.values]
                }))
            with col_r:
                st.metric("有效文獻覆蓋率", f"{(jecfa_matched_count/total_additives)*100:.2f}%",
                          f"{jecfa_matched_count}/{total_additives} 筆")
            status_mapping = {
                "APPROVED": "Approved",
                "PENDING_REVIEW": "Pending",
                "NO_MATCH": "No Match",
                "REJECTED": "Rejected"
            }
            mapped_status_index = [status_mapping.get(x, x) for x in jecfa_status.index]
            jecfa_status_df = pd.DataFrame({
                "狀態": mapped_status_index,
                "數量": jecfa_status.values
            })
            chart_jc = alt.Chart(jecfa_status_df).mark_bar().encode(
                x=alt.X("狀態:N", axis=alt.Axis(labelAngle=0, title="狀態")),
                y=alt.Y("數量:Q", axis=alt.Axis(title="數量"))
            ).properties(height=300)
            st.altair_chart(chart_jc, use_container_width=True)

    with sec_oft:
        st.subheader("🇪🇺 OpenFoodTox (EFSA) 對照數據")
        if df_oft.empty:
            st.info("無 OpenFoodTox 對照數據。")
        else:
            col_l, col_r = st.columns(2)
            with col_l:
                st.metric("對照覆蓋率", f"{(oft_matched_count/total_additives)*100:.2f}%",
                          f"{oft_matched_count}/{total_additives} 筆")
                st.write(f"- 未匹配筆數（待補強）：`{total_additives - oft_matched_count}` 筆")
            with col_r:
                preview_df = df_oft[['record_id', 'name_zh', 'efsa_cas', 'efsa_adi_value', 'similarity_score']].head(10)
                st.dataframe(preview_df)

    with sec_adi:
        st.subheader("🧪 ADI 每日可接受攝取量統計")
        st.markdown("基於 JECFA 官方文獻數據，區分有具體 ADI 限值、無具體限值（不設限）以及未對照成功之分佈。")
        
        has_numeric = 0
        no_numeric = 0
        not_matched = total_additives
        
        try:
            conn = make_db_conn()
            if conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM experiment.jecfa_details WHERE adi_value ~ '[0-9]';")
                has_numeric = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM experiment.jecfa_details WHERE adi_value !~ '[0-9]' OR adi_value IS NULL;")
                no_numeric = cur.fetchone()[0]
                cur.close()
                conn.close()
                not_matched = total_additives - (has_numeric + no_numeric)
        except Exception:
            pass

        ca1, ca2, ca3 = st.columns(3)
        ca1.metric("還沒查 / 未對照成功", f"{not_matched} 筆", f"{(not_matched/total_additives)*100:.1f}%")
        ca2.metric("查過後有具體 ADI 限值", f"{has_numeric} 筆", f"{(has_numeric/total_additives)*100:.1f}%")
        ca3.metric("查過後無具體限值 (不設限/不適用)", f"{no_numeric} 筆", f"{(no_numeric/total_additives)*100:.1f}%")

        st.markdown("---")
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown("##### ADI 分類數據表")
            adi_stats_df = pd.DataFrame({
                "類別": ["還沒查 / 未對照成功 (Not Matched)", "查過後有具體 ADI 限值 (Numeric ADI)", "查過後無具體限值 (No Numeric ADI)"],
                "數量": [not_matched, has_numeric, no_numeric],
                "比例": [f"{(not_matched/total_additives)*100:.2f}%", f"{(has_numeric/total_additives)*100:.2f}%", f"{(no_numeric/total_additives)*100:.2f}%"]
            })
            st.dataframe(adi_stats_df, use_container_width=True)
            
        with col_r:
            st.markdown("##### ADI 比例分佈")
            chart_adi = alt.Chart(adi_stats_df).mark_bar().encode(
                x=alt.X("類別:N", axis=alt.Axis(labelAngle=-15, title="ADI 分類")),
                y=alt.Y("數量:Q", axis=alt.Axis(title="數量")),
                color=alt.Color("類別:N", legend=None)
            ).properties(height=300)
            st.altair_chart(chart_adi, use_container_width=True)
            
        st.markdown("##### 🔍 查過後「無具體限值」品項清單範例")
        no_num_examples = pd.DataFrame()
        try:
            conn = make_db_conn()
            if conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT jd.record_id, a.name_zh, a.name_en, jd.adi_value, jd.comments
                    FROM experiment.jecfa_details jd
                    JOIN additives a ON jd.record_id = a.record_id
                    WHERE jd.adi_value !~ '[0-9]' OR jd.adi_value IS NULL
                    ORDER BY jd.record_id
                    LIMIT 50;
                """)
                rows = cur.fetchall()
                cur.close()
                conn.close()
                no_num_examples = pd.DataFrame(rows, columns=["編號", "中文名稱", "英文名稱", "JECFA 萃取 ADI 結論", "官方備註"])
        except Exception:
            pass
            
        if not no_num_examples.empty:
            st.dataframe(no_num_examples, use_container_width=True)

    with sec_audit:
        st.subheader("📝 人工審定進度")
        if not curated_data:
            st.info("尚無任何人工審定紀錄。")
        else:
            rows = []
            for rid_key, v in curated_data.items():
                rows.append({
                    "record_id":       rid_key,
                    "name_zh":         v.get("name_zh", ""),
                    "curated_status":  v.get("curated_status", ""),
                    "reviewed_by":     v.get("reviewed_by", "（未記錄）"),  # 審核者身份
                    "reviewed_at":     v.get("reviewed_at", v.get("updated_at", "")),
                    "cas_overridden":  "✔️" if v.get("cas_overridden") else "",
                    "ins_overridden":  "✔️" if v.get("ins_overridden") else "",
                    "jecfa_overridden":"✔️" if v.get("jecfa_url_overridden") else "",
                    "adi_overridden":  "✔️" if v.get("adi_overridden") else "",
                    "has_history":     "✔️" if v.get("review_history") else "",  # 是否曾被複審
                })
            audit_df = pd.DataFrame(rows)
            st.dataframe(audit_df, use_container_width=True)

            # 審核者統計
            if "reviewed_by" in audit_df.columns:
                st.markdown("##### 審核者貢獻統計")
                st.dataframe(
                    audit_df.groupby("reviewed_by").agg(
                        審核筆數=("record_id", "count"),
                        APPROVED數=("curated_status", lambda x: (x == "APPROVED").sum()),
                        PENDING數=("curated_status", lambda x: (x == "PENDING_REVIEW").sum())
                    ).reset_index(),
                    use_container_width=True
                )
