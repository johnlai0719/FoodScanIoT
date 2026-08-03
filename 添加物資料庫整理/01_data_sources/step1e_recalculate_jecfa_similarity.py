#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1e：JECFA 與多網址對照相似度重新計算（防錯配版）
說明：本腳本「完全在本地執行，不需要重新下載網頁或呼叫 API」。
      它會從資料庫載入所有 JECFA 對照記錄，並使用全新、更嚴格的化學演算法重新計算相似度與 status。
      新演算法包含：
      1. 全形括號與標點轉換（提升正確對照之相似度）。
      2. 陽離子衝突過濾（防鈉/鉀/鈣/鎂/鋁錯配）。
      3. 烷基鏈衝突過濾（防甲/乙/丙/丁基錯配，如 Methyl vs Ethyl Cellulose）。
      4. 磷酸根級態衝突過濾（防單磷酸與焦磷酸/二磷酸錯配）。
"""
import os
import re
import difflib
import psycopg2

def log(msg):
    import time
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t_str}] {msg}", flush=True)

def clean_chemical_name(name):
    if not name: return ""
    # 支援全形標點轉換
    name = name.replace("（", "(").replace("）", ")").replace("，", ",")
    n = name.lower().strip()
    n = re.sub(r'^(l-|d-|dl-|l\s*\+|d\s*-)', '', n)
    # 只拿括號前的部分、逗號前的部分
    n = n.split('(')[0].split(',')[0].strip()
    # 💡 修正：使用 Regex 選擇性切割。只切斷「前後有空格的連字號」（如 L-Lysine - L-Glutamate），保留化學品名內部的連字號（如 L-Ascorbate）
    n = re.split(r'\s+-\s+', n)[0]
    n = re.sub(r'[^a-z0-9]', '', n)
    return n

def check_clash(s1, s2):
    s1_lower = s1.lower()
    s2_lower = s2.lower()
    
    # 1. 陽離子衝突
    cations = ["sodium", "potassium", "calcium", "magnesium", "aluminium", "aluminum", "ammonium", "iron", "zinc"]
    s1_cats = {c for c in cations if c in s1_lower}
    s2_cats = {c for c in cations if c in s2_lower}
    if s1_cats and s2_cats and s1_cats != s2_cats:
        return "Cation conflict"
        
    # 2. 烷基鏈衝突 (Methyl, Ethyl, Propyl, Butyl)
    alkyls = ["methyl", "ethyl", "propyl", "butyl", "isopropyl", "isobutyl"]
    s1_alks = {a for a in alkyls if a in s1_lower}
    s2_alks = {a for a in alkyls if a in s2_lower}
    if s1_alks and s2_alks and s1_alks != s2_alks:
        return "Alkyl conflict"
        
    # 3. 磷酸根級態衝突 (Phosphate vs Diphosphate/Pyrophosphate)
    has_di_1 = "diphosphate" in s1_lower or "pyrophosphate" in s1_lower
    has_di_2 = "diphosphate" in s2_lower or "pyrophosphate" in s2_lower
    if has_di_1 != has_di_2:
        return "Phosphate class conflict"
        
    return None

def calculate_new_similarity(s1, s2):
    if not s1 or not s2: return 0.0
    
    # 衝突檢測
    clash_reason = check_clash(s1, s2)
    if clash_reason:
        return 0.0, clash_reason
        
    c1 = clean_chemical_name(s1)
    c2 = clean_chemical_name(s2)
    if not c1 or not c2: return 0.0, "Empty after cleaning"
    
    sim = difflib.SequenceMatcher(None, c1, c2).ratio()
    return round(sim, 4), None

def main():
    log("=== 啟動步驟 1e: JECFA 相似度與狀態本地重算管線 ===")
    
    # 1. 連線資料庫
    try:
        conn = psycopg2.connect(
            host="localhost", port="5432",
            database="product_db", user="postgres", password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor()
        
        # 讀取添加物英文名稱與別名
        cur.execute("SELECT record_id, name_zh, name_en, aliases FROM additives;")
        additives = {row[0]: {'zh': row[1], 'en': row[2], 'aliases': row[3]} for row in cur.fetchall()}
        log(f"成功讀取 {len(additives)} 筆添加物主資料。")
        
        # 讀取實驗對照表中的 JECFA 記錄
        cur.execute("SELECT id, record_id, matched_title, url, similarity, status FROM experiment.additive_source_urls;")
        source_urls = cur.fetchall()
        log(f"成功讀取 {len(source_urls)} 筆文獻對照記錄。")
        
    except Exception as e:
        log(f"❌ 資料庫連線或讀取失敗: {e}")
        sys.exit(1)

    # 2. 進行相似度重算與 status 判定
    updates = []
    downgrade_count = 0
    upgrade_count = 0
    
    for row_id, rid, matched_title, url, old_sim, old_status in source_urls:
        add_data = additives.get(rid)
        if not add_data:
            continue
            
        en_names = []
        if add_data['en']:
            # 💡 修正：原始英文名稱也必須依據分號和逗號進行切割
            en_names.extend([p.strip() for p in re.split(r'[,;，；|]', add_data['en']) if p.strip()])
            
        if add_data['aliases'] and isinstance(add_data['aliases'], str):
            en_names.extend([p.strip() for p in re.split(r'[,;，；|]', add_data['aliases']) if p.strip()])
            
        # 計算與各個英文名稱的最佳相似度
        best_sim = 0.0
        best_reason = None
        
        for en in en_names:
            sim, reason = calculate_new_similarity(en, matched_title)
            if sim > best_sim:
                best_sim = sim
                best_reason = reason
                
        # 判定全新 Status
        if best_sim >= 0.85:
            new_status = 'APPROVED'
        elif best_sim >= 0.50:
            new_status = 'PENDING_REVIEW'
        else:
            new_status = 'NO_MATCH'
            
        # 追蹤狀態改變
        if old_status == 'APPROVED' and new_status != 'APPROVED':
            downgrade_count += 1
            log(f"⬇️ 降級: {rid} | '{add_data['en']}' vs '{matched_title}' | 相似度 {old_sim:.4f} ➔ {best_sim:.4f} ({best_reason or 'Score dropped'})")
        elif old_status != 'APPROVED' and new_status == 'APPROVED':
            upgrade_count += 1
            log(f"⬆️ 升級: {rid} | '{add_data['en']}' vs '{matched_title}' | 相似度 {old_sim:.4f} ➔ {best_sim:.4f} (括號清洗成功)")
            
        updates.append((best_sim, new_status, row_id))

    # 3. 批次寫入資料庫
    if updates:
        try:
            log("開始更新資料庫...")
            cur.executemany("""
                UPDATE experiment.additive_source_urls
                SET similarity = %s, status = %s
                WHERE id = %s;
            """, updates)
            conn.commit()
            log(f"✅ 資料庫更新完成。共變更 {len(updates)} 筆記錄。")
            log(f"📊 統計結果：")
            log(f"  - 降級筆數 (排除錯配): {downgrade_count} 筆")
            log(f"  - 升級筆數 (修正括號): {upgrade_count} 筆")
        except Exception as e:
            conn.rollback()
            log(f"❌ 批次更新失敗: {e}")
            
    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
