#!/usr/bin/env python3
"""本地 vlcrop 管線 vs Gemini 兩種設定的**逐欄位**對照。

為什麼要有這一支：既有的比較散在各處且各用各的尺——添加物層在 `boot_compare`、
營養格在 `bench_parse`（嚴格容差），而 Gemini 的數字出自官方 `score_eval`
（寬鬆容差）。並列時曾把 vlcrop 低估 61 格（2026-09-09 更正）。
這裡對三方**用同一套定義**重算一次。

判定定義（三方相同）：
  文字欄   完全相同，或正規化後相似度 ≥0.85
  營養格   官方容差：絕對誤差 ≤0.5 或 相對誤差 ≤5%
  份量欄   同上
  成分     三個分母不同的指標並列，**不可互推**：
             添加物層   正解項配到添加物資料庫的那些（集合，521 個）—— 驗收層
             成分項     正解 ingredients_list 原樣（2888 項），一對一配對，
                        巢狀複方算一項
             成分攤平   攤平成最小成分名稱的**去重集合**（4533 項）——
                        `score_eval.flatten_items`，用來排除「拆解粒度」的差異
                        （便當／漢堡類若不攤平，F1 會掉到 0）
  編造     該欄的值在該案的 OCR 全文裡查無此字（§0 的安全閥）
"""
import collections
import difflib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import sim_match as SM           # noqa: E402
import bench_ingredients as BI   # noqa: E402
import nutrition_solver as V     # noqa: E402

# 成分攤平層的權威實作在評估集那邊（與 score_eval.py 的報告數字同源）。
# 不在這裡重寫一份——同一份知識放兩個地方遲早分岔。
sys.path.insert(0, S.EVAL_ROOT)
import score_eval as SE           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT_FIELDS = ['name', 'brand', 'manufacturer', 'allergy_warning']
SERV = ['serving_size', 'servings_per_container']
METH = [('vlcrop', 'vlcrop ＋ HunyuanOCR'),
        ('predictions_v4_struct', 'Gemini 結構化'),
        ('predictions_v4', 'Gemini 線上現行')]


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r'-?\d+(?:\.\d+)?', str(v))
    return float(m.group(0)) if m else None


def close(g, p):
    if g is None or p is None:
        return False
    return abs(g - p) <= 0.5 or (g != 0 and abs(g - p) / abs(g) <= 0.05)


def sim(a, b):
    a, b = S.normalize(a or '', True), S.normalize(b or '', True)
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def ocr_text(cid):
    """佐證的基準：低成本 OCR 與裁切辨識的全部文字。"""
    out = []
    for pre in ('vlcrop_hy_v4', 'v6_best'):
        p = os.path.join(HERE, 'out', pre, cid + '.json')
        if not os.path.exists(p):
            continue
        j = json.load(open(p, encoding='utf-8'))
        for im in j.get('images') or []:
            out += [l.get('text', '') for l in (im.get('lines') or [])]
    return S.normalize('\n'.join(out), True)


def load_pred(kind, cid):
    if kind == 'vlcrop':
        p = os.path.join(HERE, 'out', os.environ.get('CMP_JSON') or 'json', cid + '.json')
        return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None
    p = os.path.join(S.EVAL_ROOT, kind, cid + '.json')
    if not os.path.exists(p):
        return None
    return (json.load(open(p, encoding='utf-8')) or {}).get('prediction')


def f1(h, t, m):
    rc = h / t if t else 0
    pr = h / m if m else 0
    return 100 * 2 * rc * pr / (rc + pr) if rc + pr else 0


