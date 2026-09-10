#!/usr/bin/env python3
"""從既有正解自動產生「OCR 行分類」的訓練資料。**不需要任何人工標註。**

**為什麼做這個**：本專案的天花板拆解是
    91.8% 成分文字有進到 OCR 全文 → 89.6% 完美定位下的切分上限 → 69.2% 實際
定位損失約 20 點、切分損失約 10 點。**定位是最大的單一槓桿。**

而定位失敗兩次都敗在同一件事：**分隔訊號是語意的，不是空間的**。
台灣標示把品名、內容量、成分、保存、注意事項、過敏原印在同一塊密集文字裡，
空間聚類（HalalBench 的 DBSCAN＋voting）分不開它們——實測 −4.5 點，
配對 bootstrap 95% CI −9.7 ~ −0.4，不跨 0。

所以改在**文字**上做定位：逐行分類，只留成分那幾行，再交給既有的切分器。

**標籤怎麼來的**：每一行 OCR 文字拿去對正解的各個欄位。
對得上 `ingredients_raw` 的就是成分行，對得上營養數值的就是營養行，以此類推。
`substring_edit` 的起訖位置免費，所以「這一行是不是正解某一段的一部分」問得出來。

⚠ **這是弱標註，不是金標準。** 兩種已知的雜訊：
  1. OCR 讀錯字太多時對不上，真的成分行會被標成「其他」（漏標）
  2. 短行（「水」「鹽」）在很多欄位裡都找得到，可能標錯（歧義）
所以輸出附上每一行的匹配距離，訓練時可以只用高信心的那些。

用法：
    python make_linecls.py --preset=v6_hires__boxth0.4
    python make_linecls.py --sample=成分        # 抽樣看某一類標得對不對
"""
import argparse
import collections
import glob
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out', 'linecls.jsonl')

# 關鍵字只用來標**正解沒有對應欄位**的那幾類（保存、日期、注意事項…）。
# 有正解的欄位一律用比對，不用關鍵字——關鍵字會把「本產品含果汁及茶葉成分」
# 這種句子誤標成成分行。
KEY = [
    ('保存', r'保存期限|保存方法|保存條件|賞味期限|貯存|需冷藏|室溫'),
    ('日期', r'有效日期|製造日期|西元年|年.{0,2}月.{0,2}日|標示於'),
    ('份量', r'每一份量|本包裝含|淨重|净重|內容量|内容量|容量'),
    ('注意', r'注意事項|食用方法|沖泡|微波|加熱|烹調|開封'),
    ('認證', r'TQF|CAS|HACCP|ISO|清真|有機|健康食品|素食|奶素|全素'),
]
NUT_FIELD = r'熱量|蛋白質|飽和脂肪|反式脂肪|碳水化合物|膳食纖維|脂肪|糖|鈉|營養標示'
NUT_NUM = r'\d+(?:\.\d+)?\s*(?:公克|毫克|大卡|公絲|毫升|kcal|g|mg|%)'


def contained(line, field, tol=3):
    """這一行是不是正解某個欄位的一部分。回傳 (是否, 正規化後的距離比例)。

    用 `substring_edit(line, field)`：起訖免費，只計算 line 被吃掉的代價。
    問的是「這一行在那個欄位裡找得到嗎」，不是「兩者像不像」。
    """
    a, b = S.normalize(line), S.normalize(field or '')
    if len(a) < 2 or not b:
        return False, 1.0
    d, _ = S.substring_edit(a, b)
    r = d / len(a)
    return r <= 1 / tol, r


def label(line, gt):
    """回傳 (類別, 距離比例)。順序即優先序——先問有正解的，再問關鍵字。"""
    best = ('其他', 1.0)
    for tag, field in (('成分', gt.get('ingredients_raw')),
                       ('過敏原', gt.get('allergy_warning')),
                       ('品名', gt.get('name')),
                       ('廠商', gt.get('manufacturer'))):
        ok, r = contained(line, field)
        if ok and r < best[1]:
            best = (tag, r)
    if best[0] != '其他':
        return best
    # 營養行：欄位名或「數字＋單位」。這一類正解是結構化的、沒有原文可比對。
    if re.search(NUT_FIELD, line) or re.search(NUT_NUM, line):
        return ('營養', 0.0)
    for tag, pat in KEY:
        if re.search(pat, line):
            return (tag, 0.0)
    return ('其他', 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preset', default='v6_hires__boxth0.4')
    ap.add_argument('--sample', default=None, help='抽樣印出某一類，檢查標得對不對')
    ap.add_argument('--n', type=int, default=25)
    a = ap.parse_args()

    gts = {}
    for f in glob.glob(os.path.join(S.EVAL_ROOT, 'ground_truth', '*', '*.json')):
        gts[os.path.basename(f)[:-5]] = json.load(io.open(f, encoding='utf-8'))

    rows = []
    for cid, gt in sorted(gts.items()):
        p = os.path.join(HERE, 'out', a.preset, f'{cid}.json')
        if not os.path.exists(p):
            continue
        d = json.load(io.open(p, encoding='utf-8'))
        for im in d.get('images', []):
            for ln in im.get('lines', []):
                t = (ln.get('text') or '').strip()
                if len(S.normalize(t)) < 2:
                    continue
                tag, r = label(t, gt)
                rows.append({'case_id': cid, 'text': t, 'label': tag,
                             'dist': round(r, 3), 'score': ln.get('score')})

    with io.open(OUT, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    c = collections.Counter(r['label'] for r in rows)
    print('案例 %d｜OCR 行 %d 筆 → %s' % (len(gts), len(rows), OUT))
    print()
    print('%-8s %6s %7s  %s' % ('類別', '筆數', '佔比', '中位匹配距離'))
    print('-' * 46)
    for k, v in c.most_common():
        ds = sorted(r['dist'] for r in rows if r['label'] == k)
        print('%-8s %6d %6.1f%%  %.2f' % (k, v, 100 * v / len(rows),
                                          ds[len(ds) // 2]))
    print('-' * 46)
    print('%-8s %6d' % ('合計', len(rows)))

    if a.sample:
        print('\n── 「%s」抽樣 %d 筆（依匹配距離由好到壞）──' % (a.sample, a.n))
        sub = sorted([r for r in rows if r['label'] == a.sample],
                     key=lambda r: r['dist'])
        step = max(1, len(sub) // a.n)
        for r in sub[::step][:a.n]:
            print('  %.2f  %-28s %s' % (r['dist'], r['case_id'][:26], r['text'][:60]))


if __name__ == '__main__':
    main()
