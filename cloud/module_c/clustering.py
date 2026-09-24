"""
Module C — 候選文件分群建議（LLM 在本管線的唯一用途）

v1 定案（2026-07-12 修正）：
  原「LLM 用量歸零」立場經 TF-IDF 實測推翻後修正。實測真實文件顯示：
  同事件配對相似度最低 0.0768、不同事件配對最高 0.3257——**兩群分數重疊，
  無任何閾值可完美切分**。單一數值訊號不足以勝任分群，故改採 LLM。

LLM 的權限邊界（嚴格）：
  ✅ 可以做：對整批候選文件提出「建議分群」，並**強制**為每一組附上理由
  ❌ 不可以做：最終決定（人工逐一確認或覆寫）、判定 severity、判定角色
     （違規者／受影響者）、判定是否流入市面——這些維持零 LLM

為什麼要「強制附理由」：
  理由文字使判斷可稽核。純分數（如 0.3257）無法拆解為何——人工看到數字
  只能選擇相信或不信；看到「兩篇皆提及中聯油脂供油給聯華，判定同一事件」
  則可自行驗證這句話對不對。

Grounding 檢查（程式端，非 LLM 自評）：
  LLM 的輸出一律經確定性程式驗證，任一項不通過即**整批拒絕**，退回「無建議」
  狀態。壞掉的建議比沒有建議更危險——它會誤導人工審核者。
"""
import os
import re
import json
import time

from google import genai
from google.genai import types
from dotenv import load_dotenv

from database import SessionLocal
import models
from module_c import entity_resolution

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

_GEMINI_KEY = os.getenv("GEMINI_API_KEY")
_client = genai.Client(api_key=_GEMINI_KEY) if _GEMINI_KEY else None

MODEL = "gemini-2.5-flash-lite"


def _clean_snippet(text: str) -> str:
    """
    確定性雜訊清理（不經 LLM）。爬回來的原文含大量網站骨架：markdown 表格殘骸、
    導覽列、重複圖說。實測 25 筆中重複行最高佔 40%，管線符號最高佔 11%。
    """
    out, seen = [], set()
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去表格/裝飾殘骸：實質字元佔比過低者視為版面符號而非內容
        core = re.sub(r"[|!#\-\*\s]", "", line)
        if not core or len(core) / len(line) < 0.4:
            continue
        # 去重複行：導覽列與圖說常整段重複出現
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def _relevance_window(text: str, terms: list[str] | None, width: int = 400) -> str:
    """
    以「首個命中詞」為中心取窗，取代從頭截斷。

    實測 25 筆中有 4 筆的第一個關鍵詞落在第 400 字之後（最遠 1412），
    從頭截斷會讓這些文件送出的內容完全不含訊號。改用相關性取窗後，
    含關鍵詞的文件由 20/25 提升至 24/25，且送出總字數不變。
    """
    if not text:
        return ""
    pos = -1
    for t in (terms or []):
        i = text.find(t)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        return text[:width]
    start = max(0, pos - 100)
    return ("…" if start > 0 else "") + text[start:start + width]


