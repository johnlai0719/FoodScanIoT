"""
Wiki 廠商食安事件抓取測試腳本
用途：輸入廠商名稱，搜尋其 Wikipedia 頁面並萃取食安事件，不寫入資料庫
執行：python test_wiki_fetch.py 統一企業
"""
import sys
import os
import json
import re
import time
import requests

from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), "..", "..", "server", ".env")
load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ 找不到 GEMINI_API_KEY，請先填寫 server/.env")
    sys.exit(1)

from google import genai
_client = genai.Client(api_key=GEMINI_API_KEY)

HEADERS = {"User-Agent": "FoodScanIoT/1.0 (food safety research; johnlai07197@gmail.com)"}
WIKI_API = "https://zh.wikipedia.org/w/api.php"


def search_wiki_producer(producer_name: str) -> tuple[str, str]:
    """搜尋廠商 Wikipedia 頁面，回傳 (頁面標題, 內容)"""
    # 搜尋最接近的頁面
    resp = requests.get(WIKI_API, headers=HEADERS, timeout=10, params={
        "action": "opensearch",
        "search": producer_name,
        "limit": 3,
        "format": "json",
    })
    resp.raise_for_status()
    results = resp.json()
    titles = results[1] if len(results) > 1 else []

    if not titles:
        return "", ""

    page_title = titles[0]
    print(f"✅ 找到頁面: {page_title}")
    print(f"   其他候選: {titles[1:]}")

    # 抓取頁面內容
    resp = requests.get(WIKI_API, headers=HEADERS, timeout=15, params={
        "action": "query",
        "titles": page_title,
        "prop": "extracts",
        "explaintext": True,
        "format": "json",
        "redirects": 1,
    })
    resp.raise_for_status()
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        content = page.get("extract", "")
        if content:
            return page_title, content
    return page_title, ""


def parse_events(producer_name: str, content: str) -> list[dict]:
    content_trimmed = content[:8000]
    prompt = f"""你是食品安全事件資料庫整理員。以下是維基百科關於「{producer_name}」的頁面內容。

請從中萃取所有與該廠商相關的食品安全事件或爭議。

severity 定義：
1 = 標示/包裝違規
2 = 成分/添加物違規（如塑化劑、違禁添加物）
3 = 重大食安事件（致死、大規模中毒、重金屬污染）

若該頁面完全無食安相關記載，請回傳空陣列 []。
只回傳 JSON 陣列，不加 markdown：
[
  {{
    "title": "事件標題（50字內）",
    "summary": "事件摘要（100字內，繁體中文）",
    "event_date": "YYYY-MM 或 YYYY-01 或空字串",
    "severity": 1
  }}
]

維基百科內容：
{content_trimmed}
"""
    t = time.time()
    response = _client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
    print(f"⏱  Gemini 耗時: {(time.time()-t)*1000:.0f}ms")
    text = response.text.strip()
    match = re.search(r"(\[.*\])", text, re.DOTALL)
    if match:
        text = match.group(1)
    return json.loads(text)


def main():
    if len(sys.argv) > 1:
        producer_name = " ".join(sys.argv[1:])
    else:
        producer_name = input("請輸入廠商名稱: ").strip()

    if not producer_name:
        print("❌ 請提供廠商名稱")
        sys.exit(1)

    print(f"\n🏭 查詢廠商: {producer_name}")
    print("─" * 60)

    page_title, content = search_wiki_producer(producer_name)
    if not content:
        print("❌ 找不到頁面或頁面無內容")
        sys.exit(0)

    print(f"📄 頁面內容長度: {len(content)} 字（送前 8000 字給 Gemini）")

    events = parse_events(producer_name, content)

    print(f"\n{'='*60}")
    print(f"📋 食安事件解析結果：{len(events)} 筆")
    print('='*60)

    if not events:
        print("  （該廠商 Wikipedia 頁面無食安相關記載）")
    else:
        for i, ev in enumerate(events, 1):
            severity_label = {1: "⚠️ 標示違規", 2: "🔴 成分違規", 3: "🚨 重大事件"}.get(ev.get("severity"), "❓")
            print(f"\n[{i}] {severity_label}")
            print(f"  標題: {ev.get('title', '')}")
            print(f"  摘要: {ev.get('summary', '')}")
            print(f"  日期: {ev.get('event_date') or '不明'}")


if __name__ == "__main__":
    main()
