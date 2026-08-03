"""
Module C — Track 1：LLM 記憶種子生成（generate-then-verify）

目的：補救「一般廠商檢索被近期大事件擠壓、舊事件召回不到」之問題（見
[[學術文獻與理論依據]] 第十一節）。做法為以 LLM 之參數化記憶「回憶」某廠商
之重大食安事件，作為檢索種子，再逐一以 Tavily 外部驗證。

安全界限（嚴格，對應第十一節三項判斷）：
1. LLM 記憶僅作「候選事件之種子」——其輸出不寫入任何事實，僅產生搜尋關鍵字。
2. 不做 LLM 自我確認：每個假設一律送 Tavily 外部檢索，撈不到通過閘門之來源
   即淘汰（unverified），不因 LLM「很確定」而保留。
3. 實體歸屬由 Stage 0 閘門（須命中 canonical 廠商名）＋人工審核把關——若 LLM
   把某事件錯掛給本廠商，但文章實際點名的是元凶（非本廠商），閘門會擋下。

模型：本任務屬參數化記憶回憶，其品質隨模型能力擴張，故採較強之 gemini-3.5-flash
（而非分群所用之 flash-lite）。惟須知：再強之模型對台灣小眾食安事件之記憶仍有
天花板，且輸出終究只是種子。
"""
import os
import json
import time

from google import genai
from google.genai import types
from dotenv import load_dotenv

from database import SessionLocal
import models
from module_c import entity_resolution
from module_c.ingest import _search, _doc_text, _normalize_date
from module_c.gate import passes_gate, classify_domain

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

# 種子生成屬記憶回憶任務，用較強模型（見模組 docstring）
MODEL = "gemini-3.5-flash"

# ReAct 驗證用的模型（搜尋 + 推理，非記憶回憶任務，用一般模型即可）
REACT_MODEL = "gemini-2.5-flash"


import datetime

# 事件時間範圍：只回憶近 N 年之事件（過濾事件日期，非網頁日期）。
# Tavily 的 days 參數過濾網頁日期，無法可靠限制事件年份（舊事件之新網頁會漏進），
# 故時間限制在此處以 prompt 約束 LLM 回憶範圍實現。
RECALL_YEARS = 10


def generate_hypotheses(manufacturer_name: str, years_back: int = RECALL_YEARS) -> list[dict]:
    """
    步驟 A：LLM 從記憶回憶該廠商之重大食安事件（假設，未經驗證）。

    僅回憶近 years_back 年之事件（減少過舊事件之審核負擔，並聚焦於對現今消費者
    較相關者）。刻意求全（liberal recall）：範圍內寧可多列、由後續驗證過濾。
    LLM 不判斷責任歸屬（違規者／受影響者），只列事件與搜尋關鍵字。
    """
    if not _client:
        return []

    cutoff_year = datetime.date.today().year - years_back

    prompt = f"""你是台灣食品安全事件的資料助理。請就「{manufacturer_name}」這家公司，
回憶你所知、與其相關且較為人所知的食品安全事件（例如成分違規、產品回收、逾期、
中毒、油品污染、標示不實等）。

【時間範圍限制（重要）】
- **只回憶 {cutoff_year} 年（含）以後發生的事件**，即近 {years_back} 年內。
- {cutoff_year} 年以前的舊事件**一律不要列出**（例如更早年代的塑化劑、銅葉綠素、
  餿水油等事件，若發生於 {cutoff_year} 年之前，請略過）。

【重要說明】
- 你的輸出只是「待查證的假設」，之後會用網路檢索逐一驗證，查不到證據者會被剔除。
  因此近 {years_back} 年內的事件請盡量列出，寧可多列，不要因不確定而略過；但也不要純粹編造。
- 不要判斷該公司是違規者、受影響者或自主通報者——那由後續人工判斷。你只要提供
  事件名稱與有助於檢索的關鍵字。
- 若你對此公司近 {years_back} 年之食安事件毫無印象，回傳空陣列 []（這是合理的答案，不要硬湊）。

【輸出格式】只回傳 JSON 陣列，不要 markdown 圍欄：
[
  {{
    "event_name": "你印象中的事件名稱",
    "search_keywords": "有助於檢索此事件的關鍵字（含可能的元凶、物質、年份等）",
    "approx_year": "約略年份（須為 {cutoff_year} 年或以後）"
  }}
]"""

    for attempt in range(3):
        try:
            resp = _client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,  # 略高以求記憶廣度
                ),
            )
            data = json.loads(resp.text)
            if isinstance(data, list):
                # 只保留有 event_name 與 search_keywords 者
                return [
                    h for h in data
                    if isinstance(h, dict) and h.get("event_name") and h.get("search_keywords")
                ]
            return []
        except Exception as e:
            if attempt == 2:
                print(f"[WARN] 種子生成失敗 {manufacturer_name}: {e}")
                return []
            time.sleep(3 * (attempt + 1))
    return []


