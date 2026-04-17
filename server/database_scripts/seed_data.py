from sqlalchemy import create_engine, text
import os

DATABASE_URL = "mysql+pymysql://root:password@localhost:3306/product_db?charset=utf8mb4"
engine = create_engine(DATABASE_URL)

def seed():
    with engine.connect() as conn:
        print("🧹 徹底清理舊資料...")
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        conn.execute(text("DELETE FROM safety_alerts"))
        conn.execute(text("DELETE FROM products"))
        conn.execute(text("DELETE FROM producers"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()

        print("🌱 植入正確編碼的廠商資料...")
        producers = [
            {"name": "統一企業", "risk_level": "Medium", "license": "A-123", "history": "[]"},
            {"name": "義美食品", "risk_level": "Low", "license": "B-456", "history": "[]"},
            {"name": "太古可口可樂", "risk_level": "Low", "license": "C-789", "history": "[]"}
        ]
        for p in producers:
            conn.execute(text("INSERT INTO producers (name, risk_level, license_number, safety_history) VALUES (:name, :risk_level, :license, :history)"), p)

        print("🌱 植入正確編碼的商品資料...")
        products = [
            {
                "barcode": "4710088411164", "name": "統一肉燥麵", "brand": "統一",
                "calories": 470, "protein": 9.2, "fat": 22.1, "sugar": 2.5, "sodium": 1890,
                "ingredients_list": '["麵粉", "棕櫚油", "食鹽", "味精", "己二烯酸鉀"]',
                "processing_level": 4, "manufacturer": "統一企業"
            },
            {
                "barcode": "4710123456789", "name": "義美全脂鮮乳", "brand": "義美",
                "calories": 66, "protein": 3.2, "fat": 3.7, "sugar": 4.8, "sodium": 45,
                "ingredients_list": '["生乳"]',
                "processing_level": 1, "manufacturer": "義美食品"
            }
        ]
        for p in products:
            # 獲取剛才插入的 producer_id
            res = conn.execute(text("SELECT id FROM producers WHERE name = :name"), {"name": p["manufacturer"]})
            p["producer_id"] = res.fetchone()[0]
            conn.execute(text("""
                INSERT INTO products (barcode, name, brand, producer_id, manufacturer, calories, protein, fat, sugar, sodium, ingredients_list, processing_level)
                VALUES (:barcode, :name, :brand, :producer_id, :manufacturer, :calories, :protein, :fat, :sugar, :sodium, :ingredients_list, :processing_level)
            """), p)
        
        conn.commit()
        print("✅ 完美編碼資料植入成功！")

if __name__ == "__main__":
    seed()
