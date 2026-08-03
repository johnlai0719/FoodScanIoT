#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1f：文獻類別重新分類
說明：讀取 PostgreSQL 中的 experiment.additive_source_urls 表，
      依據正確的學術定義（jeceval -> EVALUATION_SUMMARY, jecmono -> TOXICOLOGY_MONOGRAPH）
      重新更新所有對照文獻的 doc_type。
"""
import os
import psycopg2

def log(msg):
    import time
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t_str}] {msg}", flush=True)

def classify_doc_type(url):
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

def main():
    log("=== 啟動步驟 1f: JECFA 文獻類別本地重劃管線 ===")
    
    # 1. 連線資料庫
    try:
        conn = psycopg2.connect(
            host="localhost", port="5432",
            database="product_db", user="postgres", password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        
        # 讀取現有對照記錄
        cur.execute("SELECT id, url, doc_type FROM experiment.additive_source_urls;")
        rows = cur.fetchall()
        log(f"讀取完成，共 {len(rows)} 筆對照記錄。")
        
    except Exception as e:
        log(f"❌ 資料庫連線或讀取失敗: {e}")
        return

    # 2. 分析並判定新類別
    updates = []
    changes = {}
    
    for row_id, url, old_doc_type in rows:
        new_doc_type = classify_doc_type(url)
        if new_doc_type != old_doc_type:
            updates.append((new_doc_type, row_id))
            change_key = f"{old_doc_type} ➔ {new_doc_type}"
            changes[change_key] = changes.get(change_key, 0) + 1

    # 3. 寫入更新
    if updates:
        try:
            log("開始更新資料庫文獻類別...")
            cur.executemany("""
                UPDATE experiment.additive_source_urls
                SET doc_type = %s
                WHERE id = %s;
            """, updates)
            conn.commit()
            log(f"✅ 更新成功！共更新 {len(updates)} 筆類別。")
            log("📊 變更統計：")
            for k, v in sorted(changes.items()):
                log(f"  - {k}: {v} 筆")
        except Exception as e:
            conn.rollback()
            log(f"❌ 資料庫更新失敗: {e}")
    else:
        log("無任何類別需要變更。")

    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
