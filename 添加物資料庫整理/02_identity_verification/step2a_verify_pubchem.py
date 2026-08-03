#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 2a：PubChem 全量身份驗證與 CAS 提取腳本（零 LLM 費用版）
說明：對資料庫中全量 804 筆添加物，依以下階層進行 PubChem 檢索：
      第一層：原始英文品名直接檢索（PubChem REST API，免費）
      第二層：讀取本地 tfda_aliases_map.json 既有別名逐一檢索（零費用）
      第三層：歷史 CID（已割捨，不使用）

注意：本腳本不呼叫任何 LLM / Gemini API。
      別名生成請執行獨立腳本 step2b_generate_aliases.py（費用可控）。
"""
import os
import sys
import csv
import json
import re
import time
import random
import urllib.request
import urllib.parse
import ssl
import difflib
from pathlib import Path
import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, 'identity_audit.csv')
LOG_FILE = OUT_CSV + '.log'

# 相似度閾值
MIN_SIMILARITY = 0.85

# 確保目錄存在
os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)

# 繞過 SSL 驗證
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 欄位定義
FIELDS = [
    'record_id', 'name_zh', 'name_en', 'ins_or_e_number', 'resolved_cid', 
    'legacy_cid', 'pubchem_title', 'cas_candidates', 'selected_cas', 
    'similarity_score', 'match_method', 'matched_name', 'status', 'notes'
]

# 正則表達式匹配 CAS 格式 (e.g., 7757-81-5)
CAS_PATTERN = re.compile(r'^\d{2,7}-\d{2}-\d$')

# 正則表達式從舊連結中提取 PubChem CID
CID_PATTERN = re.compile(r'pubchem\.ncbi\.nlm\.nih.gov/compound/(\d+)', re.I)

# 本地別名對照表路徑與載入 (防重複呼叫 API)
ALIASES_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '01_data_sources', 'harvested_links', 'tfda_aliases_map.json')
aliases_map = {}
if os.path.exists(ALIASES_JSON):
    try:
        with open(ALIASES_JSON, 'r', encoding='utf-8') as f:
            aliases_map = json.load(f)
        print(f"✅ 成功載入 {len(aliases_map)} 筆本地預生成別名對照資料。")
    except Exception as e:
        print(f"⚠️ 載入本地別名對照檔失敗: {e}")


def log(msg):
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{t_str}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(formatted + '\n')

def clean_name(name):
    if not name: return ""
    # 支援全形標點轉換
    name = name.replace("（", "(").replace("）", ")").replace("，", ",")
    n = name.lower().strip()
    n = re.sub(r'^(l-|d-|dl-|l\s*\+|d\s*-)', '', n)
    n = re.sub(r'[^a-z0-9]', '', n)
    return n

def calculate_similarity(s1, s2):
    if not s1 or not s2: return 0.0
    t1 = s1.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    t2 = s2.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    

        
    return difflib.SequenceMatcher(None, t1, t2).ratio()

def get_local_aliases(rid):
    """
    第二層：從本地 tfda_aliases_map.json 讀取既有別名（零 API 費用）。
    若無預生成記錄，直接回傳空清單。
    如需生成新別名，請執行 step2b_generate_aliases.py。
    """
    return aliases_map.get(rid, [])

def query_pubchem_by_name(name):
    """
    透過英文名稱向 PubChem 檢索其屬性 (Title, IUPACName) 並獲取 CID
    """
    encoded_name = urllib.parse.quote(name.strip())
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded_name}/property/Title,IUPACName/JSON"
    headers = {'User-Agent': 'FoodScanIoT-academic/1.0'}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
            props = data.get("PropertyTable", {}).get("Properties", [])
            if props:
                return props[0]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log(f"  ⚠️ 名稱 '{name}' 查詢 HTTP 錯誤 {e.code}")
    except Exception as e:
        log(f"  ⚠️ 名稱 '{name}' 查詢失敗: {e}")
    return None

def query_pubchem_properties_by_cid(cid):
    """
    透過 CID 查詢屬性 (用於備選方案)
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/Title,IUPACName/JSON"
    headers = {'User-Agent': 'FoodScanIoT-academic/1.0'}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
            props = data.get("PropertyTable", {}).get("Properties", [])
            if props:
                return props[0]
    except Exception as e:
        log(f"  ⚠️ CID {cid} 屬性查詢失敗: {e}")
    return None

