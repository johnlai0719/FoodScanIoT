#!/usr/bin/env python3
"""量 `allergy.py` 的抽取結果。

**兩個指標，刻意分開報**：
  1. **文字相似度** —— 抽出來的字串與正解的字元層相似度。這是「有沒有照抄對」。
  2. **過敏原集合** —— 兩邊提到的過敏原名稱做集合比較。這是「下游拿到的資訊對不對」。

分開的理由：正解本身對前綴不一致（c03 抄了「過敏原資訊:」、c21／c28 沒抄），
那個差異會壓低相似度但完全不影響下游。集合指標不受它影響。

**一律報筆數不報百分比**——比率的分母是我們自己決定的，讀不出「下一步做什麼」。

用法：
    python bench_allergy.py                    # 預設 vlcrop_hy_v4（177 案）
    python bench_allergy.py --preset=v6_best
    python bench_allergy.py --union             # 兩個讀取器合併
    python bench_allergy.py --miss             # 只列沒抓到與抓錯的
"""
import argparse
import glob
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S      # noqa: E402
import allergy as A        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EV = os.path.join(os.path.dirname(HERE), '測試', '量化測試')


def load_gt():
    out = {}
    for f in glob.glob(os.path.join(EV, 'ground_truth', '*', '*.json')):
        g = json.load(io.open(f, encoding='utf-8'))
        out[os.path.basename(f)[:-5]] = g
    return out


def ocr_lines(preset, cid):
    p = os.path.join(HERE, 'out', preset, cid + '.json')
    if not os.path.exists(p):
        return None
    d = json.load(io.open(p, encoding='utf-8'))
    return [ln.get('text') or '' for im in d.get('images', [])
            for ln in im.get('lines', [])]


def sim(pred, gt):
    """對稱的字元相似度：兩邊都正規化後，取編輯距離。

    用 substring_edit(gt, pred) 會讓「多抓一大段」不受罰（起訖免費），
    所以這裡改用全長編輯距離——多抓與少抓一樣扣分。
    """
    a, b = S.normalize(gt), S.normalize(pred)
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return 1 - prev[n] / max(m, n)


def main():
    ap = argparse.ArgumentParser()
    # 舊預設是 58 案的 `vlcrop_hy`，測試集擴到 177 後會靜默只評舊案例。
    ap.add_argument('--preset', default='vlcrop_hy_v4')
    ap.add_argument('--union', action='store_true',
                    help='兩個讀取器各抽一次，取過敏原較多的那個（不看正解）')
    ap.add_argument('--miss', action='store_true')
    a = ap.parse_args()

    gts = load_gt()
    have = {c: g['allergy_warning'] for c, g in gts.items() if g.get('allergy_warning')}
    none = [c for c, g in gts.items() if not g.get('allergy_warning')]

    rows, fp = [], []
    for cid in sorted(gts):
        if a.union:
            cands = []
            for pre in ('vlcrop_hy_v4', 'v6_best'):
                L = ocr_lines(pre, cid)
                if L:
                    c = A.extract(L)
                    if c:
                        cands.append(c)
            # 抓不到不能 continue——那會把案例從分母裡拿掉，
            # 讓「沒抓到」看起來像不存在。分母必須固定是 49。
            # 選法**不看正解**：過敏原字詞多的優先，同多取較長者。
            # 理由：漏讀會少列過敏原，而多列的風險已由誤報數控住（0/9）。
            pred = max(cands, key=lambda c: (len(A.allergens(c)), len(c)))                 if cands else None
        else:
            L = ocr_lines(a.preset, cid)
            if L is None:
                continue
            pred = A.extract(L)
        gt = have.get(cid)
        if gt is None:
            if pred:
                fp.append((cid, pred))
            continue
        rows.append((cid, gt, pred, sim(pred, gt) if pred else 0.0))

    got = [r for r in rows if r[2]]
    print('=' * 72)
    print('preset = %s     正解有警語 %d 案，正解無警語 %d 案'
          % (a.preset, len(have), len(none)))
    print('-' * 72)
    print('抽到東西            %d / %d 案' % (len(got), len(rows)))
    for th in (0.9, 0.8, 0.6):
        print('文字相似度 >= %.1f   %d / %d 案'
              % (th, sum(1 for r in rows if r[3] >= th), len(rows)))

    # 過敏原集合
    tp = fpn = fnn = 0
    exact = 0
    for cid, gt, pred, _ in rows:
        g, p = A.allergens(gt), A.allergens(pred)
        tp += len(g & p); fpn += len(p - g); fnn += len(g - p)
        if g == p:
            exact += 1
    print('-' * 72)
    print('過敏原名稱  命中 %d  多判 %d  漏判 %d   （正解共 %d 個）'
          % (tp, fpn, fnn, tp + fnn))
    print('過敏原集合完全一致  %d / %d 案' % (exact, len(rows)))
    print('-' * 72)
    print('正解無警語卻抽到東西（誤報） %d / %d 案' % (len(fp), len(none)))

    if a.miss:
        print('\n── 沒抓到 ──')
        for cid, gt, pred, s in rows:
            if not pred:
                print('  %-32s GT: %s' % (cid, gt[:60]))
        print('\n── 抓到但相似度 < 0.8 ──')
        for cid, gt, pred, s in sorted(rows, key=lambda r: r[3]):
            if pred and s < 0.8:
                print('  %-32s %.2f' % (cid, s))
                print('      GT  : %s' % gt)
                print('      PRED: %s' % pred)
        if fp:
            print('\n── 誤報 ──')
            for cid, pred in fp:
                print('  %-32s %s' % (cid, pred[:70]))


if __name__ == '__main__':
    main()
