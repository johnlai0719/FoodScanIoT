"""
Module C — Track 2：Google Search Grounding 直接發布管線

背景：與 Tavily 管線（ingest.py + seed_generation.py）的關係見
_retrieval_tuning_log.md。2026-07-25 決議：實測 Grounding 品質穩定優於
Tavily 自建檢索（同名廠商測試無需白名單/兩層 fallback 即可拿到乾淨、
附日期、附法律結果的內容），改以此管線為主要發現機制。Tavily 管線程式碼
保留但不再排程執行，留作備援/對照，不刪除。

2026-07-25 決議（重要，偏離本檔案家族原本「人工審核為發布前強制關卡」的
既有設計，見 models.py 對 EventCandidate 的說明）：
本管線核准後**直接寫入已發布三張表**，不經過 EventCandidate pending 佇列。
review_console.py 的角色從「發布前關卡」轉為「事後稽核」——人工仍可事後
編輯/下架/補標 role，但不再擋在使用者看到之前。這是刻意的風險取捨（信任
Grounding 自帶的搜尋佐證能力），非疏漏。

不變的部分：
- entity_resolution 複核仍是硬性關卡——Grounding 本身也會混淆同名不同
  法人（實測案例：太古可口可樂把香港子公司事件跟台灣子公司搞混）。解析
  失敗、或判定為 ambiguous（同時提及易混淆兄弟法人）者一律跳過，不自動
  歸入本廠商。
- role 欄位依然一律寫 None，不由 LLM 自動判定責任歸屬——這與「是否要
  人工審核才能發布」是兩個獨立的決定，本次決議只鬆綁後者。
- 沒有可追溯來源（grounding_supports 對應不到任何 chunk）的段落一律
  不發布，不接受「模型自己說的、查無引用佐證」的內容。

2026-07-25 第二次修訂（依 _retrieval_experiment.py / _split_compare.py /
_source_coverage.py 三組離線量測結果）：
- 切段改以模型結構化萃取為主、正則排版比對為備援（_split_events_llm）。
  同一批原始回應（3 家廠商 × 5 種提問角度）可用事件數 50 → 86 件，
  來源覆蓋率 89% → 87%（持平），定位成功率 100%。
- 檢索改為多角度提問後聯集去重（QUERY_ANGLES）。單一通用提問對久遠事件與
  裁罰類事件覆蓋明顯不足：義美食品通用提問 5 件、改問 2010-2016 區間 8 件，
  且兩者幾乎不重疊。
- 附帶效果：切段步驟已同時產出結構化欄位，不再逐段呼叫 _extract_event_meta
  （該函式僅備援路徑仍會用到），單一角度的 API 呼叫次數反而下降。

已知限制（2026-07-25 實測發現，非本次目標，記錄供後續評估）：
- 同一真實事件若波及多家廠商（如中聯油脂事件同時涉及聯華、統一），逐一對
  每家廠商呼叫本函式會各自建立獨立的 Event，不會像 ingest.py Stage 1b
  （discover_by_event）那樣合併成同一 Event 掛多筆 EventManufacturer。
  review_console.py 的合併工具（merge.py）目前只處理 pending 的
  EventCandidate，對已發布的 Event 沒有對應的合併機制。
- entity_resolution 的 ambiguous_with 判定對集團型廠商（如統一企業，事件
  段落常順帶提及子品牌 7-ELEVEN／統一超商）曾頻繁觸發，導致本屬該廠商的
  事件被整段跳過而不發布。2026-07-25 已改為「目標公司提及次數須嚴格多於
  兄弟法人」才判定歧義（見 _is_truly_ambiguous），大幅降低誤殺率；真的
  難以區分主體的段落（雙方提及次數相當）仍會被跳過，此為刻意保留的行為。
"""
import json
import os
import re
import time

import requests
from google import genai
from google.genai import types
from dotenv import load_dotenv

from database import SessionLocal
import models
from module_c import entity_resolution
from module_c.gate import classify_domain
from module_c.dedup import _incident_signature, _event_year

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

DISCOVERY_MODEL = "gemini-2.5-flash"
EXTRACTION_MODEL = "gemini-2.5-flash"

