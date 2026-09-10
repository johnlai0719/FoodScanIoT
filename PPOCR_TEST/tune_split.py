#!/usr/bin/env python3
# 切分模型的參數搜尋，帶內建的過擬合檢查。
#
# **這個問題的難點不是搜尋演算法，是只有 57 個真實案例。**
# 在 57 案上掃出來的最佳參數，有多少是真訊號、多少是配合這 57 案的雜訊？
# 不回答這個，掃出來的數字就不能信。
#
# 協定沿用既有結論的做法（見 ingredient-list-extraction 的過擬合檢查）：
#   1. 把 57 案**固定**切成半組 A / 半組 B（用 case_id 的雜湊，不用隨機種子，
#      這樣每次跑的分法一致、結果可重現）
#   2. 只在 A 上選參數 → 得到 best_A
#   3. 回報 **best_A 在 B 上的成績**——這才是誠實的數字
#   4. 同時在 B 上選一次 → best_B。`best_B在B` 與 `best_A在B` 的差距
#      **就是過擬合量**。差距小 → 參數是真訊號；差距大 → 是在配合特定案例
#   5. 與雜訊底線比較：小於雜訊的差距不該當真
#      （既有結論的底線是 ±4 項，本支會就添加物層另外估一次）
#
# 為什麼門檻掃描用窮舉而不是貝氏最佳化：它是一維的，而且每案的邊界機率可以
# **算一次快取起來**，掃 40 個門檻只要幾秒。對一維問題用自適應搜尋是白費工，
# 而且會讓「掃了哪些點」變得不可重現。
#
# 用法：
#   python tune_split.py thresh                 # 掃邊界門檻（不需重訓）
#   python tune_split.py thresh --grid=60
#   python tune_split.py noise                  # 估雜訊底線
import argparse
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "output", "split_model")
SRC = "v6_hires__boxth0.4"


def half(case_id):
    """固定的 A/B 分組。用雜湊而不是隨機種子——分法要可重現。"""
    h = hashlib.md5(case_id.encode("utf-8")).hexdigest()
    return "A" if int(h[:8], 16) % 2 == 0 else "B"


def load_probs():
    """每案算一次邊界機率並快取。掃門檻時不必重跑模型。"""
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModelForTokenClassification
    import bench_ingredients as BI

    src = open(os.path.join(HERE, "train_split.py"), encoding="utf-8").read()
    ns = {"__file__": os.path.join(HERE, "train_split.py")}
    exec(compile(src[:src.index("def cmd_train")], "ts", "exec"), ns)
    exec(compile(src[src.index("def predict_text"):src.index("def cmd_predict")],
                 "ts2", "exec"), ns)
    MAXLEN, STRIDE = ns["MAXLEN"], ns["STRIDE"]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(OUTDIR)
    model = AutoModelForTokenClassification.from_pretrained(OUTDIR).to(device)
    model.eval()

    BI.BOXES = SRC
    out = []
    for cid, d, gt in BI.load_cases():
        seg = BI.p_union(cid, d, gt) or []
        text = "、".join(seg) if seg else BI.full_text(d)[:2000]
        score = np.zeros(len(text))
        weight = np.zeros(len(text))
        for start in range(0, max(1, len(text)), STRIDE):
            chunk = text[start:start + MAXLEN - 2]
            if not chunk:
                break
            enc = tok(chunk, truncation=True, max_length=MAXLEN,
                      return_offsets_mapping=True, return_tensors="pt")
            off = enc.pop("offset_mapping")[0].tolist()
            with torch.no_grad():
                lg = model(**{k: v.to(device)
                              for k, v in enc.items()}).logits[0]
            pb = torch.softmax(lg, -1)[:, 1].cpu().numpy()
            n = len(chunk)
            for k, (s, e) in enumerate(off):
                if e == s or start + s >= len(text):
                    continue
                w = 1.0 - abs((s - n / 2) / (n / 2 + 1e-9)) * 0.9
                score[start + s] += pb[k] * w
                weight[start + s] += w
            if start + MAXLEN - 2 >= len(text):
                break
        out.append((cid, text, np.divide(score, np.maximum(weight, 1e-9)),
                    gt["ingredients_list"]))
    return out


def cut(text, prob, thresh):
    cuts = [i for i in range(len(text)) if prob[i] >= thresh]
    if not cuts or cuts[0] != 0:
        cuts = [0] + cuts
    items = []
    for i, c in enumerate(cuts):
        e = cuts[i + 1] if i + 1 < len(cuts) else len(text)
        s = text[c:e].strip(" 、,，;；·\n")
        if s:
            items.append(s)
    return items


def additive_score(rows, thresh, adds, generic):
    import sim_match as SM
    hit = tru = got = 0
    for cid, text, prob, gl in rows:
        truth = SM.additives_of(gl, adds, generic)
        mine = SM.additives_of(cut(text, prob, thresh), adds, generic)
        hit += len(truth & mine)
        tru += len(truth)
        got += len(mine)
    rc = hit / tru if tru else 0
    pr = hit / got if got else 0
    return (2 * rc * pr / (rc + pr) if rc + pr else 0), rc, pr


