#!/usr/bin/env python3
# 在**添加物層**（下游使用者真正看到的那一層）比較不同 OCR 來源。
#
# 為什麼不能只看 bench_ingredients 的清單層 F1：既有結論已經踩過一次——
# p_dict 與 p_boxsep 在清單層 F1 打平（67.6 vs 66.8，看起來 dict 略贏），
# 但到了添加物層是 70.2 vs 72.3，boxsep 才是對的選擇。
# 「中間層的指標不能直接拿來做產品決策」。
#
# 這支就是把 sim_match.py 的同一套規則套到不同 preset 上。
#
# 用法：
#   python sim_match_preset.py v6_hires__boxth0.4 vlcrop
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_ingredients as BI   # noqa: E402
import sim_match as SM           # noqa: E402

# p_boxseg 需要 box 座標，VL 的輸出沒有；p_boxsep 只用「行的切分」，
# 所以 VL 也能跑——而且 VL 的換行本來就是它自己判的閱讀單位。
PARSERS = ["dict", "boxsep", "hybrid", "union"]


def run(preset):
    BI.BOXES = preset
    cases = BI.load_cases()
    adds, generic = SM.load_additives()
    agg = {n: [0, 0, 0] for n in PARSERS}
    for cid, d, gt in cases:
        truth = SM.additives_of(gt["ingredients_list"], adds, generic)
        for n in PARSERS:
            try:
                items = BI.PARSERS[n](cid, d, gt) or []
            except Exception:
                items = []
            got = SM.additives_of(items, adds, generic)
            agg[n][0] += len(truth & got)
            agg[n][1] += len(truth)
            agg[n][2] += len(got)
    return len(cases), agg


if __name__ == "__main__":
    presets = sys.argv[1:] or ["v6_hires__boxth0.4", "vlcrop"]
    print(f"{'preset':<24}{'抽取器':<10}{'命中':>6}{'真值':>6}{'判定':>6}"
          f"{'召回':>8}{'精確':>8}{'F1':>8}")
    for p in presets:
        n, agg = run(p)
        for k in PARSERS:
            h, t, g = agg[k]
            rc = h / t if t else 0
            pr = h / g if g else 0
            f1 = 2 * rc * pr / (rc + pr) if rc + pr else 0
            print(f"{p:<24}{k:<10}{h:>6}{t:>6}{g:>6}"
                  f"{rc*100:>7.1f}%{pr*100:>7.1f}%{f1*100:>7.1f}%")
        print(f"{'':24}（{n} 案）")
