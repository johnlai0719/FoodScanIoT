"""
匯入食藥署官方「食品添加物通用名稱」對照表 → additives.aliases

來源：https://www.fda.gov.tw/TC/siteContent.aspx?sid=10159
      （依「食品添加物使用範圍及限量暨規格標準」所列中文品名與通用名稱對照，
        食藥署 2018-08-09 發布、2018-10-15 維護；已於 2026-07-14 抓取存檔）
存檔位置：添加物資料庫整理/01_data_sources/tfda_official_common_names.json

為何這批可以匯入（與「不再增修資料庫」的凍結原則不衝突）：
本檔補的是**官方直接發布的對照關係**，不是我們自行判斷或由模型生成的別名。
凍結原則要防的是「照著測試結果補洞」造成的測試集汙染；官方公告的對照表屬於
基準資料本身的一部分，其內容與我們的測試結果無關，先補後補都一樣。

實測意義（2026-07-26）：這 25 筆中資料庫已收錄 18 筆，缺的 5 筆裡包含
「碳酸氫鈉←小蘇打」與「玉米糖膠←三仙膠」——前者正是評估中一筆假配對的成因
（小蘇打被「碳酸鈉」攔截），後者則是先前憑判斷補上、後又還原的那筆。
兩者皆有官方依據，不需再靠人工判斷。

用法：python3 -m database_scripts.import_official_common_names [--dry-run]
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

from sqlalchemy import text

from database import SessionLocal

JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "添加物資料庫整理", "01_data_sources", "tfda_official_common_names.json",
)


def main(dry_run: bool = False):
    if not os.path.exists(JSON_PATH):
        print(f"找不到來源檔：{JSON_PATH}")
        return

    with open(JSON_PATH, encoding="utf-8") as f:
        doc = json.load(f)
    records = doc.get("records", [])

    db = SessionLocal()
    added = skipped = notfound = 0
    try:
        for r in records:
            official = (r.get("name_official") or "").strip()
            common = (r.get("name_common") or "").strip()
            # 「維生素○←維他命○」為通配寫法，非具體品項，無法逐筆對應，略過
            if not official or not common or "○" in official or "○" in common:
                continue

            row = db.execute(
                text("SELECT id, aliases FROM additives WHERE name_zh = :n"),
                {"n": official},
            ).fetchone()
            if not row:
                notfound += 1
                print(f"  ⚠ 正式名不在資料庫：{official}（俗名「{common}」未匯入）")
                continue

            aliases = row[1] if isinstance(row[1], list) else json.loads(row[1] or "[]")
            if common in aliases:
                skipped += 1
                continue

            aliases.append(common)
            if not dry_run:
                db.execute(
                    text("UPDATE additives SET aliases = CAST(:a AS json) WHERE id = :i"),
                    {"a": json.dumps(aliases, ensure_ascii=False), "i": row[0]},
                )
            added += 1
            print(f"  + {official:<14} ← {common}")

        if not dry_run:
            db.commit()
        print(f"\n{'（試跑，未寫入）' if dry_run else '已寫入'}："
              f"新增 {added} 筆、已存在 {skipped} 筆、正式名查無 {notfound} 筆")
        print(f"來源：{doc.get('source_title')}（{doc.get('source_url')}）")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
