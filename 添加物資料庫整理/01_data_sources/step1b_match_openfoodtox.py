#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1b：OpenFoodTox 資料對照與 ADI 數據提取（高相似度過濾版）
說明：讀取 PostgreSQL 中的添加物，與本地的 EFSA OpenFoodTox Excel 進行比對。
      比對成功的項目必須滿足：
      1. 正規化字串一致，且字元相似度 >= 0.80。
      2. 或 E 編號一致，且名稱字元相似度 >= 0.80。
"""
import os
import sys
import csv
import re
import difflib
import openpyxl
import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(HERE, 'OFT3.0 export repository.xlsx')
OUT_CSV = os.path.join(HERE, 'harvested_links', 'openfoodtox_matches.csv')
LOG_FILE = OUT_CSV + '.log'

# 設定相似度門檻值（0.0 至 1.0，0.80 代表極度接近）
MIN_SIMILARITY_THRESHOLD = 0.85

os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)

def log(msg):
    import time
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{t_str}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(formatted + '\n')

def clean_cas(cas_str):
    if not cas_str: return ""
    # 提取符合 CAS 格式的數字與連字號
    match = re.search(r'(\d{2,7}-\d{2}-\d)', str(cas_str))
    return match.group(1) if match else ""

def clean_name(name):
    if not name: return ""
    # 支援全形標點轉換
    name = name.replace("（", "(").replace("）", ")").replace("，", ",")
    n = name.lower().strip()
    n = re.sub(r'^(l-|d-|dl-|l\s*\+|d\s*-)', '', n)
    n = re.sub(r'[^a-z0-9]', '', n)
    return n

def calculate_similarity(s1, s2):
    """
    計算兩個字串的字元相似度比率（含陽離子衝突與全形括號清洗）
    """
    if not s1 or not s2: return 0.0
    t1 = s1.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    t2 = s2.replace("（", "(").replace("）", ")").replace("，", ",").lower().strip()
    

        
    return difflib.SequenceMatcher(None, t1, t2).ratio()

def extract_core_num(s):
    if not s: return None
    match = re.search(r'\b(E|INS)\s*(\d{3,4}[a-z]?)\b', s, re.I)
    return match.group(2).lower() if match else None

def main():
    log(f"=== 啟動步驟 1b: OpenFoodTox 高相似度比對 (門檻值: {MIN_SIMILARITY_THRESHOLD}) ===")
    
    if not os.path.exists(XLSX_PATH):
        log(f"❌ 找不到 EFSA OpenFoodTox Excel 檔，請確認路徑: {XLSX_PATH}")
        sys.exit(1)

    # 0. 讀取本機已核對的 identity_audit.csv 以取得精確 CAS 號碼對照
    audit_csv_path = os.path.join(os.path.dirname(HERE), '02_identity_verification', 'identity_audit.csv')
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
            log(f"✅ 成功載入 {len(cas_map)} 筆已驗證之 CAS 號碼。")
        except Exception as e:
            log(f"⚠️ 讀取 identity_audit.csv 失敗: {e}")

    # 1. 連線資料庫讀取添加物
    try:
        conn = psycopg2.connect(
            host="localhost",
            port="5432",
            database="product_db",
            user="postgres",
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        cur.execute("SELECT record_id, name_zh, name_en, aliases, ins_or_e_number FROM additives ORDER BY record_id;")
        db_additives = cur.fetchall()
        cur.close()
        conn.close()
        log(f"資料庫讀取完成，共 {len(db_additives)} 筆添加物。")
    except Exception as e:
        log(f"❌ 無法連線資料庫: {e}")
        sys.exit(1)

    # 2. 建立資料庫檢索索引
    db_by_cleaned_name = {}
    db_by_core_ins = {}
    db_by_cas = {}
    
    for rid, zh, en, aliases, ins in db_additives:
        names = []
        if en: names.append(en)
        if aliases and isinstance(aliases, str):
            names.extend([p.strip() for p in re.split(r'[,;，；|]', aliases) if p.strip()])
            
        for n in names:
            cleaned = clean_name(n)
            if cleaned:
                db_by_cleaned_name[cleaned] = (rid, zh, en, ins)
                
        core_ins = extract_core_num(ins)
        if core_ins:
            db_by_core_ins[core_ins] = (rid, zh, en, ins)
            
        # 建立 CAS 對照索引
        verified_cas = cas_map.get(rid, "")
        cleaned_v_cas = clean_cas(verified_cas)
        if cleaned_v_cas:
            db_by_cas[cleaned_v_cas] = (rid, zh, en, ins)

    # 3. 讀取 EFSA OpenFoodTox Excel 工作表
    log("載入 EFSA OpenFoodTox Excel 工作表...")
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True)
    
    # A. 讀取 REF_SUB (Document UUID -> Reference Substance Details)
    ref_sub_sheet = wb['REF_SUB']
    ref_sub_map = {}
    for row in ref_sub_sheet.iter_rows(min_row=2, values_only=True):
        doc_uuid = row[1]
        ref_name = row[12]
        com_name = row[17]
        cas_inv = row[5]
        cas_num = row[15]
        ec_num = row[18]
        param_code = row[19]
        name_val = row[20]
        
        ref_sub_map[doc_uuid] = {
            'ref_name': ref_name,
            'com_name': com_name,
            'cas': cas_inv or cas_num or '',
            'ec': ec_num or '',
            'param_code': param_code or '',
            'name_val': name_val
        }
    log(f"載入 REF_SUB 參考物質共 {len(ref_sub_map)} 筆。")

    # B. 讀取 SUB (Substance UUID -> Reference Substance UUID)
    sub_sheet = wb['SUB']
    sub_to_ref_map = {}
    for row in sub_sheet.iter_rows(min_row=2, values_only=True):
        sub_uuid = row[1]
        ref_sub_link = row[6]
        if sub_uuid and ref_sub_link:
            sub_to_ref_map[sub_uuid] = ref_sub_link

    # C. 讀取 FLEX_SUM.ToxRefValues
    tox_sheet = wb['FLEX_SUM.ToxRefValues']
    tox_ref_map = {}
    for row in tox_sheet.iter_rows(min_row=2, values_only=True):
        parent_uuid = row[3]
        adi_lower = row[6]
        adi_unit = row[7]
        no_allocated = row[16]
        
        if parent_uuid and (adi_lower is not None or no_allocated is not None):
            if parent_uuid not in tox_ref_map:
                tox_ref_map[parent_uuid] = {
                    'adi_value': str(adi_lower) if adi_lower is not None else '',
                    'adi_unit': str(adi_unit) if adi_unit is not None else '',
                    'no_allocated': str(no_allocated) if no_allocated is not None else ''
                }

    # 4. 執行對照與資料提取
    matches = {}
    
    # 遍歷所有載入的 REF_SUB 項目
    for ref_uuid, ref_data in ref_sub_map.items():
        ref_name = ref_data['ref_name']
        com_name = ref_data['com_name']
        name_val = ref_data['name_val']
        ref_cas = clean_cas(ref_data['cas'])
        
        rid = None
        match_method = None
        max_sim = 0.0
        
        # 策略 0：優先以 CAS 號碼比對（最權威，免受相似度門檻限制）
        if ref_cas and ref_cas in db_by_cas:
            rid, zh, en, ins = db_by_cas[ref_cas]
            match_method = 'cas_number_match'
            
            # 計算名稱實際相似度僅供參考
            efsa_names = [n for n in [ref_name, com_name, name_val] if n]
            best_sim = 0.0
            for name in efsa_names:
                sim = calculate_similarity(en, name)
                if sim > best_sim:
                    best_sim = sim
            max_sim = max(best_sim, 0.0001)  # 確保大於 0
            
        # 策略 A：名稱/別名比對（必須滿足字面清洗一致，且原始英文名相似度 >= 門檻值）
        if not rid:
            efsa_names = [n for n in [ref_name, com_name] if n]
            for name in efsa_names:
                cleaned_efsa = clean_name(name)
                if cleaned_efsa in db_by_cleaned_name:
                    temp_rid, zh, en, ins = db_by_cleaned_name[cleaned_efsa]
                    sim = calculate_similarity(en, name)
                    if sim >= MIN_SIMILARITY_THRESHOLD:
                        rid = temp_rid
                        match_method = 'name_synonym_match'
                        max_sim = sim
                        break
                
        # 策略 B：E編號 + 名稱相似性雙重比對（必須滿足相似度 >= 門檻值）
        if not rid and name_val and isinstance(name_val, str):
            parts = [p.strip() for p in name_val.split('|')]
            for p in parts:
                core_efsa = extract_core_num(p)
                if core_efsa and core_efsa in db_by_core_ins:
                    temp_rid, zh, en, ins = db_by_core_ins[core_efsa]
                    
                    # 尋找 EFSA 中相似度最高的英文名稱
                    efsa_names_all = [str(n) for n in [ref_name, name_val] if n]
                    best_sim = 0.0
                    for efsa_n in efsa_names_all:
                        sim = calculate_similarity(en, efsa_n)
                        if sim > best_sim:
                            best_sim = sim
                            
                    # 只有相似度 >= 門檻值才保留
                    if best_sim >= MIN_SIMILARITY_THRESHOLD:
                        rid = temp_rid
                        match_method = 'e_number_and_name_match'
                        max_sim = best_sim
                        break

        # 若比對成功且相似度達標，提取 ADI 數值
        if rid:
            adi_data = {'adi_value': '', 'adi_unit': '', 'no_allocated': ''}
            for sub_uuid, ref_link in sub_to_ref_map.items():
                if ref_link == ref_uuid:
                    if sub_uuid in tox_ref_map:
                        adi_data = tox_ref_map[sub_uuid]
                        break
            
            # 若已有相同 record_id 被匹配，則保留相似度更高或 ADI 非空者
            if rid in matches:
                if max_sim > matches[rid]['similarity_score']:
                    matches[rid].update({
                        'efsa_name': ref_name or com_name or '',
                        'efsa_cas': ref_data['cas'],
                        'efsa_ec': ref_data['ec'],
                        'efsa_param_code': ref_data['param_code'],
                        'efsa_adi_value': adi_data['adi_value'] or matches[rid]['efsa_adi_value'],
                        'efsa_adi_unit': adi_data['adi_unit'] or matches[rid]['efsa_adi_unit'],
                        'efsa_no_allocated': adi_data['no_allocated'] or matches[rid]['efsa_no_allocated'],
                        'similarity_score': round(max_sim, 4)
                    })
            else:
                matches[rid] = {
                    'record_id': rid,
                    'name_zh': zh,
                    'name_en': en,
                    'ins_or_e_number': ins or '',
                    'efsa_name': ref_name or com_name or '',
                    'efsa_cas': ref_data['cas'],
                    'efsa_ec': ref_data['ec'],
                    'efsa_param_code': ref_data['param_code'],
                    'efsa_adi_value': adi_data['adi_value'],
                    'efsa_adi_unit': adi_data['adi_unit'],
                    'efsa_no_allocated': adi_data['no_allocated'],
                    'similarity_score': round(max_sim, 4),
                    'match_method': match_method
                }

    # 5. 寫入結果 CSV
    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as f:
        fields = ['record_id', 'name_zh', 'name_en', 'ins_or_e_number', 'efsa_name', 'efsa_cas', 'efsa_ec', 'efsa_param_code', 'efsa_adi_value', 'efsa_adi_unit', 'efsa_no_allocated', 'similarity_score', 'match_method']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rid in sorted(matches.keys()):
            writer.writerow(matches[rid])
            
    log("🎉 對照與數據提取完成（相似度過濾版）！")
    log(f"成功匹配筆數: {len(matches)} / {len(db_additives)} ({(len(matches)/len(db_additives))*100:.2f}%)")
    log(f"對照結果已儲存至: {OUT_CSV}")

if __name__ == '__main__':
    main()
