"""
食品原料整合查詢平臺 - 官方開放資料下載
來源: 政府資料開放平臺 https://data.gov.tw/dataset/8452
授權: 政府資料開放授權條款-第1版 (免費, 可重製/散布/商業使用, 需標示出處)
提供機關: 衛生福利部食品藥物管理署
"""
import urllib.request
import json
import csv
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_URL = "https://data.fda.gov.tw/data/opendata/export/4/json"
OUT_RAW_JSON = os.path.join(_DIR, "tfda_raw_materials_official_raw.json")
OUT_JSON = os.path.join(_DIR, "tfda_raw_materials.json")
OUT_CSV = os.path.join(_DIR, "tfda_raw_materials.csv")


def fetch():
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def clean(raw):
    cleaned = []
    for i, d in enumerate(raw, start=1):
        cleaned.append({
            "seq": i,
            "category_major": d.get("大分類"),
            "category_minor": d.get("次分類"),
            "zh_name": d.get("中文名稱"),
            "foreign_name": d.get("英文名稱"),
            "sci_name": d.get("英文學名"),
            "part": d.get("部位"),
            "remarks": (d.get("備註") or "").strip() or None,
        })
    return cleaned


def main():
    raw = fetch()
    with open(OUT_RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)

    cleaned = clean(raw)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=1)

    fieldnames = ["seq", "category_major", "category_minor", "zh_name", "foreign_name", "sci_name", "part", "remarks"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(cleaned)

    print(f"rows: {len(cleaned)}")


if __name__ == "__main__":
    main()
