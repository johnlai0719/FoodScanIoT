#!/usr/bin/env python3
# 讀 markdown 表格的**欄位位置**，而不是把表格攤平後用字元距離抓值。
#
# 為什麼要有這一支（2026-09-09 的診斷，見 [[07-營養表結構化解析實驗]]）：
# HunyuanOCR 在 131/177 案已經吐出乾淨的三欄表格，逐字正確，
# 但現行六個解析器全部是「攤平成一整串文字 → 欄位名 \D{0,8} 數字」。
# 表格的行列關係在攤平那一步就沒了，結果是整體錯開一列——
#
#     | 蛋白質    | 2.5公克 | 12公克 |
#     | 脂肪      | 4.2公克 | 20公克 |
#     | 碳水化合物 | 13公克  | 64公克 |
#
#     protein.每份 填入 4.2（脂肪列）、fat.每份 填入 13.0（碳水列）
#
# 這 131 案佔了 482 格缺口裡的 330 格。
#
# ⚠ 三個已知難處，實作時都要處理，不是事後才發現的：
#   1. 一案可能有多張表（c176 同時有美規 Nutrition Facts 與中文營養標示）
#   2. 第二欄不一定是每 100 公克，也可能是每日參考值百分比（177 案有 25 案）
#   3. 有些表格沒有「每一份量」列，下游求解器缺 ss 就驗不了比例約束
import re
import unicodedata


# 欄位順序＝比對優先序，與 nutrition_pipeline.FIELDS 一致：
# 「飽和脂肪」「反式脂肪」必須排在「脂肪」前面，否則 `脂肪` 會先吃掉它們。
FIELDS = [
    ('calories', r'熱量|热量|Calories'),
    ('protein', r'蛋白[質质贸]'),
    ('saturated_fat', r'[飽饱鮑鲍]和脂肪'),
    ('trans_fat', r'反式脂肪'),
    ('fat', r'脂肪'),
    ('carbohydrates', r'碳水化合物'),
    ('fiber', r'膳食[纖纤织]維|膳食[纖纤织]维'),
    ('sugar', r'糖'),
    ('sodium', r'[鈉钠纳]'),
]
NUM = re.compile(r'-?\d+(?:[.,]\d+)?')
DV = re.compile(r'每日[參参]考值?|[參参]考值百分比|Daily Value')
P100 = re.compile(r'每\s*100|每\s*一?\s*百|/\s*100')
PSERV = re.compile(r'每\s*份|每一份量')
CELL = re.compile(r'(-?\d+(?:[.,]\d+)?)\s*(大卡|千卡|kcal|毫克|mg|公克|克|g)', re.I)
V100 = re.compile(r'每\s*100\s*(?:公克|克|毫升|ml|g)', re.I)
VSERV = re.compile(r'每\s*份')


def labeled_rows(lines):
    """Read the model's plain-text table without borrowing values from another row."""
    columns = []
    rows = {}
    current = None
    for line in lines:
        t = _norm(line).strip()
        if not rows:
            headers = [(m.start(), col) for col, pattern in enumerate((VSERV, V100, DV))
                       for m in pattern.finditer(t)]
            if headers and not any(re.match(r'(?:' + pattern + r')', t) for _, pattern in FIELDS):
                columns.extend(col for _, col in sorted(headers))
                continue
        if not columns:
            continue
        for key, pattern in FIELDS:
            if key == 'saturated_fat':
                pattern += '|鹽和脂肪|盐和脂肪'
            label = re.match(r'(?:' + pattern + r')(?=$|[\s:：\d])', t)
            if label:
                current = key
                rows.setdefault(key, [])
                t = t[label.end():].lstrip(' :：')
                break
        if current is None or not t:
            continue
        matches = list(CELL.finditer(t))
        residue = CELL.sub('', t)
        if re.fullmatch(r'(?:[\s:：|｜]|\d+(?:[.,]\d+)?\s*%)*', residue):
            rows[current].extend((float(m[1].replace(',', '.')), m[2].lower()) for m in matches)
        else:
            current = None

    if len(rows) < 4 or len(set(columns)) != len(columns):
        return None
    numeric_columns = [col for col in columns if col != 2]
    if not numeric_columns:
        return None
    result = {}
    for key, values in rows.items():
        units = ('大卡', '千卡', 'kcal') if key == 'calories' else (
            ('毫克', 'mg') if key == 'sodium' else ('公克', '克', 'g'))
        # A single value under a two-column header has no known position.
        if len(values) != len(numeric_columns) or any(unit not in units for _, unit in values):
            continue
        pair = [None, None]
        for col, (value, _) in zip(numeric_columns, values):
            pair[col] = value
        result[key] = tuple(pair)
    return result


