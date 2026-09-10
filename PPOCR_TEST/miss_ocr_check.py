#!/usr/bin/env python3
# 驗收「OCR 有沒有多讀到東西」——**只看筆數，不看 F1**。
#
# 為什麼不能用 F1：添加物層（129 個真值、57 案）的判別力是 ±6 點
# （`boot_compare.py` 量過），今天所有 2 點級別的比較信賴區間都跨 0。
# 用它判斷「裁切重讀有沒有用」只會得到「不確定」。
#
# 改問一個**確定性**的問題：那些因 OCR 而漏掉的添加物，是指名道姓的一份清單
# （`miss_additives.py` 分出來的「影像／OCR」那 18 個）。
# 問「這 18 個的字，在新的 OCR 文字裡出現了嗎」——那是數得出來的，
# 不受抽樣雜訊影響。
#
# 用法：
#   python miss_ocr_check.py ing_crop
#   python miss_ocr_check.py ing_crop --base=v6_hires__boxth0.4
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_ingredients as BI   # noqa: E402
import score_ocr as S            # noqa: E402
import sim_match as SM           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def full_of(preset, cid):
    p = os.path.join(HERE, "out", preset, cid + ".json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    return S.normalize("\n".join(l["text"] for im in d["images"]
                                 for l in im.get("lines", []) if l.get("text")),
                       fold_variants=True)


def forms(name):
    """添加物的鍵可能是「正式名； 別名」的合併字串，要逐個名稱形式判。"""
    out = [S.normalize(x, fold_variants=True)
           for x in re.split(r"[；;／/]", name) if x.strip()]
    return [f for f in out if f] or [S.normalize(name, fold_variants=True)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("--base", default="v6_hires__boxth0.4")
    ap.add_argument("--parser", default="boxsep")
    a = ap.parse_args()

    adds, generic = SM.load_additives()
    BI.BOXES = a.base
    targets = []          # 基準因 OCR 而漏掉的（字根本不在文字裡）
    for cid, d, gt in BI.load_cases():
        truth = SM.additives_of(gt["ingredients_list"], adds, generic)
        got = SM.additives_of(BI.PARSERS[a.parser](cid, d, gt) or [],
                              adds, generic)
        base_full = S.normalize(BI.full_text(d), fold_variants=True)
        for name in sorted(truth - got):
            if not any(f in base_full for f in forms(name)):
                targets.append((cid, name))

    print("基準 %s + %s：因 OCR 而漏掉的添加物 %d 個\n"
          % (a.base, a.parser, len(targets)))
    rec = miss = 0
    by_case = {}
    for cid, name in targets:
        new_full = full_of(a.preset, cid)
        ok = new_full is not None and any(f in new_full for f in forms(name))
        by_case.setdefault(cid, []).append((name, ok))
        rec += ok
        miss += not ok

    print("── %s 讀到了嗎 ──" % a.preset)
    for cid in sorted(by_case, key=lambda c: -sum(1 for _, o in by_case[c] if o)):
        for name, ok in by_case[cid]:
            print("   %s %-26s%s" % ("✓" if ok else "·", cid[:24], name))
    print("\n**%d / %d 個現在讀到了**（%d 個仍然沒有）" % (rec, len(targets), miss))
    print("\n這是筆數，不是 F1——不受 57 案的抽樣雜訊影響。")


if __name__ == "__main__":
    main()