def main():
    adds, generic = SM.load_additives()
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    R = {k: collections.defaultdict(int) for k, _ in METH}
    gtn = collections.defaultdict(int)
    ncase = collections.defaultdict(int)
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        txt = ocr_text(cid)
        for f in TEXT_FIELDS:
            if (gt.get(f) or '').strip():
                gtn[f] += 1
        for f in SERV:
            if num(gt.get(f)) is not None:
                gtn[f] += 1
        for k in V.NUTRITION_FIELDS:
            for sc in ('nutrition', 'nutrition_per_serving'):
                if (gt.get(sc) or {}).get(k) is not None:
                    gtn['nutrition'] += 1
        gtn['additive'] += len(SM.additives_of(gt.get('ingredients_list') or [],
                                               adds, generic))
        gtn['item'] += len(gt.get('ingredients_list') or [])
        gtn['flat'] += len(SE.flatten_items(gt.get('ingredients_list')))
        for kind, _ in METH:
            pr = load_pred(kind, cid)
            if pr is None:
                continue
            ncase[kind] += 1
            for f in TEXT_FIELDS:
                g = (gt.get(f) or '').strip()
                p = pr.get(f)
                p = (p if isinstance(p, str) else '').strip()
                if p:
                    R[kind][f + '_out'] += 1
                    if S.normalize(p, True) not in txt:
                        R[kind][f + '_fab'] += 1
                if g and p and sim(g, p) >= 0.85:
                    R[kind][f] += 1
            for f in SERV:
                g = num(gt.get(f))
                if g is not None and close(g, num(pr.get(f))):
                    R[kind][f] += 1
            for k in V.NUTRITION_FIELDS:
                for sc in ('nutrition', 'nutrition_per_serving'):
                    g = (gt.get(sc) or {}).get(k)
                    if g is None:
                        continue
                    if close(num(g), num((pr.get(sc) or {}).get(k))):
                        R[kind]['nutrition'] += 1
            t = SM.additives_of(gt.get('ingredients_list') or [], adds, generic)
            m = SM.additives_of(pr.get('ingredients_list') or [], adds, generic)
            R[kind]['a_hit'] += len(t & m)
            R[kind]['a_mine'] += len(m)
            h, _, p2, _, _ = BI.score_one(pr.get('ingredients_list') or [],
                                          gt.get('ingredients_list') or [])
            R[kind]['i_hit'] += h
            R[kind]['i_out'] += p2
            # 成分攤平層：攤成最小成分名稱的去重集合再比
            fg = SE.flatten_items(gt.get('ingredients_list'))
            fp = SE.flatten_items(pr.get('ingredients_list') or [])
            R[kind]['f_hit'] += len(fg & fp)
            R[kind]['f_out'] += len(fp)

    print('逐欄位對照（同一批案例、同一份正解、同一套判定）\n')
    print('%-18s %8s %20s %18s %18s'
          % ('欄位', '正解有值', 'vlcrop＋HunyuanOCR', 'Gemini 結構化', 'Gemini 線上現行'))
    for f, lab in [('name', '品名'), ('manufacturer', '製造商'),
                   ('allergy_warning', '過敏原警語'), ('brand', '品牌'),
                   ('serving_size', '每份量'),
                   ('servings_per_container', '本包裝含份數')]:
        cells = ['%d (%.0f%%)' % (R[k][f], 100 * R[k][f] / max(1, gtn[f]))
                 for k, _ in METH]
        print('%-18s %8d %20s %18s %18s' % (lab, gtn[f], *cells))
    print('%-18s %8d %20s %18s %18s' % ('營養格', gtn['nutrition'],
          *['%d (%.0f%%)' % (R[k]['nutrition'],
                             100 * R[k]['nutrition'] / max(1, gtn['nutrition']))
            for k, _ in METH]))
    print('%-18s %8d %20s %18s %18s' % ('添加物層 F1', gtn['additive'],
          *['%.1f' % f1(R[k]['a_hit'], gtn['additive'], R[k]['a_mine'])
            for k, _ in METH]))
    print('%-18s %8d %20s %18s %18s' % ('成分項 F1', gtn['item'],
          *['%.1f' % f1(R[k]['i_hit'], gtn['item'], R[k]['i_out'])
            for k, _ in METH]))
    print('%-18s %8d %20s %18s %18s' % ('成分攤平 F1', gtn['flat'],
          *['%.1f' % f1(R[k]['f_hit'], gtn['flat'], R[k]['f_out'])
            for k, _ in METH]))

    print('\n── 安全判準：輸出的值在照片上查無此字（編造案數／有輸出案數）──')
    print('%-18s %20s %18s %18s'
          % ('欄位', 'vlcrop＋HunyuanOCR', 'Gemini 結構化', 'Gemini 線上現行'))
    for f, lab in [('name', '品名'), ('manufacturer', '製造商'),
                   ('allergy_warning', '過敏原警語'), ('brand', '品牌')]:
        print('%-18s %20s %18s %18s' % (lab,
              *['%d / %d' % (R[k][f + '_fab'], R[k][f + '_out']) for k, _ in METH]))
    print('\n評到的案數：', {lab: ncase[k] for k, lab in METH})


if __name__ == '__main__':
    main()
