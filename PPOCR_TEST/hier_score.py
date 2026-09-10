#!/usr/bin/env python3
# 階層解析的評分器：把正解與預測各自攤成父子配對的集合再比對。
#
# 指標定義（2026-08-24 訂，見 2026-08-27 進度報告）：
#   漏掛 = |E正 − E預|   該掛而未掛
#   錯掛 = |E預 − E正|   掛了不存在的關係
# 兩組配對算不算同一組，取決於**兩端名稱**是否都在容差內相等；容差沿用
# `bench_ingredients._match`（較短者字數的四分之一、至少一字），與清單層一致。
#
# **為什麼要容差**：不設的話，辨識把「葡萄糖漿」讀錯一個字時，即使它確實被
# 正確掛在奶精之下，仍會計為漏掛——那等於把辨識的失誤記到階層的帳上，
# 而拆兩臂量測的目的正是要把這兩者分開。
#
# **獨立失敗 vs 連帶失敗**：父邊自己就沒命中時，它底下的子邊根本沒機會被解析。
# 實測 217 條深層邊只掛在 57 條第一層邊底下（一條掛掉平均連帶 3.8 條，最糟的
# c58 香菇風味粉是 1 帶 9），全部計入的話一個根本原因會被記數次帳，
# 門檻就失去意義。故只有**獨立失敗**計入 N／M，連帶失敗另列。
#
# 不依層分報：第二層 174 條、第三層 39 條、第四層 4 條，且深層集中在泡麵與
# 微波鮮食少數案例，任何逐層的比率都不可解讀，而且答不出「下一步做什麼」。
# 層次只在基準線當描述性資訊呈現。
#
# 用法：
#   python hier_score.py pred_struct
#   python hier_score.py pred_struct --detail c58
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import score_ocr as S            # noqa: E402
import bench_ingredients as BI   # noqa: E402
import hier_baseline as H        # noqa: E402


def edge_set(items):
    """成分清單 → {(父, 子, 層)}。

    2026-08-24 起一律以「父(子)」計數，不分辨括號的真實含意——括號有時放的是
    功能類別（`維生素C(抗氧化劑)`，方向相反）或型態註記，要不要排除屬於慣例
    決定，已在進度報告向指導教授提出，待回覆前全部計入。"""
    out = []
    for it in items or []:
        if not isinstance(it, str):
            continue
        if H.classify(it) == "形狀不符":
            continue          # 括號沒成對，攤不成樹
        out.extend(H.edges(it))
    return out


def same_edge(a, b):
    """兩端都在容差內相等才算同一條邊。"""
    return BI._match(a[0], b[0]) and BI._match(a[1], b[1])


def match(gt, pred):
    """一次配對，兩個方向共用結果，回傳 (命中數, 正解漏掉的, 預測多出的)。

    ⚠ 一定要只配一次。先前寫成兩次獨立的貪婪配對（正解對預測、預測對正解），
    兩邊各自配各自的，命中數就不一致——實測正解側算出 309 命中、預測側算出
    314，差 5 條，導致 precision 與 recall 的分子不是同一個數字，公式寫不出來。
    """
    used, miss = set(), []
    for e in gt:
        hit = None
        for j, f in enumerate(pred):
            if j in used:
                continue
            if same_edge(e, f):
                hit = j
                break
        if hit is None:
            miss.append(e)
        else:
            used.add(hit)
    extra = [f for j, f in enumerate(pred) if j not in used]
    return len(used), miss, extra


