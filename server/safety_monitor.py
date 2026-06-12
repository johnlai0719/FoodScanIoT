import os
import json
import requests
import time
from google import genai
from tavily import TavilyClient
from database import SessionLocal
import models
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

_client = None
if GEMINI_API_KEY:
    _client = genai.Client(api_key=GEMINI_API_KEY)

_tavily = None
if TAVILY_API_KEY:
    _tavily = TavilyClient(api_key=TAVILY_API_KEY)

FOG_BASE = os.getenv("FOG_URL", "http://localhost:3001")

def _search_producer(producer_name: str) -> list[dict]:
    if not _tavily:
        print("[WARN] Tavily client is not initialized")
        return []
    
    from urllib.parse import urlparse
    
    # 整合 5 個查詢條件為單個合併查詢，減少 80% Tavily 呼叫次數
    query = f"{producer_name} 食安 (不合格 OR 違規 OR 回收 OR 投訴 OR 衛生局 OR 異物 OR 客訴)"
    results = []
    
    try:
        search_params = {
            "query": query,
            "max_results": 15,
            "search_depth": "advanced"
        }
        print(f"[INFO] Querying Tavily with consolidated query: {query}")
        resp = _tavily.search(**search_params)
        
        official_domains = {"fda.gov.tw", "mohw.gov.tw", "consumer.gov.tw", "gov.tw"}
        news_domains = {"udn.com", "ltn.com.tw", "cna.com.tw", "setn.com", "tvbs.com.tw", "yahoo.com", "chinatimes.com", "ettoday.net"}
        
        seen_urls = set()
        for r in resp.get("results", []):
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()
            
            is_official = False
            for od in official_domains:
                if domain == od or domain.endswith("." + od):
                    is_official = True
                    break
            
            if is_official:
                r["source_type"] = "official"
            else:
                is_news = False
                for nd in news_domains:
                    if domain == nd or domain.endswith("." + nd):
                        is_news = True
                        break
                if is_news:
                    r["source_type"] = "news"
                else:
                    r["source_type"] = "social"
                    
            results.append(r)
    except Exception as e:
        print(f"[WARN] Consolidated Tavily query failed for '{producer_name}': {e}")
        
    return results

def _validate_batch_with_gemma(producer_name: str, snippets: list[dict]) -> list[dict]:
    if not _client:
        print("[WARN] GenAI Client is not initialized")
        return []
    if not snippets:
        return []

    snippets_formatted = ""
    for idx, s in enumerate(snippets):
        snippets_formatted += f"--- Snippet {idx} ---\n"
        snippets_formatted += f"Title: {s.get('title', '')}\n"
        snippets_formatted += f"Content: {s.get('content', '')}\n"
        snippets_formatted += f"URL: {s.get('url', '')}\n"
        snippets_formatted += f"Published Date: {s.get('published_date', '')}\n"
        snippets_formatted += f"Source Type: {s.get('source_type', '')}\n\n"

    prompt = f"""You are a food safety data validator. You will be given a list of web search result snippets about a company named "{producer_name}". Each snippet has a "Source Type" associated with it (either official, news, or social).

Your task:
For each snippet, determine whether it describes a real food safety event (product recall, regulatory violation, contamination, lab test failure, or similar).

Rules:
- Gemini must only validate and extract information. Do NOT invent or infer information not present in the snippets. You are strictly forbidden from generating safety events on your own.
- If a snippet does not describe a food safety event, set is_food_safety_event to false and leave other fields empty.
- event_date must be in YYYY-MM format. If only year is known, use YYYY-01. If unknown, use "".
- summary must be under 100 characters/words in Traditional Chinese (繁體中文) and based only on the snippet.
- Return a JSON array containing one object for each snippet.
- Return JSON ONLY, no markdown fences.
- severity is an integer (1, 2, or 3) where:
  1 = labeling/tagging violation (標示違規)
  2 = chemical/ingredient violation (成分違規)
  3 = major safety incident (重大事件)

Required JSON Array Format:
[
  {{
    "is_food_safety_event": true or false,
    "title": "事件標題（原文語言）",
    "summary": "中文摘要（100字內）",
    "event_date": "YYYY-MM or YYYY-01 or empty string",
    "source_url": "URL of the snippet source",
    "source_type": "official / news / social",
    "severity": 1
  }},
  ...
]

Snippets to validate:
{snippets_formatted}
"""

    try:
        print(f"[DEBUG] Sending batch of {len(snippets)} snippets to Gemini...")
        response = _client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
        print(f"[DEBUG] Gemini response received successfully.")
        text = response.text.strip()
        import re
        match = re.search(r"(\[.*\])", text, re.DOTALL)
        if match:
            text = match.group(1)
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("Parsed JSON is not a list")

        validated = []
        for item in data:
            if isinstance(item, dict) and item.get("is_food_safety_event"):
                validated.append({
                    "is_food_safety_event": True,
                    "title": item.get("title", "") or "",
                    "summary": item.get("summary", "") or "",
                    "event_date": item.get("event_date", "") or "",
                    "source_url": item.get("source_url", "") or "",
                    "source_type": item.get("source_type", "") or "",
                    "severity": item.get("severity")
                })
        return validated
    except Exception as e:
        print(f"[WARN] Gemini batch validation failed or invalid response: {e}")
        return []

