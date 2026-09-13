#!/usr/bin/env python3
# 近似命中：OCR 只差幾個字時，能不能給「疑似」提示而不製造假警示？
#
# 動機（2026-09-13，魔芋爽那個案例）：
#   OCR 讀到  L-鈦酸鈉 / 5'-次黃膦呤核苷磷酸二鈉 / 5'-烏嘌呤核苷磷酸二鈉
#   正確是    L-麩酸鈉 / 5'-次黃嘌呤核苷磷酸二鈉 / 5'-鳥嘌呤核苷磷酸二鈉
# 三項各錯一個形近字，子字串比對全落空——而資料庫有、比對器也對。
#
# ⚠ 這**不是**已被否決的「字典後修正」。那個是把 OCR 的字換掉再當事實往下算
#   （實測 F1 70.0 → 53.0、精確度 84.6% → 48.7%）。這裡要量的是另一件事：
#   **不替換**，兩個都顯示，讓使用者判斷。前提是那個「疑似」要夠常猜對，
#   否則只是把雜訊推給使用者。
#
# 這支只回答一個問題：**在各種編輯距離門檻下，最近鄰猜對幾次、猜錯幾次。**
# 猜錯（落到另一個真的添加物上）才是危險的那種，因為它看起來很可信。
#
# 用法：
#   python near_miss.py            # 掃 ground_truth 全部案例
#   python near_miss.py --limit 30
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

from module_a.ingredient_matching import match_ingredients  # noqa: E402
from module_a.ingredient_parser import normalize_text       # noqa: E402
from seed_db import RealDictLikeCursor, load_additives_db   # noqa: E402


def edit_distance(a: str, b: str) -> int:
    """標準 Levenshtein。字串短（添加物名 3–15 字），不必優化。"""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def nearest(name: str, names: list):
    """回傳 (最近的資料庫名稱, 距離)。長度差太多的先剔除——
    「水」與「碳酸氫鈉」的距離是 4，但那不是打錯字。"""
    n = normalize_text(name)
    if not n:
        return None, 99
    best, bd = None, 99
    for cand, cand_n in names:
        if abs(len(cand_n) - len(n)) > 3:
            continue
        d = edit_distance(n, cand_n)
        if d < bd:
            best, bd = cand, d
    return best, bd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    db = load_additives_db()
    cur = RealDictLikeCursor(db)
    # 資料庫的全部可比對名稱（正式名 ＋ 別名），先正規化好。
    # load_additives_db() 回的是 sqlite 連線，要經 cursor 取列。
    cur.execute("SELECT name_zh, aliases FROM additives")
    names = []
    for row in cur.fetchall():
        for v in [row.get("name_zh")] + (row.get("aliases") or []):
            if isinstance(v, str) and v.strip():
                names.append((row["name_zh"], normalize_text(v)))
    print("資料庫可比對名稱 %d 個" % len(names))

    # 正解：哪些項目「應該」配到添加物
    gt_dir = os.path.join(HERE, "..", "測試", "量化測試", "ground_truth")
    files = []
    for root, _, fs in os.walk(gt_dir):
        files += [os.path.join(root, f) for f in fs if f.endswith(".json")]
    files.sort()
    if a.limit:
        files = files[:a.limit]
    print("正解案例 %d 個\n" % len(files))

    # 對每一個正解成分項：若**現行比對配不到**，看最近鄰是誰、差幾個字，
    # 以及那個最近鄰是不是正解真正該配到的那一個。
    rows = []
    for p in files:
        try:
            gt = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in (gt.get("ingredients_list") or []):
            if not isinstance(item, str) or not item.strip():
                continue
            r = match_ingredients([item], None, cur, None)
            if r["chemical"]:
                continue            # 現行就配得到，不在本題範圍
            cand, d = nearest(item, names)
            if cand is None or d == 0:
                continue
            rows.append((os.path.basename(p)[:-5], item, cand, d))

    if not rows:
        print("沒有『配不到但有近似鄰居』的項目。")
        return

    # ── 絕對距離不能用 ──────────────────────────────────────────────────
    # 2026-09-13 第一次量到的教訓：`水` 離 `鹵水`（氯化鎂的別名）只有 1，
    # 於是「水」會被提示成「疑似氯化鎂」。同樣是距離 1，
    #   水(1 字) 改 1 個 = 改掉 100%
    #   5'-次黃膦呤核苷磷酸二鈉(11 字) 改 1 個 = 改掉 9%
    # 意義天差地遠。改用**相對比例 ＋ 最短長度**，與比對器既有的
    # MIN_SUBSTR_RATIO=0.6 是同一個道理（當初也是靠比例擋掉「活性乳酸菌→乳酸」）。
    print("相對門檻（距離 ÷ 掃到的字數）與最短長度的組合：\n")
    print("%-8s %-8s %-8s" % ("最短長度", "比例上限", "會提示的筆數"))
    for minlen in (4, 6, 8):
        for ratio in (0.10, 0.15, 0.20, 0.34):
            n = sum(1 for _, raw, _, d in rows
                    if len(normalize_text(raw)) >= minlen
                    and d / max(len(normalize_text(raw)), 1) <= ratio)
            print("%-8d %-8.2f %-8d" % (minlen, ratio, n))
    print()

    print("最短 8 字、比例 ≤0.15 的全部（這組最保守）：")
    shown = 0
    seen = set()
    for _, raw, cand, d in rows:
        n = normalize_text(raw)
        if len(n) >= 8 and d / max(len(n), 1) <= 0.15 and raw not in seen:
            seen.add(raw)
            print("  %-30s → %-24s 距離 %d／%d 字" % (raw[:28], cand, d, len(n)))
            shown += 1
            if shown >= 30:
                break
    if shown == 0:
        print("  （一筆都沒有）")
    print()

    print("%-5s %-6s %-6s %s" % ("距離", "筆數", "占比", "說明"))
    for th in (1, 2, 3):
        hit = [r for r in rows if r[3] == th]
        print("%-5d %-6d %-6.1f%%" % (th, len(hit), 100 * len(hit) / len(rows)))
    print("合計 %d 筆配不到但有鄰居\n" % len(rows))

    print("距離 1 的前 25 筆（掃到 → 最近的資料庫項目）：")
    for _, raw, cand, d in [r for r in rows if r[3] == 1][:25]:
        print("  %-28s → %s" % (raw[:26], cand))

    print("\n距離 2 的前 15 筆：")
    for _, raw, cand, d in [r for r in rows if r[3] == 2][:15]:
        print("  %-28s → %s" % (raw[:26], cand))


if __name__ == "__main__":
    main()