def _topic_keywords(hypothesis: dict, manufacturer_name: str) -> list[str]:
    """
    取出假設本身「具主題辨識力」的關鍵字（排除廠商名／其縮寫片段與純數字/年份）。

    用於後續的內容對題檢查——廠商名與「有沒有出現任一通用風險詞彙」
    無法判斷文件是否真的在講「這個」假設，必須要求假設自己的具體
    關鍵字（如「蘇丹紅」「環氧乙烷」）實際出現在文件中。

    過濾條件須為「token 是廠商名的子字串」而非只做完整廠商名的字串替換——
    否則如「統一企業」的假設常含「統一」這種縮寫片段，會被誤留在關鍵字
    清單中；而 entity_resolution 本就是靠這類別名命中，若留下會讓對題
    檢查形同虛設（任何已通過公司比對的文件必然含此字串）。
    """
    raw = hypothesis.get("search_keywords", "")
    tokens = raw.split()
    return [t for t in tokens if t and not t.isdigit() and t not in manufacturer_name]


def verify_hypothesis(producer_id: int, manufacturer_name: str, hypothesis: dict, db) -> dict:
    """
    步驟 B：以 Tavily 外部驗證單一假設。

    檢索 query = 廠商名 + LLM 關鍵字（以廠商名錨定，只收與本廠商相關者）。
    撈回文件經 Stage 0（實體解析）+ Stage 2（閘門）+ 對題檢查後，通過者入候選佇列。
    通過閘門數為 0 → 此假設 unverified，不留任何資料。

    對題檢查（2026-07-24 新增）：公司命中 + 通用風險詞彙命中，不代表文件真的
    在講「這個」假設——實測發現文件可能是完全不相關的營收報導，只因網頁側欄
    的「延伸閱讀」區塊混入其他文章的詞彙而誤判命中。故額外要求假設自身的
    具體關鍵字（如「蘇丹紅」「環氧乙烷」，見 _topic_keywords）須實際出現於
    文件中，才視為真正對題，與「來源是否可信」無關——對不上題就是對不上題。
    """
    kw = hypothesis["search_keywords"]
    topic_kws = _topic_keywords(hypothesis, manufacturer_name)
    query = f"{manufacturer_name} {kw}"
    results = _search(query, max_results=10)

    passed = 0
    inserted = 0
    now = str(int(time.time()))

    for r in results:
        url = r.get("url", "")
        # 見 ingest.py._process_results 同一處註解：備援層會混入畸形相對路徑連結，過濾掉。
        if not url or not url.startswith(("http://", "https://")):
            continue
        text = _doc_text(r)

        hits = entity_resolution.resolve(text)
        # 【2026-07-24 修正】只在乎「本廠商」是否確實命中——文件命中其他 canonical
        # 廠商不代表本假設得證。修正前的 bug：`passed` 在篩選本廠商之前就先計數，
        # 導致「統一企業涉入蘇丹紅辣椒粉事件」這類假設，即使 Tavily 搜到的文件
        # 命中的其實是完全不同的公司（如維力食品），仍被回報 verified=True。
        target_hit = next((h for h in hits if h.producer_id == producer_id), None)
        if not target_hit:
            continue
        ok, terms = passes_gate(text, hits)
        if not ok:
            continue
        # 對題檢查：公司對、通用風險詞彙有，不代表內容真的在講這個假設。
        # 要求假設自身的具體關鍵字至少命中一個，才算真正對應到這起事件。
        if topic_kws and not any(t in text for t in topic_kws):
            continue
        passed += 1
        tier = classify_domain(url)

        exists = (
            db.query(models.EventCandidate)
            .filter(models.EventCandidate.url == url,
                    models.EventCandidate.producer_id == target_hit.producer_id)
            .first()
        )
        if exists:
            continue
        db.add(models.EventCandidate(
            producer_id=target_hit.producer_id,
            is_ambiguous=target_hit.is_ambiguous,
            ambiguous_with=target_hit.ambiguous_with or None,
            title=(r.get("title") or "")[:500],
            url=url[:1000],
            snippet=(r.get("content") or "")[:4000],
            published_date=_normalize_date(r.get("published_date")),
            source_tier=tier,
            matched_terms=terms,
            status="pending",
            discovered_at=now,
            discovery_method="memory_seed",
        ))
        inserted += 1

    db.commit()
    return {
        "event_name": hypothesis["event_name"],
        "verified": passed > 0,
        "passed_gate": passed,
        "new_candidates": inserted,
    }


