"""
gemini_enrich.py — v3（來源加權 + 可信度標注）
設計理念：AI 整合所有可用來源，輸出時依 GroupRisk 框架標注可信度，
不限制 AI 使用已知知識，但必須對每段資訊的來源和可信度負責。

可信度等級（對應 GroupRisk 框架）：
  verified          — JECFA / EFSA / FDA / 台灣衛福部 官方評估
  research_supported — PubMed peer-reviewed 論文
  public_concern    — 新聞/非官方報導（不說有害，只說曾被報導）
  ai_inferred       — AI 從機制推論，無直接文獻，強制加免責聲明
  no_data           — 查無資料

輸入：
  01_主資料庫/additives_tfda_clean.json
  03_enrichment補充/inchem_raw.json        ← JECFA 官方資料（最高權重）

輸出：
  03_enrichment補充/gemini_patch.json
  每筆含：consumer_description, overall_confidence, adi_status, adi_value, sources[]

安裝：pip install google-genai
用法：
  python gemini_enrich.py --api-key YOUR_KEY            # 全部跑
  python gemini_enrich.py --api-key YOUR_KEY --limit 5  # 測試前 5 筆
  python gemini_enrich.py --api-key YOUR_KEY --only-no-jecfa  # 只補無 JECFA 的
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    print("請先安裝：pip install google-genai")
    raise

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass  # python-dotenv 未安裝時略過，仍可用 --api-key 或環境變數

ROOT        = os.path.join(os.path.dirname(__file__), "..")
SRC_PATH    = os.path.join(ROOT, "01_主資料庫", "additives_tfda_clean.json")
INCHEM_PATH = os.path.join(ROOT, "03_enrichment補充", "inchem_raw.json")
PATCH_OUT   = os.path.join(ROOT, "03_enrichment補充", "gemini_patch.json")

DELAY        = 15.0  # 每筆間隔（秒）— gemini-3.5-flash 免費版 RPM=5，13s+ 才安全
RETRY_DELAYS = [30, 60, 120]  # 429 時依序等待（秒）後重試

# ─────────────────────────────────────────────────────────────────
# System prompt：告訴 AI 可信度框架，而非限制資料來源
# ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
你是一位食品科學專家，負責為台灣一般消費者撰寫食品添加物說明。
讀者是沒有食品科學背景的一般大眾，不是專業人士。

## 資料來源可信度框架（輸出時每條來源必須標注 confidence）

| confidence           | 定義                                        | 使用原則                         |
|----------------------|---------------------------------------------|----------------------------------|
| verified             | JECFA、EFSA、FDA、台灣衛福部 官方評估結論   | 最高權重，直接引用                |
| research_supported   | PubMed peer-reviewed 論文（附 PMID 或 URL） | 高權重，附論文標題                |
| ai_inferred          | AI 依機制推論，無直接文獻支撐               | 必須加「（此為推論，僅供參考）」  |
| no_data              | 查無足夠資料                                | 如實說明，不憑空生成              |

## 整合規則
1. 我會提供 JECFA 官方資料（若有），這是最高權重輸入，直接從中整合安全結論
2. 你可以引用訓練資料中已知的 PubMed 文獻，但必須能提供 PMID 或來源 URL
3. 若只有 AI 推論，標 ai_inferred 並加「此為推論，僅供參考」
4. 推論鏈最多一層（A→因此B），不允許 A→B→C→因此危險
5. 不要捏造 URL 或 PMID — 若不確定文獻存在，不要列入 sources

## 消費者說明的寫法（最重要）

✅ 正確方式（白話翻譯安全資訊）：
  「在正常食品使用量下，國際食品安全機構評估對人體無害」
  「長期大量攝取才有疑慮，正常飲食中的用量不需特別擔心」
  「部分研究顯示兒童過量攝取可能影響注意力，建議適量」

❌ 絕對不要出現：
  - ADI、mg/kg bw、每日允許攝取量、NOAEL、PTWI 等技術名詞
  - 任何數字加單位（0-5 mg/kg、50 ppm、0.1%）
  - 縮寫（JECFA、EFSA 可寫全名「國際食品安全機構」、「歐洲食品安全局」）

## 輸出格式（嚴格遵守）
先輸出消費者說明（80–150 字），然後緊接一行 JSON：
{"overall_confidence":"...","adi_status":"...","adi_value":"...","sources":[{"confidence":"...","type":"...","title":"...","url":"...","year":"..."}]}

overall_confidence：取所有來源中最高等級
adi_status：established / not_needed / not_allocated / withdrawn / unknown
adi_value：填原始數字字串（如 "0-5 mg/kg bw"），此欄位供後端用，不出現在說明裡

## 說明結構
這是什麼（一句話定義）→ 常見於哪些食品 → 安全性白話說明
"""

