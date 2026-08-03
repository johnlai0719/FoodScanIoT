#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 2b：Gemini 別名預生成腳本（費用可控、可中斷、可分批）

說明：針對 identity_audit.csv 中「第一層查無結果」的品項（status 為
      NO_PUBCHEM_RECORD），或 tfda_aliases_map.json 中尚無記錄的品項，
      呼叫 Gemini API 生成英文別名，並存入 tfda_aliases_map.json。

      本腳本與 step2a 完全解耦，可獨立執行與控制：
      - 修改 START_IDX / END_IDX 來決定本次只跑哪幾筆（分批費用控制）
      - 所有別名只寫入本地 JSON，不修改資料庫
      - 跑完後重跑 step2a，才會真正用這些別名去 PubChem 補查

使用方式：
  python3 step2b_generate_aliases.py                    # 跑全部尚未生成的
  python3 step2b_generate_aliases.py --start 0 --end 100   # 只跑前 100 筆
"""
import os
import sys
import csv
import json
import time
import argparse
from pathlib import Path

try:
    from google import genai
    from google.genai import types
    from dotenv import load_dotenv
except ImportError:
    print("❌ 缺少依賴套件：請先安裝 google-genai 與 python-dotenv")
    sys.exit(1)

# ── 路徑設定 ──────────────────────────────────────────────
HERE          = os.path.dirname(os.path.abspath(__file__))
PARENT        = os.path.dirname(HERE)
AUDIT_CSV     = os.path.join(HERE, 'identity_audit.csv')
ALIASES_JSON  = os.path.join(PARENT, '01_data_sources', 'harvested_links', 'tfda_aliases_map.json')
LOG_FILE      = os.path.join(HERE, 'step2b_aliases.log')

# ── Gemini 初始化 ─────────────────────────────────────────
server_env = Path("/home/laihome/projects/FoodScanIoT/server/.env")
if server_env.exists():
    load_dotenv(server_env)
client = genai.Client()

# ── 每次呼叫後的禮貌延遲（秒），避免觸發 API rate limit ──
INTER_CALL_DELAY = 2.5

def log(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def load_aliases():
    if os.path.exists(ALIASES_JSON):
        with open(ALIASES_JSON, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_aliases(data):
    with open(ALIASES_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def generate_aliases_via_gemini(name_zh, name_en):
    """
    呼叫 Gemini 生成最多 5 個英文同義詞 / 別名。
    回傳字串清單；若失敗回傳空清單。
    """
    prompt = f"""You are a food chemistry expert. Provide up to 5 alternative English names, IUPAC names, or common synonyms for this food additive:
- Chinese Name: {name_zh}
- Known English Name: {name_en}

Return ONLY a JSON array of strings. Example: ["Synonym A", "Synonym B"]
Do not include markdown, explanation, or code fences."""

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        result = json.loads(response.text)
        if isinstance(result, list):
            return [str(r).strip() for r in result if r]
        return []
    except Exception as e:
        log(f"  ⚠️ Gemini 呼叫失敗: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="Step 2b: Gemini 別名預生成（可分批控制費用）")
    parser.add_argument('--start', type=int, default=0,   help='起始索引（0-indexed）')
    parser.add_argument('--end',   type=int, default=None, help='結束索引（exclusive，預設全量）')
    parser.add_argument('--force', action='store_true',    help='強制覆蓋已有別名的品項')
    args = parser.parse_args()

    log("=== 啟動步驟 2b: Gemini 別名預生成腳本 ===")

    # 1. 讀取 identity_audit.csv，挑出需要生成別名的品項
    if not os.path.exists(AUDIT_CSV):
        log("❌ 找不到 identity_audit.csv，請先執行 step2a_verify_pubchem.py")
        sys.exit(1)

    candidates = []
    with open(AUDIT_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 針對「第一層直查失敗」的品項（亦可依需求改為全量）
            if row['status'] in ('NO_PUBCHEM_RECORD', 'NO_CAS', 'PENDING_REVIEW'):
                candidates.append({
                    'record_id': row['record_id'],
                    'name_zh':   row['name_zh'],
                    'name_en':   row['name_en'],
                })

    # 2. 套用分批範圍
    total = len(candidates)
    start = args.start
    end   = args.end if args.end is not None else total
    batch = candidates[start:end]

    log(f"共 {total} 筆待生成品項，本次處理索引 [{start}, {end})，共 {len(batch)} 筆。")

    # 3. 讀取現有別名
    aliases_map = load_aliases()
    already_count = sum(1 for c in batch if c['record_id'] in aliases_map)
    log(f"其中 {already_count} 筆已有別名（{'將跳過' if not args.force else '將強制覆蓋'}）。")

    # 4. 逐筆生成
    new_count = 0
    for i, item in enumerate(batch, 1):
        rid = item['record_id']
        if rid in aliases_map and not args.force:
            log(f"[{i}/{len(batch)}] ⏭ 跳過 {rid}（已有 {len(aliases_map[rid])} 筆別名）")
            continue

        log(f"[{i}/{len(batch)}] 🔮 生成別名: {rid} | {item['name_zh']} ({item['name_en']})")
        aliases = generate_aliases_via_gemini(item['name_zh'], item['name_en'])

        if aliases:
            aliases_map[rid] = aliases
            save_aliases(aliases_map)   # 每筆立即寫入，確保中斷也不遺失
            log(f"  ✅ 生成 {len(aliases)} 筆別名: {aliases}")
            new_count += 1
        else:
            log(f"  ❌ 生成失敗，記錄空清單以防重複呼叫")
            aliases_map[rid] = []
            save_aliases(aliases_map)

        time.sleep(INTER_CALL_DELAY)

    log(f"🎉 完成！本次新生成 {new_count} 筆，tfda_aliases_map.json 共 {len(aliases_map)} 筆。")
    log(f"   ➡ 請重新執行 step2a_verify_pubchem.py 讓這些別名發揮效果。")

if __name__ == '__main__':
    main()
