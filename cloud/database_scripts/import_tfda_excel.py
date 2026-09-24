import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import openpyxl
from database import SessionLocal
import models

EXCEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "添加物資料庫整理", "00_原始資料", "TFDA官方食品添加物.xlsx"
)

def import_tfda():
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb["食品添加物使用範圍及限量暨規格標準"]

    db = SessionLocal()
    try:
        existing = db.query(models.Additive).count()
        if existing > 0:
            print(f"已有 {existing} 筆資料，先清空再重新匯入...")
            db.query(models.Additive).delete()
            db.commit()

        items = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            idx, name_zh, name_en, usage_range, restrictions, category = row
            if not name_zh:
                continue

            additive = models.Additive(
                record_id=f"TFDA-{idx}" if idx else None,
                name_zh=str(name_zh).strip() if name_zh else None,
                name_en=str(name_en).strip() if name_en else None,
                category=[str(category).strip()] if category else None,
                description=str(usage_range).strip() if usage_range else None,
                regulatory_status_tw=str(restrictions).strip() if restrictions else None,
            )
            items.append(additive)

        db.bulk_save_objects(items)
        db.commit()
        print(f"✅ 成功匯入 {len(items)} 筆 TFDA 食品添加物資料")
    except Exception as e:
        db.rollback()
        print(f"❌ 匯入失敗: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    import_tfda()
