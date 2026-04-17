import os
import time
from database import SessionLocal
import models
from vision_service import VisionService

def process_contributions():
    db = SessionLocal()
    vision = VisionService()
    
    # 指向 Fog 的貢獻照片目錄
    contrib_dir = "../fog_server/contributions"
    if not os.path.exists(contrib_dir):
        print("📁 尚未有使用者上傳照片。")
        return

    files = [f for f in os.listdir(contrib_dir) if f.endswith(".jpg")]
    print(f"🕵️ 發現 {len(files)} 筆待處理的食品貢獻...")

    for filename in files:
        barcode = filename.split("_")[0]
        img_path = os.path.join(contrib_dir, filename)
        
        # 檢查資料庫是否已經補過這筆資料
        existing = db.query(models.Product).filter(models.Product.barcode == barcode).first()
        if existing:
            print(f"⏩ {barcode} 已存在，跳過。")
            continue

        print(f"🧠 正在分析照片: {filename}...")
        data = vision.analyze_food_package(img_path)
        
        if data:
            try:
                new_prod = models.Product(
                    barcode=barcode,
                    name=data.get('name'),
                    brand=data.get('brand'),
                    calories=data.get('calories', 0),
                    protein=data.get('protein', 0),
                    fat=data.get('fat', 0),
                    sugar=data.get('sugar', 0),
                    sodium=data.get('sodium', 0),
                    ingredients_list=data.get('ingredients_list', []),
                    processing_level=data.get('processing_level', 4),
                    is_estimated=True
                )
                db.add(new_prod)
                db.commit()
                print(f"✅ 成功補完商品: {data.get('name')} ({barcode})")
                
                # 處理完後可以將照片移到已處理目錄
                # os.rename(img_path, os.path.join(contrib_dir, "processed", filename))
            except Exception as e:
                print(f"❌ 儲存失敗: {e}")
                db.rollback()
        
        time.sleep(2) # 避免 API 頻率過高

    db.close()

if __name__ == "__main__":
    process_contributions()
