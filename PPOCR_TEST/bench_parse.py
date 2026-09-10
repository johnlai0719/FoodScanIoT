#!/usr/bin/env python3
# 解析器的快速評分台。改解析邏輯後跑這支，秒級得到 878 格的成績。
#
# 為什麼要獨立一支：out/nutrition/ 已經存好 58 案的 OCR 結果，
# 改解析邏輯不需要重跑 OCR、不需要 GPU。把評分抽出來之後，
# 一次迭代從「幾分鐘」變成「幾秒」，這是能反覆嘗試的前提。
#
# 用法：
#   python bench_parse.py                 # 比較所有解析器
#   python bench_parse.py --detail c46    # 看單一案例錯在哪
#   python bench_parse.py --worst 12      # 列出最差的案例
#   python bench_parse.py --ablate        # 各約束／各讀法分別值多少格
import argparse
import json
import os
import sys
import collections
import re

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import nutrition_pipeline as N   # noqa: E402
import table_geometry as G       # noqa: E402
import nutrition_solver as V     # noqa: E402
import table_md as TM            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠ 這個預設是 57 案的舊目錄。測試集擴充後若不覆寫，`load_boxes` 會對
# 119 案靜默回傳空清單——geom 那一路候選整個消失而且不報錯（拿掉 geom
# 實測 702→689）。第九支踩到同一個坑，見「給下一屆」。
# vlcrop 的輸出沒有 box（rec 是 HunyuanOCR），要用 PP-OCR 的 v6_best。
BOXES = os.environ.get('PARSE_BOXES') or 'v6_hires__boxth0.4'
FIELDS = V.NUTRITION_FIELDS


def load_boxes(cid):
    p = os.path.join(HERE, 'out', BOXES, f'{cid}.json')
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding='utf-8'))
    return [(l['text'], l['box']) for im in d['images']
            for l in im.get('lines', []) if l.get('box')]


def load_cases():
    out = []
    for f in sorted(os.listdir(N.OUT)):
        if not f.endswith('.json'):
            continue
        rec = json.load(open(os.path.join(N.OUT, f), encoding='utf-8'))
        gt = S.load_gt(rec['case_id'], rec.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        out.append((rec, gt))
    return out


def score_one(got, gt):
    """回傳 (正確數, 應有數, 錯誤明細)。"""
    ok = ch = 0
    bad = []
    for k in FIELDS:
        for scope, idx, tag in (('nutrition_per_serving', 0, '每份'),
                                ('nutrition', 1, '每100')):
            want = (gt.get(scope) or {}).get(k)
            if want is None:
                continue
            ch += 1
            g = got.get(k)
            v = g[idx] if (g and idx < len(g)) else None
            if v is not None and N.close(v, want):
                ok += 1
            else:
                bad.append((f'{k}.{tag}', want, v))
    return ok, ch, bad


# ─── 各種解析器 ──────────────────────────────────────────────────────────────
def p_regex(rec, cid):
    return N.parse_record(rec)


def p_geom(rec, cid):
    return G.parse_boxes(load_boxes(cid))


def p_oracle(rec, cid, gt=None):
    """逐案取較佳——不是可實作的解析器，是上限參考。"""
    return None


def _ss(rec, cands):
    """份量：先從文字抓，抓不到就由候選值反推。"""
    return (V.parse_serving_size(N.full_text(rec))
            or V.infer_serving_size(cands[0]) or V.infer_serving_size(cands[1]))


def _consistent(pair, ss):
    if not pair or ss is None:
        return False
    a, b = (pair + (None,))[:2]
    if a is None or b is None:
        return False
    return abs(b * ss / 100 - a) <= max(0.6, abs(a) * 0.06)


def p_merge(rec, cid):
    """**逐欄位**仲裁，不是逐案選解析器。

    regex 與 geom 各有擅長的版面，但同一張圖上不同欄位的勝負可能相反——
    逐案二選一會被迫連錯的一起收下。改成每個欄位獨立判斷，
    用法規的算術關係當裁判：`每份 = 每100g × 份量/100`
    （實測 ground_truth 的 430 組欄位對裡 422 組成立）。
    """
    A = N.parse_record(rec)
    B = G.parse_boxes(load_boxes(cid))
    ss = _ss(rec, (A, B))
    out = {}
    for k in FIELDS:
        a, b = A.get(k), B.get(k)
        ca, cb = _consistent(a, ss), _consistent(b, ss)
        if ca and not cb:
            out[k] = a
        elif cb and not ca:
            out[k] = b
        elif a and b:
            # 兩者都通過或都沒通過：取「兩欄都有值」的那個，
            # 再平手就取 regex（它單獨的成績較高）
            fa = sum(1 for v in a[:2] if v is not None)
            fb = sum(1 for v in b[:2] if v is not None)
            out[k] = b if fb > fa else a
        elif a or b:
            out[k] = a or b
    return out


def _norm(text):
    import unicodedata
    return unicodedata.normalize('NFKC', text or '').replace('·', '.')


def _tokens_with_pos(text):
    """把文字切成 (種類, 值, 位置) 序列：欄位名與數值各自標記。"""
    t = _norm(text)
    marks = []
    for key, pat in V_FIELD_PATTERNS:
        for m in re.finditer(pat, t):
            marks.append((m.start(), 'label', key))
    for m in re.finditer(r'(-?\d+(?:[.,]\d+)?)\s*'
                         r'(?:大卡|公克|毫克|公絲|公升|毫升|kcal|g|mg|ml)', t):
        # 「每100公克」「每一份量236.5毫升」裡的數字不是營養值
        lo = max(0, m.start() - 8)
        if re.search(r'每\s*100|每一份量|本包裝|淨重|內容量', t[lo:m.end()]):
            continue
        try:
            marks.append((m.start(), 'num', float(m.group(1).replace(',', '.'))))
        except ValueError:
            pass
    marks.sort()
    return marks


V_FIELD_PATTERNS = [
    ('calories', r'熱量|热量'),
    ('protein', r'蛋白[質质贸]'),
    ('saturated_fat', r'[飽饱鮑鲍]和脂肪'),
    ('trans_fat', r'反式脂肪'),
    ('fat', r'(?<![飽饱鮑鲍反式和])脂肪'),
    ('carbohydrates', r'碳水化合物'),
    ('fiber', r'膳食[纖纤织][維维]'),
    ('sugar', r'(?<![蔗葡萄麥芽乳砂果白黑紅寡多海藻糊焦])糖'),
    ('sodium', r'(?<![酸化磷碳])[鈉钠纳]'),
]


def p_window(rec, cid):
    """對每個欄位名，**前後都看**，用算術約束挑出最合理的 (每份, 每100)。

    起因是 c03_厚奶茶：OCR 把表格線性化成
        熱量 [132] [55.9] [1.6] 蛋白質 [3.8] [1.9] 脂肪 [4.6] [1.7] 飽和脂肪 [4.0]
    每個標籤的**每100 值在它前面、每份值在它後面**。只往後找的話整排錯位
    （蛋白質拿到 3.8/1.9，正解是 3.8/1.6）。

    版面千變萬化，與其為每種寫一條規則，不如列出候選再讓法規的算術關係
    當裁判——`每份 = 每100 × 份量/100` 在 GT 的 430 組欄位對裡成立 422 組，
    是這個題目上最可靠的先驗。
    """
    return _window_on(N.nutrition_block(rec),
                      V.parse_serving_size(N.full_text(rec))
                      or V.infer_serving_size(N.parse_record(rec)))


def _window_on(text, ss):
    marks = _tokens_with_pos(text)
    idx = {}
    for i, (_, kind, val) in enumerate(marks):
        if kind == 'label' and val not in idx:
            idx[val] = i
    out = {}
    for key, i in idx.items():
        after = [v for _, k, v in marks[i + 1:i + 4] if k == 'num']
        before = [v for _, k, v in marks[max(0, i - 3):i] if k == 'num'][::-1]
        cands = []
        # 常見的三種排列：值都在後、每100在前、只有一欄
        if len(after) >= 2:
            cands.append((after[0], after[1]))
        if after and before:
            cands.append((after[0], before[0]))
        if len(before) >= 2:
            cands.append((before[1], before[0]))
        if after:
            cands.append((after[0], None))
        if not cands:
            continue
        if ss:
            good = [c for c in cands if _consistent(c, ss)]
            if good:
                out[key] = good[0]
                continue
        out[key] = cands[0]
    return out


def _err(pair, ss):
    """候選違反算術關係的程度。越小越可信；無法判定回 None。"""
    if not pair or ss is None:
        return None
    a, b = (pair + (None,))[:2]
    if a is None or b is None:
        return None
    pred = b * ss / 100
    return abs(pred - a) / max(1.0, abs(a))


def p_merge3(rec, cid):
    """三方逐欄位仲裁：regex / geom / window，算術約束當裁判。

    仲裁是**門檻式**不是排序式：通過算術約束的候選裡取優先序最前的。
    試過改成「誤差最小者勝」，反而 610→592——錯的組合有時算術上巧合地
    更吻合，而 window>regex>geom 這個優先序本身帶著資訊（各自的單獨成績）。
    約束只該用來否決，不該用來排名。
    """
    A = N.parse_record(rec)
    B = G.parse_boxes(load_boxes(cid))
    C = p_window(rec, cid)
    ss = (V.parse_serving_size(N.full_text(rec))
          or V.infer_serving_size(A) or V.infer_serving_size(B)
          or V.infer_serving_size(C))
    out = {}
    for k in FIELDS:
        cands = [c for c in (C.get(k), A.get(k), B.get(k)) if c]
        if not cands:
            continue
        good = [c for c in cands if _consistent(c, ss)]
        if good:
            out[k] = good[0]
        else:
            # 都通不過約束時，取「兩欄都有值」的；再平手取第一個
            cands.sort(key=lambda c: -sum(1 for v in c[:2] if v is not None))
            out[k] = cands[0]
    return out


# 台灣《包裝食品營養標示應遵行事項》規定的欄位順序，是固定的。
# 單位本身就是強訊號：大卡只可能是熱量、毫克只可能是鈉，中間全是公克。
CANON = ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
         'carbohydrates', 'sugar', 'sodium']
