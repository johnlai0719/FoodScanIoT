import json
import pypdf
import re
import pandas as pd

def point_lookup():
    # 1. 讀取目前的整合進度
    json_path = 'cloud/database_scripts/integrated_additives.json'
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    missing_list = [x for x in data if not x.get('is_integrated')]
    print(f"📋 待補完清單共 {len(missing_list)} 筆")

    # 2. 讀取 PDF 全文至記憶體
    pdf_path = '資料抓取/CSX辭典.pdf'
    reader = pypdf.PdfReader(pdf_path)
    pages_text = []
    print(f"⏳ 正在快取 PDF 文字層...")
    for i in range(len(reader.pages)):
        text = reader.pages[i].extract_text()
        pages_text.append(text if text else "")

    # 3. 執行單點定位搜尋
    found_count = 0
    for entry in missing_list:
        target_en = entry['name_en'].upper().strip()
        if not target_en or len(target_en) < 4: continue
        
        # 移除一些會干擾搜尋的細節 (如 p-, L-, (i) 等)
        clean_target = re.sub(r'^[PL]\-|\(I+\)|PARA\-', '', target_en).strip()
        
        for p_idx, p_text in enumerate(pages_text):
            if clean_target in p_text.upper():
                # 找到匹配頁面後，尋找鄰近的 INS 編號
                # 模式：尋找該品名前後的 3-4 位數字
                context_match = re.search(r'([0-9]{3,4}[a-z]?)\s*' + re.escape(clean_target), p_text.upper())
                if not context_match:
                    context_match = re.search(re.escape(clean_target) + r'\s*([0-9]{3,4}[a-z]?)', p_text.upper())
                
                if context_match:
                    ins_no = context_match.group(1)
                    # 更新原始數據
                    entry['ins_no'] = ins_no
                    entry['is_integrated'] = True
                    entry['match_source'] = f"Point Lookup (Page {p_idx+1})"
                    found_count += 1
                    print(f"✨ 成功定位: {entry['name_zh']} -> INS {ins_no}")
                    break # 找到一個就繼續下一個

    # 4. 寫回結果
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"🎉 補完任務結束！本次成功找回 {found_count} 筆資料。")

if __name__ == "__main__":
    point_lookup()
