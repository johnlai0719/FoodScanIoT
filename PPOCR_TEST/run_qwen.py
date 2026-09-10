#!/usr/bin/env python3
# 本機開源 VLM（LM Studio）跑「整份 JSON 抽取」，輸出格式對齊 run_eval.py 的
# pred_*/ ，因此可以直接用 ab_gemini.py / vs_fields.py / vs_gemini.py 與 Gemini 並排。
#
# 為什麼是這個任務而不是 OCR：VL 當讀取器的路已經有三個資料點
# （整頁 109/347、官方版面 137/172、region_crop 混合 85/98），全部輸給
# Google Vision 的 34/61，失效模式一致（無界、無信心值、錯得格式良好）。
# 沒被回答過的問題是另一個：**final JSON 那五個零欄位**
# （name/brand/manufacturer/allergy_warning/servings_per_container）目前只有
# Gemini 生得出來，而 Gemini 是雲端付費且會捏造。本機開源模型能不能接手，
# 沒有任何資料點。這支就是去量那件事。
#
# 契約完全沿用 Gemini 結構化輸出那一組（測試/量化測試/gemini_schema.py）：
# 同一份 SYSTEM_INSTRUCTION、同一句 USER_PROMPT、同一個 LabelResult schema，
# 只是 response_schema 換成 OpenAI 相容的 response_format=json_schema
# （LM Studio 底層轉成 GBNF 約束解碼）。不同的只有模型與跑在哪裡。
#
# ⚠ 影像處理與 Gemini 那組**不同，而且不得不不同**。Gemini 每張圖固定 258 token，
# 原圖直接送；本機 VLM 的視覺 token 隨解析度成長，5MB 原圖會直接吃爆 context。
# 這裡沿用先前 VL 實驗的 `vlm_2048`（最長邊 2048、JPEG q90），是既有的可比基準。
# 方向**預設不轉**——Gemini 拿到的也是沒轉正的原圖，轉了就不是同一題。
#
# 跑之前：
#   lms load qwen/qwen3-vl-8b --gpu=max --context-length=32768
#   lms server start
# context 一定要開大。預設 4096 連一張 2048px 的圖都放不下，症狀是回傳被截斷
# 或整個報錯，看起來像模型不會做，其實是額度不夠。
#
# 用法：
#   python run_qwen.py --model=qwen/qwen3-vl-8b --out=pred_qwen8b
#   python run_qwen.py --cases c01 c30 c58 --force      # 冒煙測試
import argparse
import base64
import io
import json
import os
import re
import sys
import time

import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試")
)
sys.path.insert(0, EVAL_ROOT)
import gemini_schema as GS  # noqa: E402

URL = "http://localhost:1234/v1/chat/completions"


def schema_for_lmstudio():
    """Pydantic → JSON Schema。

    LM Studio 走 llama.cpp 的 json-schema-to-grammar，$defs/$ref/anyOf 都支援，
    所以直接用 Pydantic 產的即可，不必攤平。strict 一併開著——格式從來不是
    Gemini 的失效點（48 案 0 案格式失敗），但本機小模型是另一回事，
    這裡的目的正是讓「格式錯」不要污染「內容錯」的量測。
    """
    s = GS.LabelResult.model_json_schema()
    return {"type": "json_schema",
            "json_schema": {"name": "LabelResult", "strict": True, "schema": s}}


def encode(path, maxside, rot):
    im = Image.open(path).convert("RGB")
    if rot:
        im = im.rotate(-rot, expand=True)   # 正值 = 順時針
    w, h = im.size
    s = maxside / max(w, h)
    if s < 1:
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    b = buf.getvalue()
    return base64.b64encode(b).decode(), im.size, len(b)


