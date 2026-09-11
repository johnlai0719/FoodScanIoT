#!/usr/bin/env python3
"""x/y 幾何特徵對「這一行是不是成分」有沒有貢獻？——三組消融。

**為什麼要問**：本專案的定位失敗過兩次，兩次都是**純空間**的做法
（DBSCAN ＋ 詞彙投票 −4.5 點、視覺版面模型整塊判成 text），
於是結論寫成「分隔訊號是語意的，不是空間的」，`train_linecls.py`
就完全不用幾何，只吃「前一行 [SEP] 本行 [SEP] 後一行」。

但「純空間沒用」不等於「幾何當輔助特徵也沒用」。這支把兩者拆開量：

    A 只有幾何      x/y、行高、字寬、對齊、與錨點的距離…
    B 只有文字      頓號數、數字比例、長度、單位詞、錨點詞…
    C 兩者都有      ← 只有 C 明顯贏過 B，才值得把幾何加進分類器

⚠ 刻意用**簡單模型**（logistic／梯度提升），不是為了拿最好的分數，
   是為了讓三組的差異只來自特徵集合。要的是「幾何有沒有增量資訊」，
   不是「這個分類器有多強」——後者 `train_linecls.py` 已經在做了。

⚠ 切分**依案例分組**。同一案的行高度相關（同一份標示、同一種版面），
   照行隨機切會讓驗證集出現訓練集同一張圖的其他行，分數虛高。

用法：
    python geo_ablation.py
"""
import collections
import json
import os
import re
import statistics
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np           # noqa: E402
import score_ocr as S        # noqa: E402
import region_crop as RC     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SEP_RE = re.compile(r'[、，,；;]')
ANCHORS = ('成分', '成份', '原料', '內容物', '配料')
NUTRI_WORDS = ('熱量', '蛋白質', '脂肪', '碳水', '鈉', '公克', '毫克', '大卡')


def geo_feats(l, lines, idx, page):
    """只用座標算得出來的東西。"""
    x0, y0, x1, y1 = RC.bbox(l['box'])
    px0, py0, px1, py1 = page
    W = max(1, px1 - px0)
    H = max(1, py1 - py0)
    w, h = x1 - x0, y1 - y0
    n = len(re.findall(r'[一-鿿]', l.get('text') or '')) or 1
    # 與上下行的垂直間距、左緣是否對齊
    prev_gap = nxt_gap = 0.0
    dx_prev = dx_next = 1.0
    if idx > 0:
        b = RC.bbox(lines[idx - 1]['box'])
        prev_gap = (y0 - b[3]) / H
        dx_prev = abs(x0 - b[0]) / W
    if idx < len(lines) - 1:
        b = RC.bbox(lines[idx + 1]['box'])
        nxt_gap = (b[1] - y1) / H
        dx_next = abs(x0 - b[0]) / W
    return [
        (x0 - px0) / W, (y0 - py0) / H,          # 在頁面上的相對位置
        w / W, h / H,                            # 相對尺寸
        w / n,                                   # 字寬（營養表的字通常較小）
        h / max(1, w) * 10,                      # 長寬比
        prev_gap, nxt_gap,                       # 與上下行的間距
        dx_prev, dx_next,                        # 左緣對齊
        min((x0 - px0), (px1 - x1)) / W,         # 到最近水平邊緣
        float(l.get('score') or 0),              # OCR 信心（幾何側唯一的非座標項）
    ]


def txt_feats(l, lines, idx):
    """只用文字算得出來的東西，不看任何座標。"""
    t = (l.get('text') or '')
    nt = S.normalize(t)
    ln = max(1, len(nt))
    prev = (lines[idx - 1].get('text') or '') if idx > 0 else ''
    nxt = (lines[idx + 1].get('text') or '') if idx < len(lines) - 1 else ''
    return [
        len(SEP_RE.findall(t)),                                  # 頓號個數
        len(SEP_RE.findall(t)) / ln,                             # 頓號密度
        ln,
        sum(c.isdigit() for c in nt) / ln,                       # 數字比例
        float(any(a in t for a in ANCHORS)),
        float(any(a in prev for a in ANCHORS)),                  # 上一行是錨點
        sum(w in t for w in NUTRI_WORDS),                        # 營養表用詞
        sum(w in prev for w in NUTRI_WORDS),
        float('(' in t or '（' in t),
        len(SEP_RE.findall(prev)),                               # 上下文的頓號
        len(SEP_RE.findall(nxt)),
        float(bool(re.search(r'[%％]', t))),
    ]