CANON_FIBER = ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
               'carbohydrates', 'fiber', 'sugar', 'sodium']
UNIT_OF = {'calories': 'kcal', 'sodium': 'mg'}


def _units(text):
    """(值, 單位類別) 序列，依閱讀順序。

    單位寫法試過放寬到「公」「克」單字（OCR 常把「公克」截半，如 c02 的
    `5.1公`），結果 seq 46→40、pool 632→631：多抓到的雜訊比救回的值多。
    單位保持嚴格，該放寬的是串的結構而不是單位比對。
    """
    t = _norm(text)
    out = []
    for m in re.finditer(r'(-?\d+(?:[.,]\d+)?)\s*'
                         r'(大卡|仟卡|千卡|kcal|毫克|公絲|mg|公克|克|g)', t):
        u = m.group(2)
        u = 'kcal' if u in ('大卡', '仟卡', '千卡', 'kcal') else \
            'mg' if u in ('毫克', '公絲', 'mg') else 'g'
        lo = max(0, m.start() - 8)
        if re.search(r'每\s*100|每一份量|本包裝|淨重|內容量', t[lo:m.end()]):
            continue
        try:
            out.append((float(m.group(1).replace(',', '.')), u))
        except ValueError:
            pass
    return out


def _sequence_on(text, ss, fiber_order=None, reverse=False):
    """靠**欄位順序**而不是欄位名來對值——標籤讀壞或整批脫落時唯一還能用的線索。

    c41_綠巨人珍珠玉米 是典型：營養表的標籤那一欄整條沒被偵測到，
    只剩 `100大卡 2.5公克 1.1公克 0.2公克 ... 160毫克` 一串裸數字。
    任何靠 `re.search('蛋白質')` 的做法在這裡都是零分。

    但法規把欄位順序寫死了（熱量→蛋白質→脂肪→飽和→反式→碳水→糖→鈉），
    而且首尾兩個單位獨一無二：大卡只會是熱量、毫克只會是鈉。
    所以「以大卡開頭、以毫克結尾、中間全是公克」的一段就是一整欄，
    位置即欄位。這個 pattern 誤配的機率很低——要湊巧滿足首尾單位加長度，
    幾乎只有真的營養表做得到。
    """
    # 直排的營養表被線性化之後常常整段倒過來——c41_綠巨人珍珠玉米 讀出來是
    # `… 160毫克 2.1公克 4.0公克 0.2公克 1.1公克 2.5公克 100大卡`，
    # 熱量在最後、鈉在最前，正向掃描一格都對不到（實測 0/18）。
    # 把序列反轉再掃一次就行，不必判斷版面方向：兩個方向都當候選丟進池子。
    seq = _units(text)
    if reverse:
        seq = seq[::-1]
    out = {}
    # 以大卡為錨點往後掃連續的公克值。理想的一整欄是「大卡 + 6~7 個公克 + 毫克」，
    # 但鈉那格常被漏偵測或讀成別的單位，硬要求毫克結尾會整欄作廢——
    # 所以毫克是**加分項不是必要條件**：有就吃進去當鈉，沒有就只填前面幾欄。
    for i, (_, u) in enumerate(seq):
        if u != 'kcal':
            continue
        j = i + 1
        while j < len(seq) and seq[j][1] == 'g':
            j += 1
        ng = j - i - 1                      # 大卡後面連續的公克數
        if ng < 5:                          # 太短的串沒有辨識力，容易誤配
            continue
        vals = [v for v, _ in seq[i:j]]
        has_na = j < len(seq) and seq[j][1] == 'mg'
        if has_na:
            vals.append(seq[j][0])
        # 膳食纖維是選印欄位，看長度猜有沒有它會猜錯一半。呼叫端兩種欄序
        # 各跑一次、都丟進候選池，由算術約束決定哪個對——這比猜穩。
        order = fiber_order or CANON
        for key, v in zip(order, vals[:-1] if has_na else vals):
            out.setdefault(key, []).append(v)
        if has_na:
            out.setdefault('sodium', []).append(vals[-1])
    # 一張表通常有兩欄（每份、每100）→ 抓到兩段就配成對。
    # 哪一欄是「每份」由算術約束**整表投票**決定，不逐欄各判也不假設印刷順序：
    # 同一張表的兩欄不可能一半正著一半反著，逐欄獨立判斷只會製造矛盾。
    pairs = {k: (vs[0], vs[1]) for k, vs in out.items() if len(vs) >= 2}
    if pairs and ss:
        fwd = sum(1 for p in pairs.values() if _consistent(p, ss))
        rev = sum(1 for p in pairs.values() if _consistent((p[1], p[0]), ss))
        if rev > fwd:
            pairs = {k: (b, a) for k, (a, b) in pairs.items()}
    res = dict(pairs)
    for key, vs in out.items():
        if key not in res:
            res[key] = (vs[0], None)
    return res


