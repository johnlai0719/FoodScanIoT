import pandas as pd
from sqlalchemy import create_engine
import os

# 連線資訊
DATABASE_URL = "mysql+pymysql://root:password@localhost:3306/product_db?charset=utf8mb4"
engine = create_engine(DATABASE_URL)

def restore():
    csv_path = "mysql_csv/tfda_base_nutrition.csv"
    if not os.path.exists(csv_path):
        print("❌ 找不到 CSV 檔案")
        return

    print("🚀 正在讀取 TFDA 資料...")
    df = pd.read_csv(csv_path)
    
    # 確保資料表結構存在 (如果剛才沒建成功)
    from cloud_server.models import Base
    from cloud_server.database import engine as db_engine
    Base.metadata.create_all(bind=db_engine)

    print(f"📊 正在將 {len(df)} 筆資料匯入 MySQL...")
    df.to_sql('tfda_base_nutrition', con=engine, if_exists='append', index=False)
    print("✅ 資料恢復完成！")

if __name__ == "__main__":
    restore()
