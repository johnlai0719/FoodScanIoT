#!/usr/bin/env python3
# 對 run_baseline.py 的輸出評分，產出「錯了幾個字、錯哪些字」的清單。
#
# 為什麼不用量化測試現有的 F1：那是端到端添加物 F1，量的是
# OCR + 版面重建 + 語意解析 三段疊起來的結果，歸因不到 OCR。
# 要決定「該不該 fine-tune、該 fine-tune 哪一段」，需要的是 OCR 層自己的數字。
#
# 為什麼不用新標行級 ground truth：ground_truth/<類別>/<case>.json 的
# ingredients_raw 是逐字轉錄的成分字串，等於現成的字元級參考答案。58 個 case
# 全都有，零標註成本。它只涵蓋成分那一段（不含營養表），但成分正是系統的主要
# 輸入，而營養數字可以另外用「數值是否出現」來查。
#
# 輸出刻意是筆數與清單、不是百分比：
#   - 「c02 成分錯 23 字」→ 去看那 23 字
#   - 「鈉→納 出現 11 次」→ 這一條就是 fine-tune 或詞表校正的工作項
#   - 「c05 營養 9 個欄位漏了 4 個：saturated_fat, fiber, ...」→ 去看那張圖
# CER 百分比不列，因為它回答不了「下一步做什麼」。
#
# 用法：
#   python score_ocr.py --preset=v5_hires
#   python score_ocr.py --preset=v5_hires --detail c02_濃豆漿
#   python score_ocr.py --compare v5_default v5_hires
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試")
)

NUTRITION_FIELDS = [
    "calories", "protein", "fat", "saturated_fat", "trans_fat",
    "carbohydrates", "sugar", "fiber", "sodium",
]


# 繁簡轉換。PaddleOCR 3.5 的 lang="chinese_cht" 在 PP-OCRv5 是空操作——
# paddleocr/_pipelines/ocr.py 把 ch / chinese_cht / japan 全部指到同一個
# PP-OCRv5_server_rec，只有降到 PP-OCRv3 才有真正的 chinese_cht 模型。
# 所以輸出會夾雜簡體（綠→绿、維→维）。那是字集偏好，不是「認錯字」，
# 兩者的修法完全不同（前者換模型或後處理，後者才需要 fine-tune），
# 混在同一個數字裡會導致誤判該做什麼，因此分開計算。
try:
    from opencc import OpenCC

    _T2S = OpenCC("t2s").convert
except Exception:  # opencc 不在也要能跑，只是兩欄數字會相同
    _T2S = None


def normalize(s, fold_variants=False):
    """只留下有辨識意義的字元：中日文字、英數字。

    標點一律丟掉，因為 OCR 把「、」讀成「·」是分隔符風格差異，不是辨識錯誤，
    留著會把真正的錯字淹沒在標點雜訊裡。全形轉半形則是為了讓 GT 的「（）」
    與 OCR 的「()」不算錯。

    fold_variants=True 時，兩側都轉成簡體再比，用來排除繁簡差異。
    """
    s = unicodedata.normalize("NFKC", s)
    if fold_variants and _T2S is not None:
        s = _T2S(s)
    return "".join(ch for ch in s if ch.isalnum())


