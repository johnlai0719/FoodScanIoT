#!/usr/bin/env python3
# PP-OCR 管線 vs Gemini 2.5 Flash，**逐層、同一批案子、同一套評分器**。
#
# 為什麼要重跑而不引用既有數字：兩邊原本的數字不可比。
#   - `results/summary.json` 是 `score_eval.py` 算的，容差與配對規則跟這裡不同
#   - Gemini 只有 48 案的預測（v2.2 那批），我們的基準線是 58 案
#   - 營養那邊 Gemini 報 97.7%，我們報 80%，但兩者的分母定義都是「GT 有值的格」
#     ——分母一樣，案子不一樣（43 vs 57）
# 所以這支只做一件事：**取交集案例，兩邊都餵進同一個 score_one**。
#
# 用法：
#   python vs_gemini.py
#   python vs_gemini.py --detail c09
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import bench_ingredients as BI   # noqa: E402
import sim_match as SM           # noqa: E402
import nutrition_pipeline as N   # noqa: E402
import nutrition_solver as V     # noqa: E402
import emit_json as E            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT_FIELDS = ['name', 'brand', 'manufacturer', 'allergy_warning']


# 預測放哪一份。`run_eval.py` 會覆寫 `predictions/`，舊的那批已備份成
# `predictions_bak_<日期>_<模型>/`，用 --pred 指過去就能新舊對照。
PRED = 'predictions'


def gemini(cid):
    p = os.path.join(S.EVAL_ROOT, PRED, f'{cid}.json')
    if not os.path.exists(p):
        return None
    return (json.load(open(p, encoding='utf-8')) or {}).get('prediction')


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r'-?\d+(?:\.\d+)?', str(v or ''))
    return float(m.group()) if m else None


