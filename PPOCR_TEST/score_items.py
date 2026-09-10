#!/usr/bin/env python3
# 直接把 preset 的 lines 當成「最終成分清單」去量添加物層。
#
# 為什麼要另外一支：sim_match_preset.py 會在 preset 的文字上**再跑一次抽取器**，
# 那對 OCR 輸出是對的（OCR 給的是原始文字），但對切分模型的輸出是錯的——
# 模型吐出來的已經是最終清單，再抽一次會把它毀掉（實測 F1 掉到 11.6%）。
#
# 用法：
#   python score_items.py bertsplit
#   python score_items.py bertsplit --detail c38
import argparse, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_ocr as S, bench_ingredients as BI, sim_match as SM

HERE = os.path.dirname(os.path.abspath(__file__))

def items_of(preset, cid):
    p = os.path.join(HERE, 'out', preset, cid + '.json')
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding='utf-8'))
    return [l['text'] for im in d['images'] for l in im.get('lines', [])
            if l.get('text')]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('preset')
    ap.add_argument('--detail', default=None)
    ap.add_argument('--boxes', default='v6_best', help='out/ 底下的 OCR 輸出目錄')
    a = ap.parse_args()
    adds, generic = SM.load_additives()
    # 只是拿來列出「有 OCR 輸出的案例」，本身不影響評分（評的是 preset 的清單）。
    # ⚠ 寫死會靜默只評舊的 57 案——2026-09-07 測試集擴到 177 案時踩過。
    BI.BOXES = a.boxes
    hit = tru = got = 0
    lh = lg = ln_ = 0
    for cid, d, gt in BI.load_cases():
        items = items_of(a.preset, cid)
        if items is None:
            continue
        truth = SM.additives_of(gt['ingredients_list'], adds, generic)
        mine = SM.additives_of(items, adds, generic)
        hit += len(truth & mine); tru += len(truth); got += len(mine)
        h, g, p, _, _ = BI.score_one(items, gt['ingredients_list'])
        lh += h; lg += g; ln_ += p
        if a.detail and cid.startswith(a.detail):
            print(f'{cid}  抽出 {len(items)} 項')
            print('  ' + ' ｜ '.join(items[:20]))
            print(f'  真值添加物 {sorted(truth)}')
            print(f'  判定添加物 {sorted(mine)}')
    for name, h, t, p in [('清單層', lh, lg, ln_), ('添加物層', hit, tru, got)]:
        rc = h / t if t else 0; pr = h / p if p else 0
        f1 = 2 * rc * pr / (rc + pr) if rc + pr else 0
        print(f'{name:<8} 命中 {h:>4}/{t:<4} 判定 {p:<4} '
              f'召回 {rc*100:5.1f}%  精確 {pr*100:5.1f}%  F1 {f1*100:5.1f}%')

if __name__ == '__main__':
    main()
