import json
import os
import time
from sqlalchemy.orm import Session
from database import SessionLocal, engine
import models

def init_db():
    # 增加重試機制，等待 PostgreSQL 啟動
    max_retries = 5
    for i in range(max_retries):
        try:
            print(f"📡 嘗試連線至資料庫 (第 {i+1} 次)...")
            models.Base.metadata.create_all(bind=engine)
            break
        except Exception as e:
            if i == max_retries - 1:
                print(f"❌ 無法連線至資料庫: {e}")
                return
            print(f"⏳ 資料庫尚未就緒，5 秒後重試...")
            time.sleep(5)

    db = SessionLocal()
    
    try:
        # 2. 檢查 Additives 是否為空
        count = db.query(models.Additive).count()
        if count == 0:
            print("🚀 資料庫中無添加物資料，正在從 JSON 匯入...")
            
            # 定位 JSON 檔案路徑
            json_path = os.path.join("..", "additives_manifest.json")
            if not os.path.exists(json_path):
                print(f"❌ 找不到檔案: {json_path}")
                return

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            new_items = []
            for item in data:
                # 建立模型實例
                additive = models.Additive(
                    name_zh=item.get("name"),
                    aliases=item.get("aliases"),
                    category=item.get("category"),
                    description=item.get("description"),
                    food_tech_purpose=item.get("purpose")
                )
                new_items.append(additive)
            
            # 批量插入
            db.bulk_save_objects(new_items)
            db.commit()
            print(f"✅ 成功匯入 {len(new_items)} 筆添加物資料！")
        else:
            print(f"📊 資料庫已有 {count} 筆資料，跳過自動匯入。")
            
    except Exception as e:
        print(f"❌ 匯入失敗: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