# 段落標頭格式在不同次呼叫間不穩定，2026-07-25 同一天內觀察到至少三種：
# "**1. 事件名稱**"、"### 1. 事件名稱"、"1.  **事件名稱**"（號碼在外、標題
# 才加粗）。改用寬鬆比對：行首允許任意 #／* 前綴，數字＋句點後允許選擇性的
# **，最後允許選擇性的收尾 **，同時涵蓋三種寫法而不必逐一列舉。
#
# 切段失敗的代價不只是漏切：曾實測發現若整段沒切開（如同時問「統一企業與
# 統一超商」時混成一大塊），即使目標公司提及次數較多而通過歧義判定，內容
# 裡仍可能夾帶完全屬於兄弟法人自己的獨立事件（如統一超商 2011 年生菌數
# 超標與本次無關），一併被算進目標公司名下——故格式涵蓋率越高越能降低
# 這類誤歸類風險，此正則需要盡量涵蓋觀察到的格式變體。
_EVENT_HEADER_RE = re.compile(r"^\s*[#\*]*\s*(\d+)\.\s*\**\s*([^\n\*]+?)\**\s*$", re.MULTILINE)

# 多角度提問（2026-07-25 新增）。單一通用提問對「較久遠」與「裁罰類」事件的
# 覆蓋明顯不足：實測義美食品，通用提問只拿到 5 件，改問 2010-2016 年區間則
# 拿到 8 件且幾乎完全不重疊（鼠屍、頭髮、綠線、大腸桿菌超標皆為通用提問漏掉）。
# 搜尋引擎對不同措辭會命中不同文件集合，故以多個角度分別提問後聯集去重。
# 角度之間允許重疊——重疊部分由去重處理，真正的價值在各角度「額外」帶進來的事件。
QUERY_ANGLES = {
    "generic": (
        "告訴我{name}近年來（近15年）在台灣的食品安全事件，"
        "請列出每一起事件的名稱、時間、具體內容，並附上來源。"
    ),
    "era_old": (
        "台灣2010年至2016年間，{name}曾涉及哪些食品安全事件、食品爭議或產品回收？"
        "請逐一列出事件名稱、發生時間與具體內容，並附上來源。"
    ),
    "era_recent": (
        "2017年至今，{name}在台灣曾發生哪些食品安全問題、衛生稽查不合格、"
        "產品下架或回收事件？請逐一列出事件名稱、時間與內容，並附上來源。"
    ),
    "by_type": (
        "在台灣，{name}的產品是否曾被檢出添加物超標、農藥或動物用藥殘留、重金屬、塑化劑、"
        "微生物超標、異物混入，或有標示不實的情形？請逐一列出事件名稱、時間與內容，並附上來源。"
    ),
    "by_penalty": (
        "{name}曾因違反台灣食品安全衛生管理法被衛生主管機關裁罰、要求限期改善或命令下架嗎？"
        "請逐一列出案件名稱、時間、違規事由與處分結果，並附上來源。"
    ),
}

# 模型切段用的指示。刻意在輸入端加行號、要求回傳行號區間，而非要求逐字引用
# 原文片段定位：實測（2026-07-25，義美三種排版）逐字比對在項目符號式排版下
# 8 件全部對不上，模型傾向回傳整理過而非原樣的文字。行號是離散且唯一的，
# 模型不需要複製任何字元，定位率因此從 67% 提升到 100%（45 件全數定位成功）。
_SPLIT_PROMPT = """以下是一段描述某公司食品安全事件的文字，每行前面都加了行號。
請把它拆解成一件一件獨立的事件，並指出每則事件佔用哪幾行。

【判斷原則】
- 一則事件 = 一起真實發生、有具體時間與情節的事情。
- 只輸出**食品安全**相關事件：食品本身的衛生、成分、原料、製程、標示、檢驗
  或回收問題。純粹的品牌形象、廣告用語、包裝設計、行銷或經營爭議**不算**，
  不要輸出。
- **本公司未涉入者一律不要輸出。** 若原文本身即指出問題產品非本公司生產、
  製造或進口（例如為國外同名公司的產品、通路商自行進口的水貨、其他廠商的
  產品），或原文重點是本公司發表聲明澄清自己未受影響、主管機關證實未流入，
  則該段落**不是本公司的食安事件**，即使文中大量提及本公司名稱也不要輸出。
  判斷依據是「問題產品是否出自本公司」，不是「文中有沒有提到本公司」。
- 本公司確實涉入者仍要輸出，即使其角色是受上游波及、或已主動通報配合處理
  ——是否該由誰負責不在判斷範圍內，只判斷產品是否出自本公司。
- 同一起事件在文中被重複描述（例如先摘要後詳述）只能算一則。
- 純粹的前言、結語、免責聲明、資料來源說明，不是事件，不要輸出。
- 若文中明確表示查無事件，回傳空陣列。

【原文】
{body}

【輸出格式】只回傳 JSON 陣列，依事件在原文中出現的先後排序，不要 markdown：
[{{"name": "事件名稱（具體，含年份，如「2011 塑化劑事件」）",
   "start_date": "YYYY-MM-DD 或 YYYY-MM 或 YYYY，無法判斷則 null",
   "summary": "1-2 句話具體內容",
   "line_start": 該事件描述起始行號（整數）,
   "line_end": 該事件描述結束行號（整數，含）}}]"""