def substring_edit(pattern, text):
    """pattern 對 text 的「最佳子字串」編輯距離，回傳 (距離, 對齊操作列表)。

    用途是在整頁 OCR 文字裡找出成分那一段——OCR 會多讀到營養表、廠商地址、
    行銷文案，那些不該算成錯字。所以起訖位置免費（dp 第一列全 0，答案取
    最後一列最小值），只計算 pattern 被吃掉的過程中付出的代價。

    回傳的操作列表用來產生錯字對照：
      ('sub', gt字, ocr字) / ('del', gt字, None) 漏字 / ('ins', None, ocr字) 多字
    """
    m, n = len(pattern), len(text)
    if m == 0:
        return 0, []
    if n == 0:
        return m, [("del", c, None) for c in pattern]

    # dp[i][j] = pattern[:i] 對齊到某個以 text[:j] 結尾的子字串的最小代價
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        dp[i][0] = i
    # ops[i][j]: 0=match 1=sub 2=del(pattern前進) 3=ins(text前進)
    ops = [bytearray(n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        ops[i][0] = 2

    for i in range(1, m + 1):
        pi = pattern[i - 1]
        row, prev = dp[i], dp[i - 1]
        orow = ops[i]
        for j in range(1, n + 1):
            same = pi == text[j - 1]
            diag = prev[j - 1] + (0 if same else 1)
            dele = prev[j] + 1
            ins = row[j - 1] + 1
            best = diag
            op = 0 if same else 1
            if dele < best:
                best, op = dele, 2
            if ins < best:
                best, op = ins, 3
            row[j] = best
            orow[j] = op

    # 免費結尾：從最後一列的最小值往回走
    end = min(range(n + 1), key=lambda j: dp[m][j])
    dist = dp[m][end]

    trace = []
    i, j = m, end
    while i > 0:
        op = ops[i][j]
        if op == 0:
            i, j = i - 1, j - 1
        elif op == 1:
            trace.append(("sub", pattern[i - 1], text[j - 1]))
            i, j = i - 1, j - 1
        elif op == 2:
            trace.append(("del", pattern[i - 1], None))
            i -= 1
        else:
            trace.append(("ins", None, text[j - 1]))
            j -= 1
    trace.reverse()
    return dist, trace


def ingredient_hits(gt_list, ocr_text):
    """逐項檢查 GT 的每個成分名有沒有出現在 OCR 文字裡（順序無關）。

    這是主要指標，char-level CER 只能當輔助。理由：
    整頁 OCR 的「行序」與人閱讀的順序不一致——營養表是多欄、成分被分成
    麵包/油包/粉包好幾段、旁邊還夾雜行銷文案。以整段字串做連續比對時，
    行序一亂就會把「讀對了但擺錯位置」記成大量錯字（c38_科學麵 GT 154 字
    被記成錯 104 字，實際逐行看幾乎全對）。那個數字回答不了「下一步做什麼」。

    逐成分命中則直接對應下游需求：module_a 要的是 ingredients_list，
    漏掉哪幾個成分就是要去修的那幾筆。

    回傳 (exact, fuzzy, misses)：
      exact = 完全出現；fuzzy = 允許 len//4（至少 1）個字的誤差；misses = 沒找到
    """
    exact, fuzzy, misses = [], [], []
    for name in gt_list:
        key = normalize(name, fold_variants=True)
        if not key:
            continue
        if key in ocr_text:
            exact.append(name)
            continue
        tol = max(1, len(key) // 4)
        dist, _ = substring_edit(key, ocr_text)
        (fuzzy if dist <= tol else misses).append(name)
    return exact, fuzzy, misses


def number_present(value, text):
    """數值是否以「獨立的數」出現在 OCR 文字裡。

    前後不得緊鄰其他數字，否則 sodium=7 會被 "17.7公克" 誤判為命中。
    同時接受 9 與 9.0 兩種寫法。
    """
    if value is None:
        return False
    if float(value) == int(float(value)):
        core = str(int(float(value)))
        pat = rf"(?<![\d.]){re.escape(core)}(?:\.0+)?(?![\d.])"
    else:
        core = str(value)
        pat = rf"(?<![\d.]){re.escape(core)}(?![\d])"
    return re.search(pat, text) is not None


def load_gt(case_id, category):
    path = os.path.join(EVAL_ROOT, "ground_truth", category, f"{case_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def score_preset(preset):
    outdir = os.path.join(HERE, "out", preset)
    if not os.path.isdir(outdir):
        sys.exit(f"找不到 {outdir}，先跑 run_baseline.py --preset={preset}")

    rows, sub_pairs, missing_chars = [], Counter(), Counter()
    for fn in sorted(os.listdir(outdir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(outdir, fn), encoding="utf-8") as f:
            rec = json.load(f)
        gt = load_gt(rec["case_id"], rec.get("category") or "")
        if gt is None:
            continue

        raw_text = "\n".join(
            ln["text"] for img in rec["images"] for ln in img.get("lines", [])
        )
        n_lines = sum(img.get("n_lines", 0) for img in rec["images"])

        row = {
            "case_id": rec["case_id"],
            "category": rec.get("category"),
            "n_lines": n_lines,
            "elapsed_s": round(
                sum(img.get("elapsed_s", 0) for img in rec["images"]), 2
            ),
        }

        # ── 成分逐項命中（主要指標，順序無關）──
        folded_ocr = normalize(raw_text, fold_variants=True)
        ex, fz, ms = ingredient_hits(gt.get("ingredients_list") or [], folded_ocr)
        row["ing_total"] = len(ex) + len(fz) + len(ms)
        row["ing_exact"] = len(ex)
        row["ing_fuzzy"] = len(fz)
        row["ing_miss_list"] = ms

        # ── 成分字串 CER（輔助指標，對行序敏感，別單獨引用）──
        # 算兩次：fold=True 是真正的辨識錯誤，fold=False 額外含繁簡差異。
        # 兩者相減即為繁簡造成的字數。
        gt_raw_ing = gt.get("ingredients_raw") or ""
        gt_ing = normalize(gt_raw_ing, fold_variants=True)
        if gt_ing:
            dist, trace = substring_edit(gt_ing, normalize(raw_text, fold_variants=True))
            row["ing_gt_chars"] = len(gt_ing)
            row["ing_errors"] = dist
            row["ing_trace"] = trace
            for op, a, b in trace:
                if op == "sub":
                    sub_pairs[(a, b)] += 1
                elif op == "del":
                    missing_chars[a] += 1
            plain, _ = substring_edit(
                normalize(gt_raw_ing), normalize(raw_text)
            )
            row["ing_variant_errors"] = max(0, plain - dist)
        else:
            row["ing_gt_chars"] = 0
            row["ing_errors"] = None  # 無成分可比（非食品或 GT 缺）
            row["ing_variant_errors"] = None

        # ── 營養數值 ──
        checked, missed = 0, []
        for scope in ("nutrition", "nutrition_per_serving"):
            block = gt.get(scope) or {}
            for field in NUTRITION_FIELDS:
                v = block.get(field)
                if v is None:
                    continue
                checked += 1
                if not number_present(v, raw_text):
                    missed.append(f"{scope}.{field}={v}")
        row["nutri_checked"] = checked
        row["nutri_missed"] = missed
        rows.append(row)

    return rows, sub_pairs, missing_chars


def print_report(preset, rows, sub_pairs, missing_chars, top=30):
    print(f"\n{'=' * 74}\npreset = {preset}   case 數 = {len(rows)}\n{'=' * 74}")

    # ── 主要指標 ──
    ing_rows = [r for r in rows if r.get("ing_total")]
    t_tot = sum(r["ing_total"] for r in ing_rows)
    t_ex = sum(r["ing_exact"] for r in ing_rows)
    t_fz = sum(r["ing_fuzzy"] for r in ing_rows)
    t_ms = sum(len(r["ing_miss_list"]) for r in ing_rows)
    print("\n── 成分逐項命中（主要指標，順序無關）──")
    print(f"   GT 共 {t_tot} 個成分：完全命中 {t_ex}、近似命中 {t_fz}、沒讀到 {t_ms}")
    print("\n   沒讀到的案例（這就是工作清單）：")
    for r in sorted(ing_rows, key=lambda r: -len(r["ing_miss_list"])):
        if r["ing_miss_list"]:
            print(
                f"   {r['case_id']:<24} 漏 {len(r['ing_miss_list'])}/{r['ing_total']}"
                f"   {'、'.join(r['ing_miss_list'][:8])}"
                f"{' …' if len(r['ing_miss_list']) > 8 else ''}"
            )

    scored = [r for r in rows if r["ing_errors"] is not None]
    print("\n── 成分字串 CER（輔助；對行序敏感，數字偏大是正常的）──")
    print("   繁簡欄是另外算的：模型輸出簡體字，換模型或後處理就能修，不需訓練")
    print(f"{'case':<22}{'行數':>5}{'GT字數':>8}{'錯字':>7}{'繁簡':>7}{'秒':>7}")
    for r in sorted(scored, key=lambda r: -r["ing_errors"]):
        print(
            f"{r['case_id']:<22}{r['n_lines']:>5}{r['ing_gt_chars']:>8}"
            f"{r['ing_errors']:>7}{r['ing_variant_errors']:>7}{r['elapsed_s']:>7.1f}"
        )
    tot_err = sum(r["ing_errors"] for r in scored)
    tot_var = sum(r["ing_variant_errors"] for r in scored)
    tot_gt = sum(r["ing_gt_chars"] for r in scored)
    print(f"{'合計':<22}{'':>5}{tot_gt:>8}{tot_err:>7}{tot_var:>7}")
    perfect = [r["case_id"] for r in scored if r["ing_errors"] == 0]
    print(f"\n完全正確 {len(perfect)} 案：{'、'.join(perfect) if perfect else '（無）'}")

    print(f"\n── 形近字誤判 Top {top}（GT字 → OCR字，次數）──")
    print("   這張表就是詞表後校正與 rec fine-tune 的工作清單")
    for (a, b), c in sub_pairs.most_common(top):
        print(f"   {a} → {b}   {c}")

    print(f"\n── 漏字 Top {top}（GT 有、OCR 沒讀到）──")
    for ch, c in missing_chars.most_common(top):
        print(f"   {ch}   {c}")

    print("\n── 營養數值：漏掉的欄位 ──")
    nutri_rows = [r for r in rows if r["nutri_checked"]]
    tot_chk = sum(r["nutri_checked"] for r in nutri_rows)
    tot_miss = sum(len(r["nutri_missed"]) for r in nutri_rows)
    print(f"   共檢查 {tot_chk} 個數值，其中 {tot_miss} 個沒出現在 OCR 文字裡")
    for r in sorted(nutri_rows, key=lambda r: -len(r["nutri_missed"])):
        if r["nutri_missed"]:
            print(f"   {r['case_id']}  漏 {len(r['nutri_missed'])}/{r['nutri_checked']}")
            for m in r["nutri_missed"]:
                print(f"      - {m}")


def print_detail(preset, rows, case_id):
    match = [r for r in rows if r["case_id"].startswith(case_id)]
    if not match:
        sys.exit(f"沒有這個 case：{case_id}")
    for r in match:
        print(f"\n{'=' * 74}\n{r['case_id']}  錯字 {r['ing_errors']}/{r['ing_gt_chars']}\n{'=' * 74}")
        for op, a, b in r.get("ing_trace") or []:
            if op == "sub":
                print(f"  誤認  {a} → {b}")
            elif op == "del":
                print(f"  漏字  {a}")
            else:
                print(f"  多字      → {b}")
        if r["nutri_missed"]:
            print("\n  營養漏欄位：")
            for m in r["nutri_missed"]:
                print(f"    - {m}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="v5_hires")
    ap.add_argument("--detail", default=None, help="印出單一 case 的逐字對照")
    ap.add_argument("--compare", nargs=2, default=None, metavar=("A", "B"))
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    if args.compare:
        a, b = args.compare
        ra, _, _ = score_preset(a)
        rb, _, _ = score_preset(b)
        ia = {r["case_id"]: r for r in ra}
        ib = {r["case_id"]: r for r in rb}
        both = sorted(set(ia) & set(ib))
        print(f"\n{'case':<22}{a:>16}{b:>16}{'差':>8}")
        ta = tb = 0
        for cid in both:
            ea, eb = ia[cid]["ing_errors"], ib[cid]["ing_errors"]
            if ea is None or eb is None:
                continue
            ta, tb = ta + ea, tb + eb
            mark = "" if eb == ea else ("  ↓改善" if eb < ea else "  ↑退步")
            print(f"{cid:<22}{ea:>16}{eb:>16}{eb - ea:>8}{mark}")
        print(f"{'合計錯字':<22}{ta:>16}{tb:>16}{tb - ta:>8}")
        return

    rows, subs, miss = score_preset(args.preset)
    if args.detail:
        print_detail(args.preset, rows, args.detail)
    else:
        print_report(args.preset, rows, subs, miss, args.top)


if __name__ == "__main__":
    main()
