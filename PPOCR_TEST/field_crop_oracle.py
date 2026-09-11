#!/usr/bin/env python3
"""逐欄位裁切的 **oracle 上限**：假設分類完美，每一欄的框會長什麼樣？

為什麼先問這個：把整張標示切成「品名區／成分區／營養區／廠商區／過敏原區」
再分別處理，前提是**每一欄的文字在空間上聚得起來**。若某一欄的行散落在
整張圖上（例如品名印在正面、廠商印在側邊），它的外接矩形就等於整張圖，
那再準的分類器也不會讓裁切變小——這與 `region_crop` 對成分區有效
是兩回事，不能類推。

所以先用**弱標註當成完美分類器**算上限。上限不好看，就不必訓練多類別版本。

弱標註來自 `make_linecls.py`（拿正解各欄位去對每一行 OCR 文字），
不是金標準，但它正是分類器能學到的天花板。

三個指標，逐欄位算：
    框得出來      有幾案該欄至少有一行
    面積比        該欄外接矩形佔全部文字範圍的比例——**這是裁切的價值所在**
    框內含正解    框內的文字有沒有蓋到正解那個欄位的值（文字欄才有意義）

用法：
    python field_crop_oracle.py
    python field_crop_oracle.py --boxes=v6_best
"""
import argparse
import collections
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S      # noqa: E402
import region_crop as RC   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = ['成分', '營養', '過敏原', '品名', '廠商', '保存', '日期', '份量', '認證', '注意']
# 正解裡對應的欄位名（沒有對應的就只看面積，不驗內容）
GT_FIELD = {'品名': 'name', '廠商': 'manufacturer', '過敏原': 'allergy_warning'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--boxes', default='v6_best')
    a = ap.parse_args()

    # 弱標註：case_id → [(text, label), ...]，順序與 make_linecls 一致
    lab = collections.defaultdict(list)
    p = os.path.join(HERE, 'out', 'linecls.jsonl')
    for ln in open(p, encoding='utf-8'):
        r = json.loads(ln)
        lab[r['case_id']].append((r['text'], r['label']))

    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    stat = {k: {'n': 0, 'area': [], 'hit': 0, 'gtn': 0, 'lines': []} for k in LABELS}
    nskip = 0
    ncase = 0
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        rp = os.path.join(HERE, 'out', a.boxes, cid + '.json')
        if not os.path.exists(rp) or cid not in lab:
            nskip += 1
            continue
        rec = json.load(open(rp, encoding='utf-8'))
        flat = [l for im in (rec.get('images') or []) for l in (im.get('lines') or [])]
        kept = [l for l in flat
                if len(S.normalize((l.get('text') or '').strip())) >= 2]
        if len(kept) != len(lab[cid]):
            nskip += 1
            continue
        ncase += 1
        # 逐影像分組（框只在同一張圖裡才有意義）
        it = iter(lab[cid])
        tagged = []
        for l in flat:
            if len(S.normalize((l.get('text') or '').strip())) >= 2:
                tagged.append((l, next(it)[1]))
            else:
                tagged.append((l, None))
        i = 0
        per_img = []
        for im in (rec.get('images') or []):
            n = len(im.get('lines') or [])
            per_img.append(tagged[i:i + n])
            i += n

        for k in LABELS:
            best = None       # 該欄在哪張圖上、框多大
            for grp in per_img:
                lines = [l for l, _ in grp if l.get('box')]
                sel = [l for l, t in grp if t == k and l.get('box')]
                if not sel or not lines:
                    continue
                bs = [RC.bbox(l['box']) for l in sel]
                box = (min(b[0] for b in bs), min(b[1] for b in bs),
                       max(b[2] for b in bs), max(b[3] for b in bs))
                ar = RC.area_frac(box, lines)
                if best is None or (ar or 1) < (best[1] or 1):
                    best = (box, ar, lines, len(sel))
            if best is None:
                continue
            box, ar, lines, nsel = best
            stat[k]['n'] += 1
            stat[k]['lines'].append(nsel)
            if ar:
                stat[k]['area'].append(ar)
            f = GT_FIELD.get(k)
            if f:
                v = (gt.get(f) or '').strip()
                if v:
                    stat[k]['gtn'] += 1
                    txt = S.normalize(RC.text_in(lines, box), fold_variants=True)
                    key = S.normalize(v, fold_variants=True)
                    tol = max(1, len(key) // 4)
                    if key in txt or S.substring_edit(key, txt)[0] <= tol:
                        stat[k]['hit'] += 1

    print('逐欄位裁切的 oracle 上限（弱標註當成完美分類器）　%d 案\n' % ncase)
    if nskip:
        print('⚠ 略過 %d 案（行數對不上或缺檔）\n' % nskip)
    print('%-8s %8s %8s %12s %14s' % ('欄位', '框得出來', '中位行數', '面積比中位',
                                      '框內含正解'))
    for k in LABELS:
        s = stat[k]
        if not s['n']:
            continue
        area = '%.0f%%' % (100 * statistics.median(s['area'])) if s['area'] else '—'
        hit = '%d/%d (%.0f%%)' % (s['hit'], s['gtn'], 100 * s['hit'] / s['gtn']) \
            if s['gtn'] else '—'
        print('%-8s %8d %8.0f %12s %14s'
              % (k, s['n'], statistics.median(s['lines']), area, hit))
    print('\n面積比 ＝ 該欄外接矩形佔「全部文字外接框」的比例。'
          '接近 100% 代表那一欄的行散落在整張圖上，裁切沒有意義。')


if __name__ == '__main__':
    main()
