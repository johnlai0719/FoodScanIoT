"""
Module C — Stage 1：檢索 → 候選佇列（寫入迴圈的前半）

Stage 1a：廠商導向檢索（發現新事件）
Stage 1b：事件導向檢索（用事件名擴充，找出所有被波及的廠商）

本階段結束時，通過閘門的文件會落入 EventCandidate 佇列，狀態為 pending。
**使用者永遠看不到這張佇列**——它只是人工審核的待辦清單。

本階段完全不使用 LLM：檢索是 Tavily、解析是精確比對、閘門是規則。
LLM 只在後續 clustering.py 提供分群「建議」，且不具決定權。
"""
import os
import time
import json
from email.utils import parsedate_to_datetime

from tavily import TavilyClient
from dotenv import load_dotenv

from database import SessionLocal
import models
from module_c import entity_resolution
from module_c.gate import passes_gate, classify_domain

load_dotenv()

_TAVILY_KEY = os.getenv("TAVILY_API_KEY")
_tavily = TavilyClient(api_key=_TAVILY_KEY) if _TAVILY_KEY else None

# 防刷：同一廠商 7 天內不重複檢索
COOLDOWN_SECONDS = 7 * 24 * 3600

# 無效佔位符：解析不出真實廠商者，直接跳過，不浪費 Tavily 額度
PLACEHOLDER_NAMES = {"未知", "未知製造商", "無", "n/a", "none", "unknown", "test", "測試", ""}

# 台灣主流新聞媒體白名單（2026-07-24 實測定案）。
#
# 背景：原本無此白名單時，候選文件散布在 40+ 個相異網域，含企業官網首頁、
# 百科條目、社群貼文（甚至 AI 生成內容），來源型態混雜。
#
# 實測過程（重要，避免之後重蹈覆轍）：
#   曾誤以為 topic="news" + country="taiwan" 可解決此問題——單次測試看似成功
#   （published_date 命中率 0/10 → 10/10），但重複執行 5 次後發現該次成功
#   純屬偶然：country="taiwan" 對此類查詢並無穩定的地域限定效果，相關結果比例
#   反而從基準的 4/8 掉到 0/8（每次重跑皆同），是負優化而非改善。
#
#   改採 include_domains 白名單（非地域「軟訊號」，而是網域硬性限定）+
#   topic="news"，對兩家不同廠商各重跑 3 次，結果完全一致：
#   published_date 命中率 10/10、內容確實與廠商相關（非各國無關新聞）。
#   此組合才是實際驗證過、可重現的解法。
#
# 取捨：白名單天生犧牲部分涵蓋率（未列入的媒體不會被搜到），但現階段目標是
# 解決「來源型態混雜、日期缺失」的精確度問題，優先於涵蓋率。
TW_NEWS_DOMAINS = [
    "udn.com", "chinatimes.com", "ltn.com.tw", "ettoday.net", "cna.com.tw",
    "setn.com", "tvbs.com.tw", "ctee.com.tw", "businessweekly.com.tw",
    "ctinews.com", "tw.news.yahoo.com", "pts.org.tw", "mohw.gov.tw", "fda.gov.tw",
]


def _search(query: str, max_results: int = 15) -> list[dict]:
    """
    兩層 Tavily 檢索。失敗回傳空 list，不拋例外中斷整批。

    第一層（優先，乾淨）：topic="news" + 台灣媒體白名單。對大幅報導的重大
    事件效果好（實測重大事件如中聯油脂案可達 10/10 相關且皆有日期），
    但對只有小眾/專業媒體報導的次要事件會直接 0 筆（2026-07-24 實測：
    卡迪那丙烯醯胺案在白名單下 0 筆，不限網域則找得到但來源分散、
    無 published_date）。

    第二層（備援，較雜）：第一層 0 筆時才觸發，退回不限網域、不限
    topic 的原始查詢。雜訊由下游 entity_resolution + gate 把關，
    此處不過濾正確性，只確保「至少搜得到」而非直接放棄。
    """
    if not _tavily:
        print("[WARN] Tavily 未初始化（缺 TAVILY_API_KEY）")
        return []

    def _do_search(tag: str, **kw) -> list[dict]:
        t0 = time.time()
        resp = _tavily.search(query=query, max_results=max_results, search_depth="advanced",
                              days=1825, **kw)
        results = resp.get("results", [])
        print(f"[TAVILY:{tag}] {(time.time()-t0)*1000:.0f}ms | {len(results)} 筆 | {query[:50]}")
        return results

    try:
        results = _do_search("whitelist", topic="news", include_domains=TW_NEWS_DOMAINS)
        if results:
            return results
        print(f"[TAVILY] 白名單 0 筆，退回不限網域查詢 | {query[:50]}")
        return _do_search("fallback")
    except Exception as e:
        print(f"[WARN] Tavily 檢索失敗: {query[:40]} → {e}")
        return []


def _doc_text(r: dict) -> str:
    """組合供閘門判定用的文件全文（標題 + 內文）。"""
    return f"{r.get('title', '')}\n{r.get('content', '')}"


def _normalize_date(raw: str | None) -> str | None:
    """
    將 Tavily 回傳的日期正規化為 YYYY-MM-DD。

    topic="news" 模式下 Tavily 回傳 RFC 2822 格式（如
    "Fri, 03 Jul 2026 08:15:13 GMT"，29 字元），超出
    EventCandidate.published_date 的 String(20) 欄位長度，寫入會直接被
    資料庫拒絕（2026-07-24 實測發現）。正規化後的短格式同時也符合
    dedup.py._event_year() 假設「年份在字串最前面」的既有解析邏輯。
    """
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        # 非 RFC 2822 格式（如已經是 YYYY-MM-DD 或其他來源給的格式），
        # 保守起見裁切至欄位長度上限，避免整批寫入失敗。
        return raw[:20]


