#!/usr/bin/env python3
# 教授 07-30 建議 2 的驗收：展開巢狀有沒有讓成分召回上升、精確有沒有掉。
#
# > Parse the original ingredient text into a hierarchical structure to preserve
# > nested ingredients, then retest all 48 cases **to improve recall while
# > maintaining high precision.**
#
# **這跟建議 7 的 hierarchy accuracy 是兩件事。**
#   建議 2  這個成分有沒有被抽出來——不管它掛在哪、有沒有掛對
#   建議 7  有沒有掛對地方——兩端都要對才算一條邊（見 hier_score.py）
# 對食安示警來說建議 2 更貼近下游：使用者要知道吃到什麼，
# 不是要知道它藏在配方的第幾層。
#
# ## 設計：分母固定，只換預測側
#
# 陷阱是「把計分單位換成比較寬鬆的」——攤平後召回一定上升，但那不是階層解析
# 有用，是判準放寬了。所以**正解側兩邊都攤平**（同一個分母、同一份正解），
# 唯一的變因是預測側有沒有把括號裡的東西拆出來：
#
#     正解（攤平）  奶精、葡萄糖漿、氫化椰子油、乾酪素鈉        4 項
#     A 不展開      ["奶精(葡萄糖漿、氫化椰子油、乾酪素鈉)"]   只對到「奶精」
#     B 展開        ["奶精","葡萄糖漿","氫化椰子油","乾酪素鈉"] 四項全中
#
# **精確度必須一起看。** 展開後輸出項數暴增，亂讀的碎片也變成獨立項目。
# 添加物層踩過一次：不攤平時 PP-OCR 贏 Gemini +18.4，兩側都攤平後只剩 +6.7。
#
# 用法：
#   python bench_expand.py                 # 讀 out/json（emit_json 的產物）
#   python bench_expand.py --run=out/json --by=category
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EVAL = os.path.abspath(os.path.join(HERE, '..', '測試', '量化測試'))
sys.path.insert(0, EVAL)

import casetool                      # noqa: E402
import score_eval as SE              # noqa: E402


OPEN, CLOSE, SEP = '([{（〔【', ')]}）〕】', '、,，;；·'


def split_top_items(items):
    """依**括號深度 0** 的分隔符切開，巢狀整團保留成一項。

    這是「沒做階層展開」的正確對照組：抽取器把整行吐出來時，該切的分隔符仍要切，
    只有括號裡面不動。這樣 A 與 B 的唯一差異才是括號展不展開。
    """
    out = []
    for it in items or []:
        if not isinstance(it, str):
            continue
        buf, depth = [], 0
        for ch in it:
            if ch in OPEN:
                depth += 1
            elif ch in CLOSE:
                depth = max(0, depth - 1)
            elif ch in SEP and depth == 0:
                if buf:
                    out.append(''.join(buf))
                    buf = []
                continue
            buf.append(ch)
        if buf:
            out.append(''.join(buf))
    return out