def _norm(s):
    return unicodedata.normalize('NFKC', s or '').replace('·', '.')


def _cells(line):
    """`| a | b | c |` → ['a','b','c']。不是表格列回 None。"""
    t = line.strip()
    if not t.startswith('|'):
        return None
    return [c.strip() for c in t.strip('|').split('|')]


def _is_sep(cells):
    return bool(cells) and set(''.join(cells)) <= set('-: ')


def _value(cell):
    """一格 → 數值。空格、純百分比、沒有數字都回 None。

    百分比要擋掉：格式二的第三欄是每日參考值，把它當成每 100 公克
    會憑空造出假數值（專案 2026-09-07 因此造出 216 格假值）。
    """
    t = _norm(cell)
    if not t or '%' in t:
        return None
    m = NUM.search(t)
    if not m:
        return None
    try:
        return float(m.group(0).replace(',', '.'))
    except ValueError:
        return None


def _blocks(lines):
    """把連續的表格列切成一張一張表。"""
    out, cur = [], []
    for ln in lines:
        c = _cells(ln)
        if c is None:
            if cur:
                out.append(cur)
                cur = []
            continue
        cur.append(c)
    if cur:
        out.append(cur)
    return out


def _field_of(label, used):
    n = _norm(label)
    for k, pat in FIELDS:
        if k in used:
            continue
        if re.search(pat, n):
            return k
    return None


def _columns(block):
    """決定哪一欄是每份、哪一欄是每 100。

    先看表頭寫什麼；表頭認不出來時退回「第一個數值欄＝每份、第二個＝每100」，
    這是標示法規的排列順序。回 (每份欄索引, 每100欄索引, 是否為格式二)。
    """
    head = None
    for cells in block:
        if _is_sep(cells):
            continue
        if any(P100.search(_norm(c)) or PSERV.search(_norm(c)) or DV.search(_norm(c))
               for c in cells[1:]):
            head = cells
            break
    flat = ' '.join(' '.join(c) for c in block)
    dv = bool(DV.search(_norm(flat)))
    i_s = i_100 = None
    if head:
        # Normalize before reading merged or single named columns.
        merged = False
        for i, c in enumerate(head):
            if i == 0:
                continue
            t = _norm(c)
            if P100.search(t) and PSERV.search(t):
                merged = True
                break
            if P100.search(t) and i_100 is None:
                i_100 = i
            elif PSERV.search(t) and i_s is None:
                i_s = i
    # 表頭把兩欄併在同一格（`| | 每份每100毫升 |`）時，兩個都會指到同一欄，
    # 那等於沒有資訊，退回位置預設。
        if merged:
            # A merged header cannot identify the column of a lone value.
            return (1, None if dv else 2, dv) if len(head) > 2 else (None, None, dv)
        if len(head) == 2 and (i_s is not None or i_100 is not None):
            return i_s, None if dv else i_100, dv
    if i_s is None and i_100 is None:
        i_s, i_100 = 1, (None if dv else 2)
    elif i_100 is None and not dv:
        i_100 = 3 - i_s if i_s in (1, 2) else None
    elif i_s is None:
        i_s = 1 if i_100 != 1 else 2
    if dv:
        i_100 = None
    return i_s, i_100, dv


def has_named_columns(lines):
    return any(P100.search(_norm(cell)) or PSERV.search(_norm(cell)) or DV.search(_norm(cell))
               for block in _blocks(lines) for row in block for cell in row[1:])


def _parse_block(block):
    out, used = {}, set()
    i_s, i_100, dv = _columns(block)
    for cells in block:
        if _is_sep(cells) or len(cells) < 2:
            continue
        k = _field_of(cells[0], used)
        if not k:
            continue
        v1 = _value(cells[i_s]) if i_s is not None and i_s < len(cells) else None
        v2 = _value(cells[i_100]) if i_100 is not None and i_100 < len(cells) else None
        if v1 is None and v2 is None:
            continue
        # 只有一欄有值時，依法規排列當作每份；下游求解器會用比例約束仲裁。
        out[k] = (v1, v2)
        used.add(k)
    return out, dv


def pairs_from_lines(lines):
    """整案的所有行 → {欄位: (每份, 每100)}。

    一案有多張表時取「對得上最多法定欄位」的那張——`c176_鹹蛋黃餅` 同時有
    美規 Nutrition Facts 面板（英文欄名、第三欄是 %DV）與中文營養標示，
    用欄名命中數就能選對，不必靠語言判斷。
    """
    best, best_n = {}, 0
    for b in _blocks(lines):
        got, _ = _parse_block(b)
        if len(got) > best_n:
            best, best_n = got, len(got)
    return best
