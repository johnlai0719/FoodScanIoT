#!/usr/bin/env python3
# 評分：拿 predictions/ 對 ground_truth/ 算欄位層級指標，輸出到 results/。
#
# 正解只涵蓋「照片上看得到什麼」——那是人工唯一不可取代的職責。
# 成分是否為添加物**不在此評估**：我國食品添加物採正面表列制，歸屬為查表問題，
# 正解應取自官方清單而非人工判定（詳見 ingredient_types 區塊的說明）。
#
# 除了整體數字，另依 cases.json 的 set_version / category / difficulty 切片
# （2026-08-05 起，因應 07-30 回饋）。切片的用意各不相同：
#   set_version —— core48 是凍結子集，只有它的數字能跨期直接比。整體數字會隨
#     測試集擴充而變動，把兩者混為一談，就會把「案例變難」誤讀成「模型變差」。
#   category —— 報告已知成分 F1 掉分集中在便當與複合調理食品，但那是逐案看出來的；
#     切片後這件事變成一個數字，下次改動有沒有改善該類別，一眼可見。
#   difficulty —— 回饋明確要求評估「difficult images」的表現，需要有標籤才切得出來。
#
# 用法：cd 測試/量化測試 && ../../server/venv/bin/python score_eval.py
import os, json, glob, re, csv
from statistics import mean

HERE = os.path.dirname(os.path.abspath(__file__))
GT = os.path.join(HERE, 'ground_truth')
PRED = os.path.join(HERE, 'predictions')
RES = os.path.join(HERE, 'results')
CASES = os.path.join(HERE, 'cases.json')

NUM_ABS_TOL = 0.5      # 數值容差：絕對誤差 ≤0.5
NUM_REL_TOL = 0.05     # 或相對誤差 ≤5%
NUT_KEYS = ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
            'carbohydrates', 'sugar', 'fiber', 'sodium']


def norm(s):
    """正規化：全形轉半形、去括號內容、去空白標點、轉小寫。降低『同義不同寫』誤判。

    **刻意不共用 module_a.ingredient_parser.normalize_text**（兩者看似重複，實際不同）：
    - 行為本就不同：本函式會轉小寫並直接刪除標點；正式函式不轉小寫，且是把標點
      正規化而非刪除。
    - 更關鍵的是角色不同：本檔是量測工具，比的是「照片上看得到什麼」的萃取正確率。
      若改成依賴正式函式，正式函式一調整，歷次評分結果就會跟著變動，跨版本的
      分數不再可比——量測基準必須獨立於被量測的對象。
    比對邏輯本身的評估（eval_ingredient_parse.py）則相反：它量的就是正式比對流程，
    所以直接 import 正式的 normalize_text 才對。
    """
    if s is None:
        return ''
    s = str(s)
    s = ''.join(chr(ord(c) - 0xfee0) if 0xff01 <= ord(c) <= 0xff5e else c for c in s)
    s = re.sub(r'\(.*?\)|（.*?）', '', s)
    s = re.sub(r'[\s\-—–_、,，。.：:；;]', '', s)
    return s.lower()