def prf(tp, npred, ngt):
    p = tp / npred if npred else 0.0
    r = tp / ngt if ngt else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', default=os.path.join(HERE, 'out', 'json'))
    ap.add_argument('--by', choices=('category', 'set_version'))
    a = ap.parse_args()

    cases = casetool.load_cases()['cases']
    agg = {'A': [0, 0, 0], 'B': [0, 0, 0]}      # tp, npred, ngt
    slices = {}
    n = 0
    for c in cases:
        cid = c['case_id']
        if c['category'] == 'non_food':
            continue
        gp = casetool.gt_path(cid, c['category'])
        pp = os.path.join(a.run, cid + '.json')
        if not (os.path.exists(gp) and os.path.exists(pp)):
            continue
        gt = json.load(open(gp, encoding='utf-8'))
        if not gt.get('is_food_label'):
            continue
        g = SE.flatten_items(gt.get('ingredients_list'))
        if not g:
            continue
        pred = json.load(open(pp, encoding='utf-8')) or {}
        pred = pred.get('prediction', pred) if 'prediction' in pred else pred
        items = pred.get('ingredients_list') or []

        # A：只依**頂層**分隔符切（括號內不切）——巢狀整團保留成一項
        # B：括號也換成分隔符再切——巢狀展開
        #
        # ⚠ 兩者都要先做「依分隔符切開」，否則差異裡會混進「抽取器沒切乾淨的
        # 行被多切一刀」。第一版直接拿 norm(整串) 當 A，結果 A 的誤判有 96%
        # 是不含括號的碎片——那表示 A/B 的差別根本不是展不展開巢狀，量錯了。
        pa = {SE.norm(x) for x in split_top_items(items) if SE.norm(x)}
        pb = SE.flatten_items(items)

        n += 1
        for k, p in (('A', pa), ('B', pb)):
            agg[k][0] += len(g & p)
            agg[k][1] += len(p)
            agg[k][2] += len(g)
        if a.by:
            key = c.get(a.by) or '（未填）'
            s = slices.setdefault(key, {'A': [0, 0, 0], 'B': [0, 0, 0], 'n': 0})
            s['n'] += 1
            for k, p in (('A', pa), ('B', pb)):
                s[k][0] += len(g & p)
                s[k][1] += len(p)
                s[k][2] += len(g)

    print('案例 %d｜正解攤平後 %d 項（兩組共用同一分母）\n' % (n, agg['A'][2]))
    print('%-28s %7s %7s %8s %8s %8s' % ('', '命中', '抽出', '召回', '精確', 'F1'))
    for k, zh in (('A', 'A 不展開（括號內不切）'), ('B', 'B 展開（括號也切）')):
        tp, npred, ngt = agg[k]
        p, r, f = prf(tp, npred, ngt)
        print('%-28s %7d %7d %7.1f%% %7.1f%% %7.1f%%'
              % (zh, tp, npred, r * 100, p * 100, f * 100))
    ta, tb = agg['A'], agg['B']
    _, ra, fa = prf(*ta)
    pa_, rb, fb = prf(*tb)
    pa0 = ta[0] / ta[1] if ta[1] else 0
    pb0 = tb[0] / tb[1] if tb[1] else 0
    print()
    print('── 教授 07-30 建議 2 的驗收條件 ──')
    print('  召回要上升    %.1f%% → %.1f%%   %+.1f 點   %s'
          % (ra * 100, rb * 100, (rb - ra) * 100, '✔' if rb > ra else '✘'))
    print('  精確要維持    %.1f%% → %.1f%%   %+.1f 點   %s'
          % (pa0 * 100, pb0 * 100, (pb0 - pa0) * 100,
             '✔' if pb0 >= pa0 - 0.03 else '✘ 掉超過 3 點'))
    print('  （F1 %.1f%% → %.1f%%，僅供參考——條件是分開講的，不是看 F1）'
          % (fa * 100, fb * 100))

    if a.by:
        print('\n── 依 %s 分報（只作描述，切片小不可拿來排名）──' % a.by)
        print('%-16s %4s %8s %8s %9s %9s'
              % ('切片', '案數', 'A 召回', 'B 召回', 'A 精確', 'B 精確'))
        for key, s in sorted(slices.items(), key=lambda kv: -kv[1]['A'][2]):
            _, ra2, _ = prf(*s['A'])
            _, rb2, _ = prf(*s['B'])
            pa2 = s['A'][0] / s['A'][1] if s['A'][1] else 0
            pb2 = s['B'][0] / s['B'][1] if s['B'][1] else 0
            print('%-16s %4d %7.1f%% %7.1f%% %8.1f%% %8.1f%%'
                  % (key, s['n'], ra2 * 100, rb2 * 100, pa2 * 100, pb2 * 100))


if __name__ == '__main__':
    main()
