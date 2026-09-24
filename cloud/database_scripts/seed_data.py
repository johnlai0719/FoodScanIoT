import os
import sys
import json
from sqlalchemy import text

# Ensure server directory is in path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from database import engine, Base

def seed():
    with engine.connect() as conn:
        print("Cleaning up old data...")
        conn.execute(text("TRUNCATE TABLE safety_alerts, products, producers RESTART IDENTITY CASCADE"))
        conn.commit()

        print("Seeding producers...")
        producers = [
            {"name": "統一企業", "risk_level": "Medium", "license": "A-123", "history": "[]"},
            {"name": "義美食品", "risk_level": "Low", "license": "B-456", "history": "[]"},
            {"name": "太古可口可樂", "risk_level": "Low", "license": "C-789", "history": "[]"}
        ]
        for p in producers:
            conn.execute(text("""
                INSERT INTO producers (name, risk_level, license_number, safety_history) 
                VALUES (:name, :risk_level, :license, :history)
            """), p)
        conn.commit()

        print("Seeding products...")
        products = [
            {
                "barcode": "4710088411164", "name": "統一肉燥麵", "brand": "統一",
                "calories": 470.0, "protein": 9.2, "fat": 22.1, "sugar": 2.5, "sodium": 1890.0,
                "ingredients_list": '["麵粉", "棕櫚油", "食鹽", "味精", "己二烯酸鉀"]',
                "processing_level": 4, "manufacturer": "統一企業"
            },
            {
                "barcode": "4710123456789", "name": "義美全脂鮮乳", "brand": "義美",
                "calories": 66.0, "protein": 3.2, "fat": 3.7, "sugar": 4.8, "sodium": 45.0,
                "ingredients_list": '["生乳"]',
                "processing_level": 1, "manufacturer": "義美食品"
            }
        ]
        for p in products:
            res = conn.execute(text("SELECT id FROM producers WHERE name = :name"), {"name": p["manufacturer"]})
            row = res.fetchone()
            p["producer_id"] = row[0] if row else None
            
            ingredients_list_obj = json.loads(p["ingredients_list"])
            
            conn.execute(text("""
                INSERT INTO products (
                    barcode, name, brand, producer_id, manufacturer, calories, 
                    protein, fat, sugar, sodium, ingredients_list, processing_level, allergens
                ) VALUES (
                    :barcode, :name, :brand, :producer_id, :manufacturer, :calories, 
                    :protein, :fat, :sugar, :sodium, :ingredients_list, :processing_level, :allergens
                )
            """), {
                "barcode": p["barcode"],
                "name": p["name"],
                "brand": p["brand"],
                "producer_id": p["producer_id"],
                "manufacturer": p["manufacturer"],
                "calories": p["calories"],
                "protein": p["protein"],
                "fat": p["fat"],
                "sugar": p["sugar"],
                "sodium": p["sodium"],
                "ingredients_list": json.dumps(ingredients_list_obj),
                "processing_level": p["processing_level"],
                "allergens": json.dumps([])
            })
        
        conn.commit()
        print("Seed completed successfully!")

if __name__ == "__main__":
    seed()