def _build_prompt(candidates: list[models.EventCandidate],
                  known_events: list[dict] | None = None,
                  producer_name: str | None = None) -> str:
    # 檢索脈絡：候選文件是以該廠商名為關鍵字撈回來的，敘事天然以其為中心，
    # 易誘使 LLM 用「被波及者」命名事件。明確點出此偏誤來源以抵銷之。
    producer_block = ""
    if producer_name:
        producer_block = f"""
【檢索脈絡・命名偏誤警告】
本批文件是以「{producer_name}」為關鍵字檢索而來，因此文中多以該公司為敘事中心。
**這不代表該公司就是事件元凶。** 命名前請先判斷：在這起事件裡，
「{producer_name}」究竟是元凶，還是上游供應商出問題而被波及的下游業者？
若是被波及者，名稱必須點出真正的元凶（如上游供油廠商），不可用「{producer_name}」起頭。
"""

    # 承接式批次（預設關閉，見下）：把前幾批已建立的事件目錄帶入，讓本批可沿用
    # 既有事件而非重複造群。目錄僅供「唯讀參考」——本批仍只需分配本批的新 ID。
    #
    # 【why default off】2026-07-22 重複量測（每設定 ×3）結果：
    #   關閉：群數 11/11/11、最大群 6/6/6、過度合併 0/3
    #   開啟：群數  7/ 6/ 7、最大群 18/14/19、過度合併 2/3
    # 開啟後群數較少「看起來」較好，實則因錨定偏誤把卡迪那丙烯醯胺事件（聯華為
    # 責任方）吞進中聯油脂事件（聯華為受害者）。過度合併比碎片更危險：它讓一起
    # 真實事件消失，並扭曲角色歸屬。跨塊碎片仍依原設計交由 merge.py + 人工合併。
    known_block = ""
    if known_events:
        lines = "\n".join(
            f"- {e['key']}「{e['name']}」({e['kind']})：{e['reason'][:120]}"
            for e in known_events
        )
        known_block = f"""
【已知事件】以下事件由前幾批文件建立，本批文件若屬於其中之一，請直接沿用其代號：
{lines}
"""

    docs = []
    for c in candidates:
        # 先清雜訊，再以首個命中詞為中心取窗（非從頭截斷）
        snippet = _relevance_window(_clean_snippet(c.snippet or ""), c.matched_terms)
        # 受控詞彙命中數具鑑別力：事件類文件通常命中 7-9 個，官網/ESG 雜訊僅 2-3 個
        terms = "、".join(c.matched_terms or []) or "（無）"
        docs.append(
            f"[候選 {c.id}]\n"
            f"  標題：{c.title[:100]}\n"
            f"  來源層級：{c.source_tier}\n"
            f"  命中詞彙：{terms}\n"
            f"  摘錄：{snippet}"
        )
    docs_text = "\n\n".join(docs)

    return f"""你是食安事件的資料整理助理。以下是一批待審核的候選文件，請把它們分組，並為每組建議一個名稱。

【重要】你的輸出只是**建議**，最終由人工確認或修改。請如實反映判斷，不要為了湊出漂亮的分群而勉強歸類。

【三種分類】每一組的 kind 必須**恰好**是下列三個字串之一：
- `incident`（單一事件）：多份文件在講「同一起具體的食安事件」（如某次逾期食品被查獲、某次油品污染）。
- `compilation`（彙整報導）：文件本身是「涵蓋多起事件的整理」——懶人包、編年史、大事紀、歷年回顧、綜合評論。它不是單一事件，但也不是雜訊，值得獨立成一類。
- `not_an_event`（非事件）：與食安事件無關的雜訊——公司官網首頁、ESG 報告目錄、平台維護公告、公司財報、新品發表、檢舉聯絡方式等。

{producer_block}{known_block}
【待分群的候選文件】
{docs_text}

【任務】
0. 每一組的 cluster_id 擇一填寫：
   - 若該組文件屬於上方【已知事件】之一 → 填該事件代號（如 `E1`），並將
     suggested_name 填為該事件的既有名稱（不要改寫）。
   - 若屬於新的事件／新的彙整／雜訊 → 開新組，cluster_id 填 `NEW-A`、`NEW-B`…
   - **若確實屬於新事件，請放心開新組**；不要為了套用既有事件而勉強歸類。
     沿用既有事件只在確實是「同一起事件」時才做。
1. 把講同一起事件的文件分同一組（incident）；把彙整型文件歸為 compilation；把雜訊歸為 not_an_event。
2. 為 incident 與 compilation 的每一組建議名稱（suggested_name）：
   - **名稱必須點出真正的主體／元凶，而非被波及的廠商**。例如油品污染事件應命名為「2026 中聯油脂致癌油事件」，不可命名為「聯華致癌油事件」（聯華只是被波及者）。
   - compilation 的名稱應反映其彙整性質，如「義美食安相關報導彙整」。
   - not_an_event 的 suggested_name 留空字串。
3. 每組**必須**附理由，引用文件具體內容（如「兩篇皆提及中聯油脂供油給聯華」），不可空泛（如「內容相似」）。
4. 一份文件只能出現在一組。所有候選文件都必須被分配，不可遺漏。

【嚴格禁止】
- 不要判斷事件嚴重程度。
- 不要判斷廠商是違規者、受影響者還是自主通報者（名稱點主體即可，不下角色定論）。
- 不要生成事件的描述句或摘要。
- 不要捏造候選編號——只能使用上方實際列出的編號。
- 不要捏造事件代號——cluster_id 只能是上方【已知事件】列出的代號，或 `NEW-` 開頭的新代號。
- kind 只能是 incident／compilation／not_an_event 三者之一，不可放其他文字。

【輸出格式】只回傳 JSON 陣列，不要 markdown 圍欄，不要其他文字：
[
  {{
    "kind": "incident 或 compilation 或 not_an_event",
    "cluster_id": "已知事件代號（如 E1）或新代號（如 NEW-A）",
    "suggested_name": "建議名稱（incident/compilation 必填；not_an_event 留空字串）",
    "candidate_ids": [候選編號, ...],
    "reason": "必填。引用文件具體內容說明分組依據。"
  }}
]"""


