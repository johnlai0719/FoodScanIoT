#!/usr/bin/env python3
"""訓練並評估「成分候選逐項分類器」（[[10-成分候選的逐項分類器]]）。

⚠⚠ **標籤來自評估集正解，全部數字為汙染，不得寫入驗收。**

切分沿用 make_split.py 的三條規則：同系列整群不跨邊、依 category 分層、
**評估集固定、訓練集巢狀**——少一條，量出來的泛化就是錯的。
"""
import collections
import difflib
import json
import os
import re
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_ingredients as BI   # noqa: E402
import score_ocr as S            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = json.load(open(os.path.join(HERE, 'out', '_contaminated_cand.json'),
                      encoding='utf-8'))
BASE_FEATS = ['len', 'cjk', 'space', 'brack', 'digit', 'latin',
              'dict', 'noting', 'pos', 'inseg', 'agree']
CTX_FEATS = ['d_head', 'd_tail', 'in_span', 'ctx_allergen', 'ctx_nutri',
             'ctx_store', 'sep_before', 'sep_after']
# 由 --ctx 決定用哪一組。**只換特徵集，其餘完全相同**（[[11-加入上下文的候選分類器]]）
FEATS = BASE_FEATS
SIM = 0.62          # 與 make_split.py 一致


def key(cid):
    return re.sub(r'^c\d+_', '', cid)


def split_cases(seed=0):
    cases = sorted({r['case_id'] for r in ROWS})
    cat = {r['case_id']: r['category'] for r in ROWS}
    groups = []
    for cid in cases:
        n = key(cid)
        for g in groups:
            if any(difflib.SequenceMatcher(None, n, key(x)).ratio() >= SIM for x in g):
                g.append(cid)
                break
        else:
            groups.append([cid])
    rng = np.random.RandomState(seed)
    bycat = collections.defaultdict(list)
    for g in groups:
        bycat[collections.Counter(cat[c] for c in g).most_common(1)[0][0]].append(g)
    held, pool = [], []
    for c, gs in sorted(bycat.items()):
        idx = rng.permutation(len(gs))
        want = max(1, round(0.4 * sum(len(gs[i]) for i in idx)))
        got = 0
        for i in idx:
            if got < want:
                held += gs[i]; got += len(gs[i])
            else:
                pool.append(gs[i])
    flat = [c for g in pool for c in g]
    subsets = {}
    for n in (20, 40, 70, len(flat)):
        take, out = 0, []
        for g in pool:
            if take >= n:
                break
            out += g; take += len(g)
        subsets[n] = out
    return sorted(held), subsets, groups


def eval_at(clf, X, y, rows, thr):
    p = clf.predict_proba(X)[:, 1]
    keep = p >= thr
    tp = int((keep & (y == 1)).sum()); fp = int((keep & (y == 0)).sum())
    fn = int((~keep & (y == 1)).sum()); tn = int((~keep & (y == 0)).sum())
    tpr = tp / max(1, tp + fn); fpr = fp / max(1, fp + tn)
    return tpr, fpr, keep


def item_f1(rows, keep, gts):
    """把留下來的候選當抽取結果，用正式計分器算成分項 P/R/F1。"""
    bycase = collections.defaultdict(list)
    for r, k in zip(rows, keep):
        if k:
            bycase[r['case_id']].append(r['text'])
    h = g = p = 0
    for cid, gl in gts.items():
        a, b, c, _, _ = BI.score_one(bycase.get(cid, []), gl)
        h += a; g += b; p += c
    rc = h / g if g else 0; pr = h / p if p else 0
    return h, g, p, rc, pr, (2 * rc * pr / (rc + pr) if rc + pr else 0)


