"""
Module C — 切段策略對照：正則比對 vs. 模型結構化萃取（離線，讀 _captures/）

問題背景：現行切段靠正則比對「**N. 事件名稱**」這類標頭，但模型的排版在不同
次呼叫、不同提問角度之間並不穩定——2026-07-25 一天內就觀察到至少三種編號
標頭寫法，而多角度實驗又發現第四種（「一、」中文編號＋「* **事件名稱：**」
項目符號）。每遇到一種新排版就補一次正則，是補不完的。

本檔驗證替代作法：不解析排版，改把整段文字交給模型做結構化萃取，直接要回
事件陣列。同時要求模型附上每則事件在原文中的起始逐字片段（anchor），用來
還原字元位置——因為來源引用是以字元範圍對應的，失去位置就失去來源歸屬。

輸出兩者在同一批原始回應上的事件數與內容差異，供判斷是否值得換掉正則。
"""
import json
import os
import sys

from google import genai
from google.genai import types
from dotenv import load_dotenv

from module_c.grounding_discovery import _split_events as split_regex

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

MODEL = "gemini-2.5-flash"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_captures")

_PROMPT = """以下是一段描述某公司食品安全事件的文字，每行前面都加了行號。
請把它拆解成一件一件獨立的事件，並指出每則事件佔用哪幾行。

【判斷原則】
- 一則事件 = 一起真實發生、有具體時間與情節的事情。
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


def split_llm(text: str) -> list[dict]:
    """
    用模型把整段文字拆成事件陣列，並以「行號區間」還原每則事件的字元範圍。

    不用逐字 anchor 定位：實測（2026-07-25，義美三種排版）逐字比對在項目
    符號式排版下 8/8 全部對不上，模型傾向回傳整理過而非原樣的片段。改為在
    輸入端加上行號、要求回傳行號區間——行號是離散且唯一的，模型不需要逐字
    複製任何東西，定位率因此大幅提高。
    """
    lines = text.split("\n")
    numbered = "\n".join(f"{i}| {ln}" for i, ln in enumerate(lines))
    # 每行的起始字元位置（+1 為換行字元），用來把行號換算回字元範圍
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    resp = _client.models.generate_content(
        model=MODEL, contents=_PROMPT.format(body=numbered),
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
    )
    try:
        raw = json.loads(resp.text)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    events = []
    for item in raw:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        try:
            ls = int(item.get("line_start"))
            le = int(item.get("line_end"))
        except (TypeError, ValueError):
            ls = le = -1
        if 0 <= ls < len(lines) and ls <= le:
            le = min(le, len(lines) - 1)
            start = offsets[ls]
            end = offsets[le] + len(lines[le])
        else:
            start = end = -1
        events.append({"header": name, "start": start, "end": end,
                       "body": text[start:end] if start >= 0 else "",
                       "summary": (item.get("summary") or "").strip(),
                       "start_date": item.get("start_date")})
    return sorted(events, key=lambda e: (e["start"] < 0, e["start"]))


def main(only_producer: str | None):
    files = sorted(f for f in os.listdir(CACHE_DIR) if f.endswith(".json"))
    totals = {"regex": 0, "llm": 0, "unlocated": 0}
    for fn in files:
        with open(os.path.join(CACHE_DIR, fn), encoding="utf-8") as f:
            cap = json.load(f)
        if only_producer and cap["producer"] != only_producer:
            continue
        text = cap["text"]
        r = split_regex(text)
        l = split_llm(text)
        unloc = sum(1 for e in l if e["start"] < 0)
        totals["regex"] += len(r)
        totals["llm"] += len(l)
        totals["unlocated"] += unloc
        print(f"\n=== {cap['producer']} / {cap['angle']} （{len(text)} 字）===")
        print(f"  正則切段: {len(r)} 段")
        for s in r:
            print(f"     - {(s['header'] or s['body'][:40]).strip()[:60]}")
        print(f"  模型切段: {len(l)} 件（其中 {unloc} 件無法定位字元範圍）")
        for s in l:
            loc = "" if s["start"] >= 0 else "  [未定位]"
            print(f"     - {s['header'][:60]}{loc}")
    print(f"\n=== 合計 ===\n正則: {totals['regex']} 段 / 模型: {totals['llm']} 件"
          f"（未定位 {totals['unlocated']}）")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
