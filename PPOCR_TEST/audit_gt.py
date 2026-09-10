#!/usr/bin/env python3
# 找出 ground_truth 裡本身就錯的營養資料。
#
# 為什麼要先做這件事：拿錯的正解去訓練或評估，結果都沒有意義。這個專案已經
# 踩過兩次——c24 的整份正解原本是 c23 的產品；修完之後 serving_size 仍記 180，
# 由數值反推應為 375。兩次都不是人工逐頁核對發現的，是被自動檢查抓出來的。
#
# 三道獨立檢查，強度由弱到強：
#
#   A. **GT 自身的算術一致性**
#      法規要求同時標「每份」與「每100公克」，關係固定：
#          每份值 = 每100g值 × (份量 / 100)
#      實測這條在 GT 的 430 組欄位對裡成立 422 組（98%），
#      不成立的 8 組全部來自 c24。這道檢查不需要任何 OCR 結果。
#
#   B. **GT 的值在所有 OCR 輸出裡都找不到**
#      弱訊號：可能是 GT 錯，也可能只是照片糊到讀不出來。當佐證用。
#
#   C. **兩個獨立系統都讀到同一個值，而它與 GT 不同**  ← 最強
#      PPOCR 與 Gemini 是完全不同的模型、不同的失敗模式。兩者一致卻與正解
#      相左時，錯的幾乎必然是正解。
#
# 用法：
#   python audit_gt.py                # 全部檢查
#   python audit_gt.py --only=A       # 只跑某一道
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S          # noqa: E402
import nutrition_pipeline as N  # noqa: E402
import table_geometry as G      # noqa: E402
import nutrition_solver as V    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'v6_hires__boxth0.4'


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r'-?\d+(?:\.\d+)?', v)
        return float(m.group()) if m else None
    return None


def load_cases():
    return {c['case_id']: c for c in json.load(
        open(os.path.join(S.EVAL_ROOT, 'cases.json'), encoding='utf-8'))['cases']}


def check_a(cases):
    """GT 自身算術一致性。不需要 OCR。"""
    out = []
    for cid, c in sorted(cases.items()):
        gt = S.load_gt(cid, c['category'])
        if not gt or not gt.get('is_food_label', True):
            continue
        ss = num(gt.get('serving_size'))
        if not ss:
            continue
        ps, p100 = gt.get('nutrition_per_serving') or {}, gt.get('nutrition') or {}
        bad, ratios = [], []
        for k in S.NUTRITION_FIELDS:
            a, b = num(ps.get(k)), num(p100.get(k))
            if a is None or b is None or not b:
                continue
            ratios.append(a * 100 / b)
            if abs(b * ss / 100 - a) > max(0.6, abs(a) * 0.06):
                bad.append(f'{k} 每份{a} vs 每100g{b}')
        if bad and ratios:
            ratios.sort()
            implied = ratios[len(ratios) // 2]
            out.append((cid, len(bad), ss, implied, bad))
    return out


def check_c(cases):
    """PPOCR 與 Gemini 一致、但與 GT 不同。"""
    pred_dir = os.path.join(S.EVAL_ROOT, 'predictions')
    out = []
    for cid, c in sorted(cases.items()):
        gt = S.load_gt(cid, c['category'])
        pp = os.path.join(pred_dir, f'{cid}.json')
        rp = os.path.join(N.OUT, f'{cid}.json')
        bp = os.path.join(HERE, 'out', BASE, f'{cid}.json')
        if not (gt and os.path.exists(pp) and os.path.exists(rp) and os.path.exists(bp)):
            continue
        gem = json.load(open(pp, encoding='utf-8'))['prediction']
        rec = json.load(open(rp, encoding='utf-8'))
        d = json.load(open(bp, encoding='utf-8'))
        lines = [(l['text'], l['box']) for im in d['images']
                 for l in im.get('lines', []) if l.get('box')]
        ocr = {**N.parse_record(rec), **{k: v for k, v in G.parse_boxes(lines).items()}}
        hits = []
        for k in S.NUTRITION_FIELDS:
            for scope, idx in (('nutrition_per_serving', 0), ('nutrition', 1)):
                g = num((gt.get(scope) or {}).get(k))
                m = num((gem.get(scope) or {}).get(k))
                o = ocr.get(k)
                o = num(o[idx]) if o and idx < len(o) else None
                if g is None or m is None or o is None:
                    continue
                # 兩個系統彼此相符，卻都與 GT 不同
                if abs(m - o) <= max(0.05, abs(m) * 0.01) and \
                   abs(m - g) > max(0.6, abs(g) * 0.06):
                    hits.append(f'{k}.{"每份" if idx == 0 else "每100"}：'
                                f'GT {g}｜PPOCR 與 Gemini 都讀到 {m}')
        if hits:
            out.append((cid, hits))
    return out


def check_b(cases):
    """GT 的值在所有 OCR 文字裡都找不到（弱訊號，當佐證）。"""
    out = []
    for cid, c in sorted(cases.items()):
        gt = S.load_gt(cid, c['category'])
        rp = os.path.join(N.OUT, f'{cid}.json')
        if not gt or not os.path.exists(rp):
            continue
        txt = N.full_text(json.load(open(rp, encoding='utf-8')))
        miss = []
        tot = 0
        for k in S.NUTRITION_FIELDS:
            for scope in ('nutrition_per_serving', 'nutrition'):
                v = num((gt.get(scope) or {}).get(k))
                if v is None:
                    continue
                tot += 1
                if not S.number_present(v, txt):
                    miss.append(f'{k}.{scope[-3:]}={v}')
        if tot and len(miss) / tot >= 0.6 and len(miss) >= 6:
            out.append((cid, len(miss), tot, miss))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default=None, choices=['A', 'B', 'C'])
    a = ap.parse_args()
    cases = load_cases()

    if a.only in (None, 'A'):
        r = check_a(cases)
        print(f'═══ A. GT 自身算術不一致 ── {len(r)} 案 ═══')
        for cid, n, ss, implied, bad in r:
            print(f'  {cid}：{n} 個欄位不符')
            print(f'     serving_size 記 {ss}，由各欄數值反推應為 ~{implied:.0f}')
            for b in bad[:3]:
                print(f'     {b}')
        print()

    if a.only in (None, 'C'):
        r = check_c(cases)
        print(f'═══ C. PPOCR 與 Gemini 一致但與 GT 不同 ── {len(r)} 案 ═══')
        print('   （兩個獨立系統相互印證，這類最可能是正解錯）')
        for cid, hits in r:
            print(f'  {cid}：')
            for h in hits[:6]:
                print(f'     {h}')
        print()

    if a.only in (None, 'B'):
        r = check_b(cases)
        print(f'═══ B. GT 的值大量找不到（弱訊號，可能只是照片讀不出來）── {len(r)} 案 ═══')
        for cid, n, tot, miss in r:
            print(f'  {cid}：{n}/{tot} 個值在 OCR 文字裡完全沒出現')


if __name__ == '__main__':
    main()
