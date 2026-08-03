#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 4：將驗證完畢的化學身份與 JECFA 毒理上限合併升格至 staging 審核表
說明：讀取 identity_audit.csv 中的已驗證 CAS 與 JECFA 萃取結論（experiment.jecfa_details），
      並依據雙 APPROVED 判定策略將其 UPSERT 寫入 staging.additives_curated。
"""
import os
import re
import csv
import psycopg2

def log(msg):
    import time
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t_str}] {msg}", flush=True)

def parse_numeric_adi(adi_str):
    """
    從 JECFA ADI 字串中解析出數值 limit 與單位
    例如: "0-25 mg/kg bw" -> (25.0, "mg/kg bw")
          "NOT SPECIFIED" -> (None, "NOT SPECIFIED")
    """
    if not adi_str:
        return None, None
    adi_clean = adi_str.lower().strip()
    if 'not specified' in adi_clean or 'not limited' in adi_clean or 'no adi' in adi_clean or 'not limited' in adi_clean:
        return None, 'NOT SPECIFIED'
    
    # 搜尋 0-25 或 0-0.7 格式
    m = re.search(r'(?:0\s*-\s*)?([0-9.]+)\s*(mg/kg|g/kg)', adi_clean)
    if m:
        val = float(m.group(1))
        unit = m.group(2) + " bw"
        return val, unit
        
    # 一般數字搜尋
    m2 = re.search(r'([0-9.]+)', adi_clean)
    if m2:
        return float(m2.group(1)), 'mg/kg bw'
        
    return None, adi_str

def main():
    log("=== 啟動步驟 4: 實驗與身份數據升格 staging 管線 ===")
    
    HERE = os.path.dirname(os.path.abspath(__file__))
    AUDIT_CSV = os.path.join(HERE, "identity_audit.csv")
    
    if not os.path.exists(AUDIT_CSV):
        log(f"❌ 找不到身份驗證 CSV 檔：{AUDIT_CSV}")
        return

    # 1. 讀取 JECFA details 萃取結果與 JECFA 狀態
    jecfa_details = {}
    jecfa_statuses = {}
    try:
        conn = psycopg2.connect(
            host="localhost", port="5432",
            database="product_db", user="postgres", password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        
        # 讀取 JECFA 萃取結論
        cur.execute("SELECT record_id, ins_number, adi_value, comments FROM experiment.jecfa_details;")
        for rid, ins, adi_val, comm in cur.fetchall():
            jecfa_details[rid] = {
                'ins': ins,
                'adi_raw': adi_val,
                'comments': comm
            }
        log(f"成功讀取 {len(jecfa_details)} 筆 JECFA 萃取數據。")
        
        # 讀取 JECFA 的 APPROVED/PENDING 狀態
        cur.execute("""
            SELECT record_id, status 
            FROM experiment.additive_source_urls 
            WHERE source_name = 'JECFA' AND status = 'APPROVED';
        """)
        for rid, status in cur.fetchall():
            jecfa_statuses[rid] = status
            
    except Exception as e:
        log(f"❌ 資料庫連線或讀取失敗: {e}")
        return

    # 2. 讀取 identity_audit.csv
    audit_records = []
    with open(AUDIT_CSV, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            audit_records.append(row)
    log(f"成功自 CSV 讀取 {len(audit_records)} 筆化學身份記錄。")

    # 3. 逐筆進行決策與合併
    upsert_count = 0
    approved_count = 0
    pending_count = 0
    
    for row in audit_records:
        rid = row['record_id']
        pc_status = row['status']
        selected_cas = row['selected_cas']
        
        # JECFA 數據
        j_data = jecfa_details.get(rid, {})
        j_ins = j_data.get('ins', '')
        j_adi_raw = j_data.get('adi_raw', '')
        
        # 解析 ADI
        curated_adi_val, curated_adi_unit = parse_numeric_adi(j_adi_raw)
        
        # INS 優先權：JECFA 萃取結論優先，否則採用 CSV 原始 INS
        ins_raw = row['ins_or_e_number'] or ''
        clean_ins = j_ins
        if not clean_ins:
            # 去除 "INS" 或 "E" 前綴，只保留數字
            ins_match = re.search(r'\d+', ins_raw)
            if ins_match:
                clean_ins = ins_match.group(0)
                
        # 決策狀態：如果 PubChem 是 APPROVED 且 JECFA 是 APPROVED ➔ APPROVED
        # 否則 ➔ PENDING_REVIEW (待人工確認)
        is_jecfa_approved = jecfa_statuses.get(rid) == 'APPROVED'
        is_pc_approved = pc_status == 'APPROVED'
        
        # 讀取 CSV 中的比對中繼資料
        pc_method = row.get('match_method', 'unknown')
        pc_matched_name = row.get('matched_name', '')
        pc_sim = row.get('similarity_score', '0.0')
        
        # 格式化 PubChem 比對軌跡說明
        if pc_method == 'direct_name_match':
            pc_trace = f"原始英文名對照 (相似度: {pc_sim})"
        elif pc_method == 'local_alias_match':
            pc_trace = f"別名對照 [{pc_matched_name}] (相似度: {pc_sim})"
        else:
            pc_trace = "查無對照"

        # 核心安全性判定：僅允許 100% 純淨的「直接品名匹配」自動核准。
        # 任何透過 LLM/別名表對位的項目 (local_alias_match)，皆視為未對位，清空 CAS 並強制降級。
        is_auto_approvable = is_pc_approved and is_jecfa_approved and (pc_method == 'direct_name_match')

        # 如果是別名匹配，我們清空 selected_cas 以防 LLM 數據污染，待人工審查決定是否採用
        if pc_method == 'local_alias_match':
            selected_cas = None
            clean_ins = None
            curated_adi_val = None
            curated_adi_unit = None
            curated_status = 'PENDING_REVIEW'
            pending_count += 1
            notes = f"待覆核：基於別名對照 [{pc_matched_name}] (相似度: {pc_sim})，強制安全降級並清空 CAS，避免 LLM 數據污染。"
        elif is_auto_approvable:
            curated_status = 'APPROVED'
            approved_count += 1
            notes = f"自動核准：PubChem經由【{pc_trace}】驗證通過，且 JECFA 限量相似度達標。"
        else:
            curated_status = 'PENDING_REVIEW'
            pending_count += 1
            reasons = []
            if not is_pc_approved:
                reasons.append(f"PubChem驗證未過 ({pc_trace})")
            if not is_jecfa_approved:
                reasons.append("JECFA未自動核准文獻")
            notes = f"待覆核：{'；'.join(reasons)}。已預填當前最佳候選值。"

        # 寫入 staging.additives_curated (UPSERT)
        try:
            cur.execute("""
                INSERT INTO staging.additives_curated (
                    record_id, selected_cas, curated_ins,
                    curated_adi_value, curated_adi_unit, curated_status,
                    cas_overridden, ins_overridden, jecfa_url_overridden, adi_overridden,
                    reviewed_by, reviewer_notes, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (record_id)
                DO UPDATE SET
                    selected_cas = EXCLUDED.selected_cas,
                    curated_ins = EXCLUDED.curated_ins,
                    curated_adi_value = EXCLUDED.curated_adi_value,
                    curated_adi_unit = EXCLUDED.curated_adi_unit,
                    curated_status = EXCLUDED.curated_status,
                    reviewer_notes = EXCLUDED.reviewer_notes,
                    updated_at = NOW();
            """, (
                rid, selected_cas or None, clean_ins or None,
                curated_adi_val, curated_adi_unit or None, curated_status,
                False, False, False, False,
                'SYSTEM_AUTO_CALIBRATED', notes
            ))
            upsert_count += 1
        except Exception as e:
            log(f"❌ 寫入 staging 失敗 ({rid}): {e}")
            conn.rollback()
            cur.close()
            conn.close()
            return

    conn.commit()
    log(f"=== 數據遷移完畢 ===")
    log(f"📊 統計：共寫入/更新 {upsert_count} 筆審定紀錄至 staging.additives_curated。")
    log(f"  - 自動 APPROVED: {approved_count} 筆")
    log(f"  - 設為 PENDING_REVIEW: {pending_count} 筆")
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
