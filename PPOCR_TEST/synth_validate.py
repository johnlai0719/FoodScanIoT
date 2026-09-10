#!/usr/bin/env python3
# 用「未微調的 PP-OCRv5」去讀合成資料，檢查這批資料的難度是否落在有用的區間。
#
# 為什麼需要這支：合成資料最常見的失敗不是「做不出來」，而是**做得太乾淨**。
# 第一版渲染的 300 張，未微調模型只錯 13/5576 字（CER 0.23%）——模型本來就會，
# 拿去 fine-tune 學不到東西，卻要花數小時訓練才發現。這支把那個檢查提前到幾十秒。
#
# 判讀標準：
#   CER < 1%   太簡單。模型已經會了，訓練無效，要加強退化（優先加 lowres）
#   CER 3-12%  合理。有東西可學，也還沒糊到失去監督訊號
#   CER > 25%  太難。字都糊爛了，label 與影像的對應關係太弱，會教壞模型
#
# 另外會列出形近字誤判——那份清單要與真實照片上的誤判**重疊**，
# 合成資料才算是有效的代理。只有難度對、錯的地方不對，一樣沒用。
# 真實照片上的對照組見 `score_ocr.py --preset=v5_hires` 的「形近字誤判」表。
#
# 用法：
#   python synth_validate.py                     # 抽 300 張
#   python synth_validate.py --n=1000 --top=25
import argparse
import os
import sys
import random
import unicodedata
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(
    os.path.join(HERE, '..', 'FoodScanData', 'train_rec', 'synth'))

try:
    from opencc import OpenCC
    _T2S = OpenCC('t2s').convert
except Exception:
    _T2S = None


def norm(s, fold):
    s = unicodedata.normalize('NFKC', s or '')
    if fold and _T2S:
        s = _T2S(s)
    return ''.join(c for c in s if c.isalnum())


def lev(a, b):
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=DEFAULT_ROOT)
    ap.add_argument('--n', type=int, default=300)
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--model', default='PP-OCRv5_server_rec')
    ap.add_argument('--device', default='gpu')
    a = ap.parse_args()

    lp = os.path.join(a.root, 'label.txt')
    if not os.path.exists(lp):
        sys.exit(f'找不到 {lp}，先跑 synth_render.py')
    rows = [l.rstrip('\n').split('\t', 1)
            for l in open(lp, encoding='utf-8') if '\t' in l]
    random.Random(a.seed).shuffle(rows)
    rows = rows[:a.n]

    from paddleocr import TextRecognition
    rec = TextRecognition(model_name=a.model, device=a.device)
    out = rec.predict([os.path.join(a.root, p) for p, _ in rows], batch_size=16)

    err = errv = tot = 0
    subs = Counter()
    worst = []
    for (p, gt), res in zip(rows, out):
        pred = (res.json['res'] if hasattr(res, 'json') else res).get('rec_text', '')
        a_, b_ = norm(gt, True), norm(pred, True)
        d = lev(a_, b_)
        err += d
        tot += len(a_)
        errv += lev(norm(gt, False), norm(pred, False))
        if d:
            worst.append((d, gt[:40], pred[:40]))
        if len(a_) == len(b_):
            for x, y in zip(a_, b_):
                if x != y:
                    subs[(x, y)] += 1

    cer = err / tot * 100 if tot else 0
    # 崩壞樣本：單筆錯超過六成的字。這種圖已經失去監督訊號，是雜訊不是難題。
    # 只看整體 CER 會漏掉它——「大多數全對 ＋ 少數全爛」和「普遍偏難」的
    # 平均值可以一樣，但前者該修渲染、後者可以直接訓練。
    broken = sum(1 for d, g, _ in worst if d >= 0.6 * max(1, len(norm(g, True))))
    print(f'\n=== {len(rows)} 張合成圖 / 未微調 {a.model} ===')
    print(f'GT 總字數 {tot}｜錯字 {err}｜CER {cer:.2f}%')
    print(f'含繁簡差異的錯字 {errv}｜其中繁簡貢獻 {errv - err}')
    print(f'崩壞樣本（單筆錯 ≥60%）：{broken} / {len(rows)}'
          f'（{broken / max(1, len(rows)) * 100:.1f}%）')
    verdict = ('太簡單——模型本來就會，加強退化（優先提高 lowres 機率）' if cer < 1
               else '太難——label 與影像對應太弱，會教壞模型，降低退化強度' if cer > 25
               else '難度合理，可以拿去訓練')
    if broken > len(rows) * 0.08:
        verdict += '；但崩壞樣本偏多，建議提高 deg_lowres 的高度下限'
    print(f'判讀：{verdict}')

    print(f'\n形近字誤判 Top {a.top}（要與真實照片的誤判清單重疊才算有效代理）：')
    for (x, y), c in subs.most_common(a.top):
        print(f'   {x} → {y}   {c}')

    worst.sort(reverse=True)
    print('\n錯最多的 5 筆：')
    for d, g, p in worst[:5]:
        print(f'   錯{d}  GT: {g}')
        print(f'         預測: {p}')


if __name__ == '__main__':
    main()
