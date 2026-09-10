#!/usr/bin/env python3
# 三組（含以上）Gemini 設定的並排比較：基準 vs 結構化輸出 vs 結構化+低溫。
#
# 為什麼不直接跑三次 vs_gemini.py：那支每次都會重跑一遍 PP-OCR 管線（慢），
# 而且它的版面是為「我們 vs Gemini」設計的。這次的問題不同——**三組 Gemini
# 之間哪個好**，PP-OCR 只是背景常數。
#
# 而且要重測 2026-08-19 那個關鍵發現：Gemini 多判的添加物裡有一部分**憑空捏造**
# （59/122 在 GT 原文與我們的 OCR 文字裡都查無此物，c30 一案就佔 23 個）。
# 成本降了但捏造變多是不能接受的交換，所以捏造率要跟成本一起看。
#
# 用法：
#   python ab_gemini.py pred_base pred_struct pred_struct_t01
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

TEXT_FIELDS = ['name', 'brand', 'manufacturer', 'allergy_warning']


def pred(run, cid):
    p = os.path.join(S.EVAL_ROOT, run, f'{cid}.json')
    if not os.path.exists(p):
        return None
    return (json.load(open(p, encoding='utf-8')) or {}).get('prediction')


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r'-?\d+(?:\.\d+)?', str(v or ''))
    return float(m.group()) if m else None


def samples(run):
    """該次執行的逐次樣本，用來報成本與延遲。"""
    d = 'results' if run == 'predictions' else run + '_results'
    p = os.path.join(S.EVAL_ROOT, d, 'samples.jsonl')
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]


def ocr_text(cid):
    """我們自己 OCR 讀到的整頁文字，當作「這東西真的印在包裝上」的第二個證據來源。"""
    try:
        rec = N.load(cid, E.BOXES)
        return N.norm(N.full_text(rec) or '')
    except Exception:
        return ''


