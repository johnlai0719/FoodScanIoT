#!/usr/bin/env python3
"""比較兩個定位器（PP-OCR vs RapidOCR）對**裁切**的影響。

`region_crop.find_regions()` 吃的是「文字行 ＋ 框」，不在乎誰產的。
所以換定位器要問三件事，而且要分開問——只看端到端分數會混淆歸因：

  1. **找不找得到**  兩個區域各自的命中率（found）
  2. **框在不在同一個地方**  兩邊框的 IoU（這才是「裁切效果」本身）
  3. **下游分數**  用各自的文字跑同一套規則，添加物層與成分項

⚠ 第 3 項會同時受「定位」與「辨識」影響。若 IoU 很高但分數掉了，
   問題在辨識；若 IoU 就低，問題在定位。分開量才歸因得了。

用法：
    python cmp_locator.py                       # v6_best vs rapid_v6
    python cmp_locator.py rapid_cht             # 指定另一個對照組
"""
import collections
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import region_crop as RC         # noqa: E402
import sim_match as SM           # noqa: E402
import bench_ingredients as BI   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('CMP_BASE') or 'v6_best'


def rec(pre, cid):
    p = os.path.join(HERE, 'out', pre, cid + '.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def iou(a, b):
    if not a or not b:
        return None
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else None


def regions_of(r):
    """回傳每張圖的 {ingredients: box, nutrition: box}。"""
    out = []
    for im in (r.get('images') or []):
        lines = [l for l in (im.get('lines') or []) if l.get('box')]
        out.append(RC.find_regions(lines) if lines else
                   {'ingredients': None, 'nutrition': None})
    return out


def main():
    other = sys.argv[1] if len(sys.argv) > 1 else 'rapid_v6'
    adds, generic = SM.load_additives()
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    found = {BASE: collections.Counter(), other: collections.Counter()}
    ious = collections.defaultdict(list)
    txt = {BASE: collections.Counter(), other: collections.Counter()}
    sc = {BASE: collections.Counter(), other: collections.Counter()}
    gtn = collections.Counter()
    n = 0
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        ra, rb = rec(BASE, cid), rec(other, cid)
        if ra is None or rb is None:
            continue
        n += 1
        gl = gt.get('ingredients_list') or []
        t = SM.additives_of(gl, adds, generic)
        gtn['add'] += len(t)
        gtn['item'] += len(gl)
        A, B = regions_of(ra), regions_of(rb)
        for pre, R in ((BASE, A), (other, B)):
            for r in R:
                for k in ('ingredients', 'nutrition'):
                    if r.get(k):
                        found[pre][k] += 1
            found[pre]['img'] += len(R)
        for ra_, rb_ in zip(A, B):
            for k in ('ingredients', 'nutrition'):
                v = iou(ra_.get(k), rb_.get(k))
                if v is not None:
                    ious[k].append(v)
                elif bool(ra_.get(k)) != bool(rb_.get(k)):
                    ious[k + '_只有一邊有'].append(1)
        # 逐行文字量與下游分數
        for pre, r in ((BASE, ra), (other, rb)):
            L = [l.get('text', '') for im in (r.get('images') or [])
                 for l in (im.get('lines') or [])]
            txt[pre]['lines'] += len(L)
            txt[pre]['chars'] += sum(len(x) for x in L)
            items = BI.PARSERS['boxsep'](cid, r, None) or []
            m = SM.additives_of(items, adds, generic)
            sc[pre]['ah'] += len(t & m)
            sc[pre]['am'] += len(m)
            if t and not (t & m):
                sc[pre]['zero'] += 1
            h, _, pn, _, _ = BI.score_one(items, gl)
            sc[pre]['ih'] += h
            sc[pre]['im'] += pn

    def f1(h, tt, m):
        r = h / tt if tt else 0
        p = h / m if m else 0
        return 100 * 2 * r * p / (r + p) if r + p else 0

    print('比較 %d 案：%s（基準） vs %s\n' % (n, BASE, other))
    print('① 定位命中率（每張圖）')
    print('%-14s %10s %12s %12s' % ('', '圖數', '成分區', '營養區'))
    for pre in (BASE, other):
        g = found[pre]
        print('%-14s %10d %11d(%.0f%%) %11d(%.0f%%)'
              % (pre, g['img'], g['ingredients'], 100 * g['ingredients'] / max(1, g['img']),
                 g['nutrition'], 100 * g['nutrition'] / max(1, g['img'])))
    print('\n② 兩邊的框重疊多少（IoU；1.0 ＝ 完全一樣）')
    import statistics
    for k in ('ingredients', 'nutrition'):
        v = ious.get(k) or []
        if not v:
            continue
        v.sort()
        hi = sum(1 for x in v if x >= 0.7)
        print('   %-12s n=%3d  中位 %.3f  ≥0.7 的比例 %.0f%%  最差 %.3f'
              % (k, len(v), statistics.median(v), 100 * hi / len(v), v[0]))
        only = len(ious.get(k + '_只有一邊有') or [])
        if only:
            print('   %-12s 只有一邊框得出來：%d 張' % ('', only))
    print('\n③ 讀到的文字量')
    for pre in (BASE, other):
        print('   %-12s 行數 %5d   字數 %6d' % (pre, txt[pre]['lines'], txt[pre]['chars']))
    print('\n④ 下游（同一套規則，只換文字來源）')
    print('%-14s %12s %12s %10s' % ('', '添加物 F1', '成分項 F1', '空清單'))
    for pre in (BASE, other):
        g = sc[pre]
        print('%-14s %12.1f %12.1f %10d'
              % (pre, f1(g['ah'], gtn['add'], g['am']),
                 f1(g['ih'], gtn['item'], g['im']), g['zero']))
    print('\n（添加物分母 %d、成分項分母 %d）' % (gtn['add'], gtn['item']))


if __name__ == '__main__':
    main()
