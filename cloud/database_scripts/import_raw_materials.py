"""
匯入食藥署「食品原料整合查詢平臺」原料清單 → raw_materials 表

來源檔：添加物資料庫整理/01_data_sources/tfda_raw_materials.json（1,702 筆，
由 step1i_fetch_tfda_official_opendata.py 於 2026-07-14 抓取）

中文名欄位可能含多個別名（以「；」分隔，如「山牡荊；薄姜木；山埔姜」），
一併拆開存入以提高比對命中率——比對端只做精確子字串比對，別名缺一個就少
一次命中機會。

用法：python3 -m database_scripts.import_raw_materials
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

from database import SessionLocal, engine
import models

JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "添加物資料庫整理", "01_data_sources", "tfda_raw_materials.json",
)


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    return s


def main():
    if not os.path.exists(JSON_PATH):
        print(f"找不到來源檔：{JSON_PATH}")
        return

    models.Base.metadata.create_all(bind=engine, tables=[models.RawMaterial.__table__])

    with open(JSON_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    db = SessionLocal()
    try:
        existing = db.query(models.RawMaterial).count()
        if existing:
            print(f"已有 {existing} 筆，先清空再重新匯入…")
            db.query(models.RawMaterial).delete()
            db.commit()

        n = 0
        for r in rows:
            db.add(models.RawMaterial(
                seq=_clean(r.get("seq")),
                category_major=_clean(r.get("category_major")),
                category_minor=_clean(r.get("category_minor")),
                name_zh=_clean(r.get("zh_name")),
                name_foreign=_clean(r.get("foreign_name")),
                name_sci=_clean(r.get("sci_name")),
                part=_clean(r.get("part")),
                remarks=_clean(r.get("remarks")),
            ))
            n += 1
        db.commit()

        total = db.query(models.RawMaterial).count()
        named = db.query(models.RawMaterial).filter(models.RawMaterial.name_zh.isnot(None)).count()
        print(f"✅ 匯入 {n} 筆（表內共 {total} 筆，其中 {named} 筆有中文名）")

        from sqlalchemy import func
        for cat, cnt in (db.query(models.RawMaterial.category_major, func.count())
                         .group_by(models.RawMaterial.category_major)
                         .order_by(func.count().desc()).all()):
            print(f"   {cnt:>5}  {cat}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
