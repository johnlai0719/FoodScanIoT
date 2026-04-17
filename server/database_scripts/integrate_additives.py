import pandas as pd
import json
import os
import re

def clean_name(name):
    if pd.isna(name): return ""
    # 轉小寫、移除多餘空格及括號內容，以便匹配
    name = str(name).lower().strip()
    return re.sub(r'\(.*?\)', '', name).strip()

def integrate():
    print("🚀 開始執行數據整合鏈路...")
    
    # 1. 讀取台灣 TFDA 資料 (Excel)
    tw_path = '資料抓取/台灣公開食品添加物.xlsx'
    try:
        tw_df = pd.read_excel(tw_path)
        print(f"✅ 已讀取台灣資料：{len(tw_df)} 筆")
    except Exception as e:
        print(f"❌ 讀取台灣 Excel 失敗: {e}")
        return

    # 2. 讀取國際 JECFA 資料 (CSV)
    intl_path = 'csvfiles/additives.csv'
    try:
        intl_df = pd.read_csv(intl_path)
        print(f"✅ 已讀取國際資料：{len(intl_df)} 筆")
    except Exception as e:
        print(f"❌ 讀取國際 CSV 失敗: {e}")
        return

    integrated_data = []
    
    # 3. 執行鏈路匹配
    for _, tw_row in tw_df.iterrows():
        tw_zh = tw_row['中文品名']
        tw_en = tw_row['英文品名']
        tw_limit = tw_row['使用食品範圍及限量']
        
        match_en = clean_name(tw_en)
        
        # 在國際資料中尋找匹配 (比對 name 或 aliases)
        matched_row = None
        for _, intl_row in intl_df.iterrows():
            intl_name = clean_name(intl_row['name'])
            intl_aliases = intl_row['aliases'] # 這是 JSON 字串
            
            # 判斷英文名是否匹配
            if match_en == intl_name:
                matched_row = intl_row
                break
            
            # 或者判斷是否在別名中 (處理 INS 編號或 E-code)
            if pd.notna(intl_aliases):
                aliases_list = [a.lower().strip() for a in json.loads(intl_aliases)]
                if match_en in aliases_list:
                    matched_row = intl_row
                    break
        
        # 4. 數據融合
        entry = {
            "name_zh": tw_zh,
            "name_en": tw_en,
            "tfda_limit": tw_limit,
            "is_integrated": False
        }
        
        if matched_row is not None:
            entry.update({
                "is_integrated": True,
                "adi": matched_row['adi'],
                "jecfa_summary": matched_row['jecfa_summary'],
                "iarc_class": matched_row['iarc_class'],
                "medical_caution": matched_row['medical_caution'],
                "risks_score": matched_row['risks'] # 這是 JSON 字串
            })
            
        integrated_data.append(entry)

    # 5. 儲存結果
    output_path = 'cloud/database_scripts/integrated_additives.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(integrated_data, f, ensure_ascii=False, indent=2)
    
    success_count = sum(1 for x in integrated_data if x['is_integrated'])
    print(f"🎊 整合完成！")
    print(f"📊 總計處理: {len(integrated_data)} 筆")
    print(f"🔗 成功鏈結國際數據: {success_count} 筆")
    print(f"💾 檔案已儲存至: {output_path}")

if __name__ == "__main__":
    integrate()
