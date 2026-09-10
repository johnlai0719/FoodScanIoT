#!/usr/bin/env python3
# 用 Gemini **純文字**把 OCR 讀出來的字組成整份 JSON，不送圖片。
#
# 要回答的問題：`emit_json.py` 組裝後有五個欄位是零（name／brand／manufacturer／
# allergy_warning／certification_marks）。規則抽取器已經補上其中三欄，但 name 輸給
# Gemini、brand 完全沒做。既然 OCR 的文字已經在手上，把「結構化」這件事交給
# 文字模型是自然的一步——而且它有一個讀圖做不到的性質，見下。
#
# **這條路唯一的結構性優勢：輸出可以機械檢查。**
# 輸入是文字，所以可以驗「輸出的字元是不是都來自輸入」。讀圖時沒有這個對照物，
# Gemini 才會憑空生出 59 個查無此物的添加物。這裡把該檢查做成 `_traceable()`，
# 逐欄記錄違反情形——**它不修改輸出，只標記**，因為修掉就看不出模型的真實行為。
#
# ⚠ **不要假設純文字比較省。** Gemini 的圖片是固定 258 token，而一張標示的 OCR
# 文字約 500–1500 token，走文字可能更貴，而且還多付了 PP-OCR 的時間。省不省要看
# samples.jsonl 的 tokens_prompt，不要用直覺。
#
# ⚠ **要比的對象是規則抽取器，不是零。** manufacturer 的規則已經 40/52 勝過
# Gemini 讀圖的 37/52。這支要贏的是那個數字。
#
# 契約沿用 gemini_schema.LabelResult，與 pred_struct 同一份 schema，
# 差別只有「看圖」換成「看 OCR 文字」，故兩者可直接並排。
#
# 用法：
#   python run_gemini_text.py --cases c01 c07        # 冒煙測試（Gemini）
#   python run_gemini_text.py --backend=lmstudio --model=qwen/qwen3.5-9b #       --src=v6_hires__boxth0.4 --out=pred_pp_9b     # 本機，免費、離線
#   python run_gemini_text.py --src=gvision --out=pred_ocrllm
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import score_ocr as S            # noqa: E402
import bench_ingredients as BI   # noqa: E402
import table_geometry as TG      # noqa: E402

EVAL_ROOT = S.EVAL_ROOT
sys.path.insert(0, EVAL_ROOT)
import gemini_schema as GS       # noqa: E402

# 純文字任務要換掉 system_instruction 裡預設「看照片」的措辭，其餘規則照抄。
# 不重寫規則的理由：兩組要可比，差異必須只有輸入形態這一項。
TEXT_INSTRUCTION = GS.SYSTEM_INSTRUCTION.replace("照片", "文字").replace(
    "這張", "這段").replace("影像", "文字")
TEXT_PROMPT = ("以下是某食品包裝標示的 OCR 辨識文字，行序可能錯亂、可能有錯字。"
               "請據此填寫欄位，以繁體中文輸出。"
               "**只能使用文字中出現過的內容，不得補充文字裡沒有的東西。**\n\n")


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


ZH = {'calories': '熱量', 'protein': '蛋白質', 'fat': '脂肪',
      'saturated_fat': '飽和脂肪', 'trans_fat': '反式脂肪',
      'carbohydrates': '碳水化合物', 'sugar': '糖',
      'fiber': '膳食纖維', 'sodium': '鈉'}


def nutrition_layout(boxsrc, cid):
    """用框座標判定營養表的欄位順序，**只回報順序，不回報數值**。

    ⚠ **2026-08-26 實測：這整條路沒有用，預設關閉。** 兩個變體都試過：
      給完整的欄位↔數值表 → c01 修好（13→16）但 c07 弄壞（16→13）
      只給欄位順序        → c01 沒修好（13→12）、c07 不變
    再回頭量「兩欄對調」到底多常見：**317 組只有 4 組（1.3%）**。
    也就是說當初從 c01 一案推論「欄位對調是問題」是以偏概全——營養那 9% 的
    失分約七十格是讀字錯（小數點掉了、數字看錯），不是歸錯欄。
    對著 1.3% 的問題投入，還可能把另外 98.7% 弄壞，不划算。

    為什麼不把重建出來的數值一起給：實測會更糟。c01 的鈉本來被對調（7/23），
    給了表就修好；但 c07 反而從 16/16 掉到 13/16——因為 PP-OCR 把小數點讀丟，
    表裡是「蛋白質 12.0/3.0」而正解是 1.2/0.3，模型相信了那張表而不是原文。
    prompt 裡寫「數字以原文為準」擋不住，表格看起來太權威。

    所以只給模型它真正缺的那一件事：**哪一欄是每份、哪一欄是每100公克**。
    這樣結構上不可能引入數值錯誤，因為根本沒給數值。
    """
    p = os.path.join(HERE, "out", boxsrc, cid + ".json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    lines = [(l["text"], l.get("box"))
             for im in d["images"] for l in im.get("lines", []) if l.get("text")]
    pairs = TG.parse_boxes(lines)
    if not pairs:
        return None
    return ("此標示的營養表為兩欄。依框座標判定，"
            "**第一欄為「每份」，第二欄為「每100公克/毫升」**。"
            "線性化文字裡兩欄的數字可能交錯，請依此順序歸欄。")


