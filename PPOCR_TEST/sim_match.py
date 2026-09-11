#!/usr/bin/env python3
# 把抽取器的輸出丟進**下游真正的添加物比對規則**，量「最後使用者看到的」對不對。
#
# 為什麼需要這一支：bench_ingredients.py 量的是清單本身的召回／精確，但那不是
# 產品的輸出。下游 module_a 會拿清單去比對添加物庫，只有比對出來的才會變成警示。
# 兩者未必同向——多抽出來的碎片若配不到任何添加物就是無害的，
# 而漏掉的成分若本來就不是添加物也不影響結果。
#
# 這支要回答的是一個具體的取捨（bench_ingredients 量到 F1 打平、輪廓相反）：
#     p_dict    召回 62.5%｜精確 73.7%
#     p_boxsep  召回 68.1%｜精確 65.6%
# 到底哪個做出來的添加物判定比較好。
#
# 比對規則照抄 `server/module_a/ingredient_matching.py`，離線重現：
#   優先序 0  完全相等（正式名／分段名／別名）
#   優先序 2  **資料庫名稱是成分名的子字串**，且匹配長度 ≥ 成分名的 60%
#   三道保護  比例門檻 0.6、微生物字尾、DL- 消旋物不配 L- 版
# 向量語義比對不模擬——它預設停用（VECTOR_RAG_ENABLED），且停用理由是
# 「命中時無出處可寫」，不該納入基準。
#
# 真值定義：把 **GT 的 ingredients_list** 跑過同一套規則，得到的添加物集合。
# 這樣量到的就是「因為抽取而多出／少掉的添加物判定」，把比對規則本身的
# 誤差從等式兩邊消掉。
#
# 用法：
#   python sim_match.py
#   python sim_match.py --detail c09
# ⚠ **這不是線上那份。** App 實際走的是
#    `server/module_a/ingredient_matching.py`，兩份已經分岔過：
#      類別詞不得比對物質庫   那邊 2026-07-30 修，這邊 **2026-09-10** 才補
#      `調味劑(A、B、C)` 展開  那邊有（`_expand_generic_items`），這邊沒有
#                             → 這邊的分母因此少 173 個（決策單 #21）
#    改這裡的比對規則之前，先看那邊有沒有已經解過同一題。

import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import bench_ingredients as BI   # noqa: E402
import build_dict as BD          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

_PUNCT_CANON = {
    "‘": "'", "’": "'", "ʼ": "'", "´": "'", "`": "'",
    "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
}
_PAREN_RE = re.compile(r"[（({\[]([^（()）{}\[\]]*)[）)}\]]")
_GENERIC_EXTRA = {"品質改良劑", "糊料", "香辛料", "色素", "酸味劑", "安定劑",
                  "凝固劑", "膠質", "酵素", "萃取物", "營養強化劑"}
ORGANISM_SUFFIXES = ("菌", "菌種", "菌粉", "黴", "酵母")
MIN_SUBSTR_RATIO = 0.6
COMPOUND_MIN_PARTS = 3


# 繁簡摺疊。**只用於比對，不改變任何輸出。**
#
# ⚠ 2026-09-10 加。原本本檔的 `normalize_text` 兩邊都不折繁簡，於是
# HunyuanOCR 吐的簡體永遠對不到繁體的添加物資料庫：
#     碳酸氢钠 ↛ 碳酸氫鈉｜碳酸钙 ↛ 碳酸鈣｜棕榈蜡 ↛ 棕櫚蠟
#     食用黄色四號 ↛ 食用黃色四號｜食用红色四十号 ↛ 食用紅色四十號
# 實測「字串完全相同卻沒命中」的 56 個添加物裡，47 個是這個原因。
#
# 用 t2s（繁→簡）而不是 s2t：t2s 是多對一，方向安全；
# s2t 會遇到一簡對多繁而選錯（專案 2026-09-09 在 OpenCC s2twp 上踩過
# 影象→影像、型別→類型 這類過度轉換）。
# 這裡兩邊都轉成簡體只是為了比對，**輸出的字元一個都不變**，
# 故不影響 §0 的字元佐證率（實測仍為 1.0000）。
try:
    from opencc import OpenCC
    _FOLD = OpenCC("t2s").convert