def _coerce_id(x) -> int | None:
    """
    容忍格式差異：LLM 可能回傳 19、"19" 或「候選 19」。

    僅做格式正規化，**不放寬安全性**——擷取出的編號仍須通過 input_ids 成員檢查，
    捏造的編號（如「候選 999」）照樣會被擋下。
    """
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        digits = "".join(ch for ch in x if ch.isdigit())
        if digits:
            return int(digits)
    return None


def _validate(raw: list, candidates: list[models.EventCandidate],
              known_keys: set[str] | None = None) -> tuple[bool, str, list | None]:
    """
    Grounding 檢查（確定性程式，非 LLM 自評）。

    任一項失敗即整批拒絕——壞掉的建議會誤導人工審核者，比沒有建議更危險。

    回傳 (是否通過, 訊息, 正規化後的分群)。
    """
    if not isinstance(raw, list) or not raw:
        return False, "輸出不是非空陣列", None

    input_ids = {c.id for c in candidates}
    seen: set[int] = set()
    normalized = []
    valid_kinds = {"incident", "compilation", "not_an_event"}

    for i, cluster in enumerate(raw):
        if not isinstance(cluster, dict):
            return False, f"第 {i} 組不是物件", None

        kind = cluster.get("kind")
        if kind not in valid_kinds:
            return False, f"第 {i} 組 kind={kind!r} 不合法（須為 {valid_kinds}）", None

        cid = cluster.get("cluster_id")
        if not cid or not isinstance(cid, str):
            return False, f"第 {i} 組缺少 cluster_id", None

        # 承接式批次：cluster_id 只能是「目錄中實際存在的既有代號」或「NEW- 開頭的新代號」。
        # 其餘一律視為捏造——與候選編號同等嚴格，避免 LLM 掰出不存在的事件代號。
        kk = known_keys or set()
        if cid not in kk and not cid.startswith("NEW-"):
            return False, f"第 {i} 組 cluster_id={cid!r} 非既有代號亦非 NEW- 開頭（捏造）", None

        reason = (cluster.get("reason") or "").strip()
        if not reason:
            return False, f"第 {i} 組（{cid}）未附理由——理由為強制欄位", None

        # 名稱：incident / compilation 必填；not_an_event 允許空
        name = (cluster.get("suggested_name") or "").strip()
        if kind in ("incident", "compilation") and not name:
            return False, f"第 {i} 組（kind={kind}）缺少建議名稱", None

        ids = cluster.get("candidate_ids")
        if not isinstance(ids, list) or not ids:
            return False, f"第 {i} 組（{cid}）candidate_ids 為空", None

        clean_ids = []
        for x in ids:
            n = _coerce_id(x)
            if n is None:
                return False, f"候選編號 {x!r} 無法解析為編號", None
            if n not in input_ids:
                # LLM 捏造了不存在的候選編號 → 幻覺，整批不可信
                return False, f"候選編號 {n} 不存在於輸入（LLM 捏造）", None
            if n in seen:
                return False, f"候選編號 {n} 重複出現於多個群組", None
            seen.add(n)
            clean_ids.append(n)

        normalized.append({
            "kind": kind,
            "cluster_id": cid,
            "suggested_name": name,
            "candidate_ids": clean_ids,
            "reason": reason,
        })

    missing = input_ids - seen
    if missing:
        return False, f"候選 {sorted(missing)} 未被分配（LLM 遺漏）", None

    return True, "ok", normalized


# 單批上限：實測 LLM 對 >15 筆之批次難以做出「每個ID恰好一次」之乾淨分配
# （會重複或漏ID），超過即自動切塊，每塊獨立分群。同事件若跨塊，由人工於審核台
# 「掛到既有事件」合併——此合併本就交由人工，故切塊不損正確性。
MAX_BATCH = 7


def _cluster_batch(candidates: list[models.EventCandidate],
                   known_events: list[dict] | None = None,
                   producer_name: str | None = None) -> tuple[list | None, list]:
    """
    對單一批候選跑 LLM 分群 + grounding 檢查 + 重試。
    known_events 為前幾批已建立之事件目錄（承接式批次），僅供沿用，不可被重新指派。
    回傳 (通過驗證之 clusters 或 None, attempts_log)。
    """
    base_prompt = _build_prompt(candidates, known_events, producer_name)
    known_keys = {e["key"] for e in (known_events or [])}
    valid_ids = sorted(c.id for c in candidates)
    attempts_log = []
    prompt = base_prompt

    for attempt in range(3):
        try:
            resp = _client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            raw_text = resp.text
        except Exception as e:
            attempts_log.append(f"API 失敗: {e}")
            time.sleep(5 * (attempt + 1))
            continue

        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as e:
            attempts_log.append(f"非合法 JSON: {e}")
            prompt = base_prompt + f"\n\n【上次失敗原因】輸出不是合法 JSON（{e}）。請只回傳 JSON 陣列。"
            continue

        ok, msg, normalized = _validate(raw, candidates, known_keys)
        if ok:
            return normalized, attempts_log

        attempts_log.append(msg)
        prompt = (
            base_prompt
            + f"\n\n【上次輸出未通過檢查】{msg}\n"
            + f"請重新輸出。務必遵守：candidate_ids 只能使用這些編號（純整數）：{valid_ids}，"
            + "每個編號必須恰好出現一次、全部都要分配；每組都要有合法的 kind、reason；"
            + "incident 與 compilation 每組都要有 suggested_name；"
            + f"cluster_id 只能是既有代號 {sorted(known_keys) or '（無）'} 或 NEW- 開頭。"
        )

    return None, attempts_log