def verify_hypothesis_react(producer_id: int, manufacturer_name: str, hypothesis: dict, db,
                            max_search_calls: int = 4) -> dict:
    """
    ReAct 版本的假設驗證：讓 LLM 自主決定搜尋策略、判斷證據是否充分，
    取代 verify_hypothesis() 的固定查詢模板 + 確定性 topic_keywords 對題檢查。

    【2026-07-25 定案，跨越既有設計原則，經使用者明確決定採用】
    本模組 docstring 原本的安全界限第 2 條「不做 LLM 自我確認」，本函式
    已不再遵守——LLM 現在會自行判斷搜尋結果是否構成證據，此判斷不再由
    topic_keywords 這類確定性字串比對把關。原因：固定查詢模板查不到就直接
    放棄，無法應對「關鍵字下得不好」的情況；而 LLM 能自主換關鍵字重試，
    且在測試中展現出比字串比對更細緻的實體辨識能力（如正確分辨「統一
    超食代」與「統一企業」為不同法人，字串比對無法做到這點）。

    跨越此界限後，刻意保留的安全網（不因採用 LLM 判斷就一併放棄）：
      1. entity_resolution 仍為硬性檢查——公司比對不交給 LLM 自由心證。
         即使 LLM 判斷 verified=True，若其引用的證據文字經程式檢查後
         未命中目標廠商，仍強制駁回（對應今天稍早「維力食品被誤判為
         統一企業」那類錯誤，此檢查成本極低卻能攔下同類問題）。
      2. 要求 LLM 附上直接引用的原文佐證（evidence_quote），不可只給結論。
      3. 結果依然只寫入 pending 候選，不自動發布——人工審核仍是最終關卡，
         LLM 判斷錯誤的代價僅止於「多一筆需要人工複核的候選」，而非
         「錯誤事實直接呈現給使用者」。
    """
    if not _client:
        return {"event_name": hypothesis["event_name"], "verified": False,
                "error": "GEMINI_API_KEY 未設定"}

    call_log: list[str] = []

    def search_tavily(query: str) -> str:
        """搜尋台灣新聞，查詢與食品安全事件相關的公開報導。回傳最多 5 筆結果的標題、網址與內文摘錄。"""
        if len(call_log) >= max_search_calls:
            return "已達本次調查之搜尋次數上限，請根據目前已知資訊做出結論，不要再搜尋。"
        call_log.append(query)
        results = _search(query, max_results=5)
        if not results:
            return "無搜尋結果。"
        out = []
        for r in results[:5]:
            out.append(
                f"標題：{r.get('title','')}\n"
                f"網址：{r.get('url','')}\n"
                f"內文：{(r.get('content','') or '')[:800]}"
            )
        return "\n\n---\n\n".join(out)

    task_prompt = f"""你正在調查「{manufacturer_name}」是否曾涉入「{hypothesis['event_name']}」這起食品安全事件。

已知線索：{hypothesis['search_keywords']}

請使用搜尋工具查證。若第一次搜尋證據不足或無結果，換個角度（如不同關鍵字組合、
更廣或更具體的用詞）再搜一次。找到證據後，具體說明：這篇文件是否真的在講這起
事件（而非只是碰巧提到公司名的無關內容）、{manufacturer_name}在其中的角色為何
（元凶／被波及的下游業者／自主通報）。若多次搜尋都找不到明確證據，請誠實說明
查無證據，不要臆測或硬湊。

【格式要求，重要】你的每一項具體事實陳述後面，都必須立刻附上該事實出自哪個
網址（搜尋結果中的「網址：」欄位，逐字複製），格式如：「聯華食品為7-ELEVEN
代工鮮食（來源：https://...）」。沒有網址佐證的陳述不要寫進結論。"""

    try:
        step1 = _client.models.generate_content(
            model=REACT_MODEL,
            contents=task_prompt,
            config=types.GenerateContentConfig(tools=[search_tavily]),
        )
        investigation = step1.text or ""
    except Exception as e:
        return {"event_name": hypothesis["event_name"], "verified": False,
                "error": f"調查階段失敗: {e}", "search_calls": call_log}

    verdict_prompt = f"""以下是你剛才針對「{manufacturer_name}」與「{hypothesis['event_name']}」的調查過程與結論：

{investigation}

請將上述內容轉為 JSON，格式：
{{
  "verified": true 或 false,
  "role": "perpetrator"（元凶／違規方）或 "affected_downstream"（受波及下游業者）或 "self_reported"（自主通報）或 null（無法判斷）,
  "evidence_quote": "直接引用調查過程中的具體句子作為佐證，不可空泛或自行改寫",
  "evidence_url": "佐證來源的網址",
  "reasoning": "一句話理由"
}}

【重要】若 verified 為 true，evidence_url 為必填欄位，且**必須是調查內容中
真實出現過的網址，逐字複製，不可省略、不可留空、不可自行編造**——調查內容
最後通常會列出「引用來源網址」或類似清單，請從中選一個與 evidence_quote
對應的網址填入。若調查內容確實沒有任何網址可用，才將 verified 設為 false。

只回傳 JSON，不要 markdown 圍欄。若調查結果本身沒有明確證據，verified 應為 false，
evidence_quote 與 evidence_url 可留空字串——誠實反映調查結果，不要為了湊出「已驗證」而勉強。"""

    try:
        step2 = _client.models.generate_content(
            model=REACT_MODEL,
            contents=verdict_prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        verdict = json.loads(step2.text)
    except (json.JSONDecodeError, TypeError, Exception) as e:
        return {"event_name": hypothesis["event_name"], "verified": False,
                "error": f"結構化輸出解析失敗: {e}", "search_calls": call_log,
                "raw_investigation": investigation}

    result = {
        "event_name": hypothesis["event_name"],
        "verified": bool(verdict.get("verified")),
        "role": verdict.get("role"),
        "evidence_quote": (verdict.get("evidence_quote") or "").strip(),
        "evidence_url": (verdict.get("evidence_url") or "").strip(),
        "reasoning": verdict.get("reasoning", ""),
        "search_calls": call_log,
        "new_candidates": 0,
    }

    if not result["verified"] or not result["evidence_url"] or not result["evidence_quote"]:
        result["verified"] = False
        return result

    # --- 硬性複核（不因 LLM 判斷 verified=True 就跳過）---
    hits = entity_resolution.resolve(result["evidence_quote"])
    target_hit = next((h for h in hits if h.producer_id == producer_id), None)
    if not target_hit:
        result["verified"] = False
        result["reasoning"] = (result["reasoning"] + "（程式端複核：引用內容未通過廠商比對，已駁回）").strip()
        return result

    url = result["evidence_url"]
    if not url.startswith(("http://", "https://")):
        result["verified"] = False
        result["reasoning"] += "（程式端複核：來源網址格式不正確，已駁回）"
        return result

    exists = (
        db.query(models.EventCandidate)
        .filter(models.EventCandidate.url == url,
                models.EventCandidate.producer_id == target_hit.producer_id)
        .first()
    )
    if exists:
        return result  # verified=True 但非新資料，new_candidates 維持 0

    tier = classify_domain(url)
    now = str(int(time.time()))
    db.add(models.EventCandidate(
        producer_id=target_hit.producer_id,
        is_ambiguous=target_hit.is_ambiguous,
        ambiguous_with=target_hit.ambiguous_with or None,
        title=hypothesis["event_name"][:500],
        url=url[:1000],
        snippet=result["evidence_quote"][:4000],
        published_date=None,  # ReAct 調查過程未結構化擷取日期，留待人工審核時補充
        source_tier=tier,
        matched_terms=[result["role"]] if result["role"] else [],
        status="pending",
        discovered_at=now,
        discovery_method="memory_seed_react",
    ))
    db.commit()
    result["new_candidates"] = 1
    return result


def run_seed(producer_id: int, manufacturer_name: str, use_react: bool = False) -> dict:
    """
    對單一廠商執行完整 generate-then-verify：
    Step A（generate_hypotheses）Gemini 自己回憶該廠商可能的食安事件，
    Step B（verify_hypothesis 或 verify_hypothesis_react）逐一外部驗證。

    use_react=True 時改用 ReAct 版驗證（見 verify_hypothesis_react 的
    docstring，2026-07-25 定案）；預設 False 維持舊版確定性驗證，
    兩者可並存比較。
    """
    verify_fn = verify_hypothesis_react if use_react else verify_hypothesis
    db = SessionLocal()
    try:
        hypotheses = generate_hypotheses(manufacturer_name)
        outcomes = [verify_fn(producer_id, manufacturer_name, h, db) for h in hypotheses]
        return {
            "manufacturer": manufacturer_name,
            "hypotheses_generated": len(hypotheses),
            "hypotheses": [h["event_name"] for h in hypotheses],
            "verified": [o for o in outcomes if o["verified"]],
            "unverified": [o["event_name"] for o in outcomes if not o["verified"]],
            "total_new_candidates": sum(o["new_candidates"] for o in outcomes),
        }
    finally:
        db.close()


if __name__ == "__main__":
    import sys
    # 預設 7 家 demo 廠商
    DEMO = [(1, "統一企業"), (2, "義美食品"), (3, "太古可口可樂"),
            (4, "味全食品工業"), (5, "聯華食品工業"),
            (14, "味丹企業"), (18, "奇美食品")]
    for pid, name in DEMO:
        print(json.dumps(run_seed(pid, name), ensure_ascii=False))