except Exception:
    _FOLD = None


def normalize_text(text):
    if not text:
        return ""
    text = "".join(chr(ord(c) - 0xfee0) if 0xff01 <= ord(c) <= 0xff5e else c
                   for c in text)
    text = "".join(_PUNCT_CANON.get(c, c) for c in text)
    # ⚠ **撇號一律去掉，兩邊都去。**
    #    核苷酸類調味劑寫作 `5'-次黃嘌呤核苷磷酸二鈉`，而 PP-OCR 對那一撇
    #    時有時無——同一批照片裡 `5'-次黃` 出現 13 次、`5-次黃` 也出現 5 次。
    #    沒有那一撇就配不到，而它**不帶任何區辨資訊**：資料庫裡不存在
    #    「5-次黃」與「5'-次黃」兩種不同的添加物。
    #    以資料庫自身驗證：去撇號前後，會撞號的鍵都是 39 個（未新增任何混淆）。
    #    含直撇、彎撇、prime(′)、重音符——OCR 這幾種都輸出過。
    text = re.sub(r"['’‘′`ˊ]", '', text)
    text = re.sub(r'\(.*?\)|（.*?）|\s+', '', text)
    return _FOLD(text) if _FOLD else text


def strip_brackets(text):
    prev = None
    while prev != text:
        prev = text
        text = _PAREN_RE.sub("", text)
    return text


def load_additives():
    """從 reference_seed.sql 讀出比對需要的欄位。"""
    s = open(BD.SQL, encoding='utf-8', errors='replace').read()
    out, generic = [], set(_GENERIC_EXTRA)
    for m in re.finditer(r'INSERT INTO public\.additives \([^)]*\) VALUES \((.*?)\);\n',
                         s, re.S):
        c = BD._split_values(m.group(1))
        if len(c) < 8:
            continue
        zh, en, aliases, cat = (BD._unquote(c[2]), BD._unquote(c[3]),
                                BD._unquote(c[4]), BD._unquote(c[6]))
        try:
            al = [normalize_text(a) for a in json.loads(aliases)] if aliases else []
        except (ValueError, TypeError):
            al = []
        n_zh = normalize_text(zh or '')
        # 「醋酸鈉； 醋酸鈉（無水）」一格塞兩個名稱，要拆
        parts = [normalize_text(p) for p in re.split(r'[；;、]', zh or '') if p.strip()]
        out.append({'zh': zh, 'n_zh': n_zh, 'parts': [p for p in parts if p],
                    'aliases': [a for a in al if a], 'en': en})
        try:
            for x in (json.loads(cat) if cat else []):
                t = normalize_text(strip_brackets(x))
                for seg in re.split(r'[、,，]', t):
                    if len(seg) >= 2 and seg != '其他':
                        generic.add(seg)
        except (ValueError, TypeError):
            pass
    # ⚠ 別名不可以是**類別詞**。資料庫裡 `阿斯巴甜` 的別名收了「甜味劑」、
    #    `酵素製劑` 收了「酵素」——那不是同義詞，是它所屬的分類。
    #    後果是任何一句「甜味劑(蔗糖素)」都會被判成含阿斯巴甜：
    #    2026-09-10 實測在 177 案裡誤配 48 次，其中 16 案讓正解憑空多出阿斯巴甜。
    #    這一關要在迴圈**之後**做——`generic` 是邊讀邊長出來的，
    #    在迴圈裡過濾會漏掉還沒讀到的類別詞。
    for a in out:
        a['aliases'] = [x for x in a['aliases'] if not is_generic(x, generic)]
    return out, generic


def is_generic(name, generic):
    return name in generic or (name.startswith("複方") and name[2:] in generic)


def is_compound(ing, generic):
    outer = normalize_text(strip_brackets(ing))
    if not outer or is_generic(outer, generic):
        return False
    m = re.search(r"[（({\[](.*)[）)}\]]", ing, re.S)
    if not m:
        return False
    parts = [x for x in re.split(r"[、,，/]", m.group(1)) if normalize_text(x)]
    return len(parts) >= COMPOUND_MIN_PARTS