def _serving_size(rec, cands):
    """份量：所有算術約束的基礎，抓不到等於整案的仲裁全部失效。

    實測 57 案有 5 案抓不到、1 案抓錯，那 6 案共 90 幾格的仲裁品質全被拖累。
    逐案看失敗原因各不相同，所以疊四條路徑，任一條成立就夠：

      1. 每一份量 N —— 原本就有的，但間隙放寬到 8 個非數字字元
         （c18 是 `每一份量 营餐標示 本包装 30公克`，中間插了整段雜訊）
      2. N 公克 每一份量 —— 數字印在標籤**前面**（c46 是 `410 公克 每一份量 1份`）
      3. 淨重 ÷ 本包裝含 N 份 —— 法規保證的關係（c18: 60公克/2份 = 30）
      4. 由候選值反推 —— 每份/每100×100 就是份量。多個欄位算出同一個比值時
         那個比值幾乎不可能是巧合；取眾數。這條不需要文字裡提到份量，
         是 c11/c12/c58 這種「標示整段沒讀到」唯一還有的路

    只回落在 5–2000 的值：超出這個範圍多半是把別的數字抓進來了
    （c52 的 `每一份量3毫升` 是 310 掉了兩位數，寧可當抓不到）。
    """
    t = _norm(N.full_text(rec))

    def ok(v):
        return v if v and 5 <= v <= 2000 else None

    def num(m):
        try:
            return float(m.group(1).replace(',', '.'))
        except (ValueError, AttributeError):
            return None

    for pat in (r'每\s*一?\s*份\s*量\D{0,8}?(\d+(?:[.,]\d+)?)',
                r'(\d+(?:[.,]\d+)?)\s*(?:公克|毫升|g|ml)\D{0,6}每\s*一?\s*份\s*量'):
        for m in re.finditer(pat, t):
            v = ok(num(m))
            if v:
                return v
    mw = re.search(r'(?:淨重|净重|內容量|内容量)\D{0,3}(\d+(?:[.,]\d+)?)', t)
    # 份數與「本包裝」之間可能還隔著份量本身：c18 是 `本包装 30公克 2份`，
    # 所以中間要允許數字通過，不能用 \D
    mn = re.search(r'本\s*包\s*[裝装].{0,12}?(\d+(?:[.,]\d+)?)\s*份', t)
    if mw and mn:
        w, n = num(mw), num(mn)
        if w and n and n >= 1:
            v = ok(round(w / n, 1))
            if v:
                return v
    # 由候選值反推：多個欄位算出同一個比值，那個比值就是份量。
    # 用分群而非精確相等——OCR 的四捨五入會讓同一張表算出 372 與 375，
    # 精確比對會把它們當成兩個不同的答案，各只有一票而全部落選。
    rs = sorted(r for d in cands
                for a, b in ((p + (None,))[:2] for p in d.values())
                if a and b and b > 0 for r in [a / b * 100] if 5 <= r <= 2000)
    best_r, best_n = None, 0
    for r0 in rs:
        grp = [r for r in rs if abs(r - r0) <= max(2.0, r0 * 0.03)]
        if len(grp) > best_n:
            best_r, best_n = sum(grp) / len(grp), len(grp)
    if best_n >= 2:                # 至少兩個欄位同意才採信
        return round(best_r, 1)
    return None


def _slots(order, column_major):
    """把整張表攤成一串「格子」，順序照它在版面上被讀出來的樣子。

    兩種掃描方式涵蓋了絕大多數版面：
      逐列（row-major）  熱量每份 熱量每100 蛋白質每份 蛋白質每100 …
      逐欄（column-major）熱量每份 蛋白質每份 … 熱量每100 蛋白質每100 …
    """
    if column_major:
        return [(k, c) for c in (0, 1) for k in order]
    return [(k, c) for k in order for c in (0, 1)]


def _unit_of(key):
    return 'kcal' if key == 'calories' else 'mg' if key == 'sodium' else 'g'


