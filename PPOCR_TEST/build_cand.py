#!/usr/bin/env python3
"""為「逐項分類器」產生候選池與弱標籤（[[10-成分候選的逐項分類器]]）。

⚠⚠ **標籤來自評估集的正解，產出為汙染資料。** 一律寫進 `out/_contaminated_*`。

候選 = 最大切分（split_top ＋ _dict_split 全留 ＋ 括號內逐項展開）。
天花板實測：17,673 個候選裡含 2,226 個真值（12.6%）——邊界不是瓶頸，選擇才是。
"""
import collections
import difflib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_ingredients as BI   # noqa: E402
import score_ocr as S            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out', '_contaminated_cand.json')
READERS = ('vlcrop_hy_v4', 'v6_best', 'vlcrop_qwen_v4')
PAREN = re.compile(r'[（(【\[]([^（()）【】\[\]]{2,80})[）)】\]]')
BR = re.compile(r'[()（）\[\]【】{}｛｝]')


def _lines(pre, cid):
    p = os.path.join(HERE, 'out', pre, cid + '.json')
    if not os.path.exists(p):
        return []
    j = json.load(open(p, encoding='utf-8'))
    out = []
    for im in j.get('images') or []:
        out += [l.get('text', '') for l in (im.get('lines') or [])]
    return out


ALLERGEN = re.compile(r'過敏原|过敏原|本產品含|本产品含|生產線|生产线|共線|同一工廠|不適合')
NUTRI = re.compile(r'營養標示|营养标示|每一份量|本包裝含|熱量|蛋白質|飽和脂肪|碳水化合物')
STORE = re.compile(r'保存|有效日期|保存期限|冷藏|冷凍|開封|注意事項|請置於|食用方法|建議')
SEPCH = set('、,，;；')


def context_feats(text, pos_ch, cand):
    """候選在全文中的位置周邊資訊。`10` 的字面特徵看不到這些。"""
    before = text[max(0, pos_ch - 60):pos_ch]
    after = text[pos_ch + len(cand):pos_ch + len(cand) + 40]
    hs = [m.end() for m in BI.HEAD.finditer(text) if m.end() <= pos_ch]
    ts = [m.start() for m in BI.TAIL.finditer(text) if m.start() >= pos_ch]
    d_head = (pos_ch - hs[-1]) if hs else 9999
    d_tail = (ts[0] - pos_ch) if ts else 9999
    # HEAD…TAIL 區間內
    in_span = 0
    if hs:
        nt = [m.start() for m in BI.TAIL.finditer(text) if m.start() > hs[-1]]
        in_span = 1 if (not nt or pos_ch < nt[0]) else 0
    pb = before[-1] if before else ''
    pa = after[0] if after else ''
    return {
        'd_head': min(d_head, 2000), 'd_tail': min(d_tail, 2000),
        'in_span': in_span,
        'ctx_allergen': 1 if ALLERGEN.search(before) else 0,
        'ctx_nutri': 1 if NUTRI.search(before) else 0,
        'ctx_store': 1 if STORE.search(before) else 0,
        'sep_before': 1 if pb in SEPCH else 0,
        'sep_after': 1 if pa in SEPCH else 0,
        'ctx': (before[-40:] + ' ‖ ' + after[:20]),
    }


def candidates(text):
    """最大切分。順序保留，之後要算「在全文的相對位置」。"""
    out = []
    for x in BI.split_top(text):
        out.append(x)
        out.extend(BI._dict_split(x))
    for x in list(out):
        for m in PAREN.finditer(x):
            out.extend([y for y in re.split(r'[、,，;；]', m.group(1))
                        if len(S.normalize(y)) >= 2])
    return out


def main():
    D = BI.load_dict()
    BI.BOXES = 'vlcrop_hy_v4'
    rows = []
    for cid, d, gt in BI.load_cases():
        gl = gt.get('ingredients_list') or []
        if not gl:
            continue
        text = BI.full_text(d)
        cand = candidates(text)
        # 段落路徑抽到的集合（當特徵，不是當標籤）
        inseg = {S.normalize(x, fold_variants=True)
                 for x in (BI.p_union(cid, d, gt) or [])}
        # 其他讀取器的候選，用來算共識
        other = []
        for pre in READERS[1:]:
            other.append({S.normalize(x, fold_variants=True)
                          for x in candidates('\n'.join(_lines(pre, cid)))})
        n = len(cand)
        seen = collections.Counter()
        for i, x in enumerate(cand):
            k = S.normalize(x, fold_variants=True)
            if not k:
                continue
            seen[k] += 1
            if seen[k] > 1:          # 同一字串只留第一次，位置特徵才有意義
                continue
            y = 1 if any(BI._match(x, g) for g in gl) else 0
            at = text.find(x)
            cf = context_feats(text, at if at >= 0 else 0, x)
            rows.append({
                'case_id': cid, 'category': gt.get('category') or '?',
                'text': x, 'y': y,
                'len': len(k),
                'cjk': sum(1 for ch in x if '\u4e00' <= ch <= '\u9fff') / max(1, len(x)),
                'space': 1 if (' ' in x.strip() or '　' in x) else 0,
                'brack': 1 if BR.search(x) else 0,
                'digit': 1 if re.search(r'\d', x) else 0,
                'latin': 1 if re.search(r'[A-Za-z]{2,}', x) else 0,
                'dict': 1 if any(w in k for w in D) else 0,
                'noting': 1 if BI.NOT_ING.search(x) else 0,
                'pos': i / max(1, n - 1),
                'inseg': 1 if k in inseg else 0,
                'agree': sum(1 for o in other if k in o),
                **cf,
            })
    json.dump(rows, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    pos = sum(r['y'] for r in rows)
    print('候選 %d 個｜正例 %d (%.1f%%)｜案例 %d'
          % (len(rows), pos, 100 * pos / len(rows),
             len({r['case_id'] for r in rows})))
    print('→', OUT)


if __name__ == '__main__':
    main()