# ─────────────────────────────────────────────────────────────────
# Prompt 建構
# ─────────────────────────────────────────────────────────────────
def build_prompt(tfda_rec: dict, inchem_rec: dict | None) -> str:
    name_zh = tfda_rec.get("name_zh", "")
    name_en = tfda_rec.get("name_en", "")
    tw_func = "、".join(tfda_rec.get("function_class") or []) or "未分類"
    usage   = (tfda_rec.get("usage_notes") or "資料不足")[:250]
    ins     = tfda_rec.get("ins_or_e_number") or ""

    lines = [
        "## 台灣 TFDA 法規資料（confidence: verified）",
        f"名稱：{name_zh}（{name_en}）" + (f"  INS/E號：{ins}" if ins and ins != "unknown" else ""),
        f"功能分類：{tw_func}",
        f"台灣法規用途：{usage}",
        "",
    ]

    jeceval = (inchem_rec or {}).get("jeceval") or {}
    has_jecfa = bool(jeceval)

    if has_jecfa:
        adi_raw  = jeceval.get("adi") or "未提供"
        comments = (jeceval.get("comments") or "")[:300]
        func_cls = jeceval.get("functional_class") or ""
        latest   = jeceval.get("latest_evaluation") or "不明"
        jecurl   = jeceval.get("jeceval_url") or ""

        # 嘗試取 monograph evaluation 段落
        mono_eval = ""
        for mono in ((inchem_rec or {}).get("monographs") or []):
            seg = (mono.get("evaluation") or "").strip()
            if len(seg) > 80:
                mono_eval = seg[:500]
                break

        lines += [
            f"## JECFA 官方安全評估（confidence: verified，最近評估年：{latest}）",
            f"ADI：{adi_raw}",
        ]
        if func_cls:
            lines.append(f"JECFA 功能類別：{func_cls}")
        if comments:
            lines.append(f"JECFA 備註：{comments}")
        if mono_eval:
            lines.append(f"JECFA 評估結論節錄：{mono_eval}")
        if jecurl:
            lines.append(f"JECFA 來源 URL：{jecurl}")
        lines += [
            "",
            "## 補充說明",
            "JECFA 資料已提供（verified），請直接從上方文字整合安全結論。",
            f"若訓練資料中有已知的 PubMed PMID 或 EFSA/FDA 文獻（{name_en}），可附上，否則不必強求。",
        ]
    else:
        lines += [
            "## JECFA 資料：查無比對",
            "請依據訓練資料中對此添加物的已知安全評估（JECFA/EFSA/FDA/衛福部）撰寫說明。",
            "若訓練資料中有確定的 PMID 或官方文獻 URL，可列入 sources，否則標 ai_inferred。",
            "查無可靠資料時如實說明（no_data），不要憑空生成。",
        ]

    lines += [
        "",
        "請輸出消費者說明（80–150 字），然後緊接一行 JSON（格式見 system prompt）。",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# 解析 AI 輸出
# ─────────────────────────────────────────────────────────────────
def parse_response(text: str, inchem_rec: dict | None) -> dict:
    description = text.strip()
    meta = {
        "overall_confidence": "ai_inferred",
        "adi_status": "unknown",
        "adi_value": None,
        "sources": [],
    }

    # 找最後一行的 JSON blob
    match = re.search(
        r'\{[^{}]*"overall_confidence"[^{}]*"sources"\s*:\s*\[.*?\]\s*\}',
        text, re.DOTALL
    )
    if match:
        try:
            blob = json.loads(match.group())
            meta.update({k: blob[k] for k in ("overall_confidence","adi_status","adi_value","sources") if k in blob})
            description = text[:match.start()].strip()
        except json.JSONDecodeError:
            pass

    # 若 AI 沒輸出 sources 但有 JECFA，自動補上
    jeceval = (inchem_rec or {}).get("jeceval") or {}
    if jeceval and not meta["sources"]:
        jecurl = jeceval.get("jeceval_url", "")
        latest = jeceval.get("latest_evaluation", "")
        if jecurl:
            meta["sources"] = [{
                "confidence": "verified",
                "type": "JECFA",
                "title": f"JECFA Evaluation — {jeceval.get('name', '')}",
                "url": jecurl,
                "year": latest,
            }]
        if not meta["overall_confidence"] or meta["overall_confidence"] == "ai_inferred":
            meta["overall_confidence"] = "verified"

    # 同步 adi 欄位（優先用 JECFA 解析結果）
    if jeceval.get("adi_status"):
        meta["adi_status"] = jeceval["adi_status"]
        meta["adi_value"]  = jeceval.get("adi_value")

    return {"consumer_description": description, **meta}


# ─────────────────────────────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY"),
                        help="Gemini API key（可省略，從 .env 的 GEMINI_API_KEY 讀取）")
    parser.add_argument("--model",   default="gemini-2.0-flash")
    parser.add_argument("--limit",   type=int, default=0)
    parser.add_argument("--only-no-jecfa", action="store_true")
    parser.add_argument("--no-thinking", action="store_true",
                        help="關閉 thinking_config（gemini-2.0-flash 不支援時用此旗標）")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("請在 .env 填入 GEMINI_API_KEY 或用 --api-key 帶入")
    client = genai.Client(api_key=args.api_key)

    # ── 啟動時先 ping 確認 model 可用 ──
    print(f"使用模型：{args.model}")
    print("測試 API 連線...", end="", flush=True)
    try:
        ping = client.models.generate_content(
            model=args.model,
            contents="ping",
            config=genai_types.GenerateContentConfig(max_output_tokens=5),
        )
        print(f" OK（回應：{ping.text.strip()[:20]!r}）")
    except Exception as e:
        raise SystemExit(f" 失敗\n模型 [{args.model}] 無法使用：{e}")

    # 所有筆數共用同一組工具：
    #   url_context  → Gemini 實際瀏覽 prompt 裡的 JECFA/PubMed URL，不只是看到文字
    #   googleSearch → 搜尋補充 PubMed、EFSA、FDA 等來源
    # thinking_level MEDIUM → 啟用推理模式，提升整合多來源的品質
    cfg_kwargs = dict(system_instruction=SYSTEM_PROMPT)
    if not args.no_thinking:
        cfg_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_level="MEDIUM")
    config_search = genai_types.GenerateContentConfig(**cfg_kwargs)

    # ── 載入資料 ──
    print("載入 TFDA...")
    with open(SRC_PATH, encoding="utf-8") as f:
        tfda = {r["record_id"]: r for r in json.load(f)}

    print("載入 INCHEM/JECFA...")
    with open(INCHEM_PATH, encoding="utf-8") as f:
        inchem = {r["record_id"]: r for r in json.load(f)}

    # ── 續跑 ──
    done: dict[str, dict] = {}
    if os.path.exists(PATCH_OUT):
        with open(PATCH_OUT, encoding="utf-8") as f:
            for p in json.load(f):
                done[p["record_id"]] = p
        print(f"續跑：已完成 {len(done)} 筆")

    all_ids = list(tfda.keys())
    if args.limit:
        all_ids = all_ids[:args.limit]
    if args.only_no_jecfa:
        all_ids = [rid for rid in all_ids if not inchem.get(rid, {}).get("jeceval")]
        print(f"--only-no-jecfa：{len(all_ids)} 筆")

    remaining = [rid for rid in all_ids if rid not in done]
    print(f"待處理：{len(remaining)} 筆\n{'='*55}")

    errors = []
    for i, rid in enumerate(remaining, 1):
        rec     = tfda.get(rid, {})
        inc_rec = inchem.get(rid)
        has_jecfa = bool((inc_rec or {}).get("jeceval"))
        name_zh = rec.get("name_zh", rid)
        mode_label = "JECFA+AI" if has_jecfa else "SEARCH+AI"

        print(f"[{i}/{len(remaining)}] {rid} {name_zh} [{mode_label}]", end="", flush=True)

        prompt   = build_prompt(rec, inc_rec)
        response = None

        for attempt, wait in enumerate([0] + RETRY_DELAYS):
            if wait:
                print(f" 429，等 {wait}s 後重試({attempt}/{len(RETRY_DELAYS)})...", end="", flush=True)
                time.sleep(wait)
            try:
                response = client.models.generate_content(
                    model=args.model,
                    contents=prompt,
                    config=config_search,
                )
                break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str and attempt < len(RETRY_DELAYS):
                    continue
                print(f" ERR {e}")
                errors.append(rid)
                done[rid] = {
                    "record_id": rid,
                    "consumer_description": None,
                    "overall_confidence": "error",
                    "adi_status": None,
                    "adi_value": None,
                    "sources": [],
                    "_error": err_str,
                }
                response = None
                break

        if response is not None:
            raw_text = response.text.strip()
            parsed   = parse_response(raw_text, inc_rec)
            done[rid] = {"record_id": rid, **parsed}
            conf  = parsed.get("overall_confidence", "?")
            nsrc  = len(parsed.get("sources", []))
            ndesc = len(parsed.get("consumer_description", ""))
            print(f" OK [{conf}] ({ndesc}字, {nsrc}來源)")

        with open(PATCH_OUT, "w", encoding="utf-8") as f:
            json.dump(list(done.values()), f, ensure_ascii=False, indent=2)

        time.sleep(DELAY)

    # ── 統計 ──
    conf_counts: dict[str, int] = {}
    for v in done.values():
        c = v.get("overall_confidence", "unknown")
        conf_counts[c] = conf_counts.get(c, 0) + 1

    print(f"\n{'='*55}")
    print(f"完成！輸出：{PATCH_OUT}")
    for c, n in sorted(conf_counts.items()):
        print(f"  {c:<22}: {n} 筆")
    with_src = sum(1 for v in done.values() if v.get("sources"))
    print(f"  附有來源            : {with_src} 筆")
    if errors:
        print(f"  失敗                : {len(errors)} 筆")


if __name__ == "__main__":
    main()