def _align_on(text, ss, order, column_major):
    """把讀到的數字序列**對齊**到表格格子，允許缺口與雜訊。

    這是 `_sequence_on` 的正解版。那支只認「大卡後面連著 N 個公克」這種
    完整無缺口的串，可是真實輸出到處是洞——某一格沒被偵測到、單位讀壞、
    中間插進地址電話的數字，整段就報廢。實測 133/186 的剩餘失誤是
    「候選池裡根本沒有正確值」，就是這樣掉的。

    改成序列對齊之後，缺一格只是付一次跳過的代價，不會拖垮整段：
      dp[i][j] = 前 i 個格子對上前 j 個數字的最佳分數
        對上   dp[i][j] + 1        （單位要相容）
        跳過數字 dp[i][j] - 0.6      （這個數字不屬於營養表）
        跳過格子 dp[i][j] - 0.4      （這一格沒印或沒讀到）
    跳過數字比跳過格子貴一點：漏讀比誤收常見，寧可承認沒讀到。
    """
    obs = _units(text)
    slots = _slots(order, column_major)
    n, m = len(obs), len(slots)
    if not n:
        return {}
    NEG = float('-inf')
    dp = [[NEG] * (n + 1) for _ in range(m + 1)]
    bt = [[None] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for i in range(m + 1):
        for j in range(n + 1):
            if dp[i][j] == NEG:
                continue
            if i < m and j < n and _unit_of(slots[i][0]) == obs[j][1]:
                if dp[i][j] + 1 > dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = dp[i][j] + 1
                    bt[i + 1][j + 1] = ('m', i, j)
            if j < n and dp[i][j] - 0.6 > dp[i][j + 1]:
                dp[i][j + 1] = dp[i][j] - 0.6
                bt[i][j + 1] = ('o', i, j)
            if i < m and dp[i][j] - 0.4 > dp[i + 1][j]:
                dp[i + 1][j] = dp[i][j] - 0.4
                bt[i + 1][j] = ('s', i, j)
    got = {}
    i, j = m, n
    while bt[i][j]:
        kind, pi, pj = bt[i][j]
        if kind == 'm':
            key, col = slots[pi]
            got.setdefault(key, [None, None])[col] = obs[pj][0]
        i, j = pi, pj
    return {k: tuple(v) for k, v in got.items() if any(x is not None for x in v)}


def _by_unit(text, ss):
    """靠**單位的專屬性**定欄位：營養標示裡只有熱量用大卡、只有鈉用毫克。

    這是整批規則裡最強的一條，因為它完全不依賴欄位名被讀對、也不依賴
    版面順序——只要那個數字連著單位被讀出來就抓得到。

    序列掃描（_sequence_on）會漏掉它們：c41_綠巨人珍珠玉米 的 `71大卡` 與
    `100大卡` 分屬兩欄，但第二欄後面只剩一個公克值，串長不足被整段丟棄，
    連帶把讀對的熱量一起丟了。c38_科學麵 的 `720毫克 / 1800毫克` 同理，
    而 1800×40/100 = 720 完全吻合份量——這種對子幾乎不可能是巧合。

    配對方式是列出所有有序組合、留下滿足算術關係的。只有一個值時不猜
    它屬於哪一欄，一律當每份（法規的印刷順序，每份在前）。
    """
    seq = _units(text)
    out = {}
    for key, unit in (('calories', 'kcal'), ('sodium', 'mg')):
        vs = [v for v, u in seq if u == unit]
        if not vs:
            continue
        pairs = [(a, b) for i, a in enumerate(vs) for j, b in enumerate(vs)
                 if i != j and _consistent((a, b), ss)]
        out[key] = pairs[0] if pairs else (vs[0], None)
    return out


def load_views(rec, cid):
    """把同一案切成多個「視角」，每個視角是一段可獨立解析的文字。

    起因是 c04_比菲多：它有兩張圖，第一張拍到瓶身曲面、讀出來整段亂碼，
    第二張是攤平的標籤、`碳水化合物69.7公克 14.8公克` 幾乎逐字正確。
    但 base_lines 是把兩張圖串在一起的，regex 先配到亂碼那段就停了，
    乾淨的那份根本沒機會被看到。

    視角之間會互相矛盾，這是好事——有矛盾才有得選。仲裁交給算術約束。
    排序即優先序：精確度高的在前，涵蓋廣的在後。
    """
    views = [('block', N.nutrition_block(rec))]
    # 裁切重讀那一遍（crop_reread.py）排在最前面：它看到的營養表字高是
    # 整張圖那遍的好幾倍，同一格數字它讀對的機率明顯較高。
    # c02_濃豆漿 是最清楚的例子——整張圖讀出 `191公克 79公 21公`
    #（小數點整批掉），裁切後是 `19.1公克 7.9公克 60毫克 16毫克`。
    cp = os.path.join(HERE, 'out', 'nutrition_crop', f'{cid}.json')
    if os.path.exists(cp):
        d = json.load(open(cp, encoding='utf-8'))
        for i, im in enumerate(d.get('images') or []):
            txt = ' '.join(l['text'] for l in im.get('lines', []))
            if re.search(r'熱量|热量|每100|營養標示|大卡', txt):
                views.append((f'crop{i}', txt))
    for i, t in enumerate(rec.get('table_html_text') or []):
        if t and re.search(r'熱量|每100|每一份量', t):
            views.append((f'table{i}', t))
    p = os.path.join(HERE, 'out', BOXES, f'{cid}.json')
    if os.path.exists(p):
        d = json.load(open(p, encoding='utf-8'))
        for i, im in enumerate(d['images']):
            txt = ' '.join(l['text'] for l in im.get('lines', []))
            if re.search(r'熱量|每100|營養標示', txt):
                views.append((f'img{i}', txt))
    views.append(('full', N.full_text(rec)))
    return views


def _pairs_from(text, ss=None):
    """一段文字 →〈欄位: 候選 pair 清單〉。三種讀法都收：

    regex（靠欄位名）、window（靠欄位名前後）、sequence（靠法定欄序）。
    三者的失效情境不重疊——這是它們該同時存在的理由，不是冗餘。
    """
    got = collections.defaultdict(list)
    # 這裡曾經有 `N.parse(text)`——就是最早那個「抓欄位名往右取值」的解析器，
    # 單獨跑 538/878。它現在**拿掉反而更好**（697→701，再連同 parse_record
    # 一起拿掉是 702）。不是它突然變爛，是前後窗口與序列對齊已經完全涵蓋
    # 它讀得到的東西，而它多出來的候選是錯的：右邊第一個數字未必屬於這個欄位。
    # 消融的意義就在這裡——沒量過就不會發現當初的主力已經變成負擔。
    for k, v in _window_on(text, ss).items():
        if v not in got[k]:
            got[k].append(v)
    for k, v in _by_unit(text, ss).items():
        if v not in got[k]:
            got[k].append(v)
    for order in (None, CANON_FIBER):
        for rev in (False, True):
            for k, v in _sequence_on(text, ss, order, rev).items():
                if v not in got[k]:
                    got[k].append(v)
    # 序列對齊的結果**不放進池子**，只當整表起點（見 _whole_table_starts）。
    # 放進來實測 692→685：它填得滿、逐欄位看每一格都像樣，
    # 爬山就會東拿一格西拿一格，把 c24（16→8）、c55（16→10）這種
    # 本來全對的案例拆掉。它的價值在整表一致，拆散就沒了。
    return got


def p_seq(rec, cid):
    ss = V.parse_serving_size(N.full_text(rec)) or \
        V.infer_serving_size(N.parse_record(rec))
    return _sequence_on(N.nutrition_block(rec), ss) or \
        _sequence_on(N.full_text(rec), ss)


def _candidates(rec, cid):
    """把一案的所有讀法攤成〈欄位 → 候選 pair 清單（依可信度排序）〉。"""
    A = N.parse_record(rec)
    B = G.parse_boxes(load_boxes(cid))
    ss = _serving_size(rec, (A, B))
    pool = collections.defaultdict(list)
    # ── 表格欄位（rank -1，優先於所有攤平式讀法）──────────────────────────
    # 模型已經吐出的 markdown 表格，按欄位位置取值。放在最前面是因為它是
    # 唯一保留了行列關係的來源；其餘五路都是攤平後靠字元距離猜的。
    # 2026-09-09 診斷：482 格缺口有 330 格發生在「表格已經是乾淨三欄」的
    # 131 案上，錯法是整體錯開一列（見 [[07-營養表結構化解析實驗]]）。
    if USE_TABLE:
        lines = (rec.get('base_lines') or []) + (rec.get('struct_lines') or [])
        for k, v in TM.pairs_from_lines(lines).items():
            pool[k].append((-1, v))
    rank = 0
    for _, text in load_views(rec, cid):
        for k, vs in _pairs_from(text, ss).items():
            for v in vs:
                pool[k].append((rank, v))
        rank += 1
    # A（nutrition_pipeline.parse_record）同樣已被取代，只留著算份量。
    # geom 則還有價值：拿掉它 702→689，它看得到座標，是唯一不靠文字順序的來源。
    for k, v in B.items():
        pool[k].append((rank + 1, v))
    # 序列對齊排在**最後**：它是補洞用的，不該和讀得到欄位名的來源競爭。
    # 試過讓它平起平坐（692→685）、只當整表起點（→657），都更差。
    # 它強在「別人整段讀不出來」時還能給出一整張表，弱在單看一格沒有說服力。
    for _, text in load_views(rec, cid):
        for order in (CANON, CANON_FIBER):
            for cm in (False, True):
                for k, v in _align_on(text, ss, order, cm).items():
                    pool[k].append((rank + 2, v))
    out = {}
    for k, cands in pool.items():
        # 記票：同一個 pair 被幾種讀法各自產生出來。去重時把票丟掉太可惜——
        # regex、前後窗口、序列對齊三者互相獨立，都指向同一組數字時，
        # 那是遠比「某一種讀法覺得它一致」更強的證據。
        votes = collections.Counter(v for _, v in cands)
        seen, uniq = set(), []
        for r, v in sorted(cands, key=lambda x: x[0]):
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        out[k] = uniq
        VOTES[k] = votes
    return out, ss


# 多來源票數。試過拿它當 _score 的加分項（同一組數字被 regex／窗口／對齊
# 各自產生出來，直覺上更可信），實測權重 0.15/0.3/0.6/1.0 分別是
# 689/690/680/657，全部比不加（692）差。理由事後看很清楚：票數高的多半
# 是「好讀的欄位」，那些本來就不會錯；真正難的欄位只有一種讀法給得出答案，
# 加票數等於系統性地懲罰它們。留著這份記錄，別再試一次。
VOTES = {}

# 表格欄位候選的開關。
# 2026-09-09 起**線上組態為開**（`emit_json.py` 明確設定，見 [[07-營養表結構化解析實驗]]）：
# 營養格 2121 → 2347，配對 bootstrap +8.7 點、CI +6.0 ～ +11.6、不跨 0。
# 這裡的模組預設維持關閉，讓任何直接 import 本檔的舊腳本行為不變；
# `p_fill`／`p_table` 兩個具名解析器各自硬寫死關／開，不受預設影響。
USE_TABLE = False


def _pick(uniq, ss):
    """單一欄位的貪婪選擇：先看算術約束，再看完整度。"""
    good = [v for v in uniq if _consistent(v, ss)]
    if good:
        return good[0]
    return sorted(uniq, key=lambda c: -sum(1 for x in c[:2] if x is not None))[0]


# 四大卡九大卡：蛋白質與碳水 4 kcal/g、脂肪 9 kcal/g。
# 這在 GT 上驗過——108 組欄位裡 106 組落在 ±10%，偏差中位數 0.1%。
# 唯一的例外是 c23_in果凍（含糖醇，熱量係數不是 4），所以它是「加分項」
# 不是「否決條件」：吻合就大幅加分，不吻合只是沒加分，不排除該組合。
ATWATER = {'protein': 4.0, 'fat': 9.0, 'carbohydrates': 4.0}
# 包含關係：飽和脂肪與反式脂肪都是脂肪的一部分，糖與膳食纖維是碳水的一部分。
# 違反就是**確定**配錯了，不像熱量公式有例外。
SUBSET = [('saturated_fat', 'fat'), ('trans_fat', 'fat'),
          ('sugar', 'carbohydrates'), ('fiber', 'carbohydrates')]


def _atwater_ok(vals, i):
    """第 i 欄（0=每份 1=每100）的熱量與三大營養素對不對得上。"""
    c = (vals.get('calories') or (None, None))[i:i + 1]
    c = c[0] if c else None
    if c is None or c <= 0:
        return None
    tot = 0.0
    for k, coef in ATWATER.items():
        p = vals.get(k)
        v = p[i] if p and i < len(p) else None
        if v is None:
            return None
        tot += coef * v
    return abs(tot - c) / c <= 0.10


# 糖醇的熱量係數不是 4 kcal/g（赤藻糖醇在台灣法規算 0，其餘多為 2.4），
# 所以含糖醇的產品套四大卡九大卡公式對不上——GT 上唯一不吻合的
# c23_in果凍 正是含赤藻糖醇。
#
# **但實測是零效果**：接上去（含糖醇就把熱量公式關掉）697→697、全對 27→27。
# 這條規則在 6 案觸發，而那幾案的勝負本來就不是熱量公式決定的。
# 道理上成立不等於有用；沒有量到效果就不接，免得在新資料上誤觸發。
# 留著常數與 _score 的 atwater 參數，是為了記錄這件事試過了。
SUGAR_ALCOHOL = re.compile(r'糖醇|赤藻|山梨醇|木糖醇|甘露醇|麥芽糖醇|麦芽糖醇')


def _score(vals, ss, atwater=True):
    """一整組欄位指派的可信度。分數只用來排名，不做否決。"""
    s = 0
    for k, p in vals.items():
        a, b = (p + (None,))[:2]
        if _consistent(p, ss):
            s += 2
        elif a is not None and b is not None and ss:
            # 兩欄都有值卻通不過算術關係——這不是「資訊比較多」，是**配錯的證據**。
            # 原本只給完整度加分不給懲罰，結果 c02_濃豆漿 的蛋白質選了
            # (51.0, 98.0)（來自相鄰兩列的數字）而不是 (19.1, None)，
            # 只因為前者兩欄都有值、多 0.25 分。誠實的單值好過亂配的雙值。
            s -= 1.0
        s += sum(1 for x in p[:2] if x is not None) * 0.25
    if atwater:
        for i in (0, 1):
            if _atwater_ok(vals, i):
                s += 4
    for sub, sup in SUBSET:
        a, b = vals.get(sub), vals.get(sup)
        if not a or not b:
            continue
        for i in (0, 1):
            x = a[i] if i < len(a) else None
            y = b[i] if i < len(b) else None
            # 容一點餘裕：0.1 級距的四捨五入會讓「糖=碳水」正好相等
            if x is not None and y is not None and x > y + 0.15:
                s -= 3
    s -= DUP_W * _dup_count(vals)
    return s


def _dup_count(vals):
    """同一個數字被幾個欄位重複認領。

    標示上每一格印的是不同的數字，一個值被兩個欄位同時認領必有一個是錯的。
    這件事之前完全沒管——實測 57 案裡 56 案有重複認領，牽涉 109 格錯誤。
    c46_雙拼起司紅醬焗飯 最清楚：碳水與糖都拿到 `(93.1, 1.5)`，
    那是同一列的數字被兩個欄位各認領一次。

    真正的形式是**指派問題**（每個印出來的數字最多給一個欄位），
    但零是例外——反式脂肪與糖同時是 0 很常見，所以零不計入。
    這裡用軟性懲罰而非硬性禁止：OCR 本來就可能把兩格讀成同一個數字，
    硬禁會逼求解器去選更差的候選。
    """
    seen = collections.Counter()
    for p in vals.values():
        for x in (p + (None,))[:2]:
            if x:                      # 0 與 None 都跳過
                seen[x] += 1
    return sum(c - 1 for c in seen.values() if c > 1)


DUP_W = 0          # 實測 >0 都變差，見 _dup_count
START_MARGIN = 2.0  # 見 p_solve：整表換假設要跨過的門檻


def p_solve(rec, cid):
    """**整表**一起解，不再逐欄位各自決定。

    到 pool 為止每個欄位是獨立仲裁的，這放棄了欄位之間的關係。但營養標示
    的欄位彼此是綁死的：熱量 = 4×蛋白質 + 9×脂肪 + 4×碳水（GT 上 108 組
    有 106 組落在 ±10%，偏差中位數 0.1%），而且飽和脂肪不可能大於脂肪、
    糖不可能大於碳水。

    這些關係補上了算術約束做不到的事——`每份 = 每100 × 份量/100` 只能驗證
    同一欄位的兩個數字，換掉整欄還是通過；熱量公式則是**跨欄位**的，
    蛋白質和脂肪的值對調就會立刻不成立。剩下的失誤有 154 格是「值讀到了
    但配錯欄位」，正是這種錯。

    做法是先貪婪取一組，再對熱量公式牽涉到的四個欄位做局部搜尋
    （其餘欄位候選少、貪婪已經夠）。用爬山不用窮舉：候選數乘起來會到
    上萬組，而爬山幾輪就收斂，實測差別在雜訊內。
    """
    pool, ss = _candidates(rec, cid)
    # `_whole_table_starts` 在 2026-08 被停用（下方註解講的是另一件事——
    # 「兩欄對調」的候選），停用後它就成了死碼：START_MARGIN 掃 0～8
    # 完全不影響結果，因為 starts 永遠是空的。2026-09-07 改成可開關，
    # 用 vlcrop 的候選池重測。
    starts = _whole_table_starts(rec, cid, ss) if os.environ.get('PARSE_MULTISTART') else []
    # 試過再補一份「兩欄對調」的候選讓分數決定方向：689→685。
    # 對調版本幾乎總能找到某個約束勉強成立，等於給每個欄位一次
    # 「亂猜也可能加分」的機會，雜訊大於資訊。方向留給 _sequence_on
    # 用整表投票決定，那裡的證據是整欄一致的，可靠得多。
    greedy = {k: _pick(v, ss) for k, v in pool.items()}
    best_vals, best = None, float('-inf')
    for si, start in enumerate([greedy] + starts):
        vals = dict(greedy)
        vals.update(start)
        sc = _score(vals, ss)
        # 整表改用另一種版面假設是很大的動作，要有明顯證據才做。
        # 沒設這個門檻時實測 692→685：對齊把 c24（16→8）、c55（16→10）
        # 這些**本來就對**的案例弄壞了，因為它填得比較滿、靠完整度加分
        # 就贏過了正確答案。加分項的平手不足以推翻一整張表。
        if si:
            sc -= START_MARGIN
        # 爬山：每輪讓每個欄位單獨試遍自己的候選，收下有進步的
        for _ in range(6):
            moved = False
            for k in list(vals):
                for cand in pool.get(k, [])[:16]:
                    if cand == vals[k]:
                        continue
                    trial = dict(vals, **{k: cand})
                    s = _score(trial, ss)
                    if s > sc:
                        vals, sc, moved = trial, s, True
            if not moved:
                break
        if sc > best:
            best_vals, best = vals, sc
    return best_vals


def _whole_table_starts(rec, cid, ss):
    """整表一致的假設，每一個都當爬山的起點。

    序列對齊產生的是**一整張表**的指派，欄位之間彼此呼應。把它打散成
    逐欄位的候選丟進池子就毀掉了這個性質——實測那樣做候選覆蓋率
    745→778 有進步，但仲裁流失反而從 53 漲到 91，淨值是負的。

    爬山只能一次動一個欄位，跨不過「整張表換一種版面假設」這種距離。
    所以改成多起點：每個假設各爬一次，取最好的。起點便宜，爬山也便宜。
    """
    out = []
    for _, text in load_views(rec, cid):
        for order in (CANON, CANON_FIBER):
            for cm in (False, True):
                a = _align_on(text, ss, order, cm)
                if a:
                    out.append(a)
    return out


def _fill(vals, ss, dv=False):
    """只讀到一欄時，用份量把另一欄推回來。

    這是**推導不是讀取**。法規保證兩欄之間就是 `每份 = 每100 × 份量/100`，
    所以推出來的值與標示上印的相同（差在四捨五入）。但它終究不是從影像上
    看到的，錯誤會沿著份量傳播——份量抓錯一次，整張表的推導欄全錯。
    所以回傳時標記 derived，讓呼叫端能選擇只採信讀到的值。

    ⚠ **`dv=True` 時完全不推導每 100 公克那一欄。** 那個「法規保證」只在
    格式一成立；法規也允許格式二（每份 ＋ 每日參考值百分比），那種商品
    **標示上根本沒有每 100 公克**，推出來的值在包裝上不存在。
    2026-09-07 實測：177 案有 25 案是格式二，換上讀得到表格的 vlcrop 之後，
    這裡憑空推出 **216 格**標示上沒有的數值（PP-OCR 讀不到那張表所以只有 27 格，
    看起來反而乾淨）。正解那一欄本來就是 null，擋掉不損失任何正確格。
    """
    if not ss:
        return vals, set()
    out, derived = {}, set()
    for k, p in vals.items():
        a, b = (p + (None,))[:2]
        if a is not None and b is None and not dv:
            b = round(a * 100 / ss, 1)
            derived.add((k, 1))
        elif b is not None and a is None:
            a = round(b * ss / 100, 1)
            derived.add((k, 0))
        out[k] = (a, b)
    return out, derived


def _is_dv(rec):
    """這張表的第二欄是不是「每日參考值百分比」（格式二）。"""
    t = N.norm(N.full_text(rec)) + N.norm(N.nutrition_block(rec))
    return bool(N.DV_HEADER.search(t))


def _fill_impl(rec, cid):
    vals = p_solve(rec, cid)
    _, ss = _candidates(rec, cid)
    return _fill(vals, ss, _is_dv(rec))[0]


def p_fill(rec, cid):
    """⚠ **基準線，永遠不含表格欄位那一路。**

    `USE_TABLE` 的模組預設是給線上組態（`emit_json.py`）用的，會是開的。
    但 `fill` 在報告裡的身分是「加表格之前的狀態」，若它跟著預設走，
    [[07-營養表結構化解析實驗]] 的 2121 vs 2347 就會在某次改預設之後
    悄悄變成 2347 vs 2347 而且不報錯。所以這裡硬寫死關閉。"""
    global USE_TABLE
    saved, USE_TABLE = USE_TABLE, False
    try:
        return _fill_impl(rec, cid)
    finally:
        USE_TABLE = saved


def p_pool(rec, cid):
    """候選池 + 約束仲裁。

    前面每加一種版面就補一條規則，規則之間會互相打架（實測「誤差最小者勝」
    比「門檻式」還差 18 格）。改成把所有讀法都當**候選**丟進池子，
    再讓法規的算術關係 `每份 = 每100 × 份量/100` 做唯一的裁判。
    加一種讀法只是多幾個候選，不會破壞既有的——這是能持續疊加的結構。
    """
    A = N.parse_record(rec)
    B = G.parse_boxes(load_boxes(cid))
    ss = (V.parse_serving_size(N.full_text(rec))
          or V.infer_serving_size(A) or V.infer_serving_size(B))

    pool = collections.defaultdict(list)          # 欄位 → [(優先序, pair)]
    rank = 0
    for _, text in load_views(rec, cid):
        for k, vs in _pairs_from(text, ss).items():
            for v in vs:
                pool[k].append((rank, v))
        rank += 1
    # A（nutrition_pipeline.parse_record）同樣已被取代，只留著算份量。
    # geom 則還有價值：拿掉它 702→689，它看得到座標，是唯一不靠文字順序的來源。
    for k, v in B.items():
        pool[k].append((rank + 1, v))

    out = {}
    for k, cands in pool.items():
        seen, uniq = set(), []
        # 只依優先序排，不比 pair 本身——pair 裡有 None，會炸
        for r, v in sorted(cands, key=lambda x: x[0]):
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        good = [v for v in uniq if _consistent(v, ss)]
        if good:
            out[k] = good[0]
        else:
            uniq.sort(key=lambda c: -sum(1 for x in c[:2] if x is not None))
            out[k] = uniq[0]
    return out


# ─── 消融 ────────────────────────────────────────────────────────────────────
# 加了六種讀法與三條約束之後，「哪一項真的有用」不能用直覺回答。
# 逐項關掉重跑，才知道每一項各值多少格——也才知道哪一項可以拿掉。
def _score_without(cons=True, atw=True, sub=True):
    def f(vals, ss, atwater=True):
        s = 0
        for _, p in vals.items():
            a, b = (p + (None,))[:2]
            # 獎勵與懲罰是同一條約束的兩面，要關要一起關——
            # 只關獎勵會讓每個雙值對都白扣 1 分，測出來的是「解析器被迫
            # 只敢輸出單值」而不是「拿掉份量算術的影響」（實測會掉到 381，
            # 那個數字沒有意義）。
            if cons:
                if _consistent(p, ss):
                    s += 2
                elif a is not None and b is not None and ss:
                    s -= 1.0
            s += sum(1 for x in p[:2] if x is not None) * 0.25
        if atw:
            for i in (0, 1):
                if _atwater_ok(vals, i):
                    s += 4
        if sub:
            for a_, b_ in SUBSET:
                a, b = vals.get(a_), vals.get(b_)
                if not a or not b:
                    continue
                for i in (0, 1):
                    x = a[i] if i < len(a) else None
                    y = b[i] if i < len(b) else None
                    if x is not None and y is not None and x > y + 0.15:
                        s -= 3
        return s
    return f


def _pairs_without(regex=False, window=True, seq=True, unit=True):
    def f(text, ss=None):
        got = collections.defaultdict(list)
        if regex:
            for k, v in N.parse(text).items():
                got[k].append(v)
        for on, fn in ((window, lambda: _window_on(text, ss)),
                       (unit, lambda: _by_unit(text, ss))):
            if not on:
                continue
            for k, v in fn().items():
                if v not in got[k]:
                    got[k].append(v)
        if seq:
            for order in (None, CANON_FIBER):
                for rev in (False, True):
                    for k, v in _sequence_on(text, ss, order, rev).items():
                        if v not in got[k]:
                            got[k].append(v)
        return got
    return f


def ablate(cases):
    """跑一次會印出各項的貢獻。數字見 README 與各函式的註解。

    這支存在的理由：加到六種讀法、四條約束之後，「哪一項真的有用」
    已經不能用直覺回答了。每次改完跑一次，才知道新加的東西是不是
    只是把別的東西的功勞搶過來。
    """
    global _score, _pairs_from
    o_s, o_p = _score, _pairs_from

    def measure():
        hit = tot = full = 0
        for rec, gt in cases:
            ok, ch, _ = score_one(p_solve(rec, rec['case_id']) or {}, gt)
            hit += ok; tot += ch; full += (ok == ch)
        return hit, tot, full

    for title, setter, rows in (
        ('約束', '_score', [('全部', {}), ('拿掉 熱量公式', dict(atw=False)),
                            ('拿掉 包含關係', dict(sub=False)),
                            ('拿掉 份量算術', dict(cons=False)),
                            ('三個都拿掉(純貪婪)', dict(cons=False, atw=False, sub=False))]),
        ('候選來源', '_pairs_from',
         [('全部', {}), ('拿掉 單位專屬(大卡/毫克)', dict(unit=False)),
          ('拿掉 法定欄序序列', dict(seq=False)),
          ('拿掉 欄位名前後窗口', dict(window=False)),
          ('加回 regex 欄位名（已淘汰）', dict(regex=True))]),
    ):
        print(f'\n{title:<26}{"正確":>6}{"%":>8}{"全對":>7}')
        for name, kw in rows:
            if setter == '_score':
                _score = _score_without(**kw)
            else:
                _pairs_from = _pairs_without(**kw)
            h, t, f = measure()
            print(f'  {name:<24}{h:>6}{h / t * 100:>7.1f}%{f:>7}')
            _score, _pairs_from = o_s, o_p


def p_table(rec, cid):
    """= p_fill，但候選池多一路「markdown 表格欄位」。只差這一個變因。"""
    global USE_TABLE
    saved, USE_TABLE = USE_TABLE, True
    try:
        return _fill_impl(rec, cid)
    finally:
        USE_TABLE = saved


PARSERS = {
    'regex': p_regex,
    'geom': p_geom,
    'merge': p_merge,
    'window': p_window,
    'merge3': p_merge3,
    'seq': p_seq,
    'pool': p_pool,
    'solve': p_solve,
    'fill': p_fill,
    'table': p_table,
}


def run(name, cases):
    fn = PARSERS[name]
    tot = hit = 0
    per = []
    for rec, gt in cases:
        got = fn(rec, rec['case_id']) or {}
        ok, ch, bad = score_one(got, gt)
        if not ch:
            continue
        tot += ch; hit += ok
        per.append((rec['case_id'], ok, ch, bad))
    return hit, tot, per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detail', default=None)
    ap.add_argument('--worst', type=int, default=0)
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--ablate', action='store_true',
                    help='逐項關掉約束與候選來源，量各自值多少格')
    a = ap.parse_args()
    cases = load_cases()

    if a.ablate:
        ablate(cases)
        return

    results = {}
    for name in (a.only or PARSERS):
        results[name] = run(name, cases)

    # 逐案取較佳（上限參考）
    byc = collections.defaultdict(dict)
    for name, (_, _, per) in results.items():
        for cid, ok, ch, bad in per:
            byc[cid][name] = (ok, ch, bad)
    best = sum(max(v[0] for v in d.values()) for d in byc.values())
    total = sum(next(iter(d.values()))[1] for d in byc.values())

    print(f'{"解析器":<12}{"正確":>7}{"應有":>7}{"%":>7}{"全對案例":>10}')
    for name, (hit, tot, per) in sorted(results.items(), key=lambda x: -x[1][0]):
        full = sum(1 for _, ok, ch, _ in per if ok == ch)
        print(f'{name:<12}{hit:>7}{tot:>7}{hit / tot * 100:>6.1f}%{full:>10}')
    print(f'{"逐案取較佳":<12}{best:>7}{total:>7}{best / total * 100:>6.1f}%'
          f'{sum(1 for d in byc.values() if max(v[0] for v in d.values()) == next(iter(d.values()))[1]):>10}')

    if a.detail:
        for cid, d in byc.items():
            if not cid.startswith(a.detail):
                continue
            print(f'\n═══ {cid} ═══')
            for name, (ok, ch, bad) in d.items():
                print(f'  {name}: {ok}/{ch}')
                for f_, want, got in bad[:12]:
                    print(f'     {f_:<28}GT {want}  →  {got}')
    if a.worst:
        print(f'\n最差的 {a.worst} 案（取兩者較佳）：')
        rank = sorted(byc.items(), key=lambda x: max(v[0] for v in x[1].values())
                      - next(iter(x[1].values()))[1])
        for cid, d in rank[:a.worst]:
            ch = next(iter(d.values()))[1]
            print(f'   {cid:<28}{max(v[0] for v in d.values()):>3}/{ch:<4}'
                  + '  '.join(f'{n}={v[0]}' for n, v in d.items()))


if __name__ == '__main__':
    main()