def cmd_thresh(a):
    import sim_match as SM
    adds, generic = SM.load_additives()
    rows = load_probs()
    A = [r for r in rows if half(r[0]) == "A"]
    B = [r for r in rows if half(r[0]) == "B"]
    print("57 案固定切半：A %d 案、B %d 案" % (len(A), len(B)))

    grid = [i / a.grid for i in range(1, a.grid)]
    # 精確度是**約束不是目標**：低於 MIN_PREC 就是在製造假添加物警示，
    # 不論 F1 多高都不採用。
    def best_on(rows_, constrained=True):
        """constrained=False 用來看「不受精確度約束時最好能到哪」。

        半組只有 26–31 案，很可能**所有門檻的精確度都低於 80%**（實測 B 就是），
        這時受約束的搜尋會找不到候選。要把這件事講出來，不能靜靜落到預設值。
        """
        cand = []
        for t in grid:
            f1, rc, pr = additive_score(rows_, t, adds, generic)
            if not constrained or pr >= a.min_prec:
                cand.append((f1, t, rc, pr))
        return (max(cand) if cand else None)

    bA, bB = best_on(A), best_on(B)
    if bA is None:
        bA = best_on(A, False); print("！A 半組在所有門檻下精確度都 < %.0f%%" % (a.min_prec*100))
    if bB is None:
        bB = best_on(B, False); print("！B 半組在所有門檻下精確度都 < %.0f%%——"
                                     "半組只有 %d 案，約束在這個樣本量下不穩定" % (a.min_prec*100, len(B)))
    fA, tA, rA, pA = bA
    fB, tB, rB, pB = bB
    fAB = additive_score(B, tA, adds, generic)
    fBA = additive_score(A, tB, adds, generic)
    fAll, rAll, pAll = additive_score(rows, tA, adds, generic)
    f05 = additive_score(rows, 0.5, adds, generic)

    print("\n── 選參數（只看 A）──")
    print("   best_A 門檻 %.3f   在 A 上 F1 %.1f%%（召回 %.1f%% 精確 %.1f%%）"
          % (tA, fA * 100, rA * 100, pA * 100))
    print("\n── 誠實的數字：best_A 套到沒看過的 B ──")
    print("   F1 %.1f%%（召回 %.1f%% 精確 %.1f%%）"
          % (fAB[0] * 100, fAB[1] * 100, fAB[2] * 100))
    print("\n── 過擬合量 ──")
    print("   在 B 上直接選最好的：門檻 %.3f  F1 %.1f%%" % (tB, fB * 100))
    print("   用 A 選的門檻在 B 上：            F1 %.1f%%" % (fAB[0] * 100))
    print("   **差距 %.1f 點** ← 這就是過擬合量" % ((fB - fAB[0]) * 100))
    print("   反向檢查（B 選、A 驗）：%.1f%% vs %.1f%%，差距 %.1f 點"
          % (fBA[0] * 100, fA * 100, (fA - fBA[0]) * 100))
    print("\n── 全 57 案（僅供對照，不可用來宣告成績）──")
    print("   門檻 %.3f  F1 %.1f%%   ｜ 未調參的 0.5： F1 %.1f%%"
          % (tA, fAll * 100, f05[0] * 100))
    print("   基準 PP-OCR + boxsep： F1 70.0%")

    json.dump({"best_A_thresh": tA, "best_B_thresh": tB,
               "A_on_B_f1": fAB[0], "B_on_B_f1": fB,
               "overfit_gap": fB - fAB[0], "all57_f1": fAll,
               "default05_f1": f05[0]},
              open(os.path.join(HERE, "out", "_tune_thresh.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_noise(a):
    """估添加物層的雜訊底線：隨機切半 N 次，看同一組參數的分數散布多大。

    小於這個散布的差異不該當真——既有結論在成分項數上的底線是 ±4 項，
    這裡是就添加物層 F1 另外估一次。
    """
    import random
    import statistics
    import sim_match as SM
    adds, generic = SM.load_additives()
    rows = load_probs()
    rng = random.Random(0)
    vals = []
    for _ in range(a.trials):
        sub = rng.sample(rows, len(rows) // 2)
        vals.append(additive_score(sub, 0.5, adds, generic)[0] * 100)
    print("隨機切半 %d 次，同一門檻 0.5：" % a.trials)
    print("   中位 %.1f%%   標準差 %.1f 點   範圍 %.1f–%.1f%%"
          % (statistics.median(vals), statistics.pstdev(vals),
             min(vals), max(vals)))
    print("\n**小於 %.1f 點的差異落在雜訊內，不該當真。**"
          % (2 * statistics.pstdev(vals)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("thresh")
    t.add_argument("--grid", type=int, default=40)
    t.add_argument("--min-prec", type=float, default=0.80)
    n = sub.add_parser("noise")
    n.add_argument("--trials", type=int, default=200)
    a = ap.parse_args()
    (cmd_thresh if a.cmd == "thresh" else cmd_noise)(a)


if __name__ == "__main__":
    main()
