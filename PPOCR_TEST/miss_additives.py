#!/usr/bin/env python3
# 添加物層漏掉的那些，逐一歸因到「掉在哪一層」。
#
# 為什麼要有這支：sim_match 告訴你召回 59.7%，但那個數字問不出下一步做什麼。
# 漏掉的 52 個添加物可能掉在三個完全不同的地方，修法互斥：
#
#   影像／OCR   全文裡根本沒有那些字      → 要改拍攝或讀取器
#   解析        字在全文裡，但沒被切成項目 → 要改抽取器（規則已到頂，見 bench_ceiling）
#   比對        項目切出來了，但沒配到 DB → 要改比對規則或補資料庫
#
# 用法：
#   python miss_additives.py                       # 預設 v6_hires__boxth0.4 + boxsep
#   python miss_additives.py vlcrop boxsep
import os
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import bench_ingredients as BI   # noqa: E402
import sim_match as SM           # noqa: E402


def main():
    preset = sys.argv[1] if len(sys.argv) > 1 else "v6_hires__boxth0.4"
    parser = sys.argv[2] if len(sys.argv) > 2 else "boxsep"
    BI.BOXES = preset
    adds, generic = SM.load_additives()
    cases = BI.load_cases()

    tally = Counter()
    detail = []
    for cid, d, gt in cases:
        truth = SM.additives_of(gt["ingredients_list"], adds, generic)
        items = BI.PARSERS[parser](cid, d, gt) or []
        got = SM.additives_of(items, adds, generic)
        if not (truth - got):
            continue
        full = S.normalize(BI.full_text(d), fold_variants=True)
        norm_items = [S.normalize(x, fold_variants=True) for x in items]
        for a in sorted(truth - got):
            # 添加物的鍵可能是「正式名； 別名1； 別名2」的合併字串，
            # 整串拿去比一定找不到（實測：醋酸鈉四案都被誤判成 OCR 沒讀到，
            # 其實「醋酸鈉」三個字明明在文字裡）。逐個名稱形式分別判。
            forms = [S.normalize(x, fold_variants=True)
                     for x in re.split(r"[；;／/]", a) if x.strip()]
            forms = [f for f in forms if f] or [S.normalize(a, fold_variants=True)]
            in_text = any(f in full for f in forms)
            # 「切出來了」＝有某個抽出項目包含這個添加物名
            in_items = any(f in x for f in forms for x in norm_items)
            if not in_text:
                lay = "影像／OCR"
            elif not in_items:
                lay = "解析"
            else:
                lay = "比對"
            tally[lay] += 1
            detail.append((lay, cid, a))

    tot = sum(tally.values())
    print(f"preset={preset}  抽取器={parser}  漏掉的添加物共 {tot} 個\n")
    for lay in ("影像／OCR", "解析", "比對"):
        print(f"  {lay:<10}{tally[lay]:>4} 個")
    print()
    for lay in ("影像／OCR", "解析", "比對"):
        rows = [(c, a) for l, c, a in detail if l == lay]
        if not rows:
            continue
        print(f"── {lay}（{len(rows)} 個）──")
        by = {}
        for c, a in rows:
            by.setdefault(c, []).append(a)
        for c in sorted(by, key=lambda k: -len(by[k])):
            print(f"   {c:<28}{'、'.join(by[c])}")
        print()


if __name__ == "__main__":
    main()
