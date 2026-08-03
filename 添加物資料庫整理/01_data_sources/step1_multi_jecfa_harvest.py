#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
實驗腳本：JECFA 多文獻來源採集管線（寫入 experiment.additive_source_urls）
說明：本腳本改寫了單一 URL 限制，改為從 JECFA Solr API 中提取所有符合閾值的文件，
      並自動分類為「規格書 (SPECIFICATION)」、「毒理評估 (TOXICOLOGY)」等類型，
      直接寫入 PostgreSQL 的 experiment.additive_source_urls 實驗表。

支援分批處理：
  python3 step1_multi_jecfa_harvest.py --limit 50   # 預設只跑前 50 筆做實驗
"""
import os
import sys
import re
import time
import random
import argparse
import urllib.request
import urllib.parse
import ssl
import difflib
import psycopg2
import csv
import json

# 繞過 SSL 驗證
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 相似度門檻
MIN_PENDING_SIM = 0.50
MIN_APPROVED_SIM = 0.85

def log(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] {msg}", flush=True)

def make_db_conn():
    try:
        return psycopg2.connect(
            host="localhost", port="5432",
            database="product_db", user="postgres", password=os.getenv("DB_PASSWORD", "")
        )
    except Exception as e:
        log(f"❌ 無法連線 PostgreSQL: {e}")
        return None

def clean_chemical_name(name):
    if not name: return ""
    # 支援全形標點轉換
    name = name.replace("（", "(").replace("）", ")").replace("，", ",")
    n = name.lower().strip()
    n = re.sub(r'^(l-|d-|dl-|l\s*\+|d\s*-)', '', n)
    n = re.sub(r'\b(solution|extract|synthetic|liquid|powder|syrup|granular|monohydrate|anhydrous)\b', '', n)
    n = n.split('(')[0].split('-')[0].split(',')[0].strip()
    n = re.sub(r'[^a-z0-9]', '', n)
    return n

def calculate_similarity(s1, s2):
    t1 = s1.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    t2 = s2.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    

        
    c1 = clean_chemical_name(s1)
    c2 = clean_chemical_name(s2)
    if not c1 or not c2: return 0.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()

def classify_doc_type(url):
    """根據 URL 路徑與檔名特徵，更精確地判斷 JECFA 文件類別"""
    url_lower = url.lower()
    if 'jeceval' in url_lower:
        return 'JECFA/EVALUATION_SUMMARY'
    elif 'jecmono' in url_lower:
        if any(term in url_lower for term in ['fnp', 'spec']):
            return 'JECFA/SPECIFICATION'
        else:
            return 'JECFA/TOXICOLOGY_MONOGRAPH'
    elif 'iarc' in url_lower:
        return 'IARC/CARCINOGENICITY'
    return 'JECFA/OTHER'

def query_solr_api_multi(query, target_name):
    """
    查詢 JECFA Solr 並回傳所有合格的候選文件清單，
    包含 URL、標題、相似度與自動分類類型。
    """
    url = f"https://www.inchem.org/lucidworks-solr-api/?q={urllib.parse.quote(query)}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    candidates = []
    delay = 5.0
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=12) as response:
                data = json.loads(response.read().decode('utf-8', 'ignore'))
                docs = data.get('response', {}).get('docs', [])
                
                for d in docs:
                    doc_url = d.get('URL_s', '')
                    doc_title = d.get('Title_s', '')
                    
                    # 篩選 JECFA 相關路徑
                    if '/jecfa/' in doc_url or 'jecmono' in doc_url or 'jeceval' in doc_url:
                        sim = calculate_similarity(target_name, doc_title)
                        doc_type = classify_doc_type(doc_url)
                        
                        # 只要相似度大於等於黃燈門檻就收集
                        if sim >= MIN_PENDING_SIM:
                            candidates.append({
                                'url': doc_url,
                                'title': doc_title,
                                'similarity': round(sim, 4),
                                'doc_type': doc_type
                            })
                return candidates
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"⚠️ [HTTP 429] Solr API 限速中，等待 {delay} 秒...")
                time.sleep(delay)
                delay *= 2
                continue
            break
        except Exception as e:
            time.sleep(1.5)
            
    return candidates

import json

def main():
    parser = argparse.ArgumentParser(description="JECFA 多文獻網址採集實驗管線")
    parser.add_argument('--limit', type=int, default=50, help='處理前幾筆添加物（預設 50 筆）')
    parser.add_argument('--all', action='store_true', help='是否全量處理 804 筆')
    args = parser.parse_args()

    log("=== 啟動步驟 1 (實驗版): JECFA 多網址採集管線 ===")

    # 1. 讀取 identity_audit.csv 作為 CAS 對照
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
            log(f"✅ 載入 {len(cas_map)} 筆 CAS 對照。")
        except Exception as e:
            log(f"⚠️ 讀取 identity_audit.csv 失敗: {e}")

    # 2. 讀取 DB
    conn = make_db_conn()
    if not conn:
        sys.exit(1)
        
    try:
        cur = conn.cursor()
        if args.all:
            cur.execute("SELECT record_id, name_zh, name_en, ins_or_e_number FROM additives ORDER BY record_id;")
        else:
            cur.execute("SELECT record_id, name_zh, name_en, ins_or_e_number FROM additives ORDER BY record_id LIMIT %s;", (args.limit,))
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        log(f"❌ 讀取 additives 失敗: {e}")
        conn.close()
        sys.exit(1)

    log(f"本次計劃處理 {len(rows)} 筆添加物。")

    # 清空本次要處理的 record_id 舊資料，避免重複
    try:
        cur = conn.cursor()
        rids_to_clean = [r[0] for r in rows]
        cur.execute("DELETE FROM experiment.additive_source_urls WHERE record_id = ANY(%s);", (rids_to_clean,))
        conn.commit()
        cur.close()
        log("🧹 已清空舊有實驗對照資料。")
    except Exception as e:
        conn.rollback()
        log(f"⚠️ 清理舊資料失敗: {e}")

    # 3. 逐筆檢索並寫入資料庫


    for idx, (rid, name_zh, name_en, ins_or_e_number) in enumerate(rows, 1):
        if not name_en or not name_en.strip():
            continue
            
        log(f"🚀 [{idx}/{len(rows)}] 採集 {rid} | {name_zh} ({name_en})...")
        
        all_docs = []
        
        # A. 名稱檢索
        name_docs = query_solr_api_multi(name_en.strip(), name_en.strip())
        all_docs.extend(name_docs)
        time.sleep(0.3)
        
        # B. INS 檢索
        if ins_or_e_number:
            clean_ins = ins_or_e_number.replace("INS", "").replace("E", "").strip()
            if clean_ins:
                ins_docs = query_solr_api_multi(clean_ins, name_en.strip())
                all_docs.extend(ins_docs)
                time.sleep(0.3)
                
        # C. CAS 檢索
        cas_num = cas_map.get(rid, "")
        if cas_num:
            cas_docs = query_solr_api_multi(cas_num, name_en.strip())
            all_docs.extend(cas_docs)
            time.sleep(0.3)
            
        # D. 去重（以 URL 為準）
        unique_docs = {}
        for d in all_docs:
            u = d['url']
            if u not in unique_docs or d['similarity'] > unique_docs[u]['similarity']:
                unique_docs[u] = d
                
        # E. 寫入資料庫
        if unique_docs:
            try:
                cur = conn.cursor()
                inserted_count = 0
                for d in unique_docs.values():
                    # 決定該單一文件的狀態
                    status = 'PENDING_REVIEW'
                    if d['similarity'] >= MIN_APPROVED_SIM:
                        status = 'APPROVED'
                    elif d['similarity'] < MIN_PENDING_SIM:
                        status = 'NO_MATCH'
                        
                    cur.execute("""
                        INSERT INTO experiment.additive_source_urls (
                            record_id, source_name, doc_type, url, matched_title, similarity, status
                        ) VALUES (%s, 'JECFA', %s, %s, %s, %s, %s);
                    """, (
                        rid,
                        d['doc_type'],
                        d['url'],
                        d['title'],
                        d['similarity'],
                        status
                    ))
                    inserted_count += 1
                conn.commit()
                cur.close()
                log(f"  ✅ 寫入 {inserted_count} 筆 URL 記錄至 experiment 表。")
            except Exception as e:
                conn.rollback()
                log(f"  ❌ 寫入資料庫失敗: {e}")
        else:
            log(f"  ❌ 查無符合相似度 (>={MIN_PENDING_SIM}) 的文獻。")
            
        time.sleep(random.uniform(1.2, 1.8))

    conn.close()
    log("🎉 實驗性多網址採集完成！請在 Streamlit 中選取前 50 筆添加物，即可看見多 URL 欄位展示！")

if __name__ == '__main__':
    main()
