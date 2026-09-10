#!/usr/bin/env python3
# 用本地 LLM（LM Studio）做成分切分，當作「切分模型值不值得訓練」的免費基線。
#
# 為什麼先做這個：切分是最大的槓桿（52 個漏失裡 34 個），但訓練一個模型要寫
# 訓練腳本、處理長序列切窗、調參。先用手上已經有的 9B 模型 few-shot 跑一次——
# **如果 9B few-shot 都打不過現有規則的 70.0%，fine-tune 一個 0.6B 大概也不行**，
# 那就省下整個訓練工程。跟前面每一步一樣：用便宜的檢查擋掉昂貴的投入。
#
# **few-shot 範例取自合成資料，不是 GT。** 拿 ground_truth 的
# ingredients_raw/ingredients_list 當範例等於把正解餵給受測對象，
# 那是汙染紅線（同 synth_corpus.py 的理由）。
#
# 輸入與現有抽取器完全相同（`bench_ingredients.full_text`），差異才能歸因於
# 「規則 vs 模型」，不會混進 OCR 或前處理的變因。
#
# 前置：lms load qwen/qwen3.5-9b --identifier=qwen ; lms server start
#
# 用法：
#   python run_llm_split.py --preset=llmsplit
#   python sim_match_preset.py v6_hires__boxth0.4 llmsplit
import argparse
import json
import os
import random
import re
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_ingredients as BI   # noqa: E402
import score_ocr as S            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen"
SRC = "v6_hires__boxth0.4"

SYSTEM = """你是食品標示的成分清單抽取器。使用者會給你一段 OCR 讀出來的文字，
裡面混雜了成分表、營養標示、廠商地址、保存方式、行銷文案。

你的工作：只抽出「成分／原料」那一段，切成清單。

規則：
1. 只輸出出現在原文裡的字，不要補字、不要改字、不要翻譯。
2. OCR 常把頓號讀丟，兩個成分會黏在一起（例：關華豆膠多磷酸鈉），要切開。
3. 括號內的巢狀配方整團保留成一項，不要拆開。
   例：抗氧化劑(混合濃縮生育醇、精製芥花油) 是一項，不是三項。
4. 不要輸出營養數值、日期、地址、電話、公司名、過敏原警語。
5. 只輸出 JSON 陣列，不要任何說明文字。"""


def few_shot(n, seed=0):
    """從合成資料抽 few-shot 範例。不可用 GT。"""
    p = os.path.join(HERE, "corpus", "split_train.jsonl")
    rows = []
    with open(p, encoding="utf-8") as f:
        for i, ln in enumerate(f):
            if i >= 300:
                break
            rows.append(json.loads(ln))
    rng = random.Random(seed)
    picked = [r for r in rows if 5 <= len(r["output"]) <= 12]
    rng.shuffle(picked)
    msgs = []
    for r in picked[:n]:
        msgs.append({"role": "user", "content": r["input"]})
        msgs.append({"role": "assistant",
                     "content": json.dumps(r["output"], ensure_ascii=False)})
    return msgs


def parse(txt):
    """從回應裡挖出 JSON 陣列。模型偶爾會包 markdown 或加說明。"""
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
    except Exception:
        return []
    return [str(x).strip() for x in v if isinstance(x, (str, int, float))
            and str(x).strip()]


def ask(shots, text, max_tokens=4096):
    body = {"model": MODEL,
            "messages": ([{"role": "system", "content": SYSTEM}] + shots
                         + [{"role": "user", "content": text}]),
            "temperature": 0, "max_tokens": max_tokens}
    r = requests.post(URL, json=body, timeout=1800).json()
    if "error" in r:
        raise RuntimeError(str(r["error"])[:120])
    ch = r["choices"][0]["message"]
    # Qwen3.5 預設開 thinking，推理走 reasoning_content、答案走 content。
    # max_tokens 給不夠的話 content 會是空的（實測 150 token 全被推理吃光）。
    return ch.get("content") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="llmsplit")
    ap.add_argument("--shots", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--max-chars", type=int, default=3000)
    a = ap.parse_args()

    BI.BOXES = SRC
    cases = BI.load_cases()
    if a.cases:
        cases = [c for c in cases
                 if any(c[0].startswith(x) for x in a.cases)]
    if a.limit:
        cases = cases[:a.limit]
    shots = few_shot(a.shots)
    outdir = os.path.join(HERE, "out", a.preset)
    os.makedirs(outdir, exist_ok=True)

    t0 = time.time()
    nsub = 0
    for i, (cid, d, gt) in enumerate(cases, 1):
        text = BI.full_text(d)[:a.max_chars]
        t = time.time()
        try:
            items = parse(ask(shots, text))
        except Exception as e:
            print("[%d/%d] %-28s 失敗 %s" % (i, len(cases), cid, str(e)[:60]))
            items = []
        # 安全檢查：正規化後輸出字元必須是輸入字元的子集
        full = S.normalize(text, fold_variants=True)
        kept, dropped = [], 0
        for x in items:
            k = S.normalize(x, fold_variants=True)
            if k and k in full:
                kept.append(x)
            else:
                dropped += 1
        nsub += dropped
        rec = {"case_id": cid, "preset": a.preset,
               "set_version": gt.get("set_version"),
               "category": gt.get("category"),
               "images": [{"path": "", "elapsed_s": round(time.time() - t, 2),
                           "n_lines": len(kept),
                           "lines": [{"text": x, "score": None, "box": None}
                                     for x in kept]}],
               "llm": {"raw_n": len(items), "kept_n": len(kept),
                       "dropped_unsupported": dropped}}
        json.dump(rec, open(os.path.join(outdir, cid + ".json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[%d/%d] %-28s %5.1fs  抽出 %2d 項（丟掉 %d 項無依據）"
              % (i, len(cases), cid, time.time() - t, len(kept), dropped))

    print("\n完成 %d 案，%.0fs，共丟掉 %d 項無依據的輸出 -> out/%s/"
          % (len(cases), time.time() - t0, nsub, a.preset))
    print("評分： python sim_match_preset.py %s %s" % (SRC, a.preset))


if __name__ == "__main__":
    main()