def char_sim(a, b):
    """字元層級相似度（Jaccard），供字串欄位參考——完全相符率對小差異過於嚴苛。"""
    a, b = norm(a), norm(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def num_ok(g, p):
    if g is None or p is None:
        return None
    try:
        g, p = float(g), float(p)
    except (TypeError, ValueError):
        return None
    return abs(g - p) <= NUM_ABS_TOL or (g != 0 and abs(g - p) / abs(g) <= NUM_REL_TOL)


# 巢狀括號的分隔符與括號符號（標示上四種括號都出現過）
_BRACKETS = r'（()）\[\]{}［］｛｝'
_SEPS = r'、,，/;；'


def flatten_items(items):
    """
    把成分清單攤平成「最小成分名稱」的集合。

    為何需要（2026-07-27 實測 48 案例）：複合食品的巢狀標示沒有唯一正確的拆法。
    同一項「豬肉餅[重組肉、香草調味粉…]」，人工正解可能整串保留、模型可能拆成組件，
    **兩者都不算錯**，但集合比對會判定完全不符——實測便當／漢堡類因此 F1 掉到 0，
    而該類商品的成分其實多數都被讀到了。

    攤平後只量「有沒有讀到這個成分」，不受拆解粒度影響；與原樣比對併陳，
    可分離「辨識能力」與「拆解慣例差異」兩件事。
    """
    out = set()
    for it in (items or []):
        if not it:
            continue
        # 括號一律換成分隔符，再依所有分隔符切開 → 巢狀自然被攤平
        t = re.sub(f'[{_BRACKETS}]', '、', str(it))
        for piece in re.split(f'[{_SEPS}]', t):
            n = norm(piece)
            if len(n) >= 2:          # 一字詞多為殘留雜訊，捨去
                out.add(n)
    return out


def flat_prf(gt_list, pred_list):
    """攤平後的集合 P/R/F1。"""
    g, p = flatten_items(gt_list), flatten_items(pred_list)
    if not g and not p:
        return None
    tp = len(g & p)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, tp, len(p - g), len(g - p)


def set_prf(gt_list, pred_list):
    g = {norm(x) for x in (gt_list or []) if norm(x)}
    p = {norm(x) for x in (pred_list or []) if norm(x)}
    if not g and not p:
        return None
    tp = len(g & p)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, tp, len(p - g), len(g - p)


def load_case_meta():
    """case_id → {set_version, category, difficulty}。cases.json 不在時切片自動關閉。"""
    if not os.path.exists(CASES):
        return {}
    return {c['case_id']: {'set_version': c.get('set_version') or 'unknown',
                           'category': c.get('category') or 'unknown',
                           'difficulty': c.get('difficulty') or []}
            for c in json.load(open(CASES, encoding='utf-8'))['cases']}


def slice_metrics(recs):
    """一組案例的精簡指標。切片與整體共用同一支，避免兩處算法漂移。

    刻意只放能跨切片比較的少數指標：相關性閘門、成分（原樣與攤平）、營養命中率。
    切片本來就樣本少，指標鋪太細只會得到一堆 n=1 的數字，看似豐富實則不可解讀。
    """
    if not recs:
        return None
    gate = {'TP': 0, 'FP': 0, 'TN': 0, 'FN': 0}
    for r in recs:
        if r['gate']:
            gate[r['gate']] += 1
    tot = sum(gate.values())
    tp, fp, fn = gate['TP'], gate['FP'], gate['FN']
    nut_ok = sum(r['nut_ok'] for r in recs)
    nut_n = sum(r['nut_n'] for r in recs)
    f1s = [r['ing_f1'] for r in recs if r['ing_f1'] is not None]
    flats = [r['ing_flat_f1'] for r in recs if r['ing_flat_f1'] is not None]
    return {
        'n_cases': len(recs),
        'is_food_label': {**gate,
                          'accuracy': round((gate['TP'] + gate['TN']) / tot, 3) if tot else None,
                          'precision': round(tp / (tp + fp), 3) if tp + fp else None,
                          'recall': round(tp / (tp + fn), 3) if tp + fn else None},
        'ingredients_list_f1': round(mean(f1s), 3) if f1s else None,
        'ingredients_flat_f1': round(mean(flats), 3) if flats else None,
        'nutrition_accuracy': round(nut_ok / nut_n, 3) if nut_n else None,
        'nutrition_n_fields': nut_n,
    }


def main():
    os.makedirs(RES, exist_ok=True)
    meta = load_case_meta()
    case_recs = []
    rows, mism = [], []
    gate = {'TP': 0, 'FP': 0, 'TN': 0, 'FN': 0}
    str_fields = {k: [] for k in ('name', 'brand', 'manufacturer', 'allergy_warning')}
    ing_prf, cert_prf, ing_flat = [], [], []
    raw_sim = []
    nut_hits = {k: [0, 0] for k in NUT_KEYS}
    nut_err = {k: [] for k in NUT_KEYS}
    ps_hits = {k: [0, 0] for k in NUT_KEYS}
    serving_hits = [0, 0]
    n_cases = 0

    for gp in sorted(glob.glob(os.path.join(GT, '*.json'))):
        cid = os.path.basename(gp)[:-5]
        pp = os.path.join(PRED, f'{cid}.json')
        if not os.path.exists(pp):
            continue
        gt = json.load(open(gp, encoding='utf-8'))
        pred = json.load(open(pp, encoding='utf-8'))
        pred = pred.get('prediction') or pred
        if pred is None:
            pred = {}
        n_cases += 1
        m = meta.get(cid, {})
        # 命名為 rec_case 而非 rec：底下成分區塊的 `prec, rec, f1` 會佔用 rec
        rec_case = {'case_id': cid,
                    'set_version': m.get('set_version', 'unknown'),
                    'category': m.get('category', 'unknown'),
                    'difficulty': m.get('difficulty', []),
                    'gate': None, 'ing_f1': None, 'ing_flat_f1': None,
                    'nut_ok': 0, 'nut_n': 0}
        case_recs.append(rec_case)

        def add(field, metric, value, detail=''):
            rows.append({'case_id': cid, 'field': field, 'metric': metric,
                         'value': value, 'detail': detail})

        # 相關性閘門
        g_food, p_food = gt.get('is_food_label'), pred.get('is_food_label')
        if g_food is not None and p_food is not None:
            key = ('TP' if (g_food and p_food) else 'TN' if (not g_food and not p_food)
                   else 'FP' if (not g_food and p_food) else 'FN')
            gate[key] += 1
            rec_case['gate'] = key
            add('is_food_label', 'correct', int(g_food == p_food))

        # 字串欄位
        for k in str_fields:
            g = gt.get(k)
            if not g:
                continue
            p = pred.get(k)
            exact = int(norm(g) == norm(p))
            sim = char_sim(g, p)
            str_fields[k].append((exact, sim))
            add(k, 'exact', exact, f'sim={sim:.2f}')
            if not exact:
                mism.append({'case_id': cid, 'field': k, 'gt': g, 'pred': p})

        # 成分清單（集合 P/R/F1）
        r = set_prf(gt.get('ingredients_list'), pred.get('ingredients_list'))
        if r:
            prec, rec, f1, tp, fp, fn = r
            ing_prf.append((prec, rec, f1))
            rec_case['ing_f1'] = f1
            add('ingredients_list', 'f1', round(f1, 3), f'tp={tp} fp={fp} fn={fn}')
            if fp or fn:
                mism.append({'case_id': cid, 'field': 'ingredients_list',
                             'gt': gt.get('ingredients_list'), 'pred': pred.get('ingredients_list')})

        # 攤平後比對（只量「有沒有讀到」，不受拆解粒度影響）
        rf = flat_prf(gt.get('ingredients_list'), pred.get('ingredients_list'))
        if rf:
            ing_flat.append((rf[0], rf[1], rf[2]))
            rec_case['ing_flat_f1'] = rf[2]
            add('ingredients_flat', 'f1', round(rf[2], 3),
                f'tp={rf[3]} fp={rf[4]} fn={rf[5]}')

        # 成分原文（逐字照抄，用相似度看，不要求完全相符）
        if gt.get('ingredients_raw'):
            sim = char_sim(gt['ingredients_raw'], pred.get('ingredients_raw'))
            raw_sim.append(sim)
            add('ingredients_raw', 'char_sim', round(sim, 3))

        # ingredient_types —— 2026-07-26 起不予評估。
        #
        # 一、正解不該由人工判定：我國食品添加物採正面表列制，列於《食品添加物使用
        #     範圍及限量暨規格標準》者方為合法添加物，歸屬是查表問題。人工再標一次
        #     只是多一層會出錯的環節——實測 20 案例即發現 4 項標注錯誤。
        # 二、原正解採「Gemini 起草＋人工校正」，與受測對象同源，有循環評估之虞。
        # 三、改以官方清單為正解後，此指標在真正需要它的地方無法評分：官方清單「有」
        #     的項目系統查表即得、不需模型判斷；官方清單「沒有」的項目（實測佔 51%）
        #     模型判斷有價值，但不存在官方答案可比對。能評之處多餘，重要之處評不了。
        #
        # 成分歸屬之評估改於 A2「標示成分名稱解析至官方品項之正確率」進行。

        # 營養（每 100g）
        gnut = gt.get('nutrition') or {}
        pnut = pred.get('nutrition') or {}
        for k in NUT_KEYS:
            ok = num_ok(gnut.get(k), pnut.get(k))
            if ok is None:
                continue
            nut_hits[k][1] += 1
            nut_hits[k][0] += int(ok)
            rec_case['nut_n'] += 1
            rec_case['nut_ok'] += int(ok)
            nut_err[k].append(abs(float(gnut[k]) - float(pnut[k])))
            add(f'nutrition.{k}', 'within_tol', int(ok))
            if not ok:
                mism.append({'case_id': cid, 'field': f'nutrition.{k}',
                             'gt': gnut.get(k), 'pred': pnut.get(k)})

        # 營養（每份）
        gps = gt.get('nutrition_per_serving') or {}
        pps = pred.get('nutrition_per_serving') or {}
        for k in NUT_KEYS:
            ok = num_ok(gps.get(k), pps.get(k))
            if ok is None:
                continue
            ps_hits[k][1] += 1
            ps_hits[k][0] += int(ok)
            add(f'per_serving.{k}', 'within_tol', int(ok))
            if not ok:
                mism.append({'case_id': cid, 'field': f'per_serving.{k}',
                             'gt': gps.get(k), 'pred': pps.get(k)})

        # 份量
        for k in ('serving_size', 'servings_per_container'):
            ok = num_ok(gt.get(k), pred.get(k))
            if ok is None:
                continue
            serving_hits[1] += 1
            serving_hits[0] += int(ok)
            add(k, 'within_tol', int(ok))
            if not ok:
                mism.append({'case_id': cid, 'field': k, 'gt': gt.get(k), 'pred': pred.get(k)})

        # 標章
        r = set_prf(gt.get('certification_marks'), pred.get('certification_marks'))
        if r:
            cert_prf.append((r[0], r[1], r[2]))
            add('certification_marks', 'f1', round(r[2], 3))

    tot = gate['TP'] + gate['FP'] + gate['TN'] + gate['FN']
    summary = {
        'n_cases': n_cases,
        'is_food_label': {**gate,
                          'accuracy': round((gate['TP'] + gate['TN']) / tot, 3) if tot else None,
                          'precision': round(gate['TP'] / (gate['TP'] + gate['FP']), 3) if gate['TP'] + gate['FP'] else None,
                          'recall': round(gate['TP'] / (gate['TP'] + gate['FN']), 3) if gate['TP'] + gate['FN'] else None},
        **{k: {'exact_rate': round(mean([x[0] for x in v]), 3),
               'char_sim': round(mean([x[1] for x in v]), 3), 'n': len(v)}
           for k, v in str_fields.items() if v},
        'ingredients_list': {'precision': round(mean([x[0] for x in ing_prf]), 3),
                             'recall': round(mean([x[1] for x in ing_prf]), 3),
                             'f1': round(mean([x[2] for x in ing_prf]), 3),
                             'n_cases': len(ing_prf)} if ing_prf else None,
        'ingredients_flat': {'precision': round(mean([x[0] for x in ing_flat]), 3),
                             'recall': round(mean([x[1] for x in ing_flat]), 3),
                             'f1': round(mean([x[2] for x in ing_flat]), 3),
                             'n_cases': len(ing_flat),
                             '_說明': '巢狀括號攤平至最小成分名稱後比對，排除拆解粒度差異'} if ing_flat else None,
        'ingredients_raw': {'char_sim': round(mean(raw_sim), 3), 'n': len(raw_sim)} if raw_sim else None,
        'ingredient_types': {'accuracy': None, 'n': 0, 'status': '不予評估',
                             'reason': ('正解不應由人工判定：食品添加物採正面表列制，歸屬為查表問題；'
                                        '原正解與受測模型同源有循環評估之虞。改於 A2 以官方清單為正解評估。')},
        'nutrition': {k: {'accuracy_within_tol': round(nut_hits[k][0] / nut_hits[k][1], 3) if nut_hits[k][1] else None,
                          'MAE': round(mean(nut_err[k]), 3) if nut_err[k] else None,
                          'n': nut_hits[k][1]} for k in NUT_KEYS},
        'nutrition_per_serving': {k: {'accuracy_within_tol': round(ps_hits[k][0] / ps_hits[k][1], 3) if ps_hits[k][1] else None,
                                      'n': ps_hits[k][1]} for k in NUT_KEYS},
        'serving_fields': {'accuracy_within_tol': round(serving_hits[0] / serving_hits[1], 3) if serving_hits[1] else None,
                           'n': serving_hits[1]},
        'certification_marks': {'f1': round(mean([x[2] for x in cert_prf]), 3),
                                'n_cases': len(cert_prf)} if cert_prf else None,
        'config': {'NUM_ABS_TOL': NUM_ABS_TOL, 'NUM_REL_TOL': NUM_REL_TOL},
    }

    # ── 切片 ──────────────────────────────────────────────────────────────
    if meta:
        by_ver, by_cat, by_diff = {}, {}, {}
        for r in case_recs:
            by_ver.setdefault(r['set_version'], []).append(r)
            by_cat.setdefault(r['category'], []).append(r)
            for d in r['difficulty']:
                by_diff.setdefault(d, []).append(r)   # 一案可帶多個標籤，故可重複計入

        summary['slices'] = {
            'by_set_version': {k: slice_metrics(v) for k, v in sorted(by_ver.items())},
            'by_category': {k: slice_metrics(v) for k, v in sorted(by_cat.items())},
            'by_difficulty': ({k: slice_metrics(v) for k, v in sorted(by_diff.items())}
                              or {'_說明': '尚無案例標註 difficulty，見 cases.json 的說明'}),
            '_說明': ('跨期比較請只引用 by_set_version.core48 —— 它是凍結子集，'
                      '整體數字會隨測試集擴充而變動。by_difficulty 一案可屬多個標籤，'
                      '各組 n 相加會大於案例總數。'),
        }
    else:
        summary['slices'] = {'_說明': f'找不到 {CASES}，未計算切片'}

    json.dump(summary, open(os.path.join(RES, 'summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    if rows:
        with open(os.path.join(RES, 'per_case.csv'), 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=['case_id', 'field', 'metric', 'value', 'detail'])
            w.writeheader(); w.writerows(rows)
    if mism:
        with open(os.path.join(RES, 'mismatches.csv'), 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=['case_id', 'field', 'gt', 'pred'])
            w.writeheader(); w.writerows(mism)

    print('=== 量化測試彙總 ===')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n明細: {RES}/per_case.csv   對不上: {RES}/mismatches.csv   彙總: {RES}/summary.json")


if __name__ == '__main__':
    main()
