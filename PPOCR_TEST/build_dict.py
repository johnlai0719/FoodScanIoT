#!/usr/bin/env python3
# 從專案自己的參考資料庫匯出成分字典，供 bench_ingredients.py 做斷詞。
#
# 為什麼需要字典：實測成分抽取漏掉的 306 項裡，有 207 項（66%）是
# **OCR 把分隔符讀丟、項目黏成一團**（`豬肉風味粉豬肉抽出物豬油麥芽糊精`）。
# 純規則怎麼切都切不出來，因為該切的地方沒有任何標記。有字典才能做最大匹配。
#
# 來源是 `server/seed_data/reference_seed.sql` 的 additives 表（804 筆，
# 含 name_zh / aliases / name_en）。這份資料是從 TFDA、JECFA、IARC 等公開
# 來源建的（見 添加物資料庫整理/01_data_sources/），與 58 案評估集無關。
#
# **紅線**：不可以拿 `測試/量化測試/ground_truth/` 的成分當字典。那是評估集的
# 答案，拿來當字典等於直接把答案抄進系統，量出來的分數毫無意義。
# 這支只讀 reference_seed.sql，並在最後印出「字典有幾項出現在 GT 裡」
# 讓人一眼看出涵蓋率來自資料庫而非抄答案。
#
# 用法：
#   python build_dict.py                 # 產生 data/ingredient_dict.json
#   python build_dict.py --check         # 順便量對 GT 的涵蓋率
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
SQL = os.path.abspath(os.path.join(
    HERE, '..', 'server', 'seed_data', 'reference_seed.sql'))
OUT = os.path.join(HERE, 'data', 'ingredient_dict.json')


def _unquote(v):
    """把 SQL 字面值轉成 Python 字串。NULL 回 None。"""
    v = v.strip()
    if v.upper() == 'NULL':
        return None
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1].replace("''", "'")
    return v


def _split_values(s):
    """切 VALUES (...) 裡的欄位。不能直接 split(',')——描述欄裡有逗號。"""
    out, buf, q = [], [], False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "'":
            if q and i + 1 < len(s) and s[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            q = not q
        if ch == ',' and not q:
            out.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append(''.join(buf))
    return out


def extract():
    if not os.path.exists(SQL):
        sys.exit(f'找不到 {SQL}')
    s = open(SQL, encoding='utf-8', errors='replace').read()
    names = []
    for m in re.finditer(r'INSERT INTO public\.additives \([^)]*\) VALUES \((.*?)\);\n',
                         s, re.S):
        cols = _split_values(m.group(1))
        if len(cols) < 5:
            continue
        zh = _unquote(cols[2])
        aliases = _unquote(cols[4])
        if zh:
            names.append(zh)
        if aliases:
            try:
                for a in json.loads(aliases):
                    if a:
                        names.append(a)
            except (ValueError, TypeError):
                pass
    # 去重、丟掉太短或太長的。單字的添加物名（如「鹽」）會把整段文字切碎，
    # 而長度超過 20 的多半是描述被誤抓進來。
    seen, out = set(), []
    for n in names:
        n = n.strip()
        k = re.sub(r'\s+', '', n)
        if not (2 <= len(k) <= 20) or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return sorted(out, key=len, reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    words = extract()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(words, f, ensure_ascii=False, indent=0)
    print(f'字典 {len(words)} 詞 → {OUT}')
    print('  最長:', words[:3])
    print('  最短:', words[-6:])

    if a.check:
        import score_ocr as S
        cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                               encoding='utf-8'))['cases']
        gt_items, covered = 0, 0
        wset = {S.normalize(w, fold_variants=True) for w in words}
        for c in cases:
            gt = S.load_gt(c['case_id'], c.get('category') or '')
            for x in ((gt or {}).get('ingredients_list') or []):
                gt_items += 1
                if S.normalize(x, fold_variants=True) in wset:
                    covered += 1
        print(f'\n字典能完全對上的 GT 成分：{covered}/{gt_items} '
              f'({covered / gt_items * 100:.1f}%)')
        print('  這個比例本來就不會高——字典只有添加物，'
              '而 GT 一半以上是水／麵粉／豬肉這類一般原料。')


if __name__ == '__main__':
    main()
