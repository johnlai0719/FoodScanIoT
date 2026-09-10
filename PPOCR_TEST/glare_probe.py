#!/usr/bin/env python3
# 反光偵測的可行性探測：有沒有任何算得出來的量值，能在配對照片裡把高反光那張分出來。
#
# 為何是這個形狀：`image_metrics.py` 的檔頭早就寫過「clipped 原意是偵測反光，
# 但目前不可用於此——食品標示底色多為白色，乾淨的白底標示與反光斑分不開，
# **待日後能框出標示區域再談**」。那個前提現在成立了：PP-OCR 的 det 框就是標示
# 區域。所以量值一律**只在文字框內**計算，不看整張圖。
#
# 為何用配對照片：32 組同一件商品、同一次、同一位置，只變動反光。商品之間的
# 差異（材質、底色、字級）在相減時抵消，這是能拿到的最乾淨標籤。
#
# ⚠ 這支**只回答「分不分得開」，不訂門檻**。門檻要從需求來（使用者願意被要求
# 重拍幾次），不能從這 32 對量到的分佈反推——那是用已蒐集的資料定驗收標準。
#
# ⚠ 基準線是 rec 的信心值。PP-OCR 本來就會吐逐行信心，不用另外做；任何自製的
# 反光量值要先證明它贏過「信心低就請使用者重拍」，否則就是多一個模組換不到東西。
#
# 用法：
#   python glare_probe.py                    # 跑全部 32 組
#   python glare_probe.py --limit=6          # 先試幾組
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVAL = os.path.join(HERE, '..', '測試', '量化測試')
EVAL = os.path.abspath(EVAL)
CLEAN = os.path.join(EVAL, '_上傳區')                       # 低反光參考影像
DIRTY = os.path.join(EVAL, 'images_pair_dirty', 'images')   # 高反光影像
OUT = os.path.join(HERE, 'out', 'glare_probe.json')

PRESET = {                       # 與 v6_best 對齊，差異才歸因得了
    'init': {'lang': 'chinese_cht', 'ocr_version': 'PP-OCRv6',
             'use_doc_orientation_classify': False, 'use_doc_unwarping': False,
             'use_textline_orientation': True},
    'predict': {'text_det_limit_side_len': 2048, 'text_det_limit_type': 'max',
                'text_det_box_thresh': 0.4},
}


def pairs():
    """[(case_id, 檔名, 乾淨路徑, 反光路徑), …]，兩臂同名才算一組。"""
    out = []
    for cat in sorted(os.listdir(DIRTY)):
        dcat = os.path.join(DIRTY, cat)
        if not os.path.isdir(dcat):
            continue
        for cid in sorted(os.listdir(dcat)):
            dd, cd = os.path.join(dcat, cid), os.path.join(CLEAN, cat, cid)
            if not os.path.isdir(dd) or not os.path.isdir(cd):
                continue
            for fn in sorted(os.listdir(dd)):
                if fn.lower().endswith('.jpg') and os.path.exists(os.path.join(cd, fn)):
                    out.append((cid, fn, os.path.join(cd, fn), os.path.join(dd, fn)))
    return out


def box_stats(path, res):
    """只在文字框內算像素量值。整張圖算不出東西，見檔頭。"""
    im = Image.open(path).convert('L')
    a = np.asarray(im, dtype=np.uint8)
    H, W = a.shape
    clipped, stds, seps, areas = [], [], [], []
    for poly in res['polys']:
        p = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
        x0, y0 = np.clip(p.min(0), 0, [W - 1, H - 1]).astype(int)
        x1, y1 = np.clip(p.max(0), 0, [W - 1, H - 1]).astype(int)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        c = a[y0:y1, x0:x1]
        n = c.size
        areas.append(n)
        clipped.append(float((c >= 250).sum()) / n)
        stds.append(float(c.std()))
        # Otsu 的類間變異除以總變異＝前景背景分得多開。反光把筆畫吃掉時會塌。
        hist = np.bincount(c.ravel(), minlength=256).astype(np.float64)
        w = hist.cumsum() / n
        mu = (hist * np.arange(256)).cumsum() / n
        mt = mu[-1]
        with np.errstate(divide='ignore', invalid='ignore'):
            between = (mt * w - mu) ** 2 / (w * (1 - w))
        v = float(c.var())
        seps.append(float(np.nanmax(between) / v) if v > 0 else 0.0)
    if not areas:
        return dict(clipped=None, std=None, sep=None)
    wgt = np.asarray(areas, dtype=np.float64)
    wgt /= wgt.sum()
    return dict(clipped=float(np.dot(wgt, clipped)),
                std=float(np.dot(wgt, stds)),
                sep=float(np.dot(wgt, seps)))