def main():
    global FEATS
    use_ctx = '--ctx' in sys.argv
    FEATS = BASE_FEATS + CTX_FEATS if use_ctx else BASE_FEATS
    print('特徵集：%s（%d 維）' % ('字面 ＋ 上下文' if use_ctx else '只有字面', len(FEATS)))
    BI.BOXES = 'vlcrop_hy_v4'
    gts = {}
    base = collections.defaultdict(list)
    for cid, d, gt in BI.load_cases():
        gl = gt.get('ingredients_list') or []
        if gl:
            gts[cid] = gl
            base[cid] = BI.PARSERS['boxsep'](cid, d, gt) or []
    held, subsets, groups = split_cases()
    print('群 %d 個｜評估集 %d 案｜訓練池 %d 案'
          % (len(groups), len(held), len({c for s in subsets.values() for c in s})))

    hs = set(held)
    Rh = [r for r in ROWS if r['case_id'] in hs]
    Xh = np.array([[r[f] for f in FEATS] for r in Rh], float)
    yh = np.array([r['y'] for r in Rh])
    gth = {c: gts[c] for c in held if c in gts}

    # 現行規則在同一組評估案例上的成績（同一把尺）
    h = g = p = 0
    for cid in gth:
        a, b, c, _, _ = BI.score_one(base[cid], gth[cid]); h += a; g += b; p += c
    rc, pr = h / g, h / p
    print('\n現行 boxsep（同一組 %d 案）  命中 %d/%d  召回 %.1f%%  精確 %.1f%%  F1 %.1f'
          % (len(gth), h, g, 100 * rc, 100 * pr, 200 * rc * pr / (rc + pr)))

    print('\n%-6s %-8s %8s %8s %10s %10s %8s' %
          ('訓練案', '模型', '真陽率', '假陽率', '命中/GT', '精確', 'F1'))
    for n in sorted(subsets):
        tr = set(subsets[n])
        Rt = [r for r in ROWS if r['case_id'] in tr]
        Xt = np.array([[r[f] for f in FEATS] for r in Rt], float)
        yt = np.array([r['y'] for r in Rt])
        gtr = np.array([r['case_id'] for r in Rt])
        for name, mk in (('logreg', lambda: make_pipeline(
                              StandardScaler(),
                              LogisticRegression(max_iter=5000, C=1.0))),
                         ('gbdt', lambda: HistGradientBoostingClassifier(
                              max_iter=200, random_state=0))):
            # ⚠ 門檻必須用**折外**分數選。用訓練集內分數選，模型對自己的訓練樣本
            # 過度自信，門檻會偏高——2026-09-09 實測那樣做訓練集假陽率 3%
            # 在評估集上實際是 11%，門檻根本沒轉移。折以 case_id 分組，
            # 同一案的候選不跨折（否則同一張圖的文字兩邊都看得到）。
            oof = np.zeros(len(yt))
            ng = len(set(gtr))
            for a, b in GroupKFold(n_splits=min(5, ng)).split(Xt, yt, gtr):
                m = mk(); m.fit(Xt[a], yt[a])
                oof[b] = m.predict_proba(Xt[b])[:, 1]
            neg = np.sort(oof[yt == 0])
            thr = float(neg[int(0.97 * (len(neg) - 1))])
            clf = mk(); clf.fit(Xt, yt)
            tpr, fpr, keep = eval_at(clf, Xh, yh, Rh, thr)
            hh, gg, pp, r2, p2, f2 = item_f1(Rh, keep, gth)
            print('%-6d %-8s %7.1f%% %7.1f%% %6d/%-5d %9.1f%% %7.1f'
                  % (len(tr), name, 100 * tpr, 100 * fpr, hh, gg, 100 * p2, 100 * f2))
    # 特徵重要性（logreg 係數）
    clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000)).fit(
        np.array([[r[f] for f in FEATS] for r in ROWS if r['case_id'] not in hs], float),
        np.array([r['y'] for r in ROWS if r['case_id'] not in hs]))
    print('\n特徵係數（正＝支持是成分）：')
    for f, c in sorted(zip(FEATS, clf[-1].coef_[0]), key=lambda x: -abs(x[1])):
        print('   %-8s %+.2f' % (f, c))


if __name__ == '__main__':
    main()
