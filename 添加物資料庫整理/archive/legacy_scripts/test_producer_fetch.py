"""
廠商食安事件抓取測試腳本
用途：輸入廠商名稱，測試 Tavily 搜尋 + Gemini 驗證流程，不寫入資料庫
執行：python test_producer_fetch.py
"""
import sys
import os
import json
import time
import re

# 載入 .env
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "server", ".env")
load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not GEMINI_API_KEY:
    print("❌ 找不到 GEMINI_API_KEY，請先填寫 server/.env")
    sys.exit(1)
if not TAVILY_API_KEY:
    print("❌ 找不到 TAVILY_API_KEY，請先填寫 server/.env")
    sys.exit(1)

from google import genai
from tavily import TavilyClient

_client = genai.Client(api_key=GEMINI_API_KEY)
_tavily = TavilyClient(api_key=TAVILY_API_KEY)


def search_producer(producer_name: str) -> list[dict]:
    """Tavily 搜尋廠商食安事件"""
    from urllib.parse import urlparse

    query = f"{producer_name} 食安 (不合格 OR 違規 OR 回收 OR 投訴 OR 衛生局 OR 異物 OR 客訴)"
    print(f"\n🔍 Tavily 查詢: {query}")

    official_domains = {"fda.gov.tw", "mohw.gov.tw", "consumer.gov.tw", "gov.tw"}
    news_domains = {"udn.com", "ltn.com.tw", "cna.com.tw", "setn.com", "tvbs.com.tw",
                    "yahoo.com", "chinatimes.com", "ettoday.net"}

    t = time.time()
    resp = _tavily.search(query=query, max_results=15, search_depth="advanced")
    print(f"⏱  Tavily 耗時: {(time.time()-t)*1000:.0f}ms | 取得 {len(resp.get('results',[]))} 筆")

    results = []
    seen_urls = set()
    for r in resp.get("results", []):
        url = r.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        domain = urlparse(url).netloc.lower()
        if any(domain == d or domain.endswith("." + d) for d in official_domains):
            r["source_type"] = "official"
        elif any(domain == d or domain.endswith("." + d) for d in news_domains):
            r["source_type"] = "news"
        else:
            r["source_type"] = "social"
        results.append(r)
    return results


def validate_with_gemini(producer_name: str, snippets: list[dict]) -> list[dict]:
    """Gemini flash-lite 驗證是否為真實食安事件"""
    snippets_formatted = ""
    for idx, s in enumerate(snippets):
        snippets_formatted += f"--- Snippet {idx} ---\n"
        snippets_formatted += f"Title: {s.get('title', '')}\n"
        snippets_formatted += f"Content: {s.get('content', '')}\n"
        snippets_formatted += f"URL: {s.get('url', '')}\n"
        snippets_formatted += f"Published Date: {s.get('published_date', '')}\n"
        snippets_formatted += f"Source Type: {s.get('source_type', '')}\n\n"

    prompt = f"""You are a strict food safety incident validator. You will be given web search result snippets about a company named "{producer_name}".

Your task:
Determine whether each snippet describes a DIRECT food safety incident — meaning a confirmed violation, recall, contamination, lab failure, or regulatory penalty that physically harmed or endangered consumers.

STRICT exclusion rules (set is_food_safety_event to false):
- PR responses, company statements, or rebuttals about food safety
- Advocacy, lobbying, or petitions related to food safety policy
- Opinion articles, commentary, or analysis that merely mention food safety
- Social media posts without verifiable source
- Articles where the incident described belongs to a DIFFERENT company
- Any snippet where the actual safety violation is not clearly stated

INCLUDE only:
- Government regulatory violations with product name and penalty
- Official product recalls with specific product and reason
- Lab test failures detecting prohibited substances or excess limits
- Confirmed contamination incidents

Additional rules:
- event_date must be the date the INCIDENT OCCURRED. Use YYYY-MM format, YYYY-01 if only year known, "" if truly unknown.
- summary must be under 80 characters in Traditional Chinese (繁體中文).
- Return JSON ONLY, no markdown fences.
- severity: 1=標示違規, 2=成分違規, 3=重大食安事件

Required JSON Array Format:
[
  {{
    "is_food_safety_event": true or false,
    "title": "事件標題",
    "summary": "中文摘要（80字內）",
    "event_date": "YYYY-MM or empty string",
    "source_url": "URL",
    "source_type": "official / news / social",
    "severity": 1
  }}
]

Snippets to validate:
{snippets_formatted}"""

    t = time.time()
    response = _client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
    print(f"⏱  Gemini 驗證耗時: {(time.time()-t)*1000:.0f}ms")

    text = response.text.strip()
    match = re.search(r"(\[.*\])", text, re.DOTALL)
    if match:
        text = match.group(1)
    data = json.loads(text)

    return [item for item in data if isinstance(item, dict) and item.get("is_food_safety_event")]


