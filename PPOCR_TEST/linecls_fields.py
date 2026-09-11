#!/usr/bin/env python3
"""用逐行分類器的**十一類**機率直接挑出欄位，與現行做法比。

現行的欄位來源各走各的路：
    品名／製造商   Qwen3.5-2B 讀兩個讀取器的 OCR 文字整理（`local_fields.py`）
    過敏原         規則掃兩個讀取器的行（`allergy.py`）
    成分           規則吃 HunyuanOCR 的行（`bench_ingredients.boxsep`）

這支問的是：**分類器的 `P(品名)`／`P(過敏原)`／`P(廠商)` 能不能取代它們？**
若可以，Qwen 就能拿掉——那對 Fog 那一階是關鍵，2B 在 Pi 上跑不動而 BERT 可以。

⚠ 分類器只挑**哪幾行**，不改寫字。這讓它落在 §0（輸出的每個字都要有影像來源）
   的安全側：輸出必然是 OCR 行的原文，不可能編造。Qwen 是生成式，
   雖然實測編造率低（name 2/175），但那是統計而非保證。

⚠ 天花板已先量過（文字在不在該讀取器的輸出裡）：
       品名 165/173　製造商 138/150　過敏原 138/153
   分類器一定比天花板差，那是上限不是預期值。

用法：
    python linecls_fields.py
    python linecls_fields.py --pred=linecls_pred_vlcrop_hy_v4
"""
import argparse
import collections
import difflib
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# 分類器類別 → 正解欄位
PAIRS = [('品名', 'name'), ('廠商', 'manufacturer'), ('過敏原', 'allergy_warning')]
# 現行實際抽出的正確數（2026-09-11 量，完整管線）
CURRENT = {'name': 138, 'manufacturer': 127, 'allergy_warning': 115}
CEILING = {'name': 165, 'manufacturer': 138, 'allergy_warning': 138}


def sim(a, b):
    a, b = S.normalize(a or '', True), S.normalize(b or '', True)
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def pick(rows, cls, mode, th):
    """從一案的行裡挑出屬於某一類的文字。

    top   只取機率最高的一行——品名、廠商這種**單行**欄位
    join  取所有超過門檻的行接起來——過敏原警語常跨行
    """
    if mode == 'top':
        best = max(rows, key=lambda r: r['ps'].get(cls, 0))
        return best['text'] if best['ps'].get(cls, 0) >= th else ''
    keep = [r['text'] for r in rows if r['ps'].get(cls, 0) >= th]
    return ''.join(keep)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pred', default='linecls_pred_vlcrop_hy_v4')
    a = ap.parse_args()
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    THS = [0.10, 0.20, 0.35, 0.50]
    hit = collections.Counter()
    gtn = collections.Counter()
    nskip = 0
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        p = os.path.join(HERE, 'out', a.pred, cid + '.json')
        if not os.path.exists(p):
            nskip += 1
            continue
        rows = json.load(open(p, encoding='utf-8'))
        if not rows or 'ps' not in rows[0]:
            nskip += 1
            continue
        for cls, f in PAIRS:
            v = (gt.get(f) or '').strip()
            if not v:
                continue
            gtn[f] += 1
            mode = 'join' if f == 'allergy_warning' else 'top'
            for th in THS:
                got = pick(rows, cls, mode, th)
                if got and sim(v, got) >= 0.85:
                    hit[(f, th)] += 1

    print('用分類器的類別機率直接挑欄位　　預測來源：%s' % a.pred)
    if nskip:
        print('⚠ 略過 %d 案' % nskip)
    print()
    print('%-14s %8s %9s %9s %9s %9s %10s %9s'
          % ('欄位', '正解有值', '≥0.10', '≥0.20', '≥0.35', '≥0.50',
             '現行做法', '天花板'))
    for cls, f in PAIRS:
        n = gtn[f]
        if not n:
            continue
        cells = ['%d (%.0f%%)' % (hit[(f, th)], 100 * hit[(f, th)] / n)
                 for th in THS]
        print('%-14s %8d %9s %9s %9s %9s %10d %9d'
              % (cls, n, *cells, CURRENT[f], CEILING[f]))
    print('\n「現行做法」：品名／製造商走 Qwen3.5-2B ＋規則退路，過敏原走規則。')
    print('「天花板」：正解的值在該讀取器輸出裡找得到的案數——分類器不可能超過它。')


if __name__ == '__main__':
    main()
