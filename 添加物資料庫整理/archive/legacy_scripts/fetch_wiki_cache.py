"""
抓取每一個添加物的 Wikipedia 資料並快取到本地（免費 MediaWiki API，無需 token）

策略：
  1. 以 name_en 直接當標題查（redirects=1 解別名）→ 一次取 extract(全文) + categories + url
  2. 查無頁面者，第二輪用 opensearch 模糊搜尋取最接近標題再抓
  3. 全部存 03_enrichment補充/wiki_cache/{record_id}.json

categories 會包含像「IARC Group 2B carcinogens」這種分類，是 IARC 分級的乾淨訊號。

執行：python fetch_wiki_cache.py            # 全部
      python fetch_wiki_cache.py --limit 20 # 測試前 20 筆
"""
import os, sys, json, time, argparse
import requests
import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(SCRIPT_DIR, "..")
CACHE_DIR = os.path.join(ROOT, "03_enrichment補充", "wiki_cache")
ENV_PATH = os.path.join(SCRIPT_DIR, "..", "..", "server", ".env")
load_dotenv(ENV_PATH)

import re
EN_API = "https://en.wikipedia.org/w/api.php"
ZH_API = "https://zh.wikipedia.org/w/api.php"
API = EN_API
HEADERS = {"User-Agent": "FoodScanIoT/1.0 (food additive research; johnlai07197@gmail.com)"}
DELAY = 0.3  # 禮貌延遲（秒）


def clean_name(en):
    """去全形/半形括號內容、取第一個名稱片段"""
    s = re.sub(r'[（(].*?[）)]', '', en or '')
    s = re.split(r'[，；;、]|\bor\b', s)[0]
    return s.strip()


def extract_abbr(en):
    """抓括號內的英文縮寫（如 BHA、BHT、EDTA）"""
    for m in re.findall(r'[（(]\s*([A-Za-z0-9\-]{2,8})\s*[）)]', en or ''):
        return m.strip()
    return None


def get_db_rows():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        dbname=os.getenv("DB_NAME", "product_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
        port=int(os.getenv("DB_PORT", "5432")),
    )
    cur = conn.cursor()
    cur.execute("SELECT record_id, name_zh, name_en FROM additives ORDER BY record_id")
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_by_title(title, api=EN_API):
    """直接以標題抓 extract + categories + url，回傳 (matched_title, url, extract, categories, lang) 或 None"""
    params = {
        "action": "query", "format": "json", "redirects": 1,
        "titles": title,
        "prop": "extracts|categories|info",
        "explaintext": 1, "inprop": "url", "cllimit": "max",
    }
    r = requests.get(api, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    for pid, page in pages.items():
        if pid == "-1" or "missing" in page:
            return None
        extract = page.get("extract", "")
        if not extract:
            return None
        cats = [c.get("title", "").replace("Category:", "") for c in page.get("categories", [])]
        lang = "zh" if api == ZH_API else "en"
        return page.get("title", title), page.get("fullurl", ""), extract, cats, lang
    return None


def opensearch(name, api=EN_API):
    """模糊搜尋回傳最接近標題"""
    params = {"action": "opensearch", "search": name, "limit": 1, "format": "json", "namespace": 0}
    r = requests.get(api, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    res = r.json()
    titles = res[1] if len(res) > 1 else []
    return titles[0] if titles else None


def resolve(zh, en):
    """多重比對級聯，回傳 (result, query_used) 或 (None, None)"""
    # 1. name_en 直接當標題（en）
    if en:
        res = fetch_by_title(en, EN_API)
        if res:
            return res, en
    # 2. 清理後的 name_en opensearch（en）
    c = clean_name(en)
    if c and c.lower() != (en or "").lower():
        time.sleep(DELAY)
        cand = opensearch(c, EN_API)
        if cand:
            time.sleep(DELAY)
            res = fetch_by_title(cand, EN_API)
            if res:
                return res, c
    # 3. 括號縮寫 opensearch（en）
    ab = extract_abbr(en)
    if ab:
        time.sleep(DELAY)
        cand = opensearch(ab, EN_API)
        if cand:
            time.sleep(DELAY)
            res = fetch_by_title(cand, EN_API)
            if res:
                return res, ab
    # 4. 中文名 opensearch（zh 維基）
    if zh:
        time.sleep(DELAY)
        cand = opensearch(zh, ZH_API)
        if cand:
            time.sleep(DELAY)
            res = fetch_by_title(cand, ZH_API)
            if res:
                return res, f"zh:{zh}"
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    rows = get_db_rows()
    if args.limit:
        rows = rows[: args.limit]

    found = miss = retried = 0
    for i, (rid, zh, en) in enumerate(rows, 1):
        out_path = os.path.join(CACHE_DIR, f"{rid}.json")
        # 已成功的跳過；not_found / error 的重試
        if os.path.exists(out_path):
            try:
                prev = json.load(open(out_path, encoding="utf-8"))
                if prev.get("status") == "found":
                    found += 1
                    continue
            except Exception:
                pass
            retried += 1

        rec = {"record_id": rid, "name_zh": zh, "name_en": en,
               "matched_title": None, "url": None, "categories": [],
               "extract": None, "status": "not_found", "query_used": None, "lang": None}
        try:
            result, used = resolve(zh, en)
            if result:
                title, url, extract, cats, lang = result
                rec.update(matched_title=title, url=url, categories=cats,
                           extract=extract, status="found", query_used=used, lang=lang)
                found += 1
            else:
                miss += 1
        except Exception as e:
            rec["status"] = f"error: {e}"
            miss += 1

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)

        if i % 25 == 0:
            print(f"[{i}/{len(rows)}] found={found} miss={miss} retried={retried}", flush=True)
        time.sleep(DELAY)

    print(f"\n完成：found={found} miss={miss} / 共 {len(rows)}（快取在 {CACHE_DIR}）")


if __name__ == "__main__":
    main()
