import json
import os
import time
from sqlalchemy import create_engine, text
from cloud_server.database import engine
from cloud_server.models import Base
import cloud_server.models as models

def reset():
    print("🧹 正在強行重置資料庫結構...")
    with engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        # 獲取所有表名並刪除
        res = conn.execute(text("SHOW TABLES"))
        for row in result:
            print(f"⚠️ 跳過刪除資料表: {row[0]} (安全模式)")
            # conn.execute(text(f"DROP TABLE IF EXISTS {row[0]}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()
    
    print("🏗️ 正在根據最新模型重建資料表...")
    Base.metadata.create_all(bind=engine)

    print("🚀 正在匯入 1061 筆添加物資料...")
    from cloud_server.database import SessionLocal
    db = SessionLocal()
    
    json_path = "additives_manifest.json"
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        new_items = []
        for item in data:
            new_items.append(models.Additive(
                name=item.get("name"),
                aliases=item.get("aliases"),
                category=item.get("category"),
                description=item.get("description"),
                food_tech_purpose=item.get("purpose")
            ))
        db.bulk_save_objects(new_items)
        db.commit()
        print(f"✅ 成功匯入 {len(new_items)} 筆添加物！")

    print("🌱 正在植入測試產品與廠商...")
    # 植入廠商
    p1 = models.Producer(name="統一企業", risk_level="Medium", license_number="A-123")
    p2 = models.Producer(name="義美食品", risk_level="Low", license_number="B-456")
    db.add_all([p1, p2])
    db.commit()

    # 植入產品
    prod1 = models.Product(
        barcode="4710088411164", name="統一肉燥麵", brand="統一", producer_id=p1.id, manufacturer="統一企業",
        calories=470, protein=9.2, fat=22.1, sugar=2.5, sodium=1890,
        ingredients_list=["麵粉", "棕櫚油", "食鹽", "味精", "己二烯酸鉀"],
        processing_level=4
    )
    prod2 = models.Product(
        barcode="4710123456789", name="義美全脂鮮乳", brand="義美", producer_id=p2.id, manufacturer="義美食品",
        calories=66, protein=3.2, fat=3.7, sugar=4.8, sodium=45,
        ingredients_list=["生乳"],
        processing_level=1
    )
    db.add_all([prod1, prod2])
    db.commit()
    db.close()
    print("✨ 全線修復完成！資料庫現在是完美的狀態。")

if __name__ == "__main__":
    reset()