def update_producer_safety_events(producer_id: int, producer_name: str):
    db = SessionLocal()
    canonical_name = producer_name
    try:
        producer = db.query(models.Producer).filter(models.Producer.id == producer_id).first()
        if producer:
            if producer.name:
                canonical_name = producer.name
            if producer.last_audit_date:
                try:
                    last_audit = float(producer.last_audit_date)
                    # 限制 7 天內不可重複查詢 Tavily (604800 秒)
                    if time.time() - last_audit < 604800:
                        print(f"[INFO] {canonical_name} 的食安事件在 7 天內已更新過，跳過 Tavily 搜尋。")
                        return
                except ValueError:
                    pass
    finally:
        db.close()

    # 跳過無效/通用佔位符名稱，節省 Tavily 額度
    cleaned_name = canonical_name.strip()
    if not cleaned_name or cleaned_name in ["未知", "未知製造商", "無", "N/A", "none", "unknown", "test", "測試"] or len(cleaned_name) < 2:
        print(f"[INFO] 檢測到通用或無效生產商名稱 '{canonical_name}'，跳過 Tavily 搜尋。")
        return

    snippets = _search_producer(cleaned_name)
    if not snippets:
        print(f"[INFO] No Tavily results for: {cleaned_name}")
        # 即使沒找到，也更新審查時間以避免頻繁重試
        db = SessionLocal()
        try:
            producer = db.query(models.Producer).filter(models.Producer.id == producer_id).first()
            if producer:
                producer.last_audit_date = str(int(time.time()))
                db.commit()
        finally:
            db.close()
        return

    print(f"[INFO] {cleaned_name}: {len(snippets)} snippets to validate")
    BATCH_SIZE = 5
    new_inserts = 0
    has_any_validated = False

    for i in range(0, len(snippets), BATCH_SIZE):
        batch = snippets[i:i + BATCH_SIZE]
        print(f"[INFO] Validating batch {i // BATCH_SIZE + 1} of {(len(snippets) + BATCH_SIZE - 1) // BATCH_SIZE} for {cleaned_name}...")
        
        batch_validated = _validate_batch_with_gemma(cleaned_name, batch)
        if batch_validated:
            has_any_validated = True
            db = SessionLocal()
            try:
                for ev in batch_validated:
                    exists = db.query(models.SafetyAlert).filter(
                        models.SafetyAlert.title == ev["title"]
                    ).first()
                    if not exists:
                        alert = models.SafetyAlert(
                            title=ev["title"][:500],
                            content=ev["summary"][:2000],
                            alert_date=ev["event_date"][:50],
                            source_url=ev.get("source_url", "")[:500],
                            source_type=ev.get("source_type", "")[:20],
                            severity=ev.get("severity"),
                            producer_id=producer_id,
                            keyword_used=cleaned_name[:50],
                        )
                        db.add(alert)
                        new_inserts += 1
                db.commit()
            except Exception as batch_e:
                db.rollback()
                print(f"[WARN] Failed to insert batch alerts: {batch_e}")
            finally:
                db.close()
                
        if i + BATCH_SIZE < len(snippets):
            print("[INFO] Rate limiting: sleeping for 1 second between batches...")
            time.sleep(1)

    # 審查完成，不論有無新增警告，皆更新該生產商的最後審查日期
    db = SessionLocal()
    try:
        producer = db.query(models.Producer).filter(models.Producer.id == producer_id).first()
        if producer:
            producer.last_audit_date = str(int(time.time()))
            db.commit()
    finally:
        db.close()

    print(f"[INFO] {cleaned_name}: {new_inserts} new alerts inserted in total")

    if new_inserts > 0:
        db = SessionLocal()
        try:
            affected = db.query(models.Product).filter(
                models.Product.producer_id == producer_id
            ).all()
            cleared = 0
            for prod in affected:
                try:
                    r = requests.delete(f"{FOG_BASE}/cache/{prod.barcode}", timeout=3)
                    if r.status_code == 200:
                        cleared += 1
                except Exception as e:
                    print(f"[WARN] Failed to clear cache for {prod.barcode}: {e}")
            print(f"[INFO] Cleared Fog cache for {cleared} products under {producer_name}")
        finally:
            db.close()

def update_safety_events():
    db = SessionLocal()
    producers = db.query(models.Producer).all()
    print(f"[INFO] Starting safety monitor for {len(producers)} producers")
    db.close()
    for p in producers:
        update_producer_safety_events(p.id, p.name)
    print("[INFO] Safety monitor run complete")

if __name__ == "__main__":
    update_safety_events()
