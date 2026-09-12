#!/usr/bin/env python3
# 營養標示擷取管線：PP-OCRv6 全圖 ＋ PP-StructureV3 表格區，聯集後用法規樣板解析。
#
# 為什麼是這個組合（都是實測出來的，不是設計出來的）：
#   - PP-OCRv6 + box_thresh 0.4 全圖：58 案漏 61 個營養數值
#   - 加上 PP-StructureV3 聯集：漏 42（-19），零訓練
#   - PP-StructureV3 的**表格結構辨識其實是失敗的**——它把整張營養表塞進一個
#     <td> 裡，沒還原出行列。但它的版面偵測會自動裁出表格區域並以較高解析度重讀，
#     撿回全圖那一遍漏掉的數字。有用的是「裁切重讀」，不是「表格理解」。
#   - 兩者互有勝負（c31 靠 StructureV3 從 4 漏變 0；c09/c38 則是全圖那遍較好），
#     所以取聯集而非二選一。
#
# 為什麼不需要表格結構辨識：台灣營養標示是衛福部規定的固定格式——八個欄位、
# 固定順序、兩欄數值（每份 / 每100g）。這不是需要理解的自由版面，是可以用規則
# 解析的樣板。OCR 只要把文字按順序讀出來就夠了。
#
# **評分比 score_ocr.py 的營養檢查嚴格得多。** 那邊只問「這個數字有沒有出現在
# 文字裡」，這裡問「解析器有沒有把正確的值填進正確的欄位」——後者才是下游
# module_b 真正需要的。同一批資料兩個數字會差很多，引用時不要混。
#
# 用法：
#   python nutrition_pipeline.py run            # 跑 OCR，存 out/nutrition/
#   python nutrition_pipeline.py parse          # 解析並對 GT 評分
#   python nutrition_pipeline.py parse --detail c31
import argparse
import json
import os
import re
import sys
import unicodedata

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠ 第七支寫死 OCR 目錄的腳本（2026-09-07）。測試集擴充或換讀取器時，
# 這裡不改就會靜默沿用舊的全圖文字，量到的是另一個系統的成績。
# 用 NUTRI_BASE 換讀取器、NUTRI_OUT 換輸出目錄，兩個要一起換否則會蓋掉。
BASE_PRESET = os.environ.get('NUTRI_BASE') or 'v6_best'
OUT = os.path.join(HERE, 'out', os.environ.get('NUTRI_OUT') or 'nutrition')

# 八個欄位。**順序就是比對優先序**：「飽和脂肪」「反式脂肪」必須排在「脂肪」
# 前面並先把命中的區段遮掉，否則 `脂肪` 會匹配到它們裡面，把飽和脂肪的數值
# 填進 fat（實測踩過這個坑）。
FIELDS = [
    ('calories', r'熱量|热量'),
    ('protein', r'蛋白[質质贸]'),
    ('saturated_fat', r'[飽饱鮑鲍]和脂肪'),
    ('trans_fat', r'反式脂肪'),
    ('fat', r'脂肪'),
    ('carbohydrates', r'碳水化合物'),
    ('fiber', r'膳食[纖纤织]維|膳食[纖纤织]维'),
    ('sugar', r'糖'),
    ('sodium', r'[鈉钠纳]'),
]
# 單位可有可無：OCR 常把「公克」讀成「公」「克」或整個掉字
NUM = r'(-?\d+(?:[.,]\d+)?)\s*(?:大卡|公克|毫克|公絲|公升|毫升|kcal|g|mg|ml)?'


def norm(s):
    s = unicodedata.normalize('NFKC', s or '')
    # OCR 把小數點讀成頓號/逗號很常見
    s = s.replace('·', '.').replace('，', ' ').replace('、', ' ')
    return re.sub(r'\s+', ' ', s)