def search_complaints(producer_name: str) -> list[dict]:
    """Tavily 搜尋消費者投訴"""
    query = f"{producer_name} (客訴 OR 抱怨 OR 異物 OR 吃壞肚子 OR 食物中毒 OR 退費 OR 問題產品) site:ptt.cc OR site:dcard.tw OR site:facebook.com"
    print(f"\n🔍 消費者投訴查詢: {query}")
    t = time.time()
    resp = _tavily.search(query=query, max_results=10, search_depth="basic")
    print(f"⏱  Tavily 耗時: {(time.time()-t)*1000:.0f}ms | 取得 {len(resp.get('results',[]))} 筆")
    results = []
    seen_urls = set()
    for r in resp.get("results", []):
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            r["source_type"] = "consumer_complaint"
            results.append(r)
    return results


def print_results(title: str, items: list[dict]):
    print(f"\n{'='*60}")
    print(f"📋 {title} ({len(items)} 筆)")
    print('='*60)
    if not items:
        print("  （無結果）")
        return
    for i, item in enumerate(items, 1):
        severity_label = {1: "⚠️ 標示違規", 2: "🔴 成分違規", 3: "🚨 重大事件"}.get(item.get("severity"), "")
        print(f"\n  [{i}] {severity_label}")
        print(f"  標題: {item.get('title', '')}")
        print(f"  摘要: {item.get('summary', '')}")
        print(f"  日期: {item.get('event_date', '不明')}")
        print(f"  來源: {item.get('source_type', '')} | {item.get('source_url', '')}")


def main():
    if len(sys.argv) > 1:
        producer_name = " ".join(sys.argv[1:])
    else:
        producer_name = input("請輸入廠商名稱: ").strip()

    if not producer_name:
        print("❌ 請提供廠商名稱")
        sys.exit(1)

    print(f"\n🏭 測試廠商: {producer_name}")
    print("─" * 60)

    # 1. 食安事件搜尋
    snippets = search_producer(producer_name)
    print(f"\n📥 原始搜尋結果 ({len(snippets)} 筆):")
    for s in snippets:
        print(f"  [{s.get('source_type','?')}] {s.get('title','')[:60]}")

    # 2. Gemini 驗證
    if snippets:
        BATCH_SIZE = 5
        validated = []
        for i in range(0, len(snippets), BATCH_SIZE):
            batch = snippets[i:i+BATCH_SIZE]
            print(f"\n🤖 驗證第 {i//BATCH_SIZE+1} 批（{len(batch)} 筆）...")
            validated.extend(validate_with_gemini(producer_name, batch))
            if i + BATCH_SIZE < len(snippets):
                time.sleep(1)
        print_results("食安事件驗證結果", validated)
    else:
        print("\n（無搜尋結果，跳過驗證）")

    # 3. 消費者投訴
    complaints = search_complaints(producer_name)
    if complaints:
        print(f"\n🤖 驗證消費者投訴（{len(complaints)} 筆）...")
        # 簡化：直接印出原始投訴，不再跑 Gemini（節省 API 額度）
        print_results("消費者投訴原始結果（未驗證）", complaints)


if __name__ == "__main__":
    main()