def main():
    runs = sys.argv[1:]
    if not runs:
        print('用法：python ab_gemini.py <預測資料夾1> <預測資料夾2> ...')
        return

    adds, generic = SM.load_additives()
    cases = []
    for cid, cat in E._cases():
        gt = S.load_gt(cid, cat)
        if not gt or not gt.get('is_food_label', True):
            continue
        if not gt.get('ingredients_list'):
            continue
        ps = {r: pred(r, cid) for r in runs}
        if any(v is None for v in ps.values()):
            continue
        cases.append((cid, gt, ps))
    print('共同案例 %d 案（%s）\n' % (len(cases), '／'.join(runs)))

    # ── 成本與延遲 ────────────────────────────────────────────────────────
    print('── 每次呼叫成本（tokens_prompt 是固定部分，可直接看出任務描述的開銷）')
    print('%-20s%12s%12s%12s%10s' % ('', 'prompt', 'output', 'total', 'p50 ms'))
    for r in runs:
        rows = samples(r)
        if not rows:
            print('%-20s%12s' % (r, '(無樣本)'))
            continue
        def med(k):
            xs = sorted(x[k] for x in rows if x.get(k) is not None)
            return xs[len(xs) // 2] if xs else 0
        print('%-20s%12d%12d%12d%10d'
              % (r, med('tokens_prompt'), med('tokens_output'),
                 med('tokens_total'), med('ms')))

    # ── 清單層 ────────────────────────────────────────────────────────────
    print('\n── 清單層（一項成分為單位，一對一配對，容 len//4 字）')
    print('%-20s%8s%8s%8s%9s%9s%9s' % ('', '命中', 'GT 項', '抽出', '召回', '精確', 'F1'))
    for r in runs:
        h = g = p = 0
        for cid, gt, ps in cases:
            a, b, c, _, _ = BI.score_one(ps[r].get('ingredients_list') or [],
                                         gt['ingredients_list'])
            h += a; g += b; p += c
        _f1(r, h, g, p)

    # ── 添加物層 ＋ 捏造率 ────────────────────────────────────────────────
    print('\n── 添加物層（下游驗收層）＋ 多判的來源分析')
    print('%-20s%8s%8s%8s%9s%9s%9s%10s%10s'
          % ('', '命中', '真值', '判定', '召回', '精確', 'F1', '多判', '查無此物'))
    texts = {cid: ocr_text(cid) for cid, _, _ in cases}
    worst = {}
    for r in runs:
        h = g = p = extra = fab = 0
        per_case = []
        for cid, gt, ps in cases:
            truth = SM.additives_of(gt['ingredients_list'], adds, generic)
            got = SM.additives_of(ps[r].get('ingredients_list') or [], adds, generic)
            h += len(truth & got); g += len(truth); p += len(got)
            ex = got - truth
            extra += len(ex)
            # 佐證來源：GT 原文，或我們自己 OCR 讀到的文字。兩邊都查不到 → 捏造。
            corp = S.normalize(gt.get('ingredients_raw') or '', fold_variants=True) \
                + ' ' + texts[cid]
            n = sum(1 for a in ex
                    if S.normalize(a, fold_variants=True) not in corp)
            fab += n
            per_case.append((n, cid))
        worst[r] = sorted(per_case, reverse=True)[:3]
        _f1(r, h, g, p, extra, fab)
    print('  捏造最多的案子：')
    for r in runs:
        print('    %-18s%s' % (r, '  '.join('%s %d' % (c, n)
                                            for n, c in worst[r] if n)))

    # ── 營養層 ────────────────────────────────────────────────────────────
    print('\n── 營養層（一格＝欄位×欄；硬填＝GT 為 null 卻給值）')
    print('%-20s%8s%8s%10s%10s' % ('', '正確', '應有', '正確率', '硬填'))
    for r in runs:
        o = c = sp = 0
        for cid, gt, ps in cases:
            for scope in ('nutrition_per_serving', 'nutrition'):
                want = gt.get(scope) or {}
                got = ps[r].get(scope) or {}
                for k in V.NUTRITION_FIELDS:
                    w, x = num(want.get(k)), num(got.get(k))
                    if w is None:
                        sp += x is not None
                    else:
                        c += 1
                        o += x is not None and N.close(x, w)
        print('%-20s%8d%8d%9.1f%%%10d' % (r, o, c, 100.0 * o / max(c, 1), sp))

    # ── 份量與文字欄 ──────────────────────────────────────────────────────
    print('\n── 份量欄與文字欄（完全相同才算對）')
    hdr = ['serving_size', 'servings_per_container'] + TEXT_FIELDS
    print('%-20s' % '' + ''.join('%14s' % f[:13] for f in hdr))
    for r in runs:
        line = ['%-20s' % r]
        for f in hdr:
            ok = 0
            for cid, gt, ps in cases:
                if f in ('serving_size', 'servings_per_container'):
                    a, b = num(ps[r].get(f)), num(gt.get(f))
                    ok += a is not None and b is not None and abs(a - b) < 0.5
                else:
                    if not gt.get(f):
                        continue
                    ok += S.normalize(ps[r].get(f) or '', fold_variants=True) \
                        == S.normalize(gt.get(f), fold_variants=True)
            line.append('%14d' % ok)
        print(''.join(line))
    n = {f: sum(1 for _, gt, _ in cases if gt.get(f)) for f in hdr}
    print('%-20s' % 'GT 有值' + ''.join('%14d' % n[f] for f in hdr))


def _f1(tag, h, g, p, *extra):
    r = h / g if g else 0
    pr = h / p if p else 0
    f1 = 2 * r * pr / (r + pr) if r + pr else 0
    print('%-20s%8d%8d%8d%8.1f%%%8.1f%%%8.1f%%' % (tag, h, g, p, r * 100, pr * 100, f1 * 100)
          + ''.join('%10d' % x for x in extra))


if __name__ == '__main__':
    main()
