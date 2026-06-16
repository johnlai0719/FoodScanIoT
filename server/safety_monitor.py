import os
import json
import re
import requests
import time
from datetime import datetime
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

_HTML_DATE_PATTERNS = [
    r'article:published_time["\s]+content=["\']([^"\']+)',
    r'datePublished["\s]*:["\s]*"([0-9]{4}-[0-9]{2}-[0-9]{2}[^"]*)"',
    r'<time[^>]*datetime=["\']([0-9]{4}-[0-9]{2}-[0-9]{2}[^"\']*)',
    r'publishdate["\s]+content=["\']([^"\']+)',
]
_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def _fetch_publish_date(url: str) -> str:
    """從 URL 的 HTML meta 標籤抓發布日期，回傳 YYYY-MM 或空字串"""
    if not url or url.endswith(".pdf"):
        return ""
    try:
        resp = requests.get(url, headers=_FETCH_HEADERS, timeout=8)
        html = resp.text[:20000]
        for p in _HTML_DATE_PATTERNS:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                raw = m.group(1)[:10]
                if re.match(r'^\d{4}-\d{2}', raw):
                    year = int(raw[:4])
                    if 1950 <= year <= datetime.now().year + 1:
                        return raw[:7]
    except Exception:
        pass
    return ""

def _normalize_url(url: str) -> str:
    """將 URL 標準化以利去重：移除 Threads/Instagram 等的尾部文字 slug"""
    if not url:
        return url
    # Threads: /post/{ID}/{slug} → 只保留 /post/{ID}
    m = re.match(r'(https?://(?:www\.)?threads\.(?:com|net)/@[^/]+/post/[^/]+)', url)
    if m:
        return m.group(1)
    return url

def _sanitize_event_date(date_str: str) -> str:
    """驗證並清理 Gemini 回傳的日期，拒絕 0000-00 或格式錯誤的佔位符"""
    if not date_str:
        return ""
    s = date_str.strip()
    if re.match(r"^\d{4}-\d{2}$", s):
        year = int(s[:4])
        if 1950 <= year <= 2100:
            return s
    return ""

def _search_producer(producer_name: str) -> list[dict]:
    if not _tavily:
        print("[WARN] Tavily client is not initialized")
        return []
    
    from urllib.parse import urlparse
    
    # 整合 5 個查詢條件為單個合併查詢，減少 80% Tavily 呼叫次數
    query = f"{producer_name} 食安 (不合格 OR 違規 OR 回收 OR 投訴 OR 衛生局 OR 異物 OR 客訴)"
    results = []
    
    try:
        _t_tavily_start = time.time()
        search_params = {
            "query": query,
            "max_results": 15,
            "search_depth": "advanced",
            "include_raw_content": True,
        }
        print(f"[INFO] Querying Tavily with consolidated query: {query}")
        resp = _tavily.search(**search_params)
        print(f"[PERF] Tavily Search: {(time.time() - _t_tavily_start)*1000:.0f}ms | results={len(resp.get('results', []))}")
        
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
        raw = (s.get('raw_content') or '')[:2000]
        snippets_formatted += f"--- Snippet {idx} ---\n"
        snippets_formatted += f"Title: {s.get('title', '')}\n"
        snippets_formatted += f"URL: {s.get('url', '')}\n"
        snippets_formatted += f"Source Type: {s.get('source_type', '')}\n"
        snippets_formatted += f"Article Content:\n{raw}\n\n"

    prompt = f"""You are a strict food safety incident validator. You will be given web search result snippets about a company named "{producer_name}".

Your task:
Determine whether each snippet describes a DIRECT food safety incident — meaning a confirmed regulatory violation, product recall, contamination, lab test failure, or government penalty involving a specific product or raw material.

STRICT exclusion rules (set is_food_safety_event to false):
- PR responses, company statements, apologies, or rebuttals about food safety
- Post-incident corporate actions: donations, compensation pledges, fines paid, or CSR responses
- News about what a company promised or did AFTER a food safety incident (e.g. "company donated after scandal")
- Advocacy, lobbying, or petitions related to food safety policy
- Opinion articles, commentary, or analysis that merely mention food safety
- Articles reporting that another party defended or criticized the company's food safety record
- Social media posts without verifiable source
- Articles where the incident described belongs to a DIFFERENT company
- Tag pages, category pages, or article compilations covering multiple companies or events
- Any snippet where the actual safety violation is not clearly and specifically stated

INCLUDE only:
- Government or health authority findings of regulatory violations with specific product
- Official product recalls with specific product and confirmed reason
- Lab test failures detecting prohibited substances, excess chemical limits, or contamination
- Confirmed foreign object incidents with specific product

Additional rules:
- event_date must be the date the INCIDENT OCCURRED, NOT the article publication date. Extract from article content. Use YYYY-MM format, YYYY-01 if only year known, "" if truly unknown.
- summary must be under 80 characters in Traditional Chinese (繁體中文), describing the actual violation only.
- Return JSON ONLY, no markdown fences.
- severity is an integer (1, 2, or 3):
  1 = labeling/tagging violation (標示違規)
  2 = chemical/ingredient violation (成分違規)
  3 = major safety incident with health risk (重大食安事件)

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
        _t_lite_start = time.time()
        response = _client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
        print(f"[PERF] Gemini-2.5-flash-lite Validation: {(time.time() - _t_lite_start)*1000:.0f}ms | snippets={len(snippets)}")
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

def _search_consumer_complaints(producer_name: str) -> list[dict]:
    if not _tavily:
        return []
    query = f"{producer_name} (客訴 OR 抱怨 OR 異物 OR 吃壞肚子 OR 食物中毒 OR 退費 OR 問題產品) site:ptt.cc OR site:dcard.tw OR site:facebook.com OR site:google.com/maps"
    try:
        _t = time.time()
        resp = _tavily.search(query=query, max_results=10, search_depth="basic")
        print(f"[PERF] Tavily Consumer Complaint Search: {(time.time() - _t)*1000:.0f}ms | results={len(resp.get('results', []))}")
        results = []
        seen_urls = set()
        for r in resp.get("results", []):
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                r["source_type"] = "consumer_complaint"
                results.append(r)
        return results
    except Exception as e:
        print(f"[WARN] Consumer complaint search failed for '{producer_name}': {e}")
        return []

def _validate_consumer_complaints(producer_name: str, snippets: list[dict]) -> list[dict]:
    if not _client or not snippets:
        return []

    snippets_formatted = ""
    for idx, s in enumerate(snippets):
        snippets_formatted += f"--- Snippet {idx} ---\n"
        snippets_formatted += f"Title: {s.get('title', '')}\n"
        snippets_formatted += f"Content: {s.get('content', '')}\n"
        snippets_formatted += f"URL: {s.get('url', '')}\n\n"

    prompt = f"""你是消費者投訴紀錄分析師。以下是網路上關於「{producer_name}」的消費者貼文與討論。