def suggest_clusters(producer_id: int | None = None, dry_run: bool = False,
                     carry_over: bool = False) -> dict:
    """
    對 pending 候選提出分群建議，寫入 llm_cluster_id / llm_cluster_reason。

    超過 MAX_BATCH 筆自動切塊，每塊獨立分群並以塊索引命名 cluster_id 避免碰撞。
    **不改變 status**——候選仍為 pending，等待人工審核。
    """
    if not _client:
        return {"error": "GEMINI_API_KEY 未設定"}

    db = SessionLocal()
    try:
        q = db.query(models.EventCandidate).filter(models.EventCandidate.status == "pending")
        producer_name = None
        if producer_id:
            q = q.filter(models.EventCandidate.producer_id == producer_id)
            m = entity_resolution.get_manufacturer(producer_id)
            producer_name = m["canonical_name"] if m else None
        candidates = q.all()

        if not candidates:
            return {"skipped": "無 pending 候選"}

        # 切塊（穩定順序：依 id）
        candidates.sort(key=lambda c: c.id)
        chunks = [candidates[i:i + MAX_BATCH] for i in range(0, len(candidates), MAX_BATCH)]

        by_id = {c.id: c for c in candidates}
        all_detail = []
        failed_chunks = []
        total_clusters = 0

        # 承接式批次：跨塊累積事件目錄。僅 incident/compilation 進目錄，
        # not_an_event 是雜訊、不應被後續批次沿用。
        known_events: list[dict] = []
        known_by_key: dict[str, dict] = {}

        for ci, chunk in enumerate(chunks):
            clusters, attempts = _cluster_batch(
                chunk, known_events if carry_over else None, producer_name)
            if clusters is None:
                failed_chunks.append({"chunk": ci, "size": len(chunk),
                                      "last_error": attempts[-1] if attempts else "未知"})
                continue

            for cluster in clusters:
                raw_cid = cluster["cluster_id"]
                if raw_cid in known_by_key:
                    # 沿用既有事件：鍵與名稱一律以目錄為準，避免同一事件名稱逐批漂移
                    gkey = raw_cid
                    name = known_by_key[gkey]["name"]
                else:
                    # 新建事件：以塊索引命名避免跨塊碰撞
                    gkey = f"c{ci}:{raw_cid}"
                    name = cluster["suggested_name"]
                    if cluster["kind"] in ("incident", "compilation"):
                        entry = {"key": gkey, "name": name,
                                 "kind": cluster["kind"], "reason": cluster["reason"]}
                        known_events.append(entry)
                        known_by_key[gkey] = entry

                cluster["_gkey"] = gkey
                cluster["_name"] = name

                if not dry_run:
                    for cid in cluster["candidate_ids"]:
                        cand = by_id[cid]
                        cand.llm_kind = cluster["kind"]
                        cand.llm_cluster_id = gkey
                        cand.llm_suggested_name = name or None
                        cand.llm_cluster_reason = cluster["reason"]

            all_detail.extend(
                {"kind": c["kind"], "name": c["_name"], "n": len(c["candidate_ids"]),
                 "gkey": c["_gkey"]}
                for c in clusters
            )

        if not dry_run:
            db.commit()

        # 承接式批次下，同一事件可跨多塊出現；群數應以「相異事件鍵」計，
        # 否則沿用既有事件會被誤計為新增一群，讓改善看不出來。
        merged: dict[str, dict] = {}
        for d in all_detail:
            m = merged.setdefault(d["gkey"], {"kind": d["kind"], "name": d["name"], "n": 0})
            m["n"] += d["n"]
        total_clusters = len(merged)

        return {
            "validated": len(failed_chunks) == 0,
            "candidates": len(candidates),
            "chunks": len(chunks),
            "clusters": total_clusters,
            "failed_chunks": failed_chunks,
            "detail": list(merged.values()),
        }
    finally:
        db.close()


if __name__ == "__main__":
    import sys
    print(json.dumps(
        suggest_clusters(dry_run="--dry-run" in sys.argv),
        ensure_ascii=False, indent=2,
    ))