def nutri_cells(pred, gt):
    """回傳 (對, 應有, 硬填)。應有＝GT 有值的格；硬填＝GT 為 null 我方卻給值。"""
    ok = ch = spur = 0
    for scope in ('nutrition_per_serving', 'nutrition'):
        want, got = (gt.get(scope) or {}), (pred.get(scope) or {})
        for k in V.NUTRITION_FIELDS:
            w, g = num(want.get(k)), num(got.get(k))
            if w is None:
                spur += g is not None
            else:
                ch += 1
                ok += g is not None and N.close(g, w)
    return ok, ch, spur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detail', default=None)
    ap.add_argument('--pred', default=PRED,
                    help='預測資料夾名（相對 EVAL_ROOT），預設 predictions')
    a = ap.parse_args()
    globals()['PRED'] = a.pred

    adds, generic = SM.load_additives()
    pairs = []
    for cid, cat in E._cases():
        gt = S.load_gt(cid, cat)
        gm = gemini(cid)
        if not gt or not gt.get('is_food_label', True) or gm is None:
            continue
        if not gt.get('ingredients_list'):
            continue
        pairs.append((cid, gt, gm, E.build(cid)))

    print('交集 %d 案（Gemini 2.5 Flash 只有 v2.2 那批 48 案的預測）\n' % len(pairs))

    # ── 清單層 ────────────────────────────────────────────────────────────
    agg = {'PP-OCR': [0, 0, 0], 'Gemini': [0, 0, 0]}
    for cid, gt, gm, ours in pairs:
        for tag, lst in (('PP-OCR', ours['ingredients_list']),
                         ('Gemini', gm.get('ingredients_list') or [])):
            h, g, p, _, _ = BI.score_one(lst, gt['ingredients_list'])
            agg[tag][0] += h
            agg[tag][1] += g
            agg[tag][2] += p
    print('── 清單層（一項成分為單位，一對一配對，容 len//4 字）')
    _pr(agg, '命中', 'GT 項', '抽出')

    # ── 添加物層 ──────────────────────────────────────────────────────────
    agg2 = {'PP-OCR': [0, 0, 0], 'Gemini': [0, 0, 0]}
    dt = {}
    for cid, gt, gm, ours in pairs:
        truth = SM.additives_of(gt['ingredients_list'], adds, generic)
        d = {'truth': truth}
        for tag, lst in (('PP-OCR', ours['ingredients_list']),
                         ('Gemini', gm.get('ingredients_list') or [])):
            got = SM.additives_of(lst, adds, generic)
            agg2[tag][0] += len(truth & got)
            agg2[tag][1] += len(truth)
            agg2[tag][2] += len(got)
            d[tag] = got
        dt[cid] = d
    print('\n── 添加物層（下游真正的驗收層，集合比對）')
    _pr(agg2, '命中', '真值', '判定')

    # ── 營養層 ────────────────────────────────────────────────────────────
    print('\n── 營養層（一格＝欄位×欄；「硬填」＝GT 為 null 卻給了值）')
    print('%-10s%8s%8s%8s%10s' % ('', '正確', '應有', '正確率', '硬填'))
    for tag, key in (('PP-OCR', None), ('Gemini', None)):
        o = c = sp = 0
        for cid, gt, gm, ours in pairs:
            x, y, z = nutri_cells(ours if tag == 'PP-OCR' else gm, gt)
            o += x
            c += y
            sp += z
        print('%-10s%8d%8d%9.1f%%%10d' % (tag, o, c, 100.0 * o / max(c, 1), sp))

    # ── 份量 ──────────────────────────────────────────────────────────────
    print('\n── 份量欄')
    for f in ('serving_size', 'servings_per_container'):
        line = ['%-24s' % f]
        for tag in ('PP-OCR', 'Gemini'):
            ok = have = 0
            for cid, gt, gm, ours in pairs:
                p = num((ours if tag == 'PP-OCR' else gm).get(f))
                w = num(gt.get(f))
                have += p is not None
                ok += p is not None and w is not None and abs(p - w) < 0.5
            line.append('%s 正確 %d/%d（給值 %d）' % (tag, ok, len(pairs), have))
        print('  '.join(line))

    # ── 五個沒做的欄位 ────────────────────────────────────────────────────
    print('\n── PP-OCR 完全沒做的欄位，Gemini 的表現')
    print('%-18s%8s%10s%10s' % ('欄位', 'GT 有值', 'Gemini 有值', '完全相同'))
    for f in TEXT_FIELDS:
        n = sum(1 for _, gt, _, _ in pairs if gt.get(f))
        gv = sum(1 for _, _, gm, _ in pairs if gm.get(f))
        ex = sum(1 for _, gt, gm, _ in pairs
                 if gt.get(f) and S.normalize(gm.get(f) or '', fold_variants=True)
                 == S.normalize(gt.get(f), fold_variants=True))
        print('%-18s%8d%10d%10d' % (f, n, gv, ex))
    n = sum(1 for _, gt, _, _ in pairs if gt.get('certification_marks'))
    gv = sum(1 for _, _, gm, _ in pairs if gm.get('certification_marks'))
    print('%-18s%8d%10d%10s' % ('certification_marks', n, gv, '—'))

    if a.detail:
        for cid, d in dt.items():
            if not cid.startswith(a.detail):
                continue
            print('\n═══ %s\n  真值 : %s' % (cid, sorted(d['truth'])))
            for tag in ('PP-OCR', 'Gemini'):
                print('  %-8s漏 %s' % (tag, sorted(d['truth'] - d[tag])))
                print('  %-8s多 %s' % ('', sorted(d[tag] - d['truth'])))


def _pr(agg, c1, c2, c3):
    print('%-10s%8s%8s%8s%9s%9s%9s' % ('', c1, c2, c3, '召回', '精確', 'F1'))
    for tag, (h, g, p) in agg.items():
        r = h / g if g else 0
        pr = h / p if p else 0
        f1 = 2 * r * pr / (r + pr) if r + pr else 0
        print('%-10s%8d%8d%8d%8.1f%%%8.1f%%%8.1f%%'
              % (tag, h, g, p, r * 100, pr * 100, f1 * 100))


if __name__ == '__main__':
    main()
