import os
import sys
import json

# Ensure server directory is in path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from database import SessionLocal, engine
from models import Base, Additive

def import_data():
    json_path = "/home/johnlai/projects/資料庫/additives_tfda_clean.json"
    if not os.path.exists(json_path):
        print(f"Error: JSON file not found at {json_path}")
        return

    print("Recreating database tables...")
    Base.metadata.create_all(bind=engine)

    print(f"Reading JSON from {json_path}...")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        print("Clearing existing additives table...")
        db.query(Additive).delete()
        db.commit()

        print(f"Importing {len(data)} additives...")
        additives_to_add = []
        for idx, item in enumerate(data):
            # Map JSON to database columns
            additives_to_add.append(Additive(
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
            ))
            
            # Commit in batches of 100 for safety and performance
            if len(additives_to_add) >= 100:
                db.bulk_save_objects(additives_to_add)
                db.commit()
                additives_to_add = []
                
        if additives_to_add:
            db.bulk_save_objects(additives_to_add)
            db.commit()
            
        print("Import completed successfully!")
    except Exception as e:
        db.rollback()
        print(f"An error occurred during import: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    import_data()
