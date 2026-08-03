"""
Module C — 抓取並保存 Grounding 原始回應（供離線反覆試驗切段策略）

切段（把一大段自由文字拆成一件一件事件）目前是整條管線最脆弱的一環，而每
改一次就重打一次 API 既慢又消耗配額。本檔把「檢索」與「解析」切開：先把
原始回應連同引用中繼資料存成檔案，之後調整解析策略時直接讀檔重跑即可。

grounding_supports / grounding_chunks 是 SDK 物件，無法直接序列化，故只保
留後續解析真正會用到的欄位（引用涵蓋的字元範圍、對應來源的標題與網址）。
"""
import json
import os
import sys
import time

from google import genai
from google.genai import types
from dotenv import load_dotenv

from module_c import entity_resolution
from module_c._retrieval_experiment import QUERY_ANGLES

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

MODEL = "gemini-2.5-flash"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_captures")


def capture(producer_name: str, angle: str, prompt: str) -> dict:
    resp = _client.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    text = resp.text or ""
    gm = resp.candidates[0].grounding_metadata if resp.candidates else None
    chunks = []
    for c in (gm.grounding_chunks if gm and gm.grounding_chunks else []):
        w = c.web
        chunks.append({"title": (w.title if w else None), "uri": (w.uri if w else None)})
    supports = []
    for s in (gm.grounding_supports if gm and gm.grounding_supports else []):
        seg = s.segment
        if seg is None or seg.start_index is None or seg.end_index is None:
            continue
        supports.append({"start": seg.start_index, "end": seg.end_index,
                         "chunks": list(s.grounding_chunk_indices or [])})
    return {"producer": producer_name, "angle": angle, "prompt": prompt,
            "text": text, "chunks": chunks, "supports": supports}


def main(names: list[str] | None):
    if not _client:
        print("GEMINI_API_KEY 未設定")
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    all_m = entity_resolution.all_manufacturers()
    targets = [m for m in all_m if not names or m["canonical_name"] in names]
    for m in targets:
        name = m["canonical_name"]
        for angle, tmpl in QUERY_ANGLES.items():
            path = os.path.join(CACHE_DIR, f"{m['producer_id']}_{angle}.json")
            if os.path.exists(path):
                print(f"skip {name}/{angle}（已存在）", flush=True)
                continue
            try:
                data = capture(name, angle, tmpl.format(name=name))
                data["producer_id"] = m["producer_id"]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"ok  {name}/{angle}  chars={len(data['text'])} chunks={len(data['chunks'])}", flush=True)
            except Exception as e:
                print(f"ERR {name}/{angle}: {str(e)[:120]}", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main([a for a in sys.argv[1:]] or None)
