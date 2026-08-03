#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1d：JECFA 毒理與 ADI 詳細數據採集
說明：讀取 experiment.additive_source_urls 中的 JECFA/TOXICOLOGY (jeceval) 連結，
      抓取網頁內容，並使用純 Python 邏輯精確解析出 INS、ADI、功能分類、最新評估年份及備註。
      結果將寫入 PostgreSQL 的 experiment.jecfa_details 表。

使用方式：
  python3 step1d_harvest_jecfa_details.py --limit 10   # 先採集前 10 筆測試
  python3 step1d_harvest_jecfa_details.py --all        # 全量採集
"""
import os
import sys
import re
import time
import random
import argparse
import urllib.request
import ssl
import psycopg2

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

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

def setup_db_table(conn):
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS experiment.jecfa_details (
                record_id           VARCHAR(20) PRIMARY KEY,
                ins_number          VARCHAR(30),
                adi_value           VARCHAR(255),
                functional_class    TEXT,
                latest_evaluation   VARCHAR(30),
                comments            TEXT,
                specifications      TEXT,
                tox_monograph       TEXT,
                fetched_at          DATE DEFAULT CURRENT_DATE
            );
        """)
        conn.commit()
        cur.close()
        log("✅ experiment.jecfa_details 表結構建立/確認成功。")
    except Exception as e:
        conn.rollback()
        log(f"❌ 建立資料表失敗: {e}")
        sys.exit(1)

def parse_jeceval(html):
    """精確解析 JECFA Evaluations HTML 頁面內容"""
    # 移除 HTML 標記以取得純文字，並保留必要間隔
    plain = re.sub(r'<[^<]+?>', ' ', html)
    plain = plain.replace('&nbsp;', ' ').replace('&amp;', '&')
    lines = [l.strip() for l in plain.split('\n') if l.strip()]
    
    data = {}
    keys = {
        'INS:': 'ins_number',
        'Chemical names:': 'chemical_names',
        'Functional class:': 'functional_class',
        'Latest evaluation:': 'latest_evaluation',
        'ADI:': 'adi_value',
        'Comments:': 'comments',
        'Specifications:': 'specifications',
        'Tox monograph:': 'tox_monograph'
    }
    
    for i, line in enumerate(lines):
        if line in keys:
            key_name = keys[line]
            if i + 1 < len(lines):
                # 清洗連續空白字元
                val = re.sub(r'\s+', ' ', lines[i+1]).strip()
                data[key_name] = val
    return data

def fetch_page(url):
    """下載 JECFA 網頁內容"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    delay = 5.0
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=12) as response:
                return response.read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"  ⚠️ [HTTP 429] 請求頻繁，等待 {delay} 秒後重試...")
                time.sleep(delay)
                delay *= 2
                continue
            log(f"  ❌ HTTP 錯誤 {e.code} for URL {url}")
            break
        except Exception as e:
            log(f"  ⚠️ 異常 (嘗試 {attempt+1}/3): {e}")
            time.sleep(2.0)
    return ""

def main():
    parser = argparse.ArgumentParser(description="JECFA 評估頁面詳情採集")
    parser.add_argument('--limit', type=int, default=10, help='處理筆數上限（預設 10 筆測試）')
    parser.add_argument('--all', action='store_true', help='全量處理')
    args = parser.parse_args()

    log("=== 啟動步驟 1d: JECFA 毒理與 ADI 詳細數據採集管線 ===")

    conn = make_db_conn()
    if not conn:
        sys.exit(1)

    setup_db_table(conn)

    # 1. 讀取 JECFA/TOXICOLOGY 網址
    try:
        cur = conn.cursor()
        query = """
            SELECT record_id, url
            FROM experiment.additive_source_urls
            WHERE doc_type = 'JECFA/EVALUATION_SUMMARY' AND url != '' AND status = 'APPROVED'
              AND record_id NOT IN (SELECT record_id FROM experiment.jecfa_details)
            ORDER BY record_id;
        """
        cur.execute(query)
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        log(f"❌ 讀取 experiment.additive_source_urls 失敗: {e}")
        conn.close()
        sys.exit(1)

    if not rows:
        log("📭 沒有發現任何 JECFA/TOXICOLOGY 網址，請先執行 step1_multi_jecfa_harvest.py。")
        conn.close()
        sys.exit(0)

    # 2. 限制處理筆數
    total = len(rows)
    limit = total if args.all else args.limit
    batch = rows[:limit]
    log(f"共發現 {total} 筆毒理網址，本次處理前 {len(batch)} 筆。")

    # 3. 循環抓取並解析
    success_count = 0
    for idx, (rid, url) in enumerate(batch, 1):
        log(f"🚀 [{idx}/{len(batch)}] 抓取 {rid} ➔ {url} ...")
        
        html = fetch_page(url)
        if not html:
            log("  ❌ 抓取失敗")
            continue
            
        data = parse_jeceval(html)
        if not data:
            log("  ❌ 解析失敗 (未找到關鍵屬性表格)")
            continue

        # 寫入資料庫 (UPSERT)
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO experiment.jecfa_details (
                    record_id, ins_number, adi_value, functional_class,
                    latest_evaluation, comments, specifications, tox_monograph, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE)
                ON CONFLICT (record_id) DO UPDATE SET
                    ins_number        = EXCLUDED.ins_number,
                    adi_value         = EXCLUDED.adi_value,
                    functional_class  = EXCLUDED.functional_class,
                    latest_evaluation = EXCLUDED.latest_evaluation,
                    comments          = EXCLUDED.comments,
                    specifications    = EXCLUDED.specifications,
                    tox_monograph     = EXCLUDED.tox_monograph,
                    fetched_at        = CURRENT_DATE;
            """, (
                rid,
                data.get('ins_number'),
                data.get('adi_value'),
                data.get('functional_class'),
                data.get('latest_evaluation'),
                data.get('comments'),
                data.get('specifications'),
                data.get('tox_monograph')
            ))
            conn.commit()
            cur.close()
            log(f"  ✅ 解析成功：INS={data.get('ins_number')} | ADI={data.get('adi_value')}")
            success_count += 1
        except Exception as e:
            conn.rollback()
            log(f"  ❌ 寫入資料庫失敗: {e}")
            
        time.sleep(random.uniform(1.2, 1.8))

    conn.close()
    log(f"🎉 採集完成！成功解析並寫入 {success_count} 筆 JECFA 毒理詳情至 experiment.jecfa_details 表。")

if __name__ == '__main__':
    main()
