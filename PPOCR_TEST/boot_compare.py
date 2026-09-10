#!/usr/bin/env python3
# 兩個方法在添加物層的**配對 bootstrap 比較**：這個差距是真的，還是雜訊？
#
# 為什麼需要：`tune_split.py noise` 量到半組樣本的 F1 標準差是 5.1 點，
# 而 BERT 切分模型對基準的增益只有 2.2 點。乍看之下增益被雜訊淹沒——
# **但那個估計是「非配對」的**，它量的是「換一批案例，分數會差多少」，
# 混進了案例難度的變異。
#
# 兩個方法跑在**同一批案例**上時，案例難度的變異對兩邊一樣，會抵銷。
# 該問的是「**差距**的分布」而不是「分數的分布」。所以對案例做 bootstrap
# 重抽，每次算兩個方法的 F1 差，看那個差的信賴區間有沒有跨過 0。
#
# 用法：
#   python boot_compare.py v6_hires__boxth0.4:boxsep bertsplit:items
#   python boot_compare.py gvision:dict bertsplit:items --n=5000
#
# 規格 `A:B` 的 B 是取用方式：
#   抽取器名稱  → 在該 preset 的文字上跑抽取器（OCR 輸出用這個）
#   items      → preset 的 lines 直接當最終清單（切分模型用這個）
#   pred       → A 是 run_eval.py 的預測資料夾名，直接讀它的 ingredients_list
#                （比較 Gemini 各設定用這個，如 `pred_struct:pred`）
import argparse
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_ingredients as BI   # noqa: E402
import sim_match as SM           # noqa: E402
import score_items as SI         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


# 列舉案例用的 OCR 輸出目錄。⚠ 原本寫死在只有 57 案的舊目錄，
# 測試集擴到 177 案之後會靜默只比 57 案，判別力平白少掉三分之二
# （2026-09-07 踩過）。用 --boxes 指定。
BOXES_DEFAULT = "v6_best"


def per_case(spec, adds, generic, boxes=BOXES_DEFAULT):
    """回傳 {case_id: (命中數, 真值數, 判定數)}。"""
    preset, how = spec.split(":")
    BI.BOXES = boxes
    out = {}
    if how == "pred":
        # run_eval.py 的產物：{"case_id":..., "prediction": {...}}。
        # 失敗的案子 prediction 是 None，直接跳過——它在共同案例取交集時會被剔掉，
        # 兩邊比的仍是同一批。
        import json
        import score_ocr as S
        for cid, d, gt in BI.load_cases():
            f = os.path.join(S.EVAL_ROOT, preset, cid + ".json")
            if not os.path.exists(f):
                continue
            pr = (json.load(open(f, encoding="utf-8")) or {}).get("prediction")
            if pr is None:
                continue
            truth = SM.additives_of(gt["ingredients_list"], adds, generic)
            mine = SM.additives_of(pr.get("ingredients_list") or [], adds, generic)
            out[cid] = (len(truth & mine), len(truth), len(mine))
        return out
    for cid, d, gt in BI.load_cases():
        if how == "items":
            items = SI.items_of(preset, cid)
            if items is None:
                continue
        else:
            saved, BI.BOXES = BI.BOXES, preset
            try:
                d2 = BI.load_cases.__wrapped__ if False else None
            finally:
                BI.BOXES = saved
            import json
            p = os.path.join(HERE, "out", preset, cid + ".json")
            if not os.path.exists(p):
                continue
            dd = json.load(open(p, encoding="utf-8"))
            items = BI.PARSERS[how](cid, dd, gt) or []
        truth = SM.additives_of(gt["ingredients_list"], adds, generic)
        mine = SM.additives_of(items, adds, generic)
        out[cid] = (len(truth & mine), len(truth), len(mine))
    return out


def f1_of(rows):
    h = sum(r[0] for r in rows)
    t = sum(r[1] for r in rows)
    g = sum(r[2] for r in rows)
    rc = h / t if t else 0
    pr = h / g if g else 0
    return 2 * rc * pr / (rc + pr) if rc + pr else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--boxes", default=BOXES_DEFAULT)
    x = ap.parse_args()

    adds, generic = SM.load_additives()
    A = per_case(x.a, adds, generic)
    B = per_case(x.b, adds, generic)
    ks = sorted(set(A) & set(B))
    print("共同案例 %d 案" % len(ks))
    fa, fb = f1_of([A[k] for k in ks]), f1_of([B[k] for k in ks])
    print("%-34s F1 %.1f%%" % (x.a, fa * 100))
    print("%-34s F1 %.1f%%" % (x.b, fb * 100))
    print("差距 %+.1f 點\n" % ((fb - fa) * 100))

    rng = random.Random(x.seed)
    diffs = []
    for _ in range(x.n):
        s = [ks[rng.randrange(len(ks))] for _ in ks]
        diffs.append(f1_of([B[k] for k in s]) - f1_of([A[k] for k in s]))
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))] * 100
    hi = diffs[int(0.975 * len(diffs))] * 100
    win = sum(1 for d in diffs if d > 0) / len(diffs)
    print("配對 bootstrap（對案例重抽 %d 次）" % x.n)
    print("   差距的 95%% 信賴區間： %+.1f 到 %+.1f 點" % (lo, hi))
    print("   B 勝過 A 的比例：      %.1f%%" % (win * 100))
    if lo > 0:
        print("\n→ 區間不跨 0，**這個增益站得住**。")
    elif hi < 0:
        print("\n→ 區間不跨 0，**B 確實比較差**。")
    else:
        print("\n→ **區間跨過 0，這個差距在雜訊內，不該當成結論。**")
        print("   要拉開判別力只有兩條路：擴充測試集，或找效果更大的改動。")


if __name__ == "__main__":
    main()
