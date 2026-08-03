#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1d：全量 804 筆食品添加物英文別名與同義詞批次生成腳本
說明：讀取資料庫中 804 筆添加物，呼叫 Gemini API 預先生成各添加物的化學同義詞與別名，
      輸出為 tfda_aliases_map.json，作為 JECFA 與 PubChem 檢索的公共別名對照庫。
"""
import os
import sys
import json
import time
import random
from pathlib import Path
import psycopg2
from google import genai
from google.genai import types
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, 'harvested_links', 'tfda_aliases_map.json')
LOG_FILE = OUT_JSON + '.log'

# 載入 Gemini API Key
server_env_path = Path("/home/laihome/projects/FoodScanIoT/server/.env")
if server_env_path.exists():
    load_dotenv(server_env_path)
client = genai.Client()

def log(msg):
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{t_str}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(formatted + '\n')

def generate_aliases_for_record(name_zh, name_en):
    """
    呼叫 Gemini 生成 3-5 個英文別名、學名或縮寫
    """
    prompt = f"""
Provide a JSON array of up to 5 alternative English chemical names, common synonyms, or IUPAC names for the food additive:
- Chinese Name: {name_zh}
- Original English Name: {name_en}

Return ONLY a JSON array of strings (e.g., ["Synonym 1", "Synonym 2"]). Do not include markdown code block formatting or other explanations.
"""
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        aliases = json.loads(response.text)
        if isinstance(aliases, list):
            # 去除與原始英文名完全一致的項（忽略大小寫與空格）
            cleaned_aliases = []
            orig_clean = name_en.lower().strip().replace(" ", "")
            for a in aliases:
                if a and a.strip() != "":
                    a_clean = a.lower().strip().replace(" ", "")
                    if a_clean != orig_clean and a not in cleaned_aliases:
                        cleaned_aliases.append(a.strip())
            return cleaned_aliases
    except Exception as e:
        log(f"  ⚠️ Gemini 生成別名失敗 for {name_en}: {e}")
    return []

def main():
    log("=== 啟動步驟 1d: 全量別名預生成任務 ===")
    
    # 1. 讀取現有已生成的對照圖 (支援斷點續傳)
    aliases_map = {}
    if os.path.exists(OUT_JSON):
        try:
            with open(OUT_JSON, 'r', encoding='utf-8') as f:
                aliases_map = json.load(f)
            log(f"ℹ️ 偵測到既存別名檔，已完成 {len(aliases_map)} 筆，將跳過。")
        except Exception as e:
            log(f"⚠️ 讀取既存別名檔失敗，將重新生成: {e}")

    # 2. 連線資料庫獲取添加物名單
    try:
        conn = psycopg2.connect(
            host="localhost",
            port="5432",
            database="product_db",
            user="postgres",
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        cur.execute("SELECT record_id, name_zh, name_en FROM additives ORDER BY record_id;")
        db_rows = cur.fetchall()
        cur.close()
        conn.close()
        log(f"成功讀取資料庫，共 {len(db_rows)} 筆記錄。")
    except Exception as e:
        log(f"❌ 無法連線資料庫: {e}")
        sys.exit(1)

    # 3. 遍歷並呼叫 Gemini 生成
    new_count = 0
    
    try:
        for idx, (rid, zh, en) in enumerate(db_rows, 1):
            if rid in aliases_map:
                continue
                
            log(f"🚀 [{idx}/{len(db_rows)}] 處理 {rid} | {zh} ({en})...")
            
            # 呼叫 Gemini
            aliases = generate_aliases_for_record(zh, en)
            aliases_map[rid] = aliases
            new_count += 1
            
            log(f"  ↳ 生成別名: {aliases}")
            
            # 定期寫入檔案以防意外中斷
            if new_count % 10 == 0:
                with open(OUT_JSON, 'w', encoding='utf-8') as f:
                    json.dump(aliases_map, f, ensure_ascii=False, indent=2)
                log("  💾 定期存檔完成。")
                
            # 🔒 限流休眠：Gemini 2.5 flash RPM 限制，每秒約 1 次
            time.sleep(1.5)
            
    except KeyboardInterrupt:
        log("⚠️ 偵測到使用者手動中斷，保存目前進度...")
    finally:
        # 最終寫入
        with open(OUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(aliases_map, f, ensure_ascii=False, indent=2)
        log(f"🎉 任務完成！共新增別名記錄: {new_count} 筆，總累積: {len(aliases_map)} 筆，已儲存至 {OUT_JSON}")

if __name__ == '__main__':
    main()
