#!/usr/bin/env python3
"""從逐行機率變成裁切框，有好幾種做法——比較它們。

**這支是為了修一個已知的失敗。** 2026-09-11 用「所有 P≥0.10 的行取外接矩形」
決定成分框，代理指標很漂亮（成分留存 101.1%、面積 33%），端到端卻是負的：
添加物 F1 75.5→73.8、零命中 14→17 案。逐案看是改善 26／退步 25，兩邊都劇烈。

病灶不是分類器不準（它的行層 F1 0.840、召回 84.5%，遠贏手工特徵的 0.734），
**是外接矩形對離群點沒有抵抗力**：一兩行判錯且位置很遠，整個框就跟著跑掉。
`c11_大飯糰椒香鮪魚` 是最清楚的例子——HunyuanOCR 從讀出 11 行 359 字
變成 1 行 15 字。

⚠ 所以這裡**多量一個指標**：`災難案數`＝裁切文字量掉到規則框的一半以下。
   上一輪的「成分留存」是全體加總，那種個案災難會被平均掉看不見——
   代理指標漏掉病灶，就是這麼發生的。

策略：
    bbox      所有選中行的外接矩形（上一輪的做法，當基線）
    trim      去掉上下各 10% 的離群行再取外接矩形
    cluster   依垂直間距分群，只保留「選中行最多」的那一群
    anchor    從最高機率的行出發，上下沿著仍達低門檻的行延伸（連續段）
    union     與規則框取聯集（保守：放棄面積收益，換覆蓋率）

用法：
    python box_strategy.py
    python box_strategy.py --th=0.10
"""
import argparse
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S      # noqa: E402
import region_crop as RC   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _bb(bs):
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))


def s_bbox(lines, ps, th, rule):
    bs = [RC.bbox(l['box']) for l, q in zip(lines, ps) if q >= th]
    return _bb(bs) if bs else None


def s_trim(lines, ps, th, rule):
    """去掉垂直方向最外面的離群行。一兩個誤判就在那裡。"""
    sel = [RC.bbox(l['box']) for l, q in zip(lines, ps) if q >= th]
    if len(sel) < 5:
        return _bb(sel) if sel else None
    sel.sort(key=lambda b: (b[1] + b[3]) / 2)
    k = max(1, int(len(sel) * 0.10))
    return _bb(sel[k:-k])


def s_cluster(lines, ps, th, rule):
    """依垂直間距分群，取選中行最多的那一群。

    間距門檻用「該圖行高中位數 × CLUSTER_K」，與 region_crop 同一個尺度。
    """
    sel = [(RC.bbox(l['box']), q) for l, q in zip(lines, ps) if q >= th]
    if not sel:
        return None
    unit = statistics.median(RC.thickness(l['box']) for l in lines) or 1
    gap = unit * RC.CLUSTER_K
    sel.sort(key=lambda x: x[0][1])
    groups, cur = [], [sel[0]]
    for b, q in sel[1:]:
        if b[1] - cur[-1][0][3] > gap:
            groups.append(cur)
            cur = []
        cur.append((b, q))
    groups.append(cur)
    best = max(groups, key=lambda g: (len(g), sum(q for _, q in g)))
    return _bb([b for b, _ in best])


def s_anchor(lines, ps, th, rule):
    """從最高機率的行出發，上下延伸到機率掉到 th/2 以下為止。

    這是「成分是一個**連續段落**」這個先驗的直接表述——
    比全域外接矩形多用了一個真實的結構假設。
    """
    idx = [i for i, q in enumerate(ps) if q >= th]
    if not idx:
        return None
    order = sorted(range(len(lines)), key=lambda i: RC.bbox(lines[i]['box'])[1])
    pos = {i: r for r, i in enumerate(order)}
    top = max(idx, key=lambda i: ps[i])
    lo = hi = pos[top]
    low = th / 2
    while lo > 0 and ps[order[lo - 1]] >= low:
        lo -= 1
    while hi < len(order) - 1 and ps[order[hi + 1]] >= low:
        hi += 1
    return _bb([RC.bbox(lines[order[r]]['box']) for r in range(lo, hi + 1)])


def s_union(lines, ps, th, rule):
    b = s_bbox(lines, ps, th, rule)
    if b is None:
        return rule
    if rule is None:
        return b
    return (min(b[0], rule[0]), min(b[1], rule[1]),
            max(b[2], rule[2]), max(b[3], rule[3]))


def s_multi(lines, ps, th, rule):
    """**多框**：依垂直間距分群，每一群各自成一個框，全部保留。

    這解掉 bbox 與 cluster 的兩難——bbox 覆蓋好但框太大（群與群之間的
    不相干內容被框進來），cluster 框很小但只留一群、漏掉其餘。
    多框的覆蓋等於 bbox，面積等於各群之和。

    ⚠ 代價是 VLM 要呼叫多次，而且**模型失去跨段的上下文**。
       實驗 12（裁切區沿長邊分條）就是敗在這裡：−9.6 點、退化 11→28 案。
       差別在那次是硬切、會切穿成分行；這裡沿群組間隙切，不切穿任何一行。
       但風險同向，必須端到端驗證，代理指標不算數。
    """
    sel = [RC.bbox(l['box']) for l, q in zip(lines, ps) if q >= th]
    if not sel:
        return None
    unit = statistics.median(RC.thickness(l['box']) for l in lines) or 1
    gap = unit * RC.CLUSTER_K
    sel.sort(key=lambda b: b[1])
    groups, cur = [], [sel[0]]
    for b in sel[1:]:
        if b[1] - max(x[3] for x in cur) > gap:
            groups.append(cur)
            cur = []
        cur.append(b)
    groups.append(cur)
    return [_bb(g) for g in groups]          # ← 回傳 list，呼叫端要能吃


