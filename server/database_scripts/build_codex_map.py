import pypdf
import re
import json
from collections import Counter

def extract_all_mappings(pdf_path):
    reader = pypdf.PdfReader(pdf_path)
    # 用於儲存 名稱 -> INS 的所有可能對應
    raw_mappings = []
    
    print(f"⏳ 正在全域掃描 {len(reader.pages)} 頁內容...")
    
    for i in range(len(reader.pages)):
        text = reader.pages[i].extract_text()
        if not text: continue
        
        # 尋找模式：大寫字母名稱後跟隨 3-4 位數字
        # 例如: SORBIC ACID 200
        matches = re.findall(r'([A-Z]{3,}[A-Z\s\-,\(\)]{3,})\s+([0-9]{3,4}[a-z]?)', text)
        for name, ins in matches:
            name = " ".join(name.split()).strip()
            if len(name) > 4:
                raw_mappings.append((name, ins))

    # 建立最終對照表
    # 如果同一個名稱對應多個 INS，我們取最常見的一個
    final_map = {}
    name_to_ins_counts = {}
    
    for name, ins in raw_mappings:
        if name not in name_to_ins_counts:
            name_to_ins_counts[name] = Counter()
        name_to_ins_counts[name][ins] += 1
        
    for name, counts in name_to_ins_counts.items():
        # 取出現次數最多的 INS 編號
        best_ins = counts.most_common(1)[0][0]
        final_map[best_ins] = name # 儲存為 INS -> Name

    return final_map

if __name__ == "__main__":
    pdf_file = '資料抓取/CSX辭典.pdf'
    result = extract_all_mappings(pdf_file)
    
    with open('codex_master_map.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 全域掃描完成！共收集 {len(result)} 個 INS 對應。")
    # 測試關鍵字
    for test_ins in ['200', '214', '102', '415']:
        print(f"🔍 INS {test_ins}: {result.get(test_ins, '未找到')}")