def call(model, images_b64, max_tokens, temperature, timeout):
    content = [{"type": "text", "text": GS.USER_PROMPT}]
    for b in images_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b}})
    body = {
        "model": model,
        "messages": [{"role": "system", "content": GS.SYSTEM_INSTRUCTION},
                     {"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": schema_for_lmstudio(),
        "stream": False,
    }
    r = requests.post(URL, json=body, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    ch = (d.get("choices") or [{}])[0]
    return (ch.get("message", {}).get("content") or "",
            ch.get("finish_reason"), d.get("usage") or {})


def write_sample(path, s):
    """一案寫一行、寫完就 flush。

    第一版只在全跑結束時才落地，結果中途停掉就把 39 案的 finish_reason 與
    error 全弄丟了——而那正是要拿來判斷失敗原因的東西。長時間執行的量測
    不能把觀測資料留在記憶體裡等最後一起寫。
    """
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")
        f.flush()


def parse(txt):
    """把回傳字串轉成 dict。約束解碼下應該直接就是 JSON，但**不要假設**——
    thinking 版模型會在前面吐一段 <think>，而那不是格式失敗、是模型種類不同。
    分開記，才看得出到底是哪一種。"""
    if not txt:
        return None, "empty"
    t = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t), None
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)          # 截斷／夾雜散文時的最後手段
        if m:
            try:
                return json.loads(m.group()), "salvaged"
            except Exception:
                pass
        return None, "unparseable"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen/qwen3-vl-8b")
    ap.add_argument("--out", default="pred_qwen")
    ap.add_argument("--maxside", type=int, default=2048)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--orient", action="store_true",
                    help="用 out/_vlm_orient.json 把側躺的圖轉正（Gemini 那組沒有這步）")
    ap.add_argument("--cases", nargs="*", default=[],
                    help="案例前綴，如 c01 c30；不給就全跑")
    ap.add_argument("--force", action="store_true", help="重跑已存在的案例")
    a = ap.parse_args()

    pred_dir = os.path.join(EVAL_ROOT, a.out)
    res_dir = os.path.join(EVAL_ROOT, a.out + "_results")
    os.makedirs(pred_dir, exist_ok=True)
    os.makedirs(res_dir, exist_ok=True)

    orient = {}
    if a.orient:
        p = os.path.join(HERE, "out", "_vlm_orient.json")
        orient = json.load(open(p, encoding="utf-8"))

    cases = json.load(open(os.path.join(EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    if a.cases:
        cases = [c for c in cases
                 if any(c["case_id"].startswith(x) for x in a.cases)]

    # 這次要跑的案例先從舊 samples 移除，之後逐案 append（理由見 write_sample）
    sp = os.path.join(res_dir, "samples.jsonl")
    todo = {c["case_id"] for c in cases}
    if os.path.exists(sp):
        keep = [json.loads(l) for l in open(sp, encoding="utf-8") if l.strip()]
        keep = [s for s in keep if s["case_id"] not in todo]
        with open(sp, "w", encoding="utf-8") as f:
            for s in keep:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
    raw_dir = os.path.join(res_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    samples, t_all = [], time.time()
    for i, c in enumerate(cases, 1):
        cid = c["case_id"]
        dst = os.path.join(pred_dir, cid + ".json")
        if os.path.exists(dst) and not a.force:
            print(f"[{i}/{len(cases)}] {cid:<30} skip")
            continue

        b64s, raw, px = [], 0, []
        for rel in c["images"]:
            path = os.path.join(EVAL_ROOT, rel)
            if not os.path.exists(path):
                alt = os.path.splitext(path)[0] + ".jpg"
                path = alt if os.path.exists(alt) else path
            if not os.path.exists(path):
                print(f"[{i}/{len(cases)}] {cid:<30} [SKIP] 缺圖 {rel}")
                b64s = None
                break
            rot = 90 if orient.get(rel, {}).get("rotate") else 0
            b, size, nbytes = encode(path, a.maxside, rot)
            b64s.append(b)
            raw += nbytes
            px.append("%dx%d%s" % (size[0], size[1], "r" if rot else ""))
        if b64s is None:
            continue

        t0 = time.time()
        err = how = None
        txt = ""
        try:
            txt, finish, usage = call(a.model, b64s, a.max_tokens,
                                      a.temperature, a.timeout)
            out, how = parse(txt)
        except Exception as e:
            out, finish, usage, err = None, "error", {}, str(e)[:300]
        ms = (time.time() - t0) * 1000

        # 解析失敗時把原始回應留下來。「unparseable」本身問不出下一步——
        # 是截斷、是夾雜散文、還是模型根本沒讀懂，處理方式完全不同，
        # 而重跑一次要 76 秒且不保證重現。
        if out is None and txt:
            open(os.path.join(raw_dir, cid + ".txt"), "w",
                 encoding="utf-8").write(txt)

        json.dump({"case_id": cid, "prediction": out},
                  open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        samples.append({
            "case_id": cid, "run": 1, "ms": round(ms, 1),
            "run_tag": a.model, "set_version": c.get("set_version"),
            "category": c.get("category"), "difficulty": c.get("difficulty") or [],
            "n_images": len(b64s), "payload_raw_bytes": raw, "px": px,
            "maxside": a.maxside, "oriented": bool(orient),
            "tokens_prompt": usage.get("prompt_tokens"),
            "tokens_output": usage.get("completion_tokens"),
            "tokens_total": usage.get("total_tokens"),
            "model": a.model, "finish_reason": finish, "parse": how,
            "is_food_label": (out or {}).get("is_food_label"),
            "ok": out is not None, "error": err, "raw_chars": len(txt),
        })
        write_sample(sp, samples[-1])
        print("[%d/%d] %-30s %7.0fms tok=%-6s %-14s %s%s"
              % (i, len(cases), cid[:30], ms,
                 usage.get("total_tokens", "?"), ",".join(px), finish or "-",
                 ("  " + (err or how)) if (err or how) else ""))

    if not samples:
        print("沒有跑到任何案例。")
        return

    ok = sum(s["ok"] for s in samples)
    mss = sorted(s["ms"] for s in samples)
    print("\n%d 案，成功 %d，p50 %.1fs，總計 %.1f 分鐘 → %s"
          % (len(samples), ok, mss[len(mss) // 2] / 1000,
             (time.time() - t_all) / 60, a.out))
    bad = [(s["case_id"], s["error"] or s["parse"] or s["finish_reason"])
           for s in samples if not s["ok"]]
    for cid, why in bad:
        print("  失敗 %-30s %s" % (cid, why))


if __name__ == "__main__":
    main()