STRATS = [('bbox', s_bbox), ('trim', s_trim), ('cluster', s_cluster),
          ('anchor', s_anchor), ('union', s_union), ('multi', s_multi)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--th', type=float, default=0.10)
    a = ap.parse_args()
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    acc = {n: {'full': 0, 'crop': 0, 'area': [], 'bad': 0, 'found': 0, 'img': 0}
           for n, _ in STRATS}
    acc['規則'] = dict(acc['bbox'])
    acc['規則'] = {'full': 0, 'crop': 0, 'area': [], 'bad': 0, 'found': 0, 'img': 0}
    nskip = 0
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        gl = gt.get('ingredients_list') or []
        if not gl:
            continue
        rp = os.path.join(HERE, 'out', 'v6_best', cid + '.json')
        pp = os.path.join(HERE, 'out', 'linecls_pred', cid + '.json')
        if not (os.path.exists(rp) and os.path.exists(pp)):
            nskip += 1
            continue
        rec = json.load(open(rp, encoding='utf-8'))
        pr = json.load(open(pp, encoding='utf-8'))
        flat = [l for im in (rec.get('images') or []) for l in (im.get('lines') or [])]
        kept = [l for l in flat
                if len(S.normalize((l.get('text') or '').strip())) >= 2]
        if len(kept) != len(pr):
            nskip += 1
            continue
        it = iter(pr)
        pmap = [next(it)['p'] if len(S.normalize((l.get('text') or '').strip())) >= 2
                else -1.0 for l in flat]
        i = 0
        per_img = []
        for im in (rec.get('images') or []):
            n = len(im.get('lines') or [])
            per_img.append((im.get('lines') or [], pmap[i:i + n]))
            i += n

        texts = {n: [] for n, _ in STRATS}
        texts['規則'] = []
        full = []
        rule_chars, new_chars = {}, {n: 0 for n, _ in STRATS}
        for lines0, ps0 in per_img:
            pairs = [(l, q) for l, q in zip(lines0, ps0) if l.get('box')]
            if not pairs:
                continue
            lines = [l for l, _ in pairs]
            ps = [q for _, q in pairs]
            rule = RC.find_regions(lines).get('ingredients')
            full.append('\n'.join(l['text'] for l in lines))
            rt = RC.text_in(lines, rule)
            texts['規則'].append(rt)
            acc['規則']['img'] += 1
            if rule:
                acc['規則']['found'] += 1
                ar = RC.area_frac(rule, lines)
                if ar:
                    acc['規則']['area'].append(ar)
            rule_chars[id(lines)] = len(rt)
            for n, fn in STRATS:
                box = fn(lines, ps, a.th, rule) or rule
                bl = box if isinstance(box, list) else [box]
                t = '\n'.join(RC.text_in(lines, b) for b in bl if b)
                texts[n].append(t)
                acc[n]['img'] += 1
                if box:
                    acc[n]['found'] += 1
                    ars = [RC.area_frac(b, lines) for b in bl if b]
                    ars = [x for x in ars if x]
                    if ars:
                        acc[n]['area'].append(sum(ars))   # 多框算面積總和
                    acc[n]['nbox'] = acc[n].get('nbox', 0) + len([b for b in bl if b])
                new_chars[n] += len(t)
        for n, _ in STRATS:
            pass
        fa = S.normalize('\n'.join(full), fold_variants=True)
        fe, ff, _ = S.ingredient_hits(gl, fa)
        nfull = len(fe) + len(ff)
        hits = {}
        for n in list(texts):
            ca = S.normalize('\n'.join(texts[n]), fold_variants=True)
            ce, cf, _ = S.ingredient_hits(gl, ca)
            hits[n] = len(ce) + len(cf)
            acc[n]['full'] += nfull
            acc[n]['crop'] += hits[n]
        # ⚠ **災難要用成分命中數衡量，不是總字數。**
        #    第一版用字數，把多框「刻意丟掉非成分文字」也記成災難
        #    （multi 118 案 vs bbox 65 案），差點得出相反的結論。
        #    端到端真正掉的是成分，不是字元。
        base = hits.get('規則', 0)
        for n, _ in STRATS:
            if base >= 2 and hits[n] < 0.5 * base:
                acc[n]['bad'] += 1

    if nskip:
        print('⚠ 略過 %d 案\n' % nskip)
    print('機率門檻 %.2f\n' % a.th)
    print('%-10s %10s %10s %8s %12s %8s' % ('策略', '框到的圖', '成分留存', '面積比',
                                            '**災難案數**', '框數'))
    order = ['規則'] + [n for n, _ in STRATS]
    for n in order:
        r = acc[n]
        keep = 100 * r['crop'] / r['full'] if r['full'] else 0
        area = '%.0f%%' % (100 * statistics.mean(r['area'])) if r['area'] else '—'
        bad = '—' if n == '規則' else str(r['bad'])
        print('%-10s %6d/%-4d %9.1f%% %8s %12s %8s'
              % (n, r['found'], r['img'], keep, area, bad,
                 r.get('nbox', r['found'])))
    print('\n災難案數 ＝ 裁切文字量掉到規則框一半以下的案例。'
          '上一輪端到端失敗的就是這種案例（c11：359 字 → 15 字），'
          '而「成分留存」是全體加總，會把它平均掉。')


if __name__ == '__main__':
    main()