def _process_results(results: list[dict], db, discovered_at: str,
                     discovery_method: str = "manufacturer_search") -> tuple[int, int]:
    """
    跑 Stage 0（實體解析）+ Stage 2（閘門），通過者寫入候選佇列。

    discovery_method 標記候選的檢索路徑（manufacturer_search / event_search /
    memory_seed），供事後追溯「這筆是用哪種方法搜到的」——2026-07-24 發現
    舊資料此欄位皆為 NULL（此函式原本未設定），事後無法區分，故補上。

    回傳 (通過閘門數, 新增候選數)。
    """
    passed = 0
    inserted = 0

    for r in results:
        url = r.get("url", "")
        # 備援層（不限網域）實測會混入畸形結果，如 "/goto?url=CAESuQEB..."
        # 這類相對路徑轉址追蹤連結（2026-07-24 實測發現），非完整網址，
        # 寫入候選佇列無意義（無法追溯來源），故過濾掉。
        if not url or not url.startswith(("http://", "https://")):
            continue

        text = _doc_text(r)

        # Stage 0：實體解析。失敗即中止此文件（不猜測是哪家廠商）
        hits = entity_resolution.resolve(text)
        if not hits:
            continue

        # Stage 2：硬性閘門（須同時命中廠商名 + 受控詞彙）
        ok, terms = passes_gate(text, hits)
        if not ok:
            continue

        passed += 1
        tier = classify_domain(url)

        # 一個文件可能同時提及多家 canonical 廠商（如中聯油脂事件同時涉及聯華、味全）
        # → 為每家各建一筆候選，人工審核時可分別確認
        for hit in hits:
            exists = (
                db.query(models.EventCandidate)
                .filter(
                    models.EventCandidate.url == url,
                    models.EventCandidate.producer_id == hit.producer_id,
                )
                .first()
            )
            if exists:
                continue

            db.add(
                models.EventCandidate(
                    producer_id=hit.producer_id,
                    is_ambiguous=hit.is_ambiguous,
                    ambiguous_with=hit.ambiguous_with or None,
                    title=(r.get("title") or "")[:500],       # 照抄原文標題
                    url=url[:1000],
                    snippet=(r.get("content") or "")[:4000],
                    published_date=_normalize_date(r.get("published_date")),
                    source_tier=tier,
                    matched_terms=terms,
                    status="pending",
                    discovered_at=discovered_at,
                    discovery_method=discovery_method,
                )
            )
            inserted += 1

    db.commit()
    return passed, inserted


def discover_by_manufacturer(producer_id: int, producer_name: str, force: bool = False) -> dict:
    """
    Stage 1a — 廠商導向檢索（發現）。

    force=True 時忽略 7 天冷卻（測試/首次建庫用）。
    """
    if producer_name.strip().lower() in PLACEHOLDER_NAMES or len(producer_name.strip()) < 2:
        return {"skipped": "placeholder_name"}

    db = SessionLocal()
    try:
        producer = db.query(models.Producer).filter(models.Producer.id == producer_id).first()
        if not producer:
            return {"skipped": "producer_not_found"}

        now = int(time.time())
        if not force and producer.last_audit_date:
            try:
                elapsed = now - int(float(producer.last_audit_date))
                if elapsed < COOLDOWN_SECONDS:
                    return {"skipped": f"cooldown ({elapsed // 3600}h ago)"}
            except (ValueError, TypeError):
                pass

        # 沿用實測驗證過的查詢模板
        query = f"{producer_name} 食安 (不合格 OR 違規 OR 回收 OR 投訴 OR 衛生局 OR 異物 OR 客訴)"
        results = _search(query)

        passed, inserted = _process_results(results, db, discovered_at=str(now),
                                            discovery_method="manufacturer_search")

        producer.last_audit_date = str(now)
        db.commit()

        return {
            "producer": producer_name,
            "fetched": len(results),
            "passed_gate": passed,
            "new_candidates": inserted,
        }
    finally:
        db.close()


def discover_by_event(event_name: str) -> dict:
    """
    Stage 1b — 事件導向檢索（擴充）。

    用事件名（如「中聯油脂 苯駢芘」）一次找出所有被波及的廠商——
    這是廠商導向檢索抓不到的：聯華、味全是「被波及」，用廠商名去搜未必搜得到
    整起事件的全貌。
    """
    db = SessionLocal()
    try:
        results = _search(f"{event_name} 食安 廠商", max_results=15)
        now = str(int(time.time()))
        passed, inserted = _process_results(results, db, discovered_at=now,
                                            discovery_method="event_search")
        return {
            "event": event_name,
            "fetched": len(results),
            "passed_gate": passed,
            "new_candidates": inserted,
        }
    finally:
        db.close()


def discover_all(force: bool = False) -> list[dict]:
    """對 manufacturers.json 中所有 canonical 廠商跑 Stage 1a。"""
    out = []
    for m in entity_resolution.all_manufacturers():
        out.append(discover_by_manufacturer(m["producer_id"], m["canonical_name"], force=force))
    return out


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    for r in discover_all(force=force):
        print(json.dumps(r, ensure_ascii=False))
