#!/usr/bin/env python3
"""用逐行分類器的機率決定**裁切框**，與現行規則比。

現行的 `region_crop.find_regions()` 用一組手訂規則挑出成分行再取外接矩形：
    錨點詞（成分／原料／…）＋ 行長 ≥0.45×p90 ＋ 頓號 ≥2 ＋ 空間分群
這支改成：**用 `train_linecls.py` 學出來的 P(成分) 挑行**，其餘不變。

⚠ 這不是「換一個新想法」，而是把**已經存在但沒接上的兩塊**接起來：
   分類器 2026-09-02 就訓練好了，`bench_ingredients.p_linecls` 卻只拿它
   **篩文字**，沒有拿去決定框。而定位是本專案記錄過最大的單一槓桿
   （定位損失約 20 點、切分損失約 10 點，見 `make_linecls.py` 檔頭）。

⚠ **純空間的做法已經失敗過兩次**，不要再走：
     DBSCAN ＋ 詞彙投票      −4.5 點，配對 bootstrap CI −9.7~−0.4，不跨 0
     視覺版面模型 PP-DocLayout  食品包裝整個背面被標成一塊 text
   原因是台灣標示把品名、內容量、成分、保存印在同一塊密集文字裡，
   x/y 上沒有分界線。所以訊號要來自文字，幾何只能當**輔助特徵**。

量的是 `region_crop` 自己那三個指標（同一套定義，直接可比）：
    找到率      有幾張圖框得出成分區
    成分留存    整張圖讀得到的成分項，裁切之後還剩幾項  ← 最重要
    面積比      成分區佔全部文字範圍的比例（送出去的資料量）

⚠ **輸出的是一條曲線，不是一個門檻。** 從測試集挑最佳門檻等於用測試集
   訂參數（本專案的既有約束）。挑點要另外用開發集或事前訂的需求來做。

用法：
    python crop_linecls.py
    python crop_linecls.py --pred=linecls_pred --boxes=v6_best
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
FALLBACK = False


def load_pred(d, cid):
    p = os.path.join(HERE, 'out', d, cid + '.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def box_from(lines, keep):
    """取這些行的外接矩形。`keep` 是與 lines 等長的布林序列。"""
    bs = [RC.bbox(l['box']) for l, k in zip(lines, keep) if k and l.get('box')]
    if not bs:
        return None
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))


def area_frac(box, lines):
    # 直接用 region_crop 的定義（分母是「所有文字的外接框」而不是整張圖），
    # 兩邊的面積比才可比。不做 hasattr 退路——那會在函式改名時靜默回 None。
    return RC.area_frac(box, lines)


def measure(cases, boxes, pred, ths):
    """回傳 {門檻: 統計}。門檻 None 代表現行規則。"""
    out = {t: {'found': 0, 'img': 0, 'full': 0, 'crop': 0, 'area': []}
           for t in ths + [None]}
    nskip = 0
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        gl = gt.get('ingredients_list') or []
        if not gl:
            continue
        rec = load_pred(boxes, cid)
        pr = load_pred(pred, cid)
        if rec is None or pr is None:
            nskip += 1
            continue
        # 逐行機率是整案攤平的，要按影像順序切回去。
        # ⚠ `make_linecls.py` 丟掉「正規化後不足 2 字」的行，所以預測的筆數
        #    比 OCR 行數少（177 案：12383 vs 14098）。接回去必須**套同一條
        #    過濾規則**，否則整案對不齊——第一版沒套，176 案裡 172 案被略過。
        flat = [l for im in (rec.get('images') or []) for l in (im.get('lines') or [])]
        kept = [l for l in flat if len(S.normalize((l.get('text') or '').strip())) >= 2]
        if len(kept) != len(pr):
            nskip += 1
            continue
        # 被丟掉的行給 -1（永遠選不到），其餘按順序吃預測
        it = iter(pr)
        pmap = []
        for l in flat:
            if len(S.normalize((l.get('text') or '').strip())) >= 2:
                pmap.append(next(it)['p'])
            else:
                pmap.append(-1.0)
        i = 0
        per_img = []
        for im in (rec.get('images') or []):
            n = len(im.get('lines') or [])
            per_img.append((im.get('lines') or [], pmap[i:i + n]))
            i += n

        for t in ths + [None]:
            full_txt, crop_txt = [], []
            for lines, ps in per_img:
                # 過濾無框的行時，機率也要跟著濾，否則索引錯位
                pairs = [(l, q) for l, q in zip(lines, ps) if l.get('box')]
                if not pairs:
                    continue
                lines = [l for l, _ in pairs]
                ps = [q for _, q in pairs]
                out[t]['img'] += 1
                if t is None:
                    box = RC.find_regions(lines).get('ingredients')
                else:
                    box = box_from(lines, [q >= t for q in ps])
                    # 分類器一行都沒選中時退回規則。這不是「兩個都試挑好的」
                    # ——那會用到正解；這裡的條件只看分類器自己有沒有輸出，
                    # 現場拿得到，而且沒有它的話那幾張圖會整個變空。
                    if box is None and FALLBACK:
                        box = RC.find_regions(lines).get('ingredients')
                if box:
                    out[t]['found'] += 1
                    a = area_frac(box, lines)
                    if a:
                        out[t]['area'].append(a)
                full_txt.append('\n'.join(l['text'] for l in lines))
                crop_txt.append(RC.text_in(lines, box))
            fa = S.normalize('\n'.join(full_txt), fold_variants=True)
            ca = S.normalize('\n'.join(crop_txt), fold_variants=True)
            fe, ff, _ = S.ingredient_hits(gl, fa)
            ce, cf, _ = S.ingredient_hits(gl, ca)
            out[t]['full'] += len(fe) + len(ff)
            out[t]['crop'] += len(ce) + len(cf)
    return out, nskip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pred', default='linecls_pred')
    ap.add_argument('--boxes', default='v6_best')
    ap.add_argument('--fallback', action='store_true',
                    help='分類器沒框到時退回現行規則')
    a = ap.parse_args()
    global FALLBACK
    FALLBACK = a.fallback
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    ths = [0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.70, 0.90]
    res, nskip = measure(cases, a.boxes, a.pred, ths)
    if nskip:
        print('⚠ 略過 %d 案（缺預測或行數對不上）\n' % nskip)

    base = res[None]
    print('裁切框的來源：%s（機率）vs 現行規則　　框的座標來源：%s\n' % (a.pred, a.boxes))
    print('%-14s %10s %14s %10s' % ('挑行的方式', '框到的圖', '成分留存', '面積比'))
    b_keep = 100 * base['crop'] / base['full'] if base['full'] else 0
    print('%-14s %6d/%-5d %8d/%-5d %9s   ← 現行'
          % ('規則', base['found'], base['img'], base['crop'], base['full'],
             '%.0f%%' % (100 * statistics.mean(base['area'])) if base['area'] else '—'))
    print('%-14s %12s %13.1f%% %10s' % ('', '', b_keep, ''))
    print()
    for t in ths:
        r = res[t]
        keep = 100 * r['crop'] / r['full'] if r['full'] else 0
        area = '%.0f%%' % (100 * statistics.mean(r['area'])) if r['area'] else '—'
        mark = ''
        if r['crop'] >= base['crop'] and r['area'] and base['area'] and \
                statistics.mean(r['area']) <= statistics.mean(base['area']):
            mark = '  ← 留存不輸且更小'
        print('%-14s %6d/%-5d %8d/%-5d %9s   %5.1f%%%s'
              % ('P(成分) ≥%.2f' % t, r['found'], r['img'], r['crop'], r['full'],
                 area, keep, mark))
    print('\n「成分留存」＝整張圖讀得到的成分項，裁切之後還剩幾項——'
          '裁切唯一會造成的損失就是這個。')


if __name__ == '__main__':
    main()