def candidate_names(ing, generic):
    outer = normalize_text(strip_brackets(ing))
    inners = []
    m = re.search(r"[（({\[](.*)[）)}\]]", ing, re.S)
    if m:
        for piece in re.split(r"[、,，/]", m.group(1)):
            piece = normalize_text(piece)
            if len(piece) >= 2:
                inners.append(piece)
    if is_compound(ing, generic):
        cands = [outer] if outer else []
    elif is_generic(outer, generic):
        cands = inners + ([outer] if outer else [])
    else:
        cands = ([outer] if outer else []) + inners
    seen, res = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            res.append(c)
    return res


def best_match(norm, adds):
    if not norm:
        return None
    is_racemic = norm.upper().startswith("DL-")
    best = None
    for a in adds:
        hit = None
        if a['n_zh'] and a['n_zh'] == norm:
            hit = (0, len(a['n_zh']))
        elif any(p == norm for p in a['parts']):
            hit = (0, len(norm))
        elif any(al == norm for al in a['aliases']):
            hit = (0, len(norm))
        elif a['n_zh'] and a['n_zh'] in norm:
            hit = (2, len(a['n_zh']))
        elif any(p and p in norm for p in a['parts']):
            hit = (2, max(len(p) for p in a['parts'] if p and p in norm))
        elif a['en'] and a['en'].lower() in norm.lower():
            hit = (2, len(a['en']))
        else:
            ma = next((al for al in a['aliases'] if al and al in norm), None)
            if ma:
                hit = (2, len(ma))
        if hit is None:
            continue
        if hit[0] == 2 and hit[1] / len(norm) < MIN_SUBSTR_RATIO:
            continue
        if hit[0] == 2 and norm.endswith(ORGANISM_SUFFIXES):
            continue
        if hit[0] == 2 and is_racemic and any(
                c.upper().startswith("L-")
                for c in [a['n_zh']] + a['parts'] + a['aliases'] if c):
            continue
        cand = (hit[0], -hit[1], a)
        if best is None or cand[:2] < best[:2]:
            best = cand
        if hit[0] == 0:
            break
    return best[2] if best else None


def additives_of(ing_list, adds, generic):
    """一份成分清單 → 判定為添加物的那些（用資料庫的正式名當鍵，才好比對）。"""
    found = set()
    for ing in ing_list:
        for c in candidate_names(ing, generic):
            a = best_match(c, adds)
            if a:
                found.add(a['zh'])
                break
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detail', default=None)
    a = ap.parse_args()

    adds, generic = load_additives()
    print(f'添加物庫 {len(adds)} 筆｜類別統稱 {len(generic)} 個\n')
    cases = BI.load_cases()

    names = ['dict', 'boxsep']
    agg = {n: [0, 0, 0] for n in names}     # 命中 / 真值數 / 判定數
    rows = []
    for cid, d, gt in cases:
        truth = additives_of(gt['ingredients_list'], adds, generic)
        r = {'cid': cid, 'truth': truth}
        for n in names:
            got = additives_of(BI.PARSERS[n](cid, d, gt) or [], adds, generic)
            agg[n][0] += len(truth & got)
            agg[n][1] += len(truth)
            agg[n][2] += len(got)
            r[n] = got
        rows.append(r)

    print(f'{"抽取器":<10}{"命中":>6}{"真值":>6}{"判定":>6}{"召回":>8}{"精確":>8}{"F1":>8}')
    for n in names:
        h, t, p = agg[n]
        rc = h / t if t else 0
        pr = h / p if p else 0
        f1 = 2 * rc * pr / (rc + pr) if rc + pr else 0
        print(f'{n:<10}{h:>6}{t:>6}{p:>6}{rc * 100:>7.1f}%{pr * 100:>7.1f}%{f1 * 100:>7.1f}%')
    print('\n（真值＝GT 的 ingredients_list 跑過同一套比對規則得到的添加物集合，'
          '所以比對規則本身的誤差已從兩邊消掉）')

    if a.detail:
        for r in rows:
            if not r['cid'].startswith(a.detail):
                continue
            print(f"\n═══ {r['cid']}")
            print('  真值 :', sorted(r['truth']))
            for n in names:
                print(f"  {n:<8}漏 {sorted(r['truth'] - r[n])}")
                print(f"  {'':<8}多 {sorted(r[n] - r['truth'])}")


if __name__ == '__main__':
    main()