def ocr_stats(ocr, path):
    r = ocr.predict(path, **PRESET['predict'])
    d = r[0] if isinstance(r, list) else r
    d = getattr(d, 'json', d)
    if isinstance(d, dict) and 'res' in d:
        d = d['res']
    scores = list(d.get('rec_scores') or [])
    polys = d.get('rec_polys')
    if polys is None:
        polys = d.get('dt_polys') or []
    polys = [np.asarray(p).tolist() for p in polys]
    return {
        'n_box': len(polys),
        'conf_med': float(np.median(scores)) if scores else None,
        'conf_lo': (float(np.mean([s < 0.9 for s in scores])) if scores else None),
        'n_char': sum(len(t) for t in (d.get('rec_texts') or [])),
        'polys': polys,
    }


METRICS = [
    ('n_box', '偵測到的框數', 'down'),      # 反光應使框數變少
    ('n_char', '讀到的字數', 'down'),
    ('conf_med', 'rec 信心中位數', 'down'),
    ('conf_lo', '信心<0.9 的行比例', 'up'),
    ('clipped', '框內高光比例', 'up'),
    ('std', '框內灰階標準差', 'down'),
    ('sep', '框內前景背景分離度', 'down'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--device', default='gpu')
    a = ap.parse_args()

    ps = pairs()
    if a.limit:
        ps = ps[:a.limit]
    print(f'配對 {len(ps)} 張（{len({p[0] for p in ps})} 個商品）\n')

    from paddleocr import PaddleOCR
    ocr = PaddleOCR(device=a.device, **PRESET['init'])

    rows = []
    for i, (cid, fn, cp, dp) in enumerate(ps, 1):
        rec = {'case_id': cid, 'file': fn}
        for arm, path in (('clean', cp), ('dirty', dp)):
            s = ocr_stats(ocr, path)
            b = box_stats(path, s)
            s.pop('polys')
            rec[arm] = {**s, **b}
        rows.append(rec)
        print(f'[{i}/{len(ps)}] {cid}/{fn}  框 {rec["clean"]["n_box"]}→'
              f'{rec["dirty"]["n_box"]}  信心 {rec["clean"]["conf_med"]:.3f}→'
              f'{rec["dirty"]["conf_med"]:.3f}')

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rows, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n寫出 →', OUT)
    report(rows)


def report(rows):
    print('\n' + '=' * 78)
    print('每個量值：反光版 − 乾淨版 的差，以及方向是否符合預期')
    print('=' * 78)
    print('%-20s %10s %10s %8s %10s' % ('量值', '乾淨中位', '反光中位', '方向對',
                                        '配對 CI'))
    rng = np.random.default_rng(0)
    for key, zh, want in METRICS:
        c = np.array([r['clean'][key] for r in rows if r['clean'][key] is not None
                      and r['dirty'][key] is not None], dtype=float)
        d = np.array([r['dirty'][key] for r in rows if r['clean'][key] is not None
                      and r['dirty'][key] is not None], dtype=float)
        if len(c) < 5:
            continue
        diff = d - c
        n_right = int((diff < 0).sum() if want == 'down' else (diff > 0).sum())
        # 配對 bootstrap：重抽「對」，CI 跨 0 就說量不出差異
        bs = np.array([rng.choice(diff, len(diff), replace=True).mean()
                       for _ in range(4000)])
        lo, hi = np.percentile(bs, [2.5, 97.5])
        star = '' if lo <= 0 <= hi else '  ✔顯著'
        print('%-20s %10.3f %10.3f %5d/%d %6.3f~%.3f%s'
              % (zh, np.median(c), np.median(d), n_right, len(diff), lo, hi, star))
    print('\n「方向對」＝ 該張反光版確實往預期方向動的組數。'
          '\n配對 CI 跨 0 → 量不出差異。')


if __name__ == '__main__':
    main()