def ocr_text(src, cid, fmt="flat"):
    """把 OCR 結果轉成要餵給模型的文字。

    flat   —— `full_text(order='reading')`：依框座標分列排序後用空格接成一長串。
               列的結構會消失，多欄版面會交錯。
    blocks —— `blocks()`：先用框的距離把版面分群成區塊（連通分量），區塊之間
               以空行隔開。成分段在版面上本來就是一塊連續文字，分群後自成一區。

    為什麼 blocks 值得試：`bench_ingredients.blocks()` 的檔頭記著一個實測——
    「全域 y 排序再 x 排序」比不排更差（F1 66.0→64.2），因為它把並排的兩欄
    交錯成「左一句右一句」。分區塊就是為了避開那件事。對 PP-OCR 尤其重要，
    它的輸出是幾十上百個小框，攤平之後版面資訊全沒了。

    成本幾乎是零：塊之間多兩個換行，不是給座標。
    """
    p = os.path.join(HERE, "out", src, cid + ".json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    if fmt == "blocks":
        bs = BI.blocks(d)
        return (chr(10) * 2).join(bs) if bs else None
    return BI.full_text(d, order="reading")


def _traceable(pred, src_text):
    """逐欄檢查「輸出字元是否都來自輸入」。回傳有疑慮的欄位。

    只看抽取型的字串欄位。正規化後比對，容忍標點與空白差異——OCR 的斷行本來
    就會讓標點漂移，那不是捏造。數字欄另外檢查：值必須以字串形式出現在原文裡。
    """
    src = set(S.normalize(src_text, fold_variants=True))
    bad = []
    for k in ("name", "brand", "manufacturer", "allergy_warning"):
        v = pred.get(k)
        if not v:
            continue
        extra = set(S.normalize(str(v), fold_variants=True)) - src
        if extra:
            bad.append({"field": k, "value": str(v)[:60],
                        "unseen_chars": "".join(sorted(extra))[:20]})
    for it in (pred.get("ingredients_list") or []):
        extra = set(S.normalize(str(it), fold_variants=True)) - src
        if extra:
            bad.append({"field": "ingredients_list", "value": str(it)[:40],
                        "unseen_chars": "".join(sorted(extra))[:20]})
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="gvision", help="OCR 結果資料夾（out/ 底下）")
    ap.add_argument("--boxsrc", default="v6_hires__boxth0.4",
                    help="提供框座標的 PP-OCR preset，用來重建營養表欄位對應")
    ap.add_argument("--fmt", default="flat", choices=("flat", "blocks"),
                    help="餵給模型的文字格式。blocks 會依框座標分區塊、以空行隔開")
    ap.add_argument("--layout", action="store_true",
                    help="附上營養表的欄位順序提示。**實測沒有用，預設關閉**，"
                         "保留只為了日後對照，理由見 nutrition_layout()")
    ap.add_argument("--out", default="pred_ocrllm")
    ap.add_argument("--backend", default="gemini", choices=("gemini", "lmstudio"),
                    help="lmstudio 走本機 LM Studio（免費、離線），先 lms load 好模型")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--thinking", type=int, default=0,
                    help="thinking 預算。預設 0（與線上管線及 pred_struct 一致）")
    ap.add_argument("--cases", nargs="*", default=[])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    # 兩個後端共用同一份 schema 與同一段文字，差異只有「誰來組裝」，故可並排。
    ask = None
    if a.backend == "gemini":
        load_env(os.path.join(EVAL_ROOT, "..", "..", "server", ".env"))
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            sys.exit("找不到 GEMINI_API_KEY（server/.env）")
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        cfg = types.GenerateContentConfig(
            system_instruction=TEXT_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=GS.LabelResult,
            temperature=a.temperature,
            # 線上管線寫死 thinking_budget=0，pred_struct 亦同。不關掉的話 thinking
            # 會吃掉七成 token（實測 total 4847 而 prompt+output 僅 1558），
            # 兩組的成本與延遲就不可比。
            thinking_config=types.ThinkingConfig(thinking_budget=a.thinking),
        )

        def ask(prompt):
            r = client.models.generate_content(model=a.model, contents=prompt,
                                               config=cfg)
            u = r.usage_metadata
            return r.text, {
                "tokens_prompt": getattr(u, "prompt_token_count", None),
                "tokens_output": getattr(u, "candidates_token_count", None),
                "tokens_total": getattr(u, "total_token_count", None)}
    else:
        import requests
        schema = GS.LabelResult.model_json_schema()
        url = "http://localhost:1234/v1/chat/completions"

        def ask(prompt):
            # Qwen3 系列預設開思考。LM Studio 會把 </think> 之前的東西放進
            # reasoning_content，content 反而空掉——實測 c01 整份 JSON 都在
            # reasoning_content 裡。兩件事一起做：關掉思考（與 Gemini 那組的
            # thinking_budget=0 對齊，否則成本與延遲不可比），並保留退路。
            r = requests.post(url, timeout=900, json={
                "model": a.model,
                "messages": [{"role": "system", "content": TEXT_INSTRUCTION},
                             {"role": "user", "content": prompt}],
                "chat_template_kwargs": {"enable_thinking": False},
                "temperature": a.temperature,
                "max_tokens": 4096,
                # LM Studio 底層轉成 GBNF 約束解碼，格式保證合法。
                # ⚠ 但它擋不住退化迴圈——Qwen3-VL 那次吐出 8192 token 的
                # 重複字串，每一個都是 schema-valid。要看 finish_reason。
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "LabelResult", "strict": True, "schema": schema}},
            })
            r.raise_for_status()
            d = r.json()
            ch = (d.get("choices") or [{}])[0]
            u = d.get("usage") or {}
            m = ch.get("message", {}) or {}
            return (m.get("content") or m.get("reasoning_content") or ""), {
                "tokens_prompt": u.get("prompt_tokens"),
                "tokens_output": u.get("completion_tokens"),
                "tokens_total": u.get("total_tokens"),
                "finish_reason": ch.get("finish_reason")}

    pred_dir = os.path.join(EVAL_ROOT, a.out)
    res_dir = os.path.join(EVAL_ROOT, a.out + "_results")
    os.makedirs(pred_dir, exist_ok=True)
    os.makedirs(res_dir, exist_ok=True)
    sp = os.path.join(res_dir, "samples.jsonl")

    cases = [c for c, _, _ in BI.load_cases()]
    if a.cases:
        cases = [c for c in cases if any(c.startswith(x) for x in a.cases)]

    n_bad = 0
    for i, cid in enumerate(cases, 1):
        dst = os.path.join(pred_dir, cid + ".json")
        if os.path.exists(dst) and not a.force:
            print("[%d/%d] %-28s skip" % (i, len(cases), cid[:26]))
            continue
        txt = ocr_text(a.src, cid, a.fmt)
        if not txt:
            print("[%d/%d] %-28s [SKIP] 沒有 %s 的 OCR 結果"
                  % (i, len(cases), cid[:26], a.src))
            continue

        tbl = nutrition_layout(a.boxsrc, cid) if a.layout else None
        prompt = TEXT_PROMPT + txt
        if tbl:
            prompt += chr(10)*2 + tbl

        t0 = time.time()
        err, out, u = None, None, None
        try:
            raw, u = ask(prompt)
            out = json.loads(raw)
        except Exception as e:
            err = str(e)[:300]
            u = {}
        ms = (time.time() - t0) * 1000

        bad = _traceable(out, txt) if out else []
        n_bad += len(bad)
        json.dump({"case_id": cid, "prediction": out,
                   "_traceability": bad},
                  open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

        row = {"case_id": cid, "ms": round(ms, 1), "src": a.src,
               "model": a.model, "ok": out is not None, "error": err,
               "chars_in": len(txt), "fmt": a.fmt, "n_untraceable": len(bad),
               "layout": bool(tbl),
               "backend": a.backend, "thinking_budget": a.thinking}
        row.update(u or {})
        with open(sp, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        print("[%d/%d] %-28s %6.0fms  in=%-5d tok=%-6s 無法溯源 %d %s"
              % (i, len(cases), cid[:26], ms, len(txt),
                 row.get("tokens_total"), len(bad), err or ""))

    print("\n完成。無法溯源的欄位共 %d 項 → %s" % (n_bad, a.out))


if __name__ == "__main__":
    main()