# 這些字出現在欄位名前面時，代表它是成分而不是營養欄位。
# 沒有這道防線，`糖` 會匹配到成分表的「蔗糖／葡萄糖／麥芽糖」、
# `鈉` 會匹配到「磷酸鈉／L-麩酸鈉」，然後抓走旁邊不相干的數字
# （實測 878 格裡錯了 222 格，多半是這個原因）。
NOT_BEFORE = {
    'sugar': r'(?<![蔗葡萄麥芽乳砂果白黑紅寡多海藻糊焦])',
    'sodium': r'(?<![酸化磷碳])',
    'fat': r'(?<![飽饱鮑鲍反式和不])',
    'protein': r'(?<![豆乳大分離濃縮水解])',
}


def nutrition_block(rec):
    """只回傳營養標示那一段文字。

    整份文件下去比對必然出錯——成分表裡有一堆「糖」「鈉」，地址電話裡有一堆數字。
    優先用 PP-StructureV3 裁出的表格區（它的版面偵測會框出營養表），
    沒有表格時退回「從『營養標示』或『每一份量』往後取一個窗口」。
    """
    tables = [t for t in (rec.get('table_html_text') or []) if t and t.strip()]
    # 表格區可能有多塊（成分表也會被當成表格），只留含營養關鍵詞的
    keep = [t for t in tables if re.search(r'營養標示|每一份量|每100|熱量', norm(t))]
    if keep:
        return '\n'.join(keep)
    whole = '\n'.join((rec.get('base_lines') or []) + (rec.get('struct_lines') or []))
    t = norm(whole)
    m = re.search(r'營養標示|每一份量|本包裝含', t)
    if m:
        # 窗口 600 字：一張營養表連同單位大約 150-300 字，留兩倍餘裕
        return t[m.start():m.start() + 600]
    return t


# 法規允許**兩種**欄位格式，第二欄不一定是「每 100 公克」：
#     格式一   每一份量 ＋ 每份 ＋ 每 100 公克（或毫升）
#     格式二   每一份量 ＋ 每份 ＋ **每日參考值百分比**
# 2026-09-07 實測：177 案裡有 25 案是格式二。原本一律把第二欄當每 100 公克，
# 於是 `熱量 151.6大卡 8%` 被讀成「每份 151.6、每100公克 8」——
# **憑空造出 216 格假數值**（換上讀得到表格的 vlcrop 之後才暴露；
# PP-OCR 讀不到那張表，所以只硬填 27 格，看起來反而「乾淨」）。
# 正解那一欄本來就是 null，所以擋掉不會損失任何正確格。
# ⚠ 要收簡體：OCR（尤其 VL）常吐簡體，實測 c128 讀成「每日参考值」，
# 而 norm() 只做全形轉半形、不轉繁簡，用繁體正則會整條失效。
DV_HEADER = re.compile(r'每日[參参]考值?|[參参]考值百分比')


def parse(text):
    """回傳 {欄位: (每份, 每100g)}。只抓得到一個數值時第二欄為 None。

    版面上的欄序固定是「每份」在前、「每100公克」在後，這是標示法規的排列；
    少數商品只印一欄，此時無法判斷是哪一欄，一律當每份處理並由呼叫端決定。

    ⚠ 但第二欄是「每日參考值百分比」時（格式二），**不可當成每 100 公克**，
    見上方 DV_HEADER 的說明。
    """
    t = norm(text)
    # 整段文字裡出現「每日參考值」→ 這張表的第二欄是百分比，不是每 100 公克
    dv = bool(DV_HEADER.search(t))
    out, spans = {}, []

    def overlaps(a, b):
        return any(not (b <= s or a >= e) for s, e in spans)

    for key, pat in FIELDS:
        # pat 必須包成非捕獲組：`A|B` 這種交替的綁定範圍是整個運算式，
        # 寫成 `A|B\D{0,8}(num)` 會變成「A」或「B\D{0,8}(num)」，
        # 只配到 A 時捕獲組是 None（實測炸過）
        guard = NOT_BEFORE.get(key, '')
        for m in re.finditer(guard + f'(?:{pat})' + r'\D{0,8}' + NUM
                             + r'(?:\D{0,10}' + NUM + r')?', t):
            if overlaps(m.start(), m.end()):
                continue          # 這段已被更長的欄位名吃掉（飽和脂肪 vs 脂肪）
            g1, g2 = m.group(1), m.group(2)
            try:
                v1 = float(g1.replace(',', '.'))
                v2 = float(g2.replace(',', '.')) if g2 is not None else None
            except ValueError:
                continue
            if dv:
                v2 = None          # 第二欄是百分比，丟掉
            out[key] = (v1, v2)
            spans.append((m.start(), m.end()))
            break
    return out


