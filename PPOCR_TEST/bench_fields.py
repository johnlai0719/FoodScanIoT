#!/usr/bin/env python3
# `name` 與 `manufacturer` 的規則抽取與評分台。與 bench_allergy.py 同一個模式：
# 吃已存的 OCR 結果，不碰 GPU、不打 API，改邏輯後秒級拿到成績。
#
# 為什麼先做規則而不是直接上模型：這兩欄在 OCR 文字裡的可得性已經量過——
#   name          57 案有正解，44 案的值原樣出現在 OCR 裡，33 案有「品名」錨詞
#   manufacturer  52 案有正解，46 案的值原樣出現在 OCR 裡，38 案有錨詞
# 而自動標註能給的訓練樣本也就是那 44／46 筆。以本專案既有的經驗
# （det 微調那輪：7 張圖、120 個梯度步不足以泛化），45 筆樣本訓一個 NER 頭
# 會過擬合，且它要打敗的規則基準已覆蓋七到九成。**先量規則的天花板**，
# 有缺口再談模型——過敏原那欄就是這樣做完的（純規則打平 Gemini）。
#
# 指標與 score_eval.py 的欄位層一致：正規化後逐字相同（exact）與 char_sim。
# 這兩欄沒有「集合」版本可用，因為它們本來就是單一字串，不像成分或過敏原
# 可以攤平成名稱集合。所以 exact 偏嚴這件事無法迴避，char_sim 併看。
#
# 用法：
#   python bench_fields.py                       # 規則 vs Gemini
#   python bench_fields.py --pred=pred_allergy_0823
#   python bench_fields.py --field=manufacturer --worst 12
#   python bench_fields.py --detail c01
import argparse
import difflib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S                       # noqa: E402
from bench_ingredients import full_text     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
READERS = {'gvision': 'gvision', 'ppocr': 'v6_hires__boxth0.4'}


# ─── 共用 ────────────────────────────────────────────────────────────────────
def norm(s):
    return re.sub(r'\s+', '', S.normalize(s or '', fold_variants=True))