def main():
    lab = collections.defaultdict(list)
    for ln in open(os.path.join(HERE, 'out', 'linecls.jsonl'), encoding='utf-8'):
        r = json.loads(ln)
        lab[r['case_id']].append(r['label'])

    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    G, T, Y, grp = [], [], [], []
    nskip = 0
    for c in cases:
        cid = c['case_id']
        p = os.path.join(HERE, 'out', 'v6_best', cid + '.json')
        if not os.path.exists(p) or cid not in lab:
            continue
        rec = json.load(open(p, encoding='utf-8'))
        flat = [l for im in (rec.get('images') or []) for l in (im.get('lines') or [])]
        kept = [l for l in flat
                if len(S.normalize((l.get('text') or '').strip())) >= 2]
        if len(kept) != len(lab[cid]):
            nskip += 1
            continue
        it = iter(lab[cid])
        tag = []
        for l in flat:
            tag.append(next(it) if len(S.normalize((l.get('text') or '').strip())) >= 2
                       else None)
        i = 0
        for im in (rec.get('images') or []):
            lines = [l for l in (im.get('lines') or []) if l.get('box')]
            tags = [t for l, t in zip(im.get('lines') or [], tag[i:i + len(im.get('lines') or [])])
                    if l.get('box')]
            i += len(im.get('lines') or [])
            if not lines:
                continue
            bs = [RC.bbox(l['box']) for l in lines]
            page = (min(b[0] for b in bs), min(b[1] for b in bs),
                    max(b[2] for b in bs), max(b[3] for b in bs))
            for j, (l, tg) in enumerate(zip(lines, tags)):
                if tg is None:
                    continue
                G.append(geo_feats(l, lines, j, page))
                T.append(txt_feats(l, lines, j))
                Y.append(1 if tg == '成分' else 0)
                grp.append(cid)
    G, T, Y = np.array(G, float), np.array(T, float), np.array(Y)
    grp = np.array(grp)
    print('樣本 %d 行（成分 %d，%.1f%%）｜案例 %d｜略過 %d 案\n'
          % (len(Y), Y.sum(), 100 * Y.mean(), len(set(grp)), nskip))

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import precision_recall_fscore_support, average_precision_score

    sets = [('A 只有幾何 (x/y…)', G),
            ('B 只有文字', T),
            ('C 幾何 ＋ 文字', np.hstack([G, T]))]
    print('%-18s %9s %9s %9s %9s' % ('特徵集合', '精確', '召回', 'F1', 'PR-AUC'))
    res = {}
    for name, X in sets:
        gk = GroupKFold(n_splits=5)
        pred = np.zeros(len(Y))
        prob = np.zeros(len(Y))
        for tr, te in gk.split(X, Y, groups=grp):
            m = HistGradientBoostingClassifier(max_iter=200, random_state=0)
            m.fit(X[tr], Y[tr])
            prob[te] = m.predict_proba(X[te])[:, 1]
            pred[te] = (prob[te] >= 0.5).astype(int)
        p, r, f, _ = precision_recall_fscore_support(Y, pred, average='binary',
                                                     zero_division=0)
        ap = average_precision_score(Y, prob)
        res[name] = (p, r, f, ap)
        print('%-18s %8.1f%% %8.1f%% %9.3f %9.3f' % (name, 100 * p, 100 * r, f, ap))

    b = res['B 只有文字']
    c = res['C 幾何 ＋ 文字']
    print('\n幾何的增量：F1 %+.3f、PR-AUC %+.3f（C 對 B）' % (c[2] - b[2], c[3] - b[3]))
    print('對照：只有幾何本身 F1 %.3f' % res['A 只有幾何 (x/y…)'][2])


if __name__ == '__main__':
    main()
