"""
Module C — 檢索策略對照實驗（唯讀，不寫入任何資料表）

目的：回答「這套系統的成果有多少是我們自己的設計貢獻，有多少只是 Google
Grounding 本身的能力」——把 Grounding 當成固定不變的零件，只變動我們自己
那一層（提問策略、輪數），量出差異。

三組實驗：
  A. no_search   — 完全不給搜尋工具，只問模型記憶。用來量「檢索到底貢獻多少」。
                   若 A 與 B 差距很小，代表整套檢索設計沒有創造價值。
  B. single      — 現行作法：單一通用提問問一次（grounding_discovery 的行為）。
  C. multi_angle — 同一個工具、同一家廠商，改成從多個角度分別提問後聯集。
                   工具沒變，只有提問策略變 → 差異即為本層設計的貢獻。

另外附帶量測「同一組提問重跑的穩定度」：單問一次的結果在不同次呼叫間是否
一致。不一致本身就是單輪檢索覆蓋不足的證據，而且這個數字不需要標準答案。

刻意不寫入資料庫：本檔只做量測與輸出報表，避免實驗污染已發布資料。
"""
import json
import os
import re
import sys
import time
from collections import defaultdict

from google import genai
from google.genai import types
from dotenv import load_dotenv

from module_c import entity_resolution
from module_c.grounding_discovery import _split_events, _is_duplicate_event, QUERY_ANGLES

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

MODEL = "gemini-2.5-flash"

_NO_RESULT_PREFIXES = ("查無", "沒有", "根據我目前", "很抱歉")


def _year_of(text: str) -> str:
    """從段落文字抓出最可能的事件年份，供跨輪事件比對用（民國年不處理）。"""
    m = re.search(r"(19|20)\d{2}", text)
    return m.group(0) if m else ""


def _call(producer_name: str, prompt: str, use_search: bool) -> tuple[str, int]:
    """呼叫模型一次，回傳（回應全文, 引用來源數）。use_search=False 即 baseline。"""
    cfg = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())] if use_search else None
    )
    resp = _client.models.generate_content(model=MODEL, contents=prompt, config=cfg)
    text = resp.text or ""
    n_src = 0
    if use_search and resp.candidates:
        gm = resp.candidates[0].grounding_metadata
        if gm and gm.grounding_chunks:
            n_src = len(gm.grounding_chunks)
    return text, n_src


def _events_of(text: str, producer_id: int) -> list[dict]:
    """
    把一次回應切成事件清單，並套用與正式管線相同的實體解析關卡。

    只保留「內容確實提及本廠商」的段落——若不套這一關，各角度會把順帶提到
    的其他公司事件也算進來，聯集數字會虛胖，實驗就失去意義。
    """
    if not text or any(text.strip().startswith(p) for p in _NO_RESULT_PREFIXES):
        return []
    out = []
    for seg in _split_events(text):
        body = seg["body"]
        if not any(h.producer_id == producer_id for h in entity_resolution.resolve(body)):
            continue
        title = (seg["header"] or body[:60]).strip()
        out.append({"title": title, "date": _year_of(body), "summary": body[:300]})
    return out


def _union(groups: list[list[dict]]) -> list[dict]:
    """把多輪結果聯集去重，沿用正式管線的同一套事件比對邏輯。"""
    merged: list[dict] = []
    for group in groups:
        for ev in group:
            if not any(_is_duplicate_event(ev, m) for m in merged):
                merged.append(ev)
    return merged


def run_producer(producer_id: int, producer_name: str, repeats: int = 3) -> dict:
    """對單一廠商跑完整對照實驗。repeats 用於量測單輪提問的穩定度。"""
    result = {"producer": producer_name, "producer_id": producer_id}

    # A. baseline：不給搜尋工具
    text, _ = _call(producer_name, QUERY_ANGLES["generic"].format(name=producer_name), use_search=False)
    base_events = _events_of(text, producer_id)
    result["no_search"] = {"events": [e["title"] for e in base_events], "count": len(base_events)}

    # B/C：各角度分別提問（generic 這一角度即為現行單問作法）
    per_angle = {}
    angle_events = {}
    for key, tmpl in QUERY_ANGLES.items():
        try:
            text, n_src = _call(producer_name, tmpl.format(name=producer_name), use_search=True)
        except Exception as e:
            per_angle[key] = {"error": str(e)[:120]}
            angle_events[key] = []
            continue
        evs = _events_of(text, producer_id)
        angle_events[key] = evs
        per_angle[key] = {"count": len(evs), "sources": n_src, "events": [e["title"] for e in evs]}
        time.sleep(1)

    single = angle_events.get("generic", [])
    multi = _union(list(angle_events.values()))

    # 每個角度的邊際貢獻：把該角度拿掉後，聯集會少掉幾件（衡量角度是否值得保留）
    marginal = {}
    for key in QUERY_ANGLES:
        without = _union([v for k, v in angle_events.items() if k != key])
        marginal[key] = len(multi) - len(without)

    result["per_angle"] = per_angle
    result["single_count"] = len(single)
    result["multi_angle_count"] = len(multi)
    result["multi_angle_events"] = [e["title"] for e in multi]
    result["marginal_gain"] = marginal

    # 穩定度：同一個通用提問重跑數次，看事件集合是否一致
    runs = []
    for _ in range(repeats):
        try:
            text, _ = _call(producer_name, QUERY_ANGLES["generic"].format(name=producer_name), use_search=True)
            runs.append(_events_of(text, producer_id))
        except Exception:
            runs.append([])
        time.sleep(1)
    run_union = _union(runs)
    # 每一件事件在幾次重跑中出現過（出現次數低者＝單輪容易漏掉的事件）
    appear = []
    for ev in run_union:
        appear.append(sum(1 for r in runs if any(_is_duplicate_event(ev, x) for x in r)))
    result["stability"] = {
        "repeats": repeats,
        "per_run_counts": [len(r) for r in runs],
        "union_count": len(run_union),
        "always_present": sum(1 for a in appear if a == repeats),
        "only_once": sum(1 for a in appear if a == 1),
        "events": [{"title": e["title"], "seen_in_runs": a} for e, a in zip(run_union, appear)],
    }
    return result


def main(producer_names: list[str] | None, repeats: int, out_path: str):
    if not _client:
        print("GEMINI_API_KEY 未設定")
        return
    all_m = entity_resolution.all_manufacturers()
    if producer_names:
        targets = [m for m in all_m if m["canonical_name"] in producer_names]
    else:
        targets = all_m

    results = []
    for m in targets:
        print(f"--- {m['canonical_name']} ---", flush=True)
        r = run_producer(m["producer_id"], m["canonical_name"], repeats=repeats)
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "per_angle"},
                         ensure_ascii=False, indent=2), flush=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n寫入 {out_path}")

    # 匯總表：三組方法在所有受測廠商上的總事件數
    tot_base = sum(r["no_search"]["count"] for r in results)
    tot_single = sum(r["single_count"] for r in results)
    tot_multi = sum(r["multi_angle_count"] for r in results)
    print(f"\n=== 匯總（{len(results)} 家廠商）===")
    print(f"A 不用搜尋（純模型記憶）: {tot_base}")
    print(f"B 單一提問 + 搜尋（現行）: {tot_single}")
    print(f"C 多角度提問 + 搜尋      : {tot_multi}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    reps = 3
    for a in sys.argv[1:]:
        if a.startswith("--repeats="):
            reps = int(a.split("=", 1)[1])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_retrieval_experiment_result.json")
    main(args or None, reps, out)
