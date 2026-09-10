#!/usr/bin/env python3
"""量「中文拼寫糾錯（CSC）」在這個專案上的**上限**。

**為什麼要先量上限**：研究文件（`研究文件/食品包裝 OCR 最佳化 Pipeline.pdf`）
建議接 MacBERT CSC 修近形字誤判。接一個模型要下載、要驗繁簡、要調詞庫，
而它**能救的東西有一個硬上限**：只有「字被讀成別的字」（substitution）才可能修，
「字根本沒讀到」（deletion）修不了——沒有東西可以改。

所以先把 GT 成分文字對齊到 OCR 全文，把錯誤拆成三類，看 sub 佔多少。
**sub 的比例就是 CSC 的天花板**，而且是樂觀的天花板：它假設每個 sub 都改得對。

用法：
    python bench_csc_ceiling.py                     # 預設兩個 preset 都算
    python bench_csc_ceiling.py --preset=vlcrop_hy
    python bench_csc_ceiling.py --pairs=40          # 多列一些替換對
"""
import argparse
import collections
import glob
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def ocr_text(preset, cid):
    p = os.path.join(HERE, 'out', preset, f'{cid}.json')
    if not os.path.exists(p):
        return None
    d = json.load(io.open(p, encoding='utf-8'))
    return '\n'.join(ln.get('text') or ''
                     for im in d.get('images', []) for ln in im.get('lines', []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preset', default=None)
    ap.add_argument('--pairs', type=int, default=25)
    a = ap.parse_args()
    presets = [a.preset] if a.preset else ['v6_hires__boxth0.4', 'vlcrop_hy']

    gts = {}
    for f in glob.glob(os.path.join(S.EVAL_ROOT, 'ground_truth', '*', '*.json')):
        gts[os.path.basename(f)[:-5]] = json.load(io.open(f, encoding='utf-8'))

    for preset in presets:
        n_sub = n_del = n_ins = n_chars = 0
        pairs = collections.Counter()
        missing = collections.Counter()
        per_case = []
        for cid, g in sorted(gts.items()):
            raw = g.get('ingredients_raw') or ''
            gt = S.normalize(raw, fold_variants=True)
            t = ocr_text(preset, cid)
            if not gt or t is None:
                continue
            dist, trace = S.substring_edit(gt, S.normalize(t, fold_variants=True))
            s = d = i = 0
            for op, x, y in trace:
                if op == 'sub':
                    s += 1; pairs[(x, y)] += 1
                elif op == 'del':
                    d += 1; missing[x] += 1
                elif op == 'ins':
                    i += 1
            n_sub += s; n_del += d; n_ins += i; n_chars += len(gt)
            per_case.append((cid, len(gt), s, d))

        err = n_sub + n_del + n_ins
        print('=' * 68)
        print('preset = %s     %d 案，GT 成分共 %d 字' % (preset, len(per_case), n_chars))
        print('-' * 68)
        print('對齊後的錯誤   合計 %d 字（字元正確率 %.1f%%）'
              % (err, 100 * (1 - err / max(n_chars, 1))))
        print('  替換 sub    %5d  (%.1f%% of 錯誤)   ← **CSC 最多只能碰這一塊**'
              % (n_sub, 100 * n_sub / max(err, 1)))
        print('  漏字 del    %5d  (%.1f%%)          ← 字沒讀到，CSC 修不了'
              % (n_del, 100 * n_del / max(err, 1)))
        print('  多字 ins    %5d  (%.1f%%)' % (n_ins, 100 * n_ins / max(err, 1)))
        print('-' * 68)
        print('若 CSC 把 sub 全部改對（樂觀上限）：字元正確率 %.1f%% → %.1f%%'
              % (100 * (1 - err / max(n_chars, 1)),
                 100 * (1 - (n_del + n_ins) / max(n_chars, 1))))
        print('-' * 68)
        print('最常見的替換對（GT字 → OCR字），前 %d：' % a.pairs)
        line = []
        for (x, y), c in pairs.most_common(a.pairs):
            line.append('%s→%s×%d' % (x, y, c))
        for k in range(0, len(line), 6):
            print('  ' + '  '.join(line[k:k + 6]))
        print('-' * 68)
        print('漏字最多的案例（GT字數 / 替換 / 漏字）：')
        for cid, L, s, d in sorted(per_case, key=lambda r: -r[3])[:6]:
            print('  %-32s %4d  sub %3d  del %3d' % (cid, L, s, d))


if __name__ == '__main__':
    main()