任務：判斷每則內容是否包含真實的消費者食品投訴（異物、食物中毒、變質、客訴未處理等）。

納入條件（以下任一即可）：
- 消費者描述吃到異物（頭髮、蟲、塑膠、金屬等）
- 消費者描述吃後身體不適（腹瀉、嘔吐、過敏）
- 消費者描述產品變質（發霉、有異味、過期）
- 廠商客訴處理不當的具體描述

排除條件：
- 單純負評或口味不喜歡
- 與食品安全無關的客訴（送貨延遲、包裝破損）
- 無具體描述的謾罵

規則：
- summary 用繁體中文，50 字內，描述投訴內容
- event_date 從文章中提取，格式 YYYY-MM，不確定填 ""
- severity 固定為 1
- source_type 固定為 "consumer_complaint"
- 只回傳 JSON 陣列，不加 markdown

[
  {{
    "is_complaint": true or false,
    "title": "貼文標題",
    "summary": "投訴摘要",
    "event_date": "YYYY-MM 或不確定填空字串",
    "source_url": "URL",
    "source_type": "consumer_complaint",
    "severity": 1
  }}
]

內容：
{snippets_formatted}"""

    try:
        import re
        _t = time.time()
        response = _client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
        print(f"[PERF] Flash Lite Consumer Complaint Validation: {(time.time() - _t)*1000:.0f}ms | snippets={len(snippets)}")
        text = response.text.strip()
        match = re.search(r"(\[.*\])", text, re.DOTALL)
        if match:
            text = match.group(1)
        data = json.loads(text)
        validated = []
        for item in data:
            if isinstance(item, dict) and item.get("is_complaint"):
                validated.append({
                    "is_food_safety_event": True,
                    "title": item.get("title", "") or "",
                    "summary": item.get("summary", "") or "",
                    "event_date": item.get("event_date", "") or "",
                    "source_url": item.get("source_url", "") or "",
                    "source_type": "consumer_complaint",
                    "severity": 1
                })
        return validated
    except Exception as e:
        print(f"[WARN] Consumer complaint validation failed: {e}")
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

    # 過濾掉 source_url 已存在於 DB 的 snippets，避免重複呼叫 Flash Lite
    db = SessionLocal()
    try:
        existing_urls = set(
            _normalize_url(row[0]) for row in db.query(models.SafetyAlert.source_url)
            .filter(models.SafetyAlert.producer_id == producer_id)
            .all()
        )
    finally:
        db.close()

    new_snippets = [s for s in snippets if _normalize_url(s.get("url", "")) not in existing_urls]
    skipped = len(snippets) - len(new_snippets)
    if skipped:
        print(f"[INFO] {cleaned_name}: 跳過 {skipped} 筆已存在 URL，剩餘 {len(new_snippets)} 筆待驗證")
    if not new_snippets:
        print(f"[INFO] {cleaned_name}: 所有 URL 皆已存在，跳過 Flash Lite 驗證")
        db = SessionLocal()
        try:
            producer = db.query(models.Producer).filter(models.Producer.id == producer_id).first()
            if producer:
                producer.last_audit_date = str(int(time.time()))
                db.commit()
        finally:
            db.close()
        return
    snippets = new_snippets

    print(f"[INFO] {cleaned_name}: {len(snippets)} snippets to validate")
    BATCH_SIZE = 5
    new_inserts = 0
    has_any_validated = False

    for i in range(0, len(snippets), BATCH_SIZE):
        batch = snippets[i:i + BATCH_SIZE]
        url_to_published = {s.get("url", ""): s.get("published_date", "") for s in batch}
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
                        db.flush()
                        event_date = _sanitize_event_date(ev.get("event_date", ""))
                        if not event_date:
                            event_date = _fetch_publish_date(ev.get("source_url", ""))
                        alert = models.SafetyAlert(
                            title=ev["title"][:500],
                            content=ev["summary"][:2000],
                            alert_date=event_date,
                            source_url=_normalize_url(ev.get("source_url", ""))[:500],
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

    # 消費者投訴線（獨立搜尋，寬鬆驗證）
    complaint_snippets = _search_consumer_complaints(cleaned_name)
    if complaint_snippets:
        db = SessionLocal()
        try:
            existing_urls = set(
                row[0] for row in db.query(models.SafetyAlert.source_url)
                .filter(models.SafetyAlert.producer_id == producer_id)
                .all()
            )
        finally:
            db.close()
        new_complaint_snippets = [s for s in complaint_snippets if s.get("url", "") not in existing_urls]
        complaint_url_to_published = {s.get("url", ""): s.get("published_date", "") for s in new_complaint_snippets}
        if new_complaint_snippets:
            validated_complaints = _validate_consumer_complaints(cleaned_name, new_complaint_snippets)
            if validated_complaints:
                db = SessionLocal()
                try:
                    complaint_inserts = 0
                    inserted_titles = set()
                    for ev in validated_complaints:
                        if ev["title"] in inserted_titles:
                            continue
                        exists = db.query(models.SafetyAlert).filter(
                            models.SafetyAlert.title == ev["title"]
                        ).first()
                        if not exists:
                            inserted_titles.add(ev["title"])
                            db.flush()
                            event_date = _sanitize_event_date(ev.get("event_date", ""))
                            if not event_date:
                                event_date = _fetch_publish_date(ev.get("source_url", ""))
                            alert = models.SafetyAlert(
                                title=ev["title"][:500],
                                content=ev["summary"][:2000],
                                alert_date=event_date,
                                source_url=ev.get("source_url", "")[:500],
                                source_type="consumer_complaint",
                                severity=1,
                                producer_id=producer_id,
                                keyword_used=cleaned_name[:50],
                            )
                            db.add(alert)
                            complaint_inserts += 1
                            new_inserts += 1
                    db.commit()
                    print(f"[INFO] {cleaned_name}: {complaint_inserts} consumer complaints inserted")
                except Exception as e:
                    db.rollback()
                    print(f"[WARN] Failed to insert complaints: {e}")
                finally:
                    db.close()

    if new_inserts > 0:
        db = SessionLocal()
        try:
            affected = db.query(models.Product).filter(
                models.Product.producer_id == producer_id
            ).all()
            cleared = 0
            for prod in affected:
                # 清除 PostgreSQL 的 AI 摘要快取，確保下次查詢重新生成含新事件的摘要
                prod.safety_events_summary = None
                prod.overall_summary = None
                try:
                    r = requests.delete(f"{FOG_BASE}/cache/{prod.barcode}", timeout=3)
                    if r.status_code == 200:
                        cleared += 1
                except Exception as e:
                    print(f"[WARN] Failed to clear Fog cache for {prod.barcode}: {e}")
            db.commit()
            print(f"[INFO] Cleared AI summaries + Fog cache for {len(affected)} products under {producer_name}")
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
