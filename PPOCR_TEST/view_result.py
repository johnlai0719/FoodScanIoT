#!/usr/bin/env python3
# 把某個 preset 的 OCR 結果整理成可以在 PPOCRLabel 裡直接檢視的資料夾。
#
# 為什麼需要這支：out/<preset>/*.json 裡有框和文字，但用眼睛看 JSON 沒有意義；
# 而 PPOCRLabel 自己的「自动标注」用的是它內建的爛設定
# （text_det_limit_side_len 未設，沿用預設 960，高解析照片會被縮小），
# 看到的不是我們實際評估的那份輸出。
# 這支把**已經跑好的結果**寫成 PPOCRLabel 的 Label.txt 格式，開資料夾就能看。
#
# 輸出放 scratchpad，不放 FoodScanData——這些是評估集的照片，
# 不可以混進訓練資料。
#
# 用法：
#   python view_result.py --preset=v6_hires__boxth0.4                 # 全部 58 案
#   python view_result.py --preset=v6_hires__boxth0.4 --cases c58 c38
#   python view_result.py --preset=v6_hires__boxth0.4 --worst=10      # 營養漏最多的 10 案
#   python view_result.py --list                                      # 有哪些 preset
import argparse
import json
import os
import shutil
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_ROOT = os.path.join(
    os.environ.get('TEMP', os.path.expanduser('~')), 'claude',
    'D--FoodScanIot', '0343bed9-8171-4525-906a-38e18a4440e4', 'scratchpad')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preset', default='v6_hires__boxth0.4')
    ap.add_argument('--cases', nargs='*', default=None)
    ap.add_argument('--worst', type=int, default=0, help='只取營養漏最多的 N 案')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()

    outdir = os.path.join(HERE, 'out')
    if a.list:
        print('可用的 preset（out/ 底下的目錄）：')
        for d in sorted(os.listdir(outdir)):
            p = os.path.join(outdir, d)
            if os.path.isdir(p):
                print(f'   {d:<32}{len(os.listdir(p))} 案')
        return

    src = os.path.join(outdir, a.preset)
    if not os.path.isdir(src):
        sys.exit(f'找不到 {src}。用 --list 看有哪些。')

    cases = {c['case_id']: c for c in json.load(
        open(os.path.join(S.EVAL_ROOT, 'cases.json'), encoding='utf-8'))['cases']}
    picked = [f[:-5] for f in sorted(os.listdir(src)) if f.endswith('.json')]
    if a.cases:
        picked = [c for c in picked if any(c.startswith(x) for x in a.cases)]
    if a.worst:
        rows = S.score_preset(a.preset)[0]
        rank = sorted(rows, key=lambda r: -len(r['nutri_missed']))
        keep = {r['case_id'] for r in rank[:a.worst]}
        picked = [c for c in picked if c in keep]
    if not picked:
        sys.exit('沒有符合條件的案例。')

    dst = os.path.join(REVIEW_ROOT, f'view_{a.preset}')
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)

    rows, total = [], 0
    for cid in picked:
        d = json.load(open(os.path.join(src, f'{cid}.json'), encoding='utf-8'))
        for i, im in enumerate(d['images']):
            rel = im.get('path')
            if not rel:
                continue
            p = os.path.join(S.EVAL_ROOT, *rel.split('/'))
            if not os.path.exists(p):
                continue
            fn = f'{cid}__{os.path.basename(p)}'
            shutil.copy2(p, os.path.join(dst, fn))
            shapes = [{'transcription': l['text'],
                       'points': [[int(round(x)), int(round(y))] for x, y in l['box']],
                       'difficult': False}
                      for l in im.get('lines', []) if l.get('box')]
            total += len(shapes)
            rows.append(f'{os.path.basename(dst)}/{fn}\t'
                        + json.dumps(shapes, ensure_ascii=False))
    with open(os.path.join(dst, 'Label.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(rows) + '\n')

    print(f'{len(rows)} 張影像、{total} 個框\n→ {dst}\n')
    print('接下來：')
    print('  1. PPOCRLabel --lang ch --gpu True '
          '--det_model_name PP-OCRv5_server_det --rec_model_name PP-OCRv5_server_rec')
    print('  2. 文件 → 打开目录 → 選上面那個路徑')
    print('  3. **不要按「自动标注」**——會用 PPOCRLabel 的預設設定覆蓋掉這份結果')


if __name__ == '__main__':
    main()
