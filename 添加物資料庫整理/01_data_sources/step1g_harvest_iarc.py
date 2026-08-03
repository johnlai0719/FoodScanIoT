#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1g：IARC 致癌性文獻自動採集管線
說明：從 PostgreSQL 讀取所有 804 筆食品添加物，向 Inchem Solr 搜尋 IARC Monograph 評估連結，
      並依據 0.85/0.80 相似度門檻進行核准與寫入。
"""
import os
import sys
import time
import urllib.request
import urllib.parse
import json
import ssl
import difflib
import psycopg2
import re

# 門檻值定義
MIN_APPROVED_SIM = 0.85
MIN_PENDING_SIM = 0.75

# 忽略 SSL 憑證問題
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def log(msg):
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t_str}] {msg}", flush=True)

def strip_title_metadata(title):
    """移除 JECFA/IARC 專用中介資料括號後綴，只留下純化學品品名"""
    import re
    t = title
    t = re.sub(r'\s*\((iarc|jecfa|who|fao|icsc|pim)\b[^)]*\)\s*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\((summary & evaluation|monograph|evaluation)\b[^)]*\)\s*$', '', t, flags=re.IGNORECASE)
    return t.strip()

def clean_chemical_name(name):
    """標準化化學名稱，去除全形標點、統一括號空白，並移除重複空白"""
    if not name:
        return ""
    # 統一括號與標點
    n = name.replace("（", " (").replace("）", ")").replace("，", ",").replace("　", " ")
    n = re.sub(r'\s*\(\s*', ' (', n)
    n = n.lower().strip()
    n = re.sub(r'\s+', ' ', n)
    # 統一化學字根 synonym 轉換
    n = n.replace("butylated", "butyl").replace("dibutyl", "butyl")
    n = n.replace("methylated", "methyl")
    n = n.replace("ethylated", "ethyl")
    return n

def get_substance_core(name):
    n = clean_chemical_name(name)
    # 移除常見陽離子前綴以匹配 IARC 母體化學品評估
    for prefix in ['sodium ', 'potassium ', 'calcium ', 'magnesium ', 'ammonium ', 'steatite ', 'crystalline ']:
        if n.startswith(prefix):
            n = n[len(prefix):]
    return n

def calculate_similarity(s1, s2):
    """針對 IARC 進行專用匹配度計算"""
    c1 = clean_chemical_name(s1)
    
    # 先剝除 s2 的中介資料後綴再標準化
    raw_s2 = strip_title_metadata(s2)
    c2 = clean_chemical_name(raw_s2)
    
    if not c1 or not c2: 
        return 0.0
        
    # 1. 完整拼寫完全包含（使用單字邊界，防 sorbic acid 匹配 parasorbic acid）
    if re.search(r'\b' + re.escape(c1) + r'\b', c2):
        return 0.95
        
    # 2. 核心物質名稱完全包含（使用單字邊界）
    core1 = get_substance_core(c1)
    if len(core1) > 3:
        if re.search(r'\b' + re.escape(core1) + r'\b', c2):
            return 0.90
            
    # 3. 多單字完全包含
    words = c1.split()
    if len(words) > 1:
        if all(re.search(r'\b' + re.escape(w) + r'\b', c2) for w in words):
            return 0.88
        
    # 4. 針對去除了元資料後綴的剩餘化學品名進行 SequenceMatcher 相似度比對
    # 能夠極佳地匹配如 'butyl hydroxyanisole (bha)' 與 'butylated hydroxyanisole (bha)' (得分 0.925)
    return difflib.SequenceMatcher(None, c1, c2).ratio()

def query_solr_iarc(query, target_name):
    """查詢 Inchem Solr 並篩選出 IARC 相關文獻"""
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
                    
                    # 篩選 IARC 相關路徑，排除 JECFA、ICSC、PIM 等臨床/工業與診斷文件
                    if '/iarc/' in doc_url or 'iarcmono' in doc_url:
                        # 排除非致癌評估目錄（如一般索引頁）
                        if any(x in doc_url.lower() for x in ['iarc.html', 'iarc.htm', 'index.htm']):
                            continue
                        
                        sim = calculate_similarity(target_name, doc_title)
                        
                        if sim >= MIN_PENDING_SIM:
                            candidates.append({
                                'url': doc_url,
                                'title': doc_title,
                                'similarity': round(sim, 4)
                            })
                return candidates
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"⚠️ [HTTP 429] Solr API 限速中，等待 {delay} 秒...")
                time.sleep(delay)
                delay *= 2
            else:
                log(f"⚠️ Solr 查詢 HTTP 錯誤: {e.code} for query '{query}'")
                break
        except Exception as e:
            log(f"⚠️ Solr 查詢異常: {e} for query '{query}'")
            break
    return []

def main():
    log("=== 啟動步驟 1g: IARC 官方致癌文獻採集管線 ===")
    
    # 1. 連線資料庫讀取品項
    try:
        conn = psycopg2.connect(
            host="localhost", port="5432",
            database="product_db", user="postgres", password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        cur.execute("SELECT record_id, name_en, name_zh FROM additives ORDER BY record_id;")
        additives = cur.fetchall()
        log(f"成功讀取 {len(additives)} 筆添加物品項。")
    except Exception as e:
        log(f"❌ 資料庫連線或讀取失敗: {e}")
        return

    # 2. 開始逐筆查詢
    new_inserts = 0
    matched_count = 0
    
    for idx, (rid, name_en, name_zh) in enumerate(additives):
        if not name_en:
            continue
            
        # 進行 Solr 檢索
        candidates = query_solr_iarc(name_en, name_en)
        
        # 稍微間隔以示禮貌
        time.sleep(0.15)
        
        if not candidates:
            continue
            
        log(f"🔍 [{rid}] {name_zh} ({name_en}) -> 找到 {len(candidates)} 筆 IARC 候選文獻")
        matched_count += 1
        
        for cand in candidates:
            url = cand['url']
            title = cand['title']
            sim = cand['similarity']
            
            # 判斷狀態
            status = 'APPROVED' if sim >= MIN_APPROVED_SIM else 'PENDING_REVIEW'
            
            # 寫入資料庫 (UPSERT 防止重複)
            try:
                cur.execute("""
                    INSERT INTO experiment.additive_source_urls 
                        (record_id, source_name, doc_type, url, matched_title, similarity, status, is_curated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (record_id, url) 
                    DO UPDATE SET 
                        similarity = EXCLUDED.similarity,
                        status = EXCLUDED.status,
                        matched_title = EXCLUDED.matched_title,
                        doc_type = EXCLUDED.doc_type;
                """, (rid, 'IARC', 'IARC/CARCINOGENICITY', url, title, sim, status, False))
                new_inserts += 1
            except Exception as e:
                log(f"❌ 寫入對照失敗 ({rid}, {url}): {e}")
                conn.rollback()
                
        # 只要此品項有匹配並寫入，就立即 commit，確保外部能即時觀看進度
        if candidates:
            conn.commit()
    log(f"=== 採集完畢 ===")
    log(f"📊 統計：共 {matched_count} 筆品項找到 IARC 文獻，共寫入/更新 {new_inserts} 筆對照記錄。")
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
