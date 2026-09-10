#!/usr/bin/env python3
# `vs_gemini.py` 給的是層級總分，但「營養 79.8% vs 96.0%」問不出下一步做什麼。
# 這支把差距拆到**欄位 × 欄**，並分開三種失效：漏（沒給值）、錯（給了但不對）、
# 硬填（GT 為 null 卻給值）。要修的是哪一種，處理方式完全不同。
#
# 用法：python vs_fields.py [--pred=pred_struct]
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import nutrition_pipeline as N   # noqa: E402
import nutrition_solver as V     # noqa: E402
import emit_json as E            # noqa: E402

LABEL = {'calories': '熱量', 'protein': '蛋白質', 'fat': '脂肪',
         'saturated_fat': '飽和脂肪', 'trans_fat': '反式脂肪',
         'carbohydrates': '碳水化合物', 'sugar': '糖',
         'fiber': '膳食纖維', 'sodium': '鈉'}


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r'-?\d+(?:\.\d+)?', str(v or ''))
    return float(m.group()) if m else None


def main():
    run = 'pred_struct'
    for a in sys.argv[1:]:
        if a.startswith('--pred='):
            run = a.split('=', 1)[1]

    cases = []
    for cid, cat in E._cases():
        gt = S.load_gt(cid, cat)
        if not gt or not gt.get('is_food_label', True) or not gt.get('ingredients_list'):
            continue
        p = os.path.join(S.EVAL_ROOT, run, cid + '.json')
        if not os.path.exists(p):
            continue
        gm = (json.load(open(p, encoding='utf-8')) or {}).get('prediction')
        if gm is None:
            continue
        cases.append((cid, gt, gm, E.build(cid)))
    print('共 %d 案　PP-OCR vs %s\n' % (len(cases), run))

    for scope, zh in (('nutrition_per_serving', '每份'), ('nutrition', '每100g')):
        print('── %s' % zh)
        print('%-12s%6s │%6s%6s%6s%6s │%6s%6s%6s%6s'
              % ('欄位', 'GT有值', 'P對', 'P漏', 'P錯', 'P硬填',
                 'G對', 'G漏', 'G錯', 'G硬填'))
        tot = [0] * 9
        for k in V.NUTRITION_FIELDS:
            row = [0] * 9
            for cid, gt, gm, ours in cases:
                w = num((gt.get(scope) or {}).get(k))
                if w is not None:
                    row[0] += 1
                for i, src in enumerate((ours, gm)):
                    g = num((src.get(scope) or {}).get(k))
                    o = 1 + i * 4
                    if w is None:
                        row[o + 3] += g is not None
                    elif g is None:
                        row[o + 1] += 1
                    elif N.close(g, w):
                        row[o] += 1
                    else:
                        row[o + 2] += 1
            tot = [a + b for a, b in zip(tot, row)]
            print('%-12s%6d │%6d%6d%6d%6d │%6d%6d%6d%6d'
                  % tuple([LABEL.get(k, k)] + row))
        print('%-12s%6d │%6d%6d%6d%6d │%6d%6d%6d%6d\n'
              % tuple(['小計'] + tot))


if __name__ == '__main__':
    main()
