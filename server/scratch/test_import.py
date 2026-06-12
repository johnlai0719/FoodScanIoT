import sys
import os

sys.path.append("/home/johnlai/projects/server")

try:
    from database import SessionLocal, engine
    from models import Base, Additive
    import json

    json_path = "/home/johnlai/projects/資料庫/additives_tfda_clean.json"
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    
    # Let's inspect the first record that causes failure
    print("Testing record insertion one by one...")
    for idx, item in enumerate(data):
        try:
            db.query(Additive).filter(Additive.name_zh == item.get("name_zh")).delete()
            db.commit()
            
            ad = Additive(
                record_id=item.get("record_id"),
                name_zh=item.get("name_zh"),
                name_en=item.get("name_en"),
                aliases=item.get("synonyms"),
                ins_or_e_number=item.get("ins_or_e_number"),
                category=item.get("function_class"),
                description=item.get("consumer_description"),
                food_tech_purpose=item.get("usage_notes"),
                adi=item.get("adi_status"),
                risks=item.get("group_risks"),
                regulatory_status_tw=item.get("regulatory_status_tw"),
                description_confidence=item.get("description_confidence"),
                description_sources=item.get("description_sources")
            )
            db.add(ad)
            db.commit()
        except Exception as e:
            print(f"Error on index {idx} ({item.get('name_zh')}):")
            print(e)
            db.rollback()
            break
    db.close()
except Exception as e:
    import traceback
    traceback.print_exc()
