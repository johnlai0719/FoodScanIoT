import pandas as pd
import json
from sqlalchemy import create_engine, text
import os

# 連線資訊
DATABASE_URL = "mysql+pymysql://root:password@localhost:3306/product_db?charset=utf8mb4"
engine = create_engine(DATABASE_URL)

def restore():
    csv_path = "/home/johnlai/projects/csvfiles/additives.csv"
    if not os.path.exists(csv_path):
        print(f"❌ 找不到 CSV 檔案: {csv_path}")
        return

    print("🚀 正在讀取完整版添加物資料 (additives.csv)...")
    # 使用 pandas 讀取，處理 JSON 字串
    df = pd.read_csv(csv_path)
    
    print("🧹 清理現有添加物資料...")
    with engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        conn.execute(text("TRUNCATE TABLE additives"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()

    print(f"📊 正在匯入 {len(df)} 筆完整資料...")
    # 確保 risks 欄位是正確的 JSON 字串
    # (如果 CSV 讀進來已經是字串，我們就直接存入)
    
    with engine.begin() as conn:
        for _, row in df.iterrows():
            # 建立 SQL 參數
            params = row.to_dict()
            # 處理可能為 NaN 的欄位
            for k, v in params.items():
                if pd.isna(v): params[k] = None
            
            # 使用 INSERT IGNORE 避免重複報錯
            conn.execute(text("""
                INSERT IGNORE INTO additives (
                    id, name, aliases, category, description, food_tech_purpose, 
                    adi, jecfa_summary, iarc_class, medical_caution, 
                    transparency_level, is_allergen, allergen_details, risks
                ) VALUES (
                    :id, :name, :aliases, :category, :description, :food_tech_purpose, 
                    :adi, :jecfa_summary, :iarc_class, :medical_caution, 
                    :transparency_level, :is_allergen, :allergen_details, :risks
                )
            """), params)

    print("✅ 完整資料庫恢復成功！現在每一筆添加物都擁有詳細風險資料了。")

if __name__ == "__main__":
    restore()
