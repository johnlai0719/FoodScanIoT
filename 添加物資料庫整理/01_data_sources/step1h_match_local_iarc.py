#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1h: 從本地 IARC JSON 進行化學 CAS 與名稱多重比對，並同步寫入實驗表與 Staging 審核表
說明：讀取由 JS 萃取出的 iarc_agents.json (包含 1,128 筆 IARC 官方評估代理物)，
      透過 CAS 號碼精確對照與名稱模糊比對，為 804 筆添加物打上 IARC 致癌分類 (curated_iarc_class)。
"""
import os
import re
import json
import difflib
import psycopg2
# 暴露途徑過濾策略 (針對純吸入性/與食品食入無關的 IARC 分類進行降級或排除)
IARC_EXCLUSIONS = {
    'Titanium dioxide': {
        'class': 'None',
        'reason': 'IARC 分類為 Group 2B，但僅限吸入呼吸道粉塵危害，與食品食入無關。'
    },
    'Talc': {
        'class': 'None',
        'reason': 'IARC 分類為 Group 2A，但僅限會陰部接觸與吸入粉塵危害，與食品食入無關。'
    },
    'Strong-inorganic-acid mists containing sulfuric acid (see Acid mists)': {
        'class': 'None',
        'reason': 'IARC 分類為 Group 1，但僅限吸入工業強酸霧滴（Mists），與食品食入無關。'
    }
}

def log(msg):
    import time
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t_str}] {msg}", flush=True)

def clean_chemical_name(name):
    if not name:
        return ""
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
    for prefix in ['sodium ', 'potassium ', 'calcium ', 'magnesium ', 'ammonium ', 'steatite ', 'crystalline ']:
        if n.startswith(prefix):
            n = n[len(prefix):]
    return n

def calculate_similarity(s1, s2):
    """精確比對食品添加物名稱與 IARC 代理物名稱，杜絕陰陽離子交叉錯配"""
    c1 = clean_chemical_name(s1)
    c2 = clean_chemical_name(s2)
    if not c1 or not c2:
        return 0.0
        
    # 1. 精確一致 (包含經過縮寫/字根標準化後的名稱)
    if c1 == c2:
        return 1.00
        
    # 2. 特殊食品添加物群組判定 (攝入型亞硝酸鹽/硝酸鹽)
    if ('nitrite' in c1 or 'nitrate' in c1) and not any(x in c1 for x in ['isobutyl', 'amyl', 'butyl', 'alkyl']):
        if 'nitrate or nitrite (ingested)' in c2:
            return 0.95
            
    # 3. 特殊食品添加物群組判定 (糖精及其鹽類)
    if 'saccharin' in c1 and 'saccharin and its salts' in c2:
        return 0.95
        
    # 4. 其他情況不進行模糊對照，以防類似 Potassium sorbate 錯配 Potassium bromate
    return 0.0

def clean_cas(cas_str):
    if not cas_str:
        return ""
    # 去除多餘空白與特殊符號，保留純數字與連字號
    return cas_str.strip().replace(" ", "")

def main():
    log("=== 啟動步驟 1h: IARC 本地精確對照管線 ===")
    
    HERE = os.path.dirname(os.path.abspath(__file__))
    IARC_JSON_PATH = os.path.join(HERE, 'iarc_agents.json')
    
    if not os.path.exists(IARC_JSON_PATH):
        log(f"❌ 找不到 IARC JSON 檔案: {IARC_JSON_PATH}，請先執行 extract_iarc.js")
        return
        
    with open(IARC_JSON_PATH, 'r', encoding='utf-8') as f:
        iarc_data = json.load(f)
    iarc_agents = iarc_data.get('agents', [])
    log(f"成功載入 {len(iarc_agents)} 筆 IARC 官方分類代理物。")
    
    # 建立 IARC CAS 索引以加速 O(1) 比對
    iarc_cas_map = {}
    for agent in iarc_agents:
        for c in agent.get('cas', []):
            norm_c = clean_cas(c)
            if norm_c:
                if norm_c not in iarc_cas_map:
                    iarc_cas_map[norm_c] = []
                iarc_cas_map[norm_c].append(agent)

    # 連線資料庫
    try:
        conn = psycopg2.connect(
            host="localhost", port="5432",
            database="product_db", user="postgres", password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        
        # 讀取 Staging 審核表中的當前數據 (含已核對 CAS)
        cur.execute("""
            SELECT sc.record_id, sc.selected_cas, a.name_en, a.name_zh
            FROM staging.additives_curated sc
            JOIN additives a ON sc.record_id = a.record_id
            ORDER BY sc.record_id;
        """)
        additives = cur.fetchall()
        log(f"成功讀取 {len(additives)} 筆 Staging 添加物審核紀錄。")
    except Exception as e:
        log(f"❌ 資料庫連線或讀取失敗: {e}")
        return

    matched_cas_count = 0
    matched_name_count = 0
    total_matches = 0
    
    for rid, sel_cas, name_en, name_zh in additives:
        norm_cas = clean_cas(sel_cas)
        matched_agents = []
        match_method = ""
        best_sim = 0.0
        
        # 1. 優先使用 CAS 進行精確對照
        if norm_cas and norm_cas in iarc_cas_map:
            matched_agents = iarc_cas_map[norm_cas]
            match_method = "CAS_EXACT_MATCH"
            best_sim = 1.0000
            matched_cas_count += 1
            
        # 2. 如果 CAS 沒有對上，退回使用名稱模糊比對
        if not matched_agents and name_en:
            candidates = []
            for agent in iarc_agents:
                agent_name = agent.get('name', '')
                sim = calculate_similarity(name_en, agent_name)
                # 只有大於等於 0.75 才可以當作候選匹配
                if sim >= 0.75:
                    candidates.append((sim, agent))
            if candidates:
                # 排序選出最相似的
                candidates.sort(key=lambda x: x[0], reverse=True)
                best_sim, best_agent = candidates[0]
                matched_agents = [best_agent]
                match_method = f"NAME_FUZZY_MATCH (score={best_sim:.4f})"
                matched_name_count += 1

        # 3. 處理匹配結果並同步寫入 DB
        if matched_agents:
            agent = matched_agents[0]
            iarc_name = agent.get('name', '')
            iarc_group = agent.get('group', '3') # 預設為 Group 3 (Not Classifiable)
            volume_str = ", ".join(agent.get('volume', []))
            year_val = agent.get('year', '')
            comment = agent.get('comment', '')
            
            # 格式化 IARC 分類字串 (例如 "Group 3", "Group 2B")
            formatted_group = f"Group {iarc_group}" if not iarc_group.startswith("Group") else iarc_group
            
            # 套用暴露途徑與 Group 3 過濾策略
            is_excluded = False
            if iarc_name in IARC_EXCLUSIONS:
                formatted_group = IARC_EXCLUSIONS[iarc_name]['class']
                exclusion_reason = IARC_EXCLUSIONS[iarc_name]['reason']
                is_excluded = True
            elif iarc_group == '3' or formatted_group == 'Group 3':
                formatted_group = 'None'
                exclusion_reason = 'IARC 分類為 Group 3（對人類致癌性無判定證據），食品食入安全無致癌疑慮。'
                is_excluded = True
                
            # 生成詳細的說明 notes
            if is_excluded:
                note_str = f"IARC 官網對照排除 ({match_method})：對齊 IARC 物質 '{iarc_name}'。理由：{exclusion_reason}"
            else:
                note_str = f"IARC 官網對照成功 ({match_method})：對齊 IARC 物質 '{iarc_name}'，分類為 [{formatted_group}]。"
                if volume_str:
                    note_str += f" 收錄於第 {volume_str} 卷。"
                if comment:
                    note_str += f" 備註：{comment}。"
                
            total_matches += 1
            log(f"🔗 [{rid}] {name_zh} -> 匹配到 IARC [{formatted_group}] ({iarc_name}), Sim: {best_sim:.2f}")

            # (A) 更新 staging.additives_curated 中的 IARC 分類與備註
            try:
                # 讀取原本的備註，避免覆蓋掉原本的 PubChem/JECFA 備註
                cur.execute("SELECT reviewer_notes FROM staging.additives_curated WHERE record_id = %s;", (rid,))
                orig_note = cur.fetchone()[0] or ""
                
                # 如果已經有 IARC 備註，則取代，否則追加
                new_note = orig_note
                if "IARC 官網對照成功" in orig_note:
                    new_note = re.sub(r'IARC 官網對照成功.*$', note_str, orig_note)
                else:
                    new_note = (orig_note + "；" + note_str) if orig_note else note_str
                
                cur.execute("""
                    UPDATE staging.additives_curated 
                    SET 
                        curated_iarc_class = %s,
                        reviewer_notes = %s,
                        updated_at = NOW()
                    WHERE record_id = %s;
                """, (formatted_group, new_note, rid))
            except Exception as e:
                log(f"❌ 更新 staging 審核表失敗 ({rid}): {e}")
                conn.rollback()
                cur.close()
                conn.close()
                return

            # (B) 寫入/更新 experiment.additive_source_urls，作為證明文獻連結
            url = f"https://monographs.iarc.who.int/list-of-classifications"
            doc_status = 'APPROVED' if best_sim >= 0.85 else 'PENDING_REVIEW'
            doc_title = f"{iarc_name} (IARC Monographs List of Classifications)"
            if volume_str:
                doc_title += f" (Vol. {volume_str})"
                
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
                """, (rid, 'IARC', 'IARC/CARCINOGENICITY', url, doc_title, best_sim, doc_status, False))
            except Exception as e:
                log(f"❌ 寫入文獻對照失敗 ({rid}): {e}")
                conn.rollback()
                cur.close()
                conn.close()
                return

    conn.commit()
    log(f"=== IARC 官網對照比對完畢 ===")
    log(f"📊 統計：共 {total_matches} 筆品項匹配到 IARC 官方致癌分類。")
    log(f"  - 透過 CAS 精確對照: {matched_cas_count} 筆")
    log(f"  - 透過名稱模糊對照: {matched_name_count} 筆")
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