# ─── 跑 OCR ──────────────────────────────────────────────────────────────────
def run(device, only, pp=None):
    """pp：已建好的 PPStructureV3。批次跑不用傳（自己建一個用到底）；
    線上推論（reader/pipeline.py）必須傳，否則每個請求都會重載模型。
    2026-09-13 加，預設 None 時行為與原本逐字相同。"""
    os.makedirs(OUT, exist_ok=True)
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    if only:
        cases = [c for c in cases if any(c['case_id'].startswith(o) for o in only)]

    if pp is None:
        from paddleocr import PPStructureV3
        pp = PPStructureV3(use_doc_orientation_classify=False,
                           use_doc_unwarping=False, device=device)

    for n, c in enumerate(cases, 1):
        cid = c['case_id']
        # 續跑：這支載入 PP-StructureV3 很吃記憶體，177 案跑到一半被系統
        # 殺掉是真的發生過（2026-09-07，與 HunyuanOCR server 同時佔用）。
        # 已完成的直接跳過，重跑就從斷點接下去。要重算整批就先清空 OUT。
        if os.path.exists(os.path.join(OUT, f'{cid}.json')):
            continue
        # 全圖那一遍直接沿用已存的結果，不重跑——省一半時間，且保證與
        # score_ocr.py 量到的是同一份輸出
        bp = os.path.join(HERE, 'out', BASE_PRESET, f'{cid}.json')
        base = []
        if os.path.exists(bp):
            d = json.load(open(bp, encoding='utf-8'))
            base = [l['text'] for im in d['images'] for l in im.get('lines', [])]
        else:
            print(f'[警告] 缺 {BASE_PRESET}/{cid}.json，只用 StructureV3')

        tables, struct = [], []
        for rel in c['images']:
            p = os.path.join(S.EVAL_ROOT, *rel.split('/'))
            if not os.path.exists(p):
                continue
            try:
                for r in pp.predict(p):
                    d = r.json['res'] if hasattr(r, 'json') else r
                    for t in (d.get('table_res_list') or []):
                        tables.append(re.sub(r'<[^>]+>', ' ', t.get('pred_html') or ''))
                    struct += (d.get('overall_ocr_res') or {}).get('rec_texts') or []
            except Exception as e:
                print(f'   {cid} StructureV3 失敗：{e!r}')

        with open(os.path.join(OUT, f'{cid}.json'), 'w', encoding='utf-8') as f:
            json.dump({'case_id': cid, 'category': c.get('category'),
                       'set_version': c.get('set_version'),
                       'base_lines': base, 'table_html_text': tables,
                       'struct_lines': struct}, f, ensure_ascii=False, indent=1)
        print(f'[{n}/{len(cases)}] {cid}  全圖 {len(base)} 行'
              f'｜表格 {len(tables)} 塊｜StructureV3 {len(struct)} 行')
    print(f'\n→ {OUT}\n下一步：python nutrition_pipeline.py parse')


# ─── 解析與評分 ──────────────────────────────────────────────────────────────
def full_text(rec):
    return '\n'.join((rec.get('table_html_text') or [])
                     + (rec.get('base_lines') or [])
                     + (rec.get('struct_lines') or []))