def char_sim(a, b):
    a, b = norm(a), norm(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def lines_of(d):
    return [l for im in d.get('images', []) for l in im.get('lines', [])
            if l.get('text')]


# ─── manufacturer ────────────────────────────────────────────────────────────
# 正解的結尾詞極集中：58 案裡 (股)公司 25、股份有限公司 22、有限公司 2，
# 其他只有 3（含一筆英文 THAI GLICO CO., LTD.）。所以「找公司名結尾」
# 是比「找錨詞」更可靠的入口——錨詞只有 38/52，結尾詞幾乎每案都在。
CORP = re.compile(r'(?:股份有限公司|\(股\)公司|（股）公司|有限公司|企業社|工業社|食品廠)')
MFG_ANCHOR = re.compile(r'製造(?:商|廠商|廠)\s*[:：]?')
# 廠別後綴：聯華食品工業(股)公司**彰化廠**、統一超食代(股)公司**台北廠**
PLANT = re.compile(r'^[一-鿿]{1,4}廠')
# 公司名往前走到這些字就停
MFG_STOP = re.compile(r'[，,。;；:：、\s0-9]|廠址|地址|電話|委託|受託|客服|服務')
# 這些是代工／通路方，不是正解要的製造商（c01 實測：正解是統一企業，
# 而 OCR 裡還有「委託宏全國際股份有限公司」）
DECOY_PREFIX = re.compile(r'(委託|受託|代理|經銷|進口)$')


def extract_manufacturer(text):
    """先用錨詞，沒有錨詞再退回「找公司名結尾往前收」。"""
    m = MFG_ANCHOR.search(text)
    if m:
        rest = text[m.end():m.end() + 60]
        cut = re.search(r'[,，。;；]|廠址|地址|電話|客服|服務專線', rest)
        cand = (rest[:cut.start()] if cut else rest).strip()
        if cand:
            return cand

    # 沒有錨詞：掃所有公司名結尾，取第一個非代工方的
    for mm in CORP.finditer(text):
        start = mm.start()
        i = start
        while i > 0 and not MFG_STOP.match(text[i - 1]):
            i -= 1
        head = text[i:start].strip()
        if not head or len(head) > 20:
            continue
        if DECOY_PREFIX.search(text[max(0, i - 4):i]):
            continue
        tail = text[mm.end():mm.end() + 6]
        pm = PLANT.match(tail.strip())
        return head + mm.group(0) + (pm.group(0) if pm else '')
    return ''


# ─── name ────────────────────────────────────────────────────────────────────
NAME_ANCHOR = re.compile(r'品\s*名\s*[:：]?|產品名稱\s*[:：]?|品項名稱\s*[:：]?')
NAME_STOP = re.compile(r'[，,。;；]|製造日期|有效日期|保存期限|成\s*分|原\s*料|'
                       r'內容物|營養標示|淨重|內容量|每一份量|廠商|製造')
# 品名後面常緊接重量或售價，中間未必有標點：
#   「in果凍(綜合礦物質) 180公克」「…嫩雞飯(辣)99元」「肉鬆飯糰25元」
# 這類尾巴用標點停不下來，要另外一條規則。
NAME_TAIL = re.compile(r'\d+\s*(?:公克|公斤|毫升|毫公升|克|g|ml|元|入|份)|\d{3,}')
def extract_name(text, d=None):
    """只走錨詞。沒有錨詞就回空字串，不猜。

    **「字級最大的那一行」這條退路已實測否決**：`品名` 錨詞只覆蓋 33/57，
    剩下 24 案原本退回最大字級的框，實測 27 次嘗試**全錯**——抓到的是條碼
    （4710088473202）、價格標（291、99元）、以及 OCR 重複輸出的行銷字樣。
    而下游 `main.py` 以 COALESCE 寫入，猜錯的品名會蓋掉資料庫裡正確的值，
    **空值比錯值安全**。這條訊號要能用，得先排除條碼與價格標，不是調門檻。

    參數 `d`（原始 OCR 物件）保留在簽章裡，供日後真的做出可用的版面退路時使用。
    """
    m = NAME_ANCHOR.search(text)
    if not m:
        # **沒有錨詞就回空字串，不猜。** 原本退回「字級最大的那一行」，
        # 實測 27 案全錯（抓到條碼 4710088473202、價格標 291、重複的行銷字樣），
        # 而下游 main.py 用 COALESCE 寫入，猜錯的品名會蓋掉正確值——
        # 空值比錯值安全。字級這條訊號要能用，得先排除條碼與價格標。
        return ''
    rest = text[m.end():m.end() + 40]
    cut = NAME_STOP.search(rest)
    cand = (rest[:cut.start()] if cut else rest).strip()
    # full_text 用空白串接各行，品名幾乎都在錨詞的同一行內
    cand = cand.split(' ')[0].strip() if ' ' in cand else cand
    t2 = NAME_TAIL.search(cand)
    if t2:
        cand = cand[:t2.start()].strip()
    return cand if 2 <= len(cand) <= 30 else ''


EXTRACT = {'manufacturer': lambda t, d: extract_manufacturer(t),
           'name': extract_name}


# ─── 評分 ────────────────────────────────────────────────────────────────────
def load_cases():
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    out = []
    for c in cases:
        gt = S.load_gt(c['case_id'], c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        out.append((c['case_id'], gt, c.get('category') or ''))
    return out


def gemini_field(cid, field, pred_dir):
    p = os.path.join(S.EVAL_ROOT, pred_dir, f'{cid}.json')
    if not os.path.exists(p):
        return None
    return (json.load(open(p, encoding='utf-8')).get('prediction') or {}).get(field) or ''


def ocr(cid, reader):
    p = os.path.join(HERE, 'out', READERS[reader], f'{cid}.json')
    if not os.path.exists(p):
        return None, None
    d = json.load(open(p, encoding='utf-8'))
    return full_text(d, order='reading'), d


def evaluate(rows, key):
    n = ex = got = 0
    sims = []
    per = []
    for cid, g, p in rows:
        if not g:
            continue
        n += 1
        e = int(norm(p) == norm(g))
        ex += e
        got += int(bool(p))
        cs = char_sim(p, g)
        sims.append(cs)
        per.append((cid, e, cs, g, p))
    return {'key': key, 'n': n, 'exact': ex, 'got': got,
            'char_sim': sum(sims) / len(sims) if sims else 0.0, 'per': per}


def show(r):
    print(f"  {r['key']:<24} 逐字全對 {r['exact']:>2}/{r['n']}   "
          f"char_sim {r['char_sim']:.3f}   有抽到 {r['got']}/{r['n']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reader', default='gvision', choices=list(READERS))
    ap.add_argument('--pred', default='pred_allergy_0823')
    ap.add_argument('--field', default=None, choices=list(EXTRACT))
    ap.add_argument('--detail', default=None)
    ap.add_argument('--worst', type=int, default=0)
    ap.add_argument('--skip-category', default=None)
    a = ap.parse_args()

    fields = [a.field] if a.field else list(EXTRACT)
    cases = load_cases()

    for f in fields:
        rule, gem = [], []
        for cid, gt, cat in cases:
            if a.skip_category and cat == a.skip_category:
                continue
            t, d = ocr(cid, a.reader)
            if t is None:
                continue
            g = (gt.get(f) or '').strip()
            rule.append((cid, g, EXTRACT[f](t, d)))
            gv = gemini_field(cid, f, a.pred)
            if gv is not None:
                gem.append((cid, g, gv.strip()))

        if a.detail:
            for cid, g, p in rule:
                if a.detail in cid:
                    print(f'=== {cid} / {f} ===')
                    print(f'  [GT ] {g}')
                    print(f'  [規則] {p}')
                    print(f'  [Gem ] {dict((c, x) for c, _, x in gem).get(cid)}')
            continue

        print(f'\n── {f} ──')
        r_rule, r_gem = evaluate(rule, '規則'), evaluate(gem, f'Gemini（{a.pred}）')
        show(r_rule)
        show(r_gem)
        if a.worst:
            print(f'  最差 {a.worst} 案（規則）：')
            for cid, e, cs, g, p in sorted(r_rule['per'], key=lambda x: x[2])[:a.worst]:
                print(f'    {cid:<28} sim {cs:.2f}')
                print(f'      GT   {g[:60]}')
                print(f'      規則 {(p or "(空)")[:60]}')


if __name__ == '__main__':
    main()
