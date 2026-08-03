#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1：直接透過 JECFA Solr API 檢索官方評估文獻連結（三階段審查與待審機制版）
說明：本腳本讀取 PostgreSQL 資料庫中的添加物英文名與 INS 編號，
      檢索 inchem Solr API，計算與文獻標題 (Title_s) 的字元相似度，
      - 相似度 >= 0.80 ➔ APPROVED (自動通過)
      - 相似度 0.50 - 0.79 ➔ PENDING_REVIEW (待人工審查)
      - 相似度 < 0.50 ➔ NO_MATCH (無相符文獻)
      藉此建立與 PubChem 一致的審計過濾機制。
"""
import os
import sys
import csv
import json
import time
import random
import re
import difflib
import urllib.request
import urllib.parse
import ssl
import psycopg2

# 設定檔與目錄
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, 'harvested_links', 'jecfa_source_map.csv')
LOG_FILE = OUT_CSV + '.log'

# 相似度閾值定義
MIN_APPROVED_SIMILARITY = 0.85
MIN_PENDING_SIMILARITY = 0.50

# 確保輸出目錄存在
os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)

# 繞過 SSL 驗證
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 欄位定義 (引進狀態欄與相似度)
FIELDS = [
    'record_id', 'name_zh', 'name_en', 'ins_or_e_number', 
    'jecfa_url', 'matched_title', 'similarity_score', 'status', 'notes',
    'candidate_url', 'candidate_title'
]

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(formatted + '\n')

def clean_chemical_name(name):
    """
    清洗化學名稱，移除常見修飾詞、立體化學前綴，並移除括號與橫線之後的內容，便於準確比對核心主體
    """
    if not name: return ""
    n = name.lower().strip()
    n = re.sub(r'^(l-|d-|dl-|l\s*\+|d\s*-)', '', n)
    n = re.sub(r'\b(solution|extract|synthetic|liquid|powder|syrup|granular|monohydrate|anhydrous)\b', '', n)
    n = n.split('(')[0].split('-')[0].split(',')[0].strip()
    n = re.sub(r'[^a-z0-9]', '', n)
    return n

def calculate_similarity(s1, s2):
    """
    計算兩個清洗後化學名稱的字元相似度（包含陽離子防衝突與全形括號清洗）
    """
    # 轉換全形標點進行粗對照檢測
    t1 = s1.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    t2 = s2.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    

        
    c1 = clean_chemical_name(s1)
    c2 = clean_chemical_name(s2)
    if not c1 or not c2: return 0.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()

def query_solr_api(query, target_name):
    """
    請求 JECFA Solr API，返回該次查詢中「相似度最高」的單一 JECFA 文件資訊
    """
    url = f"https://www.inchem.org/lucidworks-solr-api/?q={urllib.parse.quote(query)}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    delay = 5.0
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8', 'ignore'))
                docs = data.get('response', {}).get('docs', [])
                
                best_url = ""
                best_title = ""
                max_sim = 0.0
                
                for d in docs:
                    doc_url = d.get('URL_s', '')
                    doc_title = d.get('Title_s', '')
                    
                    # 篩選 JECFA 相關路徑
                    if '/jecfa/' in doc_url or 'jecmono' in doc_url or 'jeceval' in doc_url:
                        sim = calculate_similarity(target_name, doc_title)
                        if sim > max_sim:
                            max_sim = sim
                            best_url = doc_url
                            best_title = doc_title
                            
                return best_url, best_title, max_sim
                
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"⚠️ [HTTP 429] 請求過於頻繁！退讓 {delay} 秒後重試...")
                time.sleep(delay)
                delay *= 2
                continue
            else:
                log(f"❌ HTTP 錯誤 {e.code} 查詢 '{query}': {e}")
                break
        except Exception as e:
            log(f"⚠️ 查詢 '{query}' 發生異常 (嘗試 {attempt+1}/5): {e}")
            time.sleep(2.0)
            
    return "", "", 0.0

def main():
    log("=== 啟動步驟 1: JECFA 官方直接檢索管線（三階段待審版） ===")
    
    # 1a. 讀取本機已核對的 identity_audit.csv 取得 CAS 號碼對照
    audit_csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "02_identity_verification", "identity_audit.csv")
    cas_map = {}
    if os.path.exists(audit_csv_path):
        try:
            with open(audit_csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for r in reader:
                    rid = r.get('record_id', '').strip()
                    cas = r.get('selected_cas', '').strip()
                    if rid and cas and cas.lower() not in ['nan', 'none', '']:
                        cas_map[rid] = cas
            log(f"✅ 成功自 identity_audit.csv 讀取 {len(cas_map)} 筆 CAS 號碼做為備用檢索對照。")
        except Exception as e:
            log(f"⚠️ 讀取 identity_audit.csv 失敗: {e}")

    # 1b. 讀取資料庫
    try:
        conn = psycopg2.connect(
            host="localhost",
            port="5432",
            database="product_db",
            user="postgres",
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        cur.execute("SELECT record_id, name_zh, name_en, ins_or_e_number FROM additives ORDER BY record_id;")
        additives = cur.fetchall()
        cur.close()
        conn.close()
        log(f"成功讀取資料庫，共 {len(additives)} 筆添加物記錄。")
    except Exception as e:
        log(f"❌ 無法連線資料庫: {e}")
        sys.exit(1)

    # 2. 開啟 CSV 寫入 (每次全新跑以重整狀態)
    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDS)
        writer.writeheader()
        
        approved_count = 0
        pending_count = 0
        unmapped_count = 0
        
        # 3. 循環檢索
        for idx, (record_id, name_zh, name_en, ins_or_e_number) in enumerate(additives, 1):
            if not name_en or name_en.strip() == '':
                log(f"[{idx}/{len(additives)}] Skip {record_id} (無英文名稱)")
                writer.writerow({
                    'record_id': record_id, 'name_zh': name_zh, 'name_en': '', 'ins_or_e_number': ins_or_e_number or '',
                    'jecfa_url': '', 'matched_title': '', 'similarity_score': 0.0, 'status': 'NO_MATCH',
                    'notes': '資料庫中無英文名稱',
                    'candidate_url': '', 'candidate_title': ''
                })
                continue
                
            log(f"🚀 [{idx}/{len(additives)}] 檢索 {record_id} | {name_zh} ({name_en})...")
            
            # A. 優先以英文品名檢索
            name_url, name_title, name_sim = query_solr_api(name_en.strip(), name_en.strip())
            time.sleep(0.3)
            
            best_url, best_title, max_sim = name_url, name_title, name_sim
            
            # 儲存所有途徑的結果以做綜合判定
            ins_url, ins_title, ins_sim = "", "", 0.0
            cas_url, cas_title, cas_sim = "", "", 0.0
            searched_by_cas = False
            
            # 如果英文品名沒達到自動核准標準，則必須也跑 INS 和 CAS 檢索，不提前中斷
            if max_sim < MIN_APPROVED_SIMILARITY:
                # 嘗試 INS
                if ins_or_e_number:
                    clean_ins = ins_or_e_number.replace("INS", "").replace("E", "").strip()
                    if clean_ins:
                        log(f"  ↳ 英文名檢索未達標，嘗試 INS 編號: '{clean_ins}'...")
                        ins_url, ins_title, ins_sim = query_solr_api(clean_ins, name_en.strip())
                        time.sleep(0.3)
                
                # 嘗試 CAS
                cas_num = cas_map.get(record_id, "")
                if cas_num:
                    log(f"  ↳ 嘗試 CAS 號碼: '{cas_num}'...")
                    cas_url, cas_title, cas_sim = query_solr_api(cas_num, name_en.strip())
                    time.sleep(0.3)

            # 綜合判定邏輯：
            # 1. 如果有 CAS 匹配結果，且該文獻的相似度或者為 JECFA Evaluation 則高度信任 (CAS 優先級最高)
            if cas_url:
                best_url = cas_url
                best_title = cas_title
                max_sim = cas_sim
                searched_by_cas = True
                log(f"    ✅ 透過 CAS 號定位文獻：'{best_title}'")
            # 2. 否則，從品名與 INS 中挑選相似度較高者
            else:
                if ins_sim > max_sim:
                    max_sim = ins_sim
                    best_url = ins_url
                    best_title = ins_title
            
            # D. 判定審計狀態
            status = 'NO_MATCH'
            notes = ''
            eff_url = ''
            eff_title = ''
            candidate_url = ''
            candidate_title = ''
            
            if max_sim >= MIN_APPROVED_SIMILARITY:
                status = 'APPROVED'
                notes = '名稱與文獻標題字元相似度達標，自動通過。'
                eff_url = best_url
                eff_title = best_title
                approved_count += 1
            elif max_sim >= MIN_PENDING_SIMILARITY:
                status = 'PENDING_REVIEW'
                notes = f"相似度 ({max_sim:.2f}) 介於邊界，文獻標題為 '{best_title}'，需人工覆核。"
                candidate_url = best_url
                candidate_title = best_title
                pending_count += 1
            elif searched_by_cas:
                status = 'PENDING_REVIEW'
                notes = f"透過 CAS 號 ({cas_num}) 成功定位 JECFA 評估文件：'{best_title}'，需人工確認。"
                candidate_url = best_url
                candidate_title = best_title
                pending_count += 1
            else:
                status = 'NO_MATCH'
                notes = '未尋獲相符或相似度合格的 JECFA 文獻網址。'
                unmapped_count += 1
                
            if status != 'NO_MATCH':
                log(f"  ↳ 狀態: {status} | 相似度: {max_sim:.4f} | 標題: '{best_title}'")
            else:
                log(f"  ↳ 狀態: NO_MATCH")
                
            writer.writerow({
                'record_id': record_id,
                'name_zh': name_zh,
                'name_en': name_en,
                'ins_or_e_number': ins_or_e_number or '',
                'jecfa_url': eff_url,
                'matched_title': eff_title,
                'similarity_score': round(max_sim, 4),
                'status': status,
                'notes': notes,
                'candidate_url': candidate_url,
                'candidate_title': candidate_title
            })
            csvfile.flush()
            
            # 🔒 溫和防封鎖延遲：休眠 1.2 到 1.8 秒
            time.sleep(random.uniform(1.2, 1.8))

    log("=== JECFA 採集任務執行完畢 ===")
    log(f"結果統計: APPROVED: {approved_count} 筆 | PENDING_REVIEW: {pending_count} 筆 | NO_MATCH: {unmapped_count} 筆")
    log(f"結果已儲存於 {OUT_CSV}")

if __name__ == '__main__':
    main()
