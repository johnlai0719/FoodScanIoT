"""
Module C — 二次合併：收攏切塊造成的同事件碎片

背景：分群因 LLM 單批規模上限而切塊，同一起事件的文件若落在不同塊，會被切成
多個名稱相近的群（如「中聯油脂×2」「丙烯醯胺×2」）。本模組讓 LLM 檢視「各群
名稱＋範例標題」，建議哪些群其實是同一起事件，供人工合併。

安全界限（同分群）：
- 輸入很小（每家僅數個群名，非全文），故可靠、不需切塊。
- LLM 只提供合併「建議」，人工於審核台確認。
- Grounding：只能引用實際存在之群鍵，每群至多出現於一個合併組。
"""
import os
import json
import time
from collections import defaultdict

from google import genai
from google.genai import types
from dotenv import load_dotenv

from database import SessionLocal
import models

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None
MODEL = "gemini-2.5-flash-lite"


def _collect_clusters(db, producer_id: int) -> dict:
    """回傳 {cluster_id: {'name':.., 'kind':.., 'cands':[..]}}，僅 incident/compilation。"""
    clusters = defaultdict(lambda: {"name": None, "kind": None, "cands": []})
    q = (db.query(models.EventCandidate)
         .filter(models.EventCandidate.status == "pending",
                 models.EventCandidate.producer_id == producer_id,
                 models.EventCandidate.llm_kind.in_(["incident", "compilation"])))
    for c in q.all():
        g = clusters[c.llm_cluster_id]
        g["name"] = c.llm_suggested_name
        g["kind"] = c.llm_kind
        g["cands"].append(c)
    return clusters


def suggest_merges(producer_id: int) -> dict:
    """建議哪些群其實是同一起事件。回傳合併建議清單（2 群以上者才是實際合併）。"""
    if not _client:
        return {"error": "GEMINI_API_KEY 未設定"}

    db = SessionLocal()
    try:
        clusters = _collect_clusters(db, producer_id)
        if len(clusters) < 2:
            return {"skipped": "群數不足 2，無需合併", "merges": []}

        # 建 prompt：每群列 名稱 + kind + 範例標題
        lines = []
        for cid, g in clusters.items():
            titles = "；".join(c.title[:36] for c in g["cands"][:3])
            lines.append(f"群「{cid}」[{g['kind']}] 名稱：{g['name']}\n   範例標題：{titles}")
        blocks = "\n\n".join(lines)
        keys = list(clusters.keys())

        prompt = f"""以下是同一家廠商底下、系統已分好的多個事件群。因技術原因，同一起真實事件
可能被切成多個群（名稱相近）。請判斷哪些群其實在講**同一起真實事件**，應合併。

【各群】
{blocks}

【任務】
- 把講同一起事件的群歸為一個合併組，並給合併後的建議名稱（點出真正主體／元凶）。
- 一個群只能出現在一個合併組。**不確定是否同一事件的，就讓它各自獨立、不要勉強合併。**
- incident 與 compilation 不要互相合併（事件與彙整報導性質不同）。
- 只輸出「需要合併」的組（即包含 2 個以上群者）；單獨的群不必列出。

【嚴格禁止】
- 只能使用上方實際列出的群鍵：{keys}，不可捏造。
- 每組必須附理由，說明為何判定為同一事件。

【輸出格式】只回傳 JSON 陣列，不要 markdown：
[
  {{"merged_name": "合併後事件名稱", "cluster_keys": ["群鍵1","群鍵2"], "reason": "為何同一事件"}}
]
若無任何需要合併者，回傳 []。"""

        raw_text = ""
        for attempt in range(3):
            try:
                resp = _client.models.generate_content(
                    model=MODEL, contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
                raw_text = resp.text
                break
            except Exception as e:
                if attempt == 2:
                    return {"error": f"Gemini 失敗: {e}"}
                time.sleep(4 * (attempt + 1))

        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as e:
            return {"error": f"非合法 JSON: {e}"}

        # Grounding：群鍵須存在、每群至多一組、每組≥2群
        valid = set(keys)
        seen = set()
        merges = []
        for m in raw if isinstance(raw, list) else []:
            ck = m.get("cluster_keys") or []
            if not isinstance(ck, list) or len(ck) < 2:
                continue
            if any(k not in valid for k in ck):
                continue  # 捏造群鍵 → 跳過此組
            if any(k in seen for k in ck):
                continue  # 群重複出現 → 跳過
            name = (m.get("merged_name") or "").strip()
            reason = (m.get("reason") or "").strip()
            if not name or not reason:
                continue
            seen.update(ck)
            merges.append({"merged_name": name, "cluster_keys": ck, "reason": reason,
                           "total_sources": sum(len(clusters[k]["cands"]) for k in ck)})
        return {"merges": merges, "cluster_count": len(clusters)}
    finally:
        db.close()


def apply_merge(producer_id: int, cluster_keys: list[str], merged_name: str) -> dict:
    """把多個群合併為一：所有候選改為同一 cluster_id 與名稱（status 仍 pending）。"""
    db = SessionLocal()
    try:
        target_id = cluster_keys[0]  # 以第一個群鍵為合併後的 id
        n = 0
        for c in (db.query(models.EventCandidate)
                  .filter(models.EventCandidate.status == "pending",
                          models.EventCandidate.producer_id == producer_id,
                          models.EventCandidate.llm_cluster_id.in_(cluster_keys)).all()):
            c.llm_cluster_id = target_id
            c.llm_suggested_name = merged_name
            n += 1
        db.commit()
        return {"merged_into": target_id, "name": merged_name, "moved": n}
    finally:
        db.close()


if __name__ == "__main__":
    import sys
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(json.dumps(suggest_merges(pid), ensure_ascii=False, indent=2))