def _resolve_url(redirect_url: str, timeout: float = 5.0) -> str:
    """
    把 grounding 回傳的 vertexaisearch.cloud.google.com 轉址網址解析成真實網址。

    這層轉址若原樣存入 EventSource.url，classify_domain() 會全部判為
    unknown（網域永遠是 vertexaisearch.cloud.google.com），來源分級形同
    失效，故必須解析。失敗（逾時、被擋）則原樣回傳轉址網址，不中斷整批。
    """
    try:
        resp = requests.head(redirect_url, allow_redirects=True, timeout=timeout)
        if resp.url:
            return resp.url
    except Exception:
        pass
    return redirect_url


CAPTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_captures")


def _save_capture(producer_name: str, prompt: str, text: str, chunks, supports):
    """
    把每一次帶搜尋查詢的原始回應存檔，供日後離線重新解析。

    帶搜尋的查詢是本管線唯一真正花錢的呼叫；解析（切段、來源對應、去重）則是
    最容易出錯、最常需要修改的部分。不存原始回應的代價很實際：2026-07-26 發現
    來源位置換算錯誤（位元組 vs 字元）時，資料庫裡 99 筆事件的來源全部要作廢，
    而修正本身只是幾行程式——若當初留有原始回應，重新解析即可，一次 API 都不用花。
    故此處無條件存檔，讓「改解析邏輯」與「重新檢索」徹底脫鉤。
    """
    try:
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        safe = re.sub(r"[^\w一-鿿]+", "_", producer_name)[:24]
        path = os.path.join(CAPTURE_DIR, f"run_{safe}_{int(time.time() * 1000)}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "producer": producer_name, "prompt": prompt, "text": text,
                "chunks": [{"title": (c.web.title if c.web else None),
                            "uri": (c.web.uri if c.web else None)} for c in chunks],
                "supports": [{"start": s.segment.start_index, "end": s.segment.end_index,
                              "chunks": list(s.grounding_chunk_indices or [])}
                             for s in supports
                             if s.segment and s.segment.start_index is not None
                             and s.segment.end_index is not None],
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 存檔失敗不得影響主流程


def _search_events(producer_name: str, prompt: str | None = None):
    """Step 1：呼叫 Gemini + google_search grounding，取得自由文字答案與引用中繼資料。"""
    if prompt is None:
        prompt = QUERY_ANGLES["generic"].format(name=producer_name)
    prompt += "若查無相關事件，請明確回答「查無相關事件」，不要臆測或提供不相關的事件。"
    resp = _client.models.generate_content(
        model=DISCOVERY_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    text = resp.text or ""
    gm = resp.candidates[0].grounding_metadata if resp.candidates else None
    chunks = list(gm.grounding_chunks) if gm and gm.grounding_chunks else []
    supports = list(gm.grounding_supports) if gm and gm.grounding_supports else []
    _save_capture(producer_name, prompt, text, chunks, supports)
    return text, chunks, supports


def _split_events_llm(text: str) -> list[dict]:
    """
    主要切段方式：交給模型做結構化萃取，以行號區間還原字元範圍。

    取代原本的正則排版比對。理由（2026-07-25 實測，3 家廠商 × 5 種提問角度）：
    模型的排版在不同次呼叫間不穩定，一天內就觀察到四種以上寫法，每遇到一種
    就補一次正則是補不完的；且排版一旦沒對上，整段會被當成單一段落，夾帶其他
    公司事件的誤歸類風險反而更高。改用模型切段後，同一批原始回應可用事件數
    從 50 件提升到 86 件（有來源者），來源覆蓋率維持（89% → 87%）。

    另外此步驟同時回傳 name / start_date / summary，故不再需要對每個段落
    各打一次 _extract_event_meta，整體 API 呼叫次數反而下降。

    回傳空 list 代表萃取失敗（非「查無事件」），呼叫端應退回正則切段。
    """
    lines = text.split("\n")
    numbered = "\n".join(f"{i}| {ln}" for i, ln in enumerate(lines))
    # 每行的起始字元位置（+1 為換行字元），用來把行號換算回字元範圍
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    try:
        resp = _client.models.generate_content(
            model=EXTRACTION_MODEL, contents=_SPLIT_PROMPT.format(body=numbered),
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
        )
        raw = json.loads(resp.text)
    except (json.JSONDecodeError, Exception):
        return []
    if not isinstance(raw, list):
        return []

    events = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        try:
            ls, le = int(item.get("line_start")), int(item.get("line_end"))
        except (TypeError, ValueError):
            continue  # 定位不到就拿不到來源，直接捨棄，不留無來源事件
        if not (0 <= ls < len(lines) and ls <= le):
            continue
        le = min(le, len(lines) - 1)
        start, end = offsets[ls], offsets[le] + len(lines[le])
        events.append({
            "header": name, "start": start, "end": end, "body": text[start:end],
            "meta": {"name": name[:500], "start_date": item.get("start_date") or None,
                     "summary": (item.get("summary") or "").strip()},
        })
    return sorted(events, key=lambda e: e["start"])


def _split_events(text: str) -> list[dict]:
    """
    備援切段方式：依「**N. 事件名稱**」格式做正則比對。

    僅在 _split_events_llm 萃取失敗時使用，保留以免模型端異常時整批無產出。

    若切不出任何段落（模型這次沒用這個格式回答），整段文字視為單一
    compilation——Event.kind 本來就有這個設計用途（彙整報導，不帶事件
    同等份量），不強行拆解成可能不準確的多個「事件」。
    """
    matches = list(_EVENT_HEADER_RE.finditer(text))
    if not matches:
        return [{"header": None, "start": 0, "end": len(text), "body": text}]

    segments = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segments.append({"header": m.group(2).strip(), "start": start, "end": end, "body": text[start:end]})
    return segments


def _sources_for_span(text: str, start: int, end: int, supports, chunks) -> list[dict]:
    """
    找出字元範圍 [start, end) 內的 grounding_supports，回傳對應來源（去重）。

    這是句子級引用還原成段落級來源清單的關鍵：只要該段落內任一句話的
    grounding_supports 落在範圍內，就把它引用的 chunk 收進來源清單。

    ⚠️ grounding_supports 的 start_index／end_index 是 **UTF-8 位元組位置**，
    不是字元位置。中文一字佔 3 位元組，兩者相差約三倍，直接拿字元位置比對
    會讓每個段落都從文章前三分之一撈到不相干的來源（2026-07-26 實測：佳格
    食品的 2013 銅葉綠素段落與 2023 沙門氏菌段落拿到完全相同的來源清單；
    統一企業 2026 年事件掛上的來源全部是 2013 年的報導）。故先把段落的字元
    範圍換算成位元組範圍再比對。

    此為既有缺陷，非本次切段改動所致——舊的正則切段路徑同樣受影響。
    """
    b_start = len(text[:start].encode("utf-8"))
    b_end = len(text[:end].encode("utf-8"))

    chunk_idx_set = set()
    for s in supports:
        seg = s.segment
        if seg is None or seg.start_index is None or seg.end_index is None:
            continue
        if seg.start_index < b_end and seg.end_index > b_start:
            for idx in (s.grounding_chunk_indices or []):
                chunk_idx_set.add(idx)

    out = []
    seen_urls = set()
    for idx in sorted(chunk_idx_set):
        if idx >= len(chunks):
            continue
        web = chunks[idx].web
        if not web or not web.uri:
            continue
        resolved = _resolve_url(web.uri)
        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)
        out.append({"title": web.title or resolved, "url": resolved})
    return out


def _is_truly_ambiguous(body: str, target_hit) -> bool:
    """
    比對目標公司名稱與易混淆兄弟法人名稱在段落中各自出現的次數。

    2026-07-25 修正：原本只要兄弟法人名稱出現一次（哪怕只是順帶一句）就
    整段判定歧義、不發布，實測對統一企業這類集團廠商殺傷力過大（如「統清
    公司向頂新調度牛油，用於統一19項產品中……以及7-ELEVEN的麻辣關東煮」
    這種目標公司才是主體、兄弟品牌只是順帶一提的段落，也會被整段丟棄）。
    改為：目標名稱出現次數須嚴格多於每個兄弟法人名稱，才視為非歧義。
    """
    target_count = body.count(target_hit.matched_alias)
    return any(target_count <= body.count(excluded) for excluded in target_hit.ambiguous_with)


def _extract_event_meta(body: str) -> dict:
    """
    Step 2：把單一事件段落的自由文字轉成結構化 {name, start_date, summary}。

    JSON mode、不帶 tool——與 seed_generation.py 既有「先自由文字、再轉
    結構化」兩段式作法一致，因 function calling 與
    response_mime_type="application/json" 在同一次呼叫互斥（已知 SDK 限制）。
    """
    prompt = f"""以下是一段描述單一食品安全事件的文字，請萃取成結構化資訊。

【原文】
{body}

【輸出格式】只回傳 JSON，不要 markdown：
{{"name": "事件名稱（具體、含時間或關鍵詞，如「2011 塑化劑事件」）",
 "start_date": "YYYY-MM 或 YYYY-MM-DD，若原文只提供年份則用 YYYY，無法判斷則為 null",
 "summary": "1-2句話摘要具體內容"}}"""
    try:
        resp = _client.models.generate_content(
            model=EXTRACTION_MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
        )
        data = json.loads(resp.text)
        return {
            "name": (data.get("name") or "").strip()[:500],
            "start_date": (data.get("start_date") or None),
            "summary": (data.get("summary") or "").strip(),
        }
    except Exception:
        return {"name": body[:60].strip(), "start_date": None, "summary": ""}


def _is_duplicate_event(candidate: dict, existing: dict) -> bool:
    """
    比對是否為已發布的同一起事件。

    刻意不採用 dedup.py.is_similar_event() 完整邏輯裡的字元 bigram 相似度
    判定——2026-07-25 實測發現該判定門檻（0.10）對本系統的標題風格（幾乎
    都是「20XX年⋯事件」樣板）過於敏感，光是共用「20」「事件」這兩個
    bigram 就足以讓完全無關的兩起事件（2011年塑化劑 vs 2014年劣質油品）
    被誤判為重複。只保留「標題重疊」與「事件關鍵字群組＋年份相容」兩種
    判定，這兩種在同一批實測中沒有誤判。
    """
    t1 = (candidate.get("title") or "").strip()
    t2 = (existing.get("title") or "").strip()
    if t1 and t2 and (t1 in t2 or t2 in t1):
        return True
    sig1, sig2 = _incident_signature(candidate), _incident_signature(existing)
    if sig1 & sig2:
        y1, y2 = _event_year(candidate), _event_year(existing)
        return y1 is None or y2 is None or y1 == y2
    return False


_DEDUP_PROMPT = """判斷「新事件」是否與清單中某一則「既有事件」為**同一起真實事件**。

【新事件】
名稱：{name}
時間：{date}
內容：{summary}

【既有事件清單】
{existing}

【判斷原則】
- **以事件本身的情節為主要依據，時間僅供參考。** 這些時間是從報導文字推估的，
  經常出錯（例如把司法判決年份、原料採購起始年份誤當成事件發生年份）。
  若情節明確指向同一起事故，即使標示的年份不同，仍應判定為同一事件。
- 同一起事件常有多種說法（如「頂新劣質油」與「頂新黑心油」、「中聯油脂苯駢芘超標」
  與「中聯毒油風暴」），名稱不同不代表是不同事件。
- 同一起事件的後續發展（起訴、判決、求償、下架、道歉）併入原事件，不另計。
- 只是同一家公司、同一類問題（如都是異物混入、都是生菌數超標）但屬**不同次**
  事故者，不是同一事件——判斷依據是涉及的產品、起因、通報單位是否相同。

【輸出格式】只回傳 JSON，不要 markdown：
{{"same_as": 既有事件的編號（整數），若都不是同一事件則為 null,
  "reason": "簡短理由"}}"""


def _find_duplicate_llm(candidate: dict, existing: list) -> tuple:
    """
    語意層級的重複判定：交給模型比對，補上關鍵字比對做不到的部分。

    為何需要（2026-07-25 味全實測）：改成多角度提問後，同一起事件會以不同措辭
    從不同角度各被撈回一次（2026 年中聯油脂事件同時以「苯駢芘超標」「毒油風暴」
    「致癌油」三種名稱出現），而關鍵字群組比對只認得人工登錄過的詞彙，
    「中聯油脂」「苯駢芘」「毒油」都不在表內，於是同一件事被發布三次。
    人工維護關鍵字表補不完——每出現一種新說法就要補一次。

    僅在便宜的比對（標題重疊、關鍵字群組）都沒命中時才呼叫，避免無謂開銷。
    回傳 (既有事件, 理由)；判定為不重複則回傳 (None, None)。
    """
    if not existing or not _client:
        return None, None
    listing = "\n".join(
        f"{i}. {e.name}（{e.start_date or '時間不詳'}）" for i, e in enumerate(existing)
    )
    try:
        resp = _client.models.generate_content(
            model=EXTRACTION_MODEL,
            contents=_DEDUP_PROMPT.format(
                name=candidate.get("title", ""), date=candidate.get("date") or "不詳",
                summary=candidate.get("summary", "")[:300], existing=listing),
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
        )
        data = json.loads(resp.text)
    except Exception:
        return None, None
    idx = data.get("same_as")
    if isinstance(idx, int) and 0 <= idx < len(existing):
        return existing[idx], (data.get("reason") or "").strip()
    return None, None


def discover_by_grounding(producer_id: int, producer_name: str, angles: list[str] | None = None) -> dict:
    """
    Track 2 主入口：對單一廠商跑 Grounding 檢索，通過複核者直接寫入已發布三張表。

    angles 指定要用哪些提問角度（預設全部，見 QUERY_ANGLES）；傳
    ["generic"] 可還原成 2026-07-25 之前的單一提問行為，供對照用。

    回傳處理摘要（found/published/skipped 各項），供排程或 CLI 呼叫端記錄，
    不做例外吞噬——GEMINI_API_KEY 未設定時明確回報 skipped，不靜默失敗。
    """
    if not _client:
        return {"skipped": "GEMINI_API_KEY 未設定"}

    angles = list(angles or QUERY_ANGLES.keys())
    db = SessionLocal()
    try:
        published = 0
        skipped_not_target = 0
        skipped_ambiguous = 0
        skipped_no_sources = 0
        skipped_duplicate = 0
        events_found = 0
        # 每個段落的處理結果，供 Streamlit 儀表板顯示「這次查詢實際發生了什麼」
        # （哪些段落被跳過、為什麼），而不只是最終發布的數字。
        details = []
        per_angle = {}

        existing_events = (
            db.query(models.Event)
            .join(models.EventManufacturer, models.EventManufacturer.event_id == models.Event.id)
            .filter(models.EventManufacturer.producer_id == producer_id)
            .all()
        )

        for angle in angles:
            tmpl = QUERY_ANGLES.get(angle)
            if not tmpl:
                continue
            try:
                text, chunks, supports = _search_events(producer_name, tmpl.format(name=producer_name))
            except Exception as e:
                per_angle[angle] = {"error": str(e)[:120]}
                continue
            if not text or text.strip().startswith("查無相關事件"):
                per_angle[angle] = {"found": 0, "published": 0}
                continue

            # 模型切段為主，失敗才退回正則排版比對（見 _split_events_llm）
            segments = _split_events_llm(text) or _split_events(text)
            events_found += len(segments)
            angle_published = 0

            for seg in segments:
                body = seg["body"]
                seg_label = seg["header"] or body[:40].strip()

                # Stage 0：實體解析複核，grounding 本身也會混淆同名不同法人。
                hits = entity_resolution.resolve(body)
                target_hit = next((h for h in hits if h.producer_id == producer_id), None)
                if target_hit is None:
                    skipped_not_target += 1
                    details.append({"angle": angle, "event_name": seg_label,
                                    "status": "not_target", "reason": "內容未提及本廠商"})
                    continue
                if target_hit.is_ambiguous and _is_truly_ambiguous(body, target_hit):
                    skipped_ambiguous += 1
                    details.append({"angle": angle, "event_name": seg_label, "status": "ambiguous",
                                    "reason": f"同時提及易混淆對象：{'、'.join(target_hit.ambiguous_with)}"})
                    continue  # 兄弟法人提及次數不少於目標公司，主體不清，不發布

                sources = _sources_for_span(text, seg["start"], seg["end"], supports, chunks)
                if not sources:
                    skipped_no_sources += 1
                    details.append({"angle": angle, "event_name": seg_label,
                                    "status": "no_sources", "reason": "查無可追溯的引用來源"})
                    continue  # 沒有可追溯的引用來源，不發布

                # 切段時已一併取得結構化欄位；僅備援的正則切段才需另外萃取。
                meta = seg.get("meta") or _extract_event_meta(body)
                if not meta["name"]:
                    details.append({"angle": angle, "event_name": seg_label,
                                    "status": "extract_failed", "reason": "無法萃取事件名稱"})
                    continue

                # 去重：比對已發布事件，以及本次多角度先前輪次剛加入的事件
                candidate_dict = {"title": meta["name"], "date": meta["start_date"] or "",
                                  "summary": meta["summary"]}
                dup = next(
                    (e for e in existing_events
                     if _is_duplicate_event(candidate_dict,
                                            {"title": e.name, "date": e.start_date or "", "summary": ""})),
                    None,
                )
                dup_reason = None
                if dup is None:
                    # 便宜的比對沒命中，再用語意判定補一層（同事件不同說法）
                    dup, dup_reason = _find_duplicate_llm(candidate_dict, existing_events)
                if dup:
                    skipped_duplicate += 1
                    why = f"與既有事件重複：[{getattr(dup, 'id', '?')}] {dup.name}"
                    if dup_reason:
                        why += f"（{dup_reason}）"
                    details.append({"angle": angle, "event_name": seg_label,
                                    "status": "duplicate", "reason": why})
                    continue

                ev = models.Event(
                    name=meta["name"],
                    start_date=meta["start_date"],
                    created_at=str(int(time.time())),
                    kind="incident",
                )
                db.add(ev)
                db.flush()
                db.add(models.EventManufacturer(event_id=ev.id, producer_id=producer_id, role=None))
                for s in sources:
                    db.add(models.EventSource(
                        event_id=ev.id,
                        title=s["title"][:500],
                        url=s["url"][:1000],
                        source_tier=classify_domain(s["url"]),
                    ))
                existing_events.append(ev)
                published += 1
                angle_published += 1
                details.append({"angle": angle, "event_name": meta["name"], "status": "published",
                                "event_id": ev.id, "start_date": meta["start_date"],
                                "source_count": len(sources)})

            per_angle[angle] = {"found": len(segments), "published": angle_published}
            db.commit()  # 逐角度提交，中途失敗不會賠掉先前角度已完成的成果

        db.commit()
        return {
            "producer": producer_name,
            "angles": angles,
            "per_angle": per_angle,
            "events_found": events_found,
            "published": published,
            "skipped_not_target": skipped_not_target,
            "skipped_ambiguous": skipped_ambiguous,
            "skipped_no_sources": skipped_no_sources,
            "skipped_duplicate": skipped_duplicate,
            "details": details,
        }
    finally:
        db.close()


def discover_all_by_grounding() -> list[dict]:
    """對 manufacturers.json 中所有 canonical 廠商跑 Track 2。"""
    out = []
    for m in entity_resolution.all_manufacturers():
        out.append(discover_by_grounding(m["producer_id"], m["canonical_name"]))
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        pid, name = int(sys.argv[1]), sys.argv[2]
        print(json.dumps(discover_by_grounding(pid, name), ensure_ascii=False, indent=2))
    else:
        for r in discover_all_by_grounding():
            print(json.dumps(r, ensure_ascii=False))