def split_cascade(miss, pred):
    """漏掛的邊分成獨立與連帶：父節點自己都沒出現在預測裡的，算連帶。

    判準用「父有沒有以任一條邊的父或子出現」，而不是「父邊有沒有命中」——
    第一層的邊沒有父邊可看，但它的父節點就是頂層項目本身。
    """
    names = {x for e in pred for x in (e[0], e[1])}
    indep, casc = [], []
    for e in miss:
        (casc if not any(BI._match(e[0], n) for n in names) else indep).append(e)
    return indep, casc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="預測資料夾。EVAL_ROOT 底下的名稱（如 pred_struct），"
                                "或任意路徑（如 out/json，emit_json.py 的產物）")
    ap.add_argument("--detail", help="只看某案，印出逐條差異")
    ap.add_argument("--version", help="只算某個測試集版本，如 v2.2（凍結的 48 案）")
    ap.add_argument("--by", choices=("category", "set_version"),
                    help="另外依切片分報。⚠ 只是描述性的——單一切片的案數少，"
                         "任何比率的信賴區間都寬到判不出東西，不可拿來做決策")
    a = ap.parse_args()

    gt_n = pr_n = hits = 0
    miss_i = miss_c = extra = 0
    cases = 0
    worst = []
    # 切片統計：[案數, 正解邊, 預測邊, 命中, 獨漏, 連漏, 錯掛]
    slices, meta = {}, {}
    if a.by:
        meta = {c["case_id"]: c.get(a.by) or "（未填）"
                for c in json.load(open(os.path.join(S.EVAL_ROOT, "cases.json"),
                                        encoding="utf-8"))["cases"]}

    for cid, gt_items in H.load(a.version):
        g = edge_set(gt_items)
        if not g:
            continue
        p = os.path.join(S.EVAL_ROOT, a.run, cid + ".json")
        if not os.path.exists(p):
            p = os.path.join(HERE, a.run, cid + ".json")   # 本地產物，如 out/json
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8")) or {}
        # 兩種形狀：run_eval 系列包在 "prediction" 裡，emit_json.py 是頂層欄位。
        pred = d.get("prediction", d) if "prediction" in d else d
        if not isinstance(pred, dict) or "ingredients_list" not in pred:
            continue
        q = edge_set(pred.get("ingredients_list"))
        cases += 1
        gt_n += len(g)
        pr_n += len(q)

        nhit, m, x = match(g, q)
        ind, casc = split_cascade(m, q)
        hits += nhit
        miss_i += len(ind)
        miss_c += len(casc)
        extra += len(x)
        worst.append((len(ind) + len(x), cid, len(g), len(q), len(ind), len(casc), len(x)))
        if a.by:
            k = meta.get(cid, "（未登記）")
            v = slices.setdefault(k, [0, 0, 0, 0, 0, 0, 0])
            for i, n in enumerate((1, len(g), len(q), nhit,
                                   len(ind), len(casc), len(x))):
                v[i] += n

        if a.detail and cid.startswith(a.detail):
            print("== %s　正解 %d 條 / 預測 %d 條" % (cid, len(g), len(q)))
            for e in ind:
                print("  獨立漏掛  (%s, %s)  第%d層" % (e[0], e[1], e[2] + 1))
            for e in casc:
                print("  連帶漏掛  (%s, %s)  第%d層" % (e[0], e[1], e[2] + 1))
            for e in x:
                print("  錯掛      (%s, %s)  第%d層" % (e[0], e[1], e[2] + 1))
            return

    print("%s　共 %d 案\n" % (a.run, cases))
    print("正解父子配對    %d 條" % gt_n)
    print("預測父子配對    %d 條" % pr_n)
    print()
    print("獨立漏掛 N      %d 條" % miss_i)
    print("錯掛     M      %d 條" % extra)
    print("連帶漏掛        %d 條（父節點整個沒出現，不計入 N）" % miss_c)
    print()
    hit = hits
    assert hit == gt_n - miss_i - miss_c, "命中數與漏掛數對不起來"
    assert hit == pr_n - extra, "命中數與錯掛數對不起來"
    cond = gt_n - miss_c            # 排除連帶後的分母
    if gt_n and pr_n:
        print()
        print("精確率          %.1f%%（%d/%d）" % (100 * hit / pr_n, hit, pr_n))
        print("端到端召回率    %.1f%%（%d/%d）　使用者實際會遇到的" % (100 * hit / gt_n, hit, gt_n))
        if cond:
            print("條件召回率      %.1f%%（%d/%d）　排除整項漏讀後，只看階層本身"
                  % (100 * hit / cond, hit, cond))
            p, r = hit / pr_n, hit / cond
            if p + r:
                print("F1（用條件召回）%.1f%%" % (100 * 2 * p * r / (p + r)))
    if a.by:
        print()
        print("── 依 %s 分報" % a.by)
        print("%-16s %4s %7s %7s %7s %8s %8s %7s"
              % ("切片", "案數", "正解邊", "預測邊", "命中", "端到端召回",
                 "條件召回", "精確"))
        for k, (n, g, q, h, mi, mc, x) in sorted(slices.items(),
                                                 key=lambda kv: -kv[1][1]):
            cond = h + mi          # 條件召回的分母＝命中＋獨立漏掛（排除連帶）
            print("%-16s %4d %7d %7d %7d %7.1f%% %7.1f%% %6.1f%%"
                  % (k, n, g, q, h,
                     100 * h / g if g else 0,
                     100 * h / cond if cond else 0,
                     100 * h / q if q else 0))
        print()
        print("⚠ 切片只作描述。單一切片案數少，比率的信賴區間寬到判不出差異，")
        print("  不可拿來排名或做決策——同一個坑在添加物層踩過（見雜訊底線 ±8）。")
    print()
    print("── 失分最多的案例")
    print("%-28s %5s %5s %5s %5s %5s" % ("case", "正解", "預測", "獨漏", "連漏", "錯掛"))
    for _, cid, ng, nq, ni, nc, nx in sorted(worst, reverse=True)[:8]:
        print("%-28s %5d %5d %5d %5d %5d" % (cid[:26], ng, nq, ni, nc, nx))


if __name__ == "__main__":
    main()
