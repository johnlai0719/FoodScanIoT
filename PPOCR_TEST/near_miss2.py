#!/usr/bin/env python3
# 近似提示：OCR 讀錯字時給「疑似」提示，猜得對嗎？
#
# ⚠ **第一版（near_miss.py）量錯了對象**：它拿 ground_truth 的文字在量，
#   而正解是**正確的**文字，裡面沒有 OCR 錯字可以救。所以配不到的全是
#   「本來就不是添加物」（水、蔗糖、奶油），保守門檻下自然一筆都沒有。
#   要救的是 OCR 的錯字，就得拿 **OCR 的輸出**來量。這支才是對的。
#
# 量法：對每一案
#   1. 取 vlcrop 組裝出來的 ingredients_list（OCR 讀到的字）
#   2. 現行比對配得到的跳過——那些不需要提示
#   3. 配不到的，找資料庫最近鄰
#   4. 與正解的添加物清單比對：
#        猜對 = 最近鄰確實在正解裡      → 這個提示幫到使用者
#        猜錯 = 最近鄰不在正解裡        → 這個提示在誤導使用者
#
# 「猜錯」才是要控制的東西。門檻的選擇就是在猜對數與猜錯數之間取捨。
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "fog"))

from near_miss import edit_distance                       # noqa: E402
from module_a.ingredient_matching import match_ingredients  # noqa: E402
from module_a.ingredient_parser import normalize_text       # noqa: E402
from seed_db import RealDictLikeCursor, load_additives_db   # noqa: E402

EVAL = os.path.join(HERE, "..", "測試", "量化測試")


def gt_additives(cid_file, cur):
    """這一案的正解裡，哪些**確實是添加物**（正解文字拿去比對，配到的那些）。"""
    try:
        gt = json.load(open(cid_file, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = set()
    for item in (gt.get("ingredients_list") or []):
        if not isinstance(item, str) or not item.strip():
            continue
        r = match_ingredients([item], None, cur, None)
        for c in r["chemical"]:
            out.add(c["officialName"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minlen", type=int, default=4)
    a = ap.parse_args()

    cur = RealDictLikeCursor(load_additives_db())
    cur.execute("SELECT name_zh, aliases FROM additives")
    names = []
    for row in cur.fetchall():
        for v in [row.get("name_zh")] + (row.get("aliases") or []):
            if isinstance(v, str) and v.strip():
                names.append((row["name_zh"], normalize_text(v)))

    # 正解檔案索引：case_id -> 路徑
    gt_index = {}
    for root, _, fs in os.walk(os.path.join(EVAL, "ground_truth")):
        for f in fs:
            if f.endswith(".json"):
                gt_index[f[:-5]] = os.path.join(root, f)

    cands = []          # (cid, OCR 讀到的字, 最近鄰, 距離, 比例, 猜對?)
    n_case = 0
    for p in sorted(os.listdir(os.path.join(HERE, "out", "json_vlcrop"))):
        if not p.endswith(".json"):
            continue
        cid = p[:-5]
        if cid not in gt_index:
            continue
        n_case += 1
        truth = gt_additives(gt_index[cid], cur)
        if truth is None:
            continue
        d = json.load(open(os.path.join(HERE, "out", "json_vlcrop", p),
                           encoding="utf-8"))
        for item in (d.get("ingredients_list") or []):
            if not isinstance(item, str) or not item.strip():
                continue
            if match_ingredients([item], None, cur, None)["chemical"]:
                continue                      # 現行就配得到
            n = normalize_text(item)
            if len(n) < a.minlen:
                continue
            best, bd = None, 99
            for cand, cand_n in names:
                if abs(len(cand_n) - len(n)) > 3:
                    continue
                e = edit_distance(n, cand_n)
                if e < bd:
                    best, bd = cand, e
            if best is None or bd == 0:
                continue
            cands.append((cid, item, best, bd, bd / len(n), best in truth))

    print("案例 %d、候選（OCR 配不到且有鄰居、長度 ≥%d）%d 筆\n"
          % (n_case, a.minlen, len(cands)))
    print("%-10s %-8s %-8s %-8s %s" % ("比例上限", "會提示", "猜對", "猜錯", "猜對率"))
    for ratio in (0.10, 0.15, 0.20, 0.25, 0.34, 0.50):
        sel = [c for c in cands if c[4] <= ratio]
        ok = sum(1 for c in sel if c[5])
        bad = len(sel) - ok
        rate = (100.0 * ok / len(sel)) if sel else 0.0
        print("%-10.2f %-8d %-8d %-8d %.1f%%" % (ratio, len(sel), ok, bad, rate))

    print("\n猜對的例子（比例 ≤0.34）：")
    for cid, raw, cand, bd, r, good in [c for c in cands if c[5] and c[4] <= 0.34][:20]:
        print("  %-26s → %-24s %d/%d 字" % (raw[:24], cand, bd, len(normalize_text(raw))))

    print("\n猜錯的例子（比例 ≤0.34）——這些是會誤導使用者的：")
    for cid, raw, cand, bd, r, good in [c for c in cands if not c[5] and c[4] <= 0.34][:20]:
        print("  %-26s → %-24s %d/%d 字" % (raw[:24], cand, bd, len(normalize_text(raw))))


if __name__ == "__main__":
    main()