def query_pubchem_synonyms(cid):
    """
    透過 CID 查詢其同義詞列表
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
    headers = {'User-Agent': 'FoodScanIoT-academic/1.0'}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
            infos = data.get("InformationList", {}).get("Information", [])
            if infos:
                return infos[0].get("Synonym", [])
    except Exception as e:
        pass
    return []

def main():
    log("=== 啟動步驟 2a: PubChem 全量 804 筆對照驗證（階層式標註版） ===")
    
    # 1. 讀取資料庫
    try:
        conn = psycopg2.connect(
            host="localhost",
            port="5432",
            database="product_db",
            user="postgres",
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        cur.execute("SELECT record_id, name_zh, name_en, ins_or_e_number, description_sources FROM additives ORDER BY record_id;")
        db_rows = cur.fetchall()
        cur.close()
        conn.close()
        log(f"成功讀取資料庫，共 {len(db_rows)} 筆添加物記錄。")
    except Exception as e:
        log(f"❌ 無法連線資料庫: {e}")
        sys.exit(1)

    # 2. 載入第一步已匹配的 EFSA OpenFoodTox 結果
    efsa_matches = {}
    efsa_csv_path = os.path.join(HERE, '..', '01_data_sources', 'harvested_links', 'openfoodtox_matches.csv')
    if os.path.exists(efsa_csv_path):
        with open(efsa_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                efsa_matches[row['record_id']] = row
        log(f"載入 EFSA 對照表共 {len(efsa_matches)} 筆匹配記錄。")
    else:
        log("⚠️ 未偵測到 EFSA 對照表，將跳過 EFSA 三方核對步驟。")

    # 3. 開始寫入 CSV
    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        
        processed_count = 0
        
        # 4. 循環處理每筆添加物
        for idx, (rid, zh, en, ins, sources) in enumerate(db_rows, 1):
            log(f"🚀 [{idx}/{len(db_rows)}] 檢索 {rid} | {zh} ({en})...")
            
            # 解析舊 CID (已割捨，設為空字串以防污染)
            legacy_cid = ""
            
            properties = None
            resolved_cid = ""
            match_method = "no_match"
            matched_name = ""
            notes = []
            
            # --- 第一層：原始英文品名直接檢索（修正：切割分號/逗號以防長字串 404） ---
            if en and en.strip() != "":
                parts = [p.strip() for p in re.split(r'[,;，；|]', en) if p.strip()]
                for part in parts:
                    properties = query_pubchem_by_name(part)
                    time.sleep(0.3)
                    if properties:
                        matched_name = part
                        break
                
            if properties:
                resolved_cid = str(properties.get("CID", ""))
                match_method = "direct_name_match"
                matched_name = en
                log(f"  ↳ 第一層：原始名稱檢索成功，CID: {resolved_cid}")
            else:
                # --- 第二層：本地別名對照表（零費用，不呼叫任何 LLM）---
                local_aliases = get_local_aliases(rid)
                if local_aliases:
                    log(f"  ↳ 第一層查無結果，以本地 {len(local_aliases)} 筆預存別名進行第二層查詢...")
                    for alt_name in local_aliases:
                        if alt_name and alt_name.strip():
                            log(f"    ↳ 嘗試本地別名: '{alt_name}'...")
                            properties = query_pubchem_by_name(alt_name)
                            time.sleep(0.3)
                            if properties:
                                resolved_cid = str(properties.get("CID", ""))
                                match_method = "local_alias_match"
                                matched_name = alt_name
                                log(f"    ✅ 第二層：本地別名 '{alt_name}' 檢索成功，CID: {resolved_cid}")
                                notes.append(f"原始名檢索失敗，使用本地別名 '{alt_name}' 成功對齊。")
                                break
                else:
                    log(f"  ↳ 本地無預存別名，若需補強請執行 step2b_generate_aliases.py")
                                
            # --- 第三層：歷史備用舊連結 CID 檢索 (已割捨，跳過) ---
            pass
            
            # 執行同義詞抓取與 CAS 提取
            cas_candidates = []
            pubchem_title = ""
            best_sim = 0.0
            selected_cas = ""
            status = "PENDING_REVIEW"
            
            if resolved_cid:
                pubchem_title = properties.get("Title", "")
                iupac_name = properties.get("IUPACName", "")
                
                synonyms = query_pubchem_synonyms(resolved_cid)
                time.sleep(0.3)
                
                for syn in synonyms:
                    if CAS_PATTERN.match(syn):
                        if syn not in cas_candidates:
                            cas_candidates.append(syn)
                            
                # 計算與原有名稱（分開後各別名）的最佳相似度
                en_parts = [p.strip() for p in re.split(r'[,;，；|]', en) if p.strip()] if en else []
                best_sim = 0.0
                
                for part in en_parts:
                    sim_with_title = calculate_similarity(part, pubchem_title) if pubchem_title else 0.0
                    sim_with_iupac = calculate_similarity(part, iupac_name) if iupac_name else 0.0
                    max_syn_sim = 0.0
                    for syn in synonyms:
                        if not CAS_PATTERN.match(syn) and not syn.isdigit():
                            sim = calculate_similarity(part, syn)
                            if sim > max_syn_sim:
                                max_syn_sim = sim
                    term_best = max(sim_with_title, sim_with_iupac, max_syn_sim)
                    if term_best > best_sim:
                        best_sim = term_best
                
                # 歷史舊 CID 衝突核對 (已割捨，跳過)
                pass
                
                # 判定 CAS 邏輯
                if not cas_candidates:
                    status = "NO_CAS"
                    notes.append("PubChem 中未發現符合格式的 CAS 號。")
                elif len(cas_candidates) == 1:
                    selected_cas = cas_candidates[0]
                    if best_sim >= MIN_SIMILARITY:
                        status = "APPROVED"
                        notes.append("單一 CAS 且名稱相似度達標。")
                    else:
                        status = "PENDING_REVIEW"
                        notes.append(f"相似度 {best_sim:.2f} 低於門檻值。")
                else:
                    notes.append(f"多個 CAS 候選值: {cas_candidates}。")
                    efsa_cas = ""
                    if rid in efsa_matches:
                        efsa_cas = efsa_matches[rid].get('efsa_cas', '')
                        
                    if efsa_cas and efsa_cas in cas_candidates:
                        selected_cas = efsa_cas
                        status = "APPROVED"
                        notes.append(f"三方核對成功，採用 EFSA 登記之 CAS: {efsa_cas}。")
                    else:
                        selected_cas = cas_candidates[0]
                        status = "PENDING_REVIEW"
                        notes.append("無法自動判定，已預選第一筆，待覆核。")
            else:
                status = "NO_PUBCHEM_RECORD"
                notes.append("PubChem 資料庫中無此英文名稱、別名與歷史 CID 紀錄。")
                
            # 寫入 CSV
            writer.writerow({
                'record_id': rid,
                'name_zh': zh,
                'name_en': en,
                'ins_or_e_number': ins,
                'resolved_cid': resolved_cid,
                'legacy_cid': legacy_cid,
                'pubchem_title': pubchem_title,
                'cas_candidates': ' | '.join(cas_candidates),
                'selected_cas': selected_cas,
                'similarity_score': round(best_sim, 4) if resolved_cid else 0.0,
                'match_method': match_method,
                'matched_name': matched_name,
                'status': status,
                'notes': ' '.join(notes)
            })
            f.flush()
            processed_count += 1
            
            # 🔒 限流與伺服器防禦延遲：休眠 0.8 到 1.2 秒
            time.sleep(random.uniform(0.8, 1.2))
            
    log(f"🎉 PubChem 階層式全量對照完成！")
    log(f"共處理: {processed_count} 筆，對照表儲存於: {OUT_CSV}")

if __name__ == '__main__':
    main()