def parse_record(rec):
    """兩段式解析：先營養區塊（精確），缺的欄位再回落全文（補齊）。

    實測三種做法在 878 格上的表現：
      只用全文   529 正確｜錯 222｜缺 127   ← 成分表的「蔗糖/磷酸鈉」污染
      只用區塊   501 正確｜錯 171｜缺 206   ← 錯的變少了，但切太狠丟掉有效值
      兩段式     見下方結果                ← 取區塊的精確 ＋ 全文的涵蓋
    回落的值標記為 fallback，方便之後想只採信高信度來源時過濾。
    """
    out = parse(nutrition_block(rec))
    for k, v in parse(full_text(rec)).items():
        if k not in out:
            out[k] = v
        elif out[k][1] is None and v[1] is not None:
            # 區塊只抓到一欄、全文抓到兩欄時，補上第二欄
            out[k] = (out[k][0], v[1])
    return out


def close(a, b):
    if a is None or b is None:
        return False
    return abs(a - b) <= max(0.05, abs(b) * 0.01)


def do_parse(detail):
    if not os.path.isdir(OUT):
        sys.exit(f'找不到 {OUT}，先跑 python nutrition_pipeline.py run')
    files = sorted(f for f in os.listdir(OUT) if f.endswith('.json'))
    tot = hit = wrong = missing = 0
    per_field = {k: [0, 0] for k, _ in FIELDS}
    rows = []

    for fn in files:
        rec = json.load(open(os.path.join(OUT, fn), encoding='utf-8'))
        cid = rec['case_id']
        gt = S.load_gt(cid, rec.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        got = parse_record(rec)
        ch = ok = 0
        bad = []
        for k, _ in FIELDS:
            ps = (gt.get('nutrition_per_serving') or {}).get(k)
            p100 = (gt.get('nutrition') or {}).get(k)
            if ps is None and p100 is None:
                continue
            g = got.get(k)
            for want, idx, tag in ((ps, 0, '每份'), (p100, 1, '每100')):
                if want is None:
                    continue
                ch += 1
                tot += 1
                per_field[k][1] += 1
                if g is None or idx >= len(g) or g[idx] is None:
                    missing += 1
                    bad.append(f'{k}.{tag} 缺（GT {want}）')
                elif close(g[idx], want):
                    ok += 1
                    hit += 1
                    per_field[k][0] += 1
                else:
                    wrong += 1
                    bad.append(f'{k}.{tag} 錯：{g[idx]} ≠ {want}')
        rows.append((cid, ok, ch, bad))
        if detail and cid.startswith(detail):
            print(f'\n=== {cid} ===')
            print(f'解析結果：{json.dumps(got, ensure_ascii=False)}')
            for b in bad:
                print('   ', b)

    if detail:
        return
    print(f'\n{"case":<30}{"正確/應有":>12}')
    for cid, ok, ch, bad in sorted(rows, key=lambda r: (r[1] - r[2])):
        if ch:
            print(f'{cid:<30}{ok:>5}/{ch:<6}' + ('   ' + '；'.join(bad[:2]) if bad else ''))
    print(f'\n{"":<30}{"合計":>6} {hit}/{tot} 正確｜錯值 {wrong}｜缺值 {missing}')
    print(f'\n逐欄位：')
    for k, _ in FIELDS:
        o, n = per_field[k]
        if n:
            print(f'   {k:<16}{o:>4}/{n:<4}')
    print('\n注意：這裡問的是「正確的值有沒有填進正確的欄位」，'
          '比 score_ocr.py 的「數字有沒有出現」嚴格得多，兩組數字不可混用。')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--device', default='gpu')
    r.add_argument('--only', nargs='*', default=None)
    p = sub.add_parser('parse')
    p.add_argument('--detail', default=None)
    a = ap.parse_args()
    if a.cmd == 'run':
        run(a.device, a.only)
    else:
        do_parse(a.detail)


if __name__ == '__main__':
    main()
