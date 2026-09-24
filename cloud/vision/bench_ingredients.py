#!/usr/bin/env python3
# 成分清單抽取的快速評分台。與 bench_parse.py 同一個模式：吃已存的 OCR 結果，
# 不碰 GPU，改邏輯後秒級拿到成績。
#
# 為什麼需要這一步：score_ocr.py 量的是「GT 的成分名有沒有出現在 OCR 文字裡」，
# 那是**上限**——它證明字讀到了，但下游 module_a 要的是 ingredients_list，
# 一項一項的清單。從一大坨文字切成正確的清單是另一個問題，而且沒被量過。
#
# 難點不是切分本身，是三件事疊在一起：
#   1. 成分段在哪——OCR 輸出裡混著營養表、地址、行銷文案
#   2. 行序——PP-OCR 依框的位置輸出，多欄排版時「水、蔗糖」與後半段可能隔很遠
#   3. 括號——巢狀配方要整團保留成一項，而 OCR 常把「(」讀丟
#
# 指標同時看兩邊，缺一不可：
#   召回 = GT 的項目有多少被抽出來（漏了下游就查不到那個添加物）
#   精確 = 抽出來的項目有多少是真的（多切一刀會生出「醋酸鈉」「無水」兩個假項目，
#          下游會拿去比對資料庫，比對到就是誤報）
#
# 用法：
#   python bench_ingredients.py
#   python bench_ingredients.py --detail c09
#   python bench_ingredients.py --worst 10
import argparse
import collections
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# 讀哪一組 OCR 輸出。可用 --boxes 覆蓋——2026-09-06 測試集擴到 177 案時，
# 這裡寫死在只有 57 案的舊目錄，跑出來的還是舊數字而且不會報錯。
BOXES = 'v6_hires__boxth0.4'


def load_cases():
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    out = []
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        if not gt.get('ingredients_list'):
            continue
        p = os.path.join(HERE, 'out', BOXES, f'{cid}.json')
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding='utf-8'))
        out.append((cid, d, gt))
    return out


def gemini_list(cid):
    p = os.path.join(S.EVAL_ROOT, 'predictions', f'{cid}.json')
    if not os.path.exists(p):
        return None
    pr = json.load(open(p, encoding='utf-8')).get('prediction') or {}
    return pr.get('ingredients_list')


# ─── 評分 ────────────────────────────────────────────────────────────────────
def _match(a, b):
    """兩個成分名算不算同一項。容 len//4 個字的差異，與 score_ocr 一致。"""
    ka, kb = S.normalize(a, fold_variants=True), S.normalize(b, fold_variants=True)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    tol = max(1, min(len(ka), len(kb)) // 4)
    if abs(len(ka) - len(kb)) > tol:
        return False
    d, _ = S.substring_edit(ka, kb)
    return d <= tol


def score_one(got, gt_list):
    """回傳 (命中, GT數, 抽出數, 漏掉的, 多出來的)。一對一配對，不重複計。"""
    used = set()
    hit, missed = 0, []
    for g in gt_list:
        for i, p in enumerate(got):
            if i in used:
                continue
            if _match(g, p):
                used.add(i)
                hit += 1
                break
        else:
            missed.append(g)
    spurious = [p for i, p in enumerate(got) if i not in used]
    return hit, len(gt_list), len(got), missed, spurious


# ─── 抽取器 ──────────────────────────────────────────────────────────────────
OPEN, CLOSE = '([{（〔【[｛', ')]}）〕】]｝'
SEP = '、,，;；·'
# 成分段的起點與終點。終點很重要——不切掉的話營養表、地址、保存期限
# 全會被當成成分項切進來，精確度直接崩掉。
# 起點的寫法必須容忍「第一個字被讀丟」。實測 11 案完全抓不到成分段，
# 原因幾乎都是這個：c02 讀成 `料：水、非基因改造`（原字沒了）、
# c52 是 `料：水、葡萄濃縮汁`。標示上「原料」「成分」通常直排或字體較小，
# 掉一個字是常態。所以額外收「單獨一個料字後面接冒號」——它會多配到
# 「香料：」「調味料：」，但候選段落本來就是多產生再挑，多幾個不吃虧。
# 另外簡繁都要寫：v6 輸出簡體，`內容物` 會是 `内容物`。
# 「分：」單獨收也是同一個道理，但成因不同：飲料紙盒常把「品名：」與
# 「成分：」直排並列，OCR 讀成 `品成 名：義美厚奶茶 分：水、生奶、奶粉…`
# ——「成」跟「分」被拆到不同的框，中間夾了整個品名。c03_厚奶茶 就是這樣
# 整案漏光的。
HEAD = re.compile(
    r'(?:成\s*[分份]|原\s*料|配\s*料|[內内]\s*容\s*物|材\s*料)\s*[:：.．…·]?'
    # 單獨的「料」標點可選，「分」則必須跟著標點。這個不對稱是量出來的：
    # 料 可選→必填會掉 2.5 個百分點（c02 讀出來是 `料 水、非基因改造`，
    # 連冒號都沒讀到）；而 bare 分 太常見（水分、每份、成分本身），
    # 不要求標點會產生一堆垃圾候選段。
    r'|(?:(?<![香調味佐])料)\s*[:：.．…·]?'
    r'|(?:(?<![成公每份水])分)\s*[:：.．…·]')


TAIL = re.compile(r'營養標示|营养标示|每一份量|有效日期|保存期限|保存方法|保存條件|'
                  r'淨重|净重|內容量|内容量|過敏原|过敏原|製造|产地|原產地|廠址|'
                  r'地址|電話|客服|服務專線|本產品|品名|重量|公司')


def full_text(d, order='raw'):
    """把所有框的文字串起來。order 決定串接順序。

    raw    = PP-OCR 給的順序
    reading= 依框的位置重排成閱讀順序（先分列再由左到右）

    行序是這題的難點之一：成分是一長串文字，被切成幾十個框之後，
    「水、蔗糖」與後半段在輸出裡可能隔很遠。DB 的後處理有自己的排序，
    未必等於人閱讀的順序。
    """
    lines = [l for im in d['images'] for l in im.get('lines', []) if l.get('text')]
    if order == 'reading':
        with_box = [l for l in lines if l.get('box')]
        if with_box:
            hs = sorted((max(p[1] for p in l['box']) - min(p[1] for p in l['box']))
                        for l in with_box)
            row = max(8, hs[len(hs) // 2] * 0.6)
            def key(l):
                ys = [p[1] for p in l['box']]; xs = [p[0] for p in l['box']]
                return (round(sum(ys) / len(ys) / row), min(xs))
            lines = sorted(with_box, key=key) + [l for l in lines if not l.get('box')]
    return ' '.join(l['text'] for l in lines)


def _rect(b):
    xs = [p[0] for p in b]
    ys = [p[1] for p in b]
    return min(xs), min(ys), max(xs), max(ys)


def blocks(d, gap_mul=1.6):
    """用框的座標把版面切成**區塊**，再在區塊內排行序。

    先前試過的「全域 y 排序再 x 排序」更差（F1 66.0→64.2），但那個結論
    不該推廣成「座標沒用」——失敗的是那個做法，不是這個資訊。
    全域排序會把並排的兩欄交錯成「左一句右一句」，而食品包裝正是
    多欄排版：成分表、營養表、地址、行銷文案各佔一塊。

    改成先分群：兩個框如果距離小於行高的 gap_mul 倍就算同一塊，
    傳遞閉包成連通分量。成分段在版面上本來就是一塊連續文字，
    分群之後它會自成一個區塊，不必再靠「成分」兩個字去猜邊界。

    區塊內部再做 y 分列、列內 x 排序——這時候只有一欄，全域排序的
    交錯問題就不存在了。
    """
    lines = [l for im in d['images'] for l in im.get('lines', [])
             if l.get('box') and l.get('text')]
    if not lines:
        return []
    R = [_rect(l['box']) for l in lines]
    hs = sorted(r[3] - r[1] for r in R)
    h = max(6.0, hs[len(hs) // 2])
    gap = h * gap_mul

    # 併集尋找。框數幾百，O(n^2) 完全夠用
    parent = list(range(len(R)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(R)):
        ax0, ay0, ax1, ay1 = R[i]
        for j in range(i + 1, len(R)):
            bx0, by0, bx1, by1 = R[j]
            dx = max(0, max(ax0, bx0) - min(ax1, bx1))
            dy = max(0, max(ay0, by0) - min(ay1, by1))
            if dx <= gap and dy <= gap:
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b

    groups = collections.defaultdict(list)
    for i in range(len(R)):
        groups[find(i)].append(i)

    out = []
    for idx in groups.values():
        rows = sorted(idx, key=lambda i: (round((R[i][1] + R[i][3]) / 2 / (h * 0.7)),
                                          R[i][0]))
        out.append(' '.join(lines[i]['text'] for i in rows))
    # 大塊在前：成分段通常是版面上字最多的一塊
    out.sort(key=lambda s: -len(s))
    return out


def box_segments(d, max_gap=3.0, max_boxes=40):
    """從命中 HEAD 的**框**往後走，用空間連續性決定段落到哪裡結束。

    這是座標唯一真正比純文字強的地方。目前的 `segments()` 是在一長串
    串接好的字元上開視窗，結尾靠關鍵詞（營養標示／有效日期…）切斷；
    問題是成分段中間常插進不相干的框——c13_紅茶 的成分跨兩個框，
    中間夾了「本產品含咖啡因20mg/100mL」，文字視窗沒辦法跳過它。

    改用框走訪：跳過中間那些「明顯不是成分」的框而不中斷，
    但只要垂直距離超過 max_gap 倍行高就停——那表示離開這一段版面了。

    註：不重排順序。實測 PP-OCR 的輸出**本來就是依座標排好的**
    （相鄰框 y 遞增的比例中位數 77%），自己重排四種寫法全都更差。
    """
    lines = [l for im in d['images'] for l in im.get('lines', [])
             if l.get('text')]
    R = [_rect(l['box']) if l.get('box') else None for l in lines]
    hs = sorted(r[3] - r[1] for r in R if r)
    h = max(6.0, hs[len(hs) // 2]) if hs else 20.0

    out = []
    for i, l in enumerate(lines):
        m = HEAD.search(l['text'])
        if not m:
            continue
        buf = [l['text'][m.end():]]
        prev = R[i]
        for j in range(i + 1, min(i + max_boxes, len(lines))):
            t = lines[j]['text']
            if TAIL.search(t):
                break
            if R[j] and prev:
                dy = R[j][1] - prev[3]
                if dy > max_gap * h:
                    break
                prev = R[j]
            buf.append(t)
        seg = ' '.join(buf)
        if len(S.normalize(seg)) >= 3:
            out.append(seg)
    return out


def p_boxseg(cid, d, gt):
    """用框走訪產生段落，其餘沿用 union 的挑選與聯集。"""
    scored = []
    for seg in box_segments(d):
        items = split_top(seg)
        scored.append((_plausible(items), items))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    out = []
    for s, items in scored:
        if s >= max(1.0, best * UNION_FRAC):
            out.extend(items)
    return _dedupe(out) or _dedupe(max(scored, key=lambda x: x[0])[1])


BOX_SEP = '\x01'


def _text_boxsep(d):
    """把**框的邊界**也標記成分隔符。

    這是座標唯一真的有用的一條，而且用法出乎意料——不是拿來重排順序，
    是拿來當**隱形的頓號**。OCR 常把「、」讀丟，但兩個成分本來就分屬
    不同的框，那個邊界還在。單獨開這一條，召回 62.5%→71.1%。

    代價是精確度崩到 34%：跨行的單一項目會被切成兩半
    （`抗氧化劑(L-抗` ／ `壞血酸鈉)`）。所以不能直接用，
    要當成「額外的候選來源」再過濾（見 p_boxsep）。
    """
    return BOX_SEP.join(l['text'] for im in d['images']
                        for l in im.get('lines', []) if l.get('text'))


# 營養表的 token。**欄位名絕不可單獨成立**——「糖」「鈉」「脂肪」在真成分名裡
# 到處都是（葡萄糖漿、乾酪素鈉、脂肪酸甘油酯），單獨收會把它們從中間挖掉，
# 第一版就是這樣寫的，實測會生出「葡萄 漿」「乾酪素 」「 酸甘油酯」。
# 所以欄位名只有在**緊接著數字＋單位**時才一起刪；否則只刪「數字＋單位」本身。
# 例外是那幾個多字且不可能出現在成分名裡的片語，它們可以單獨刪。
_NUT_FIELD = (r'熱量|蛋白質|飽和脂肪|反式脂肪|碳水化合物|膳食纖維|脂肪|糖|鈉')
_NUT_UNIT = r'(?:公克|毫克|大卡|公絲|毫升|kcal|g|mg)'
NUT_TOKEN = re.compile(
    r'(?:每一份量|本包裝含|每100公克|每100毫升|淨重|净重|內容量|内容量)'
    r'|(?:' + _NUT_FIELD + r')?\s*\d+(?:\.\d+)?\s*' + _NUT_UNIT +
    r'|\d+(?:\.\d+)?\s*%')
NUT_MINLEN = 20


def _denoise_nutrition(items):
    """把項目**內部**交錯進來的營養表 token 剔掉，再重新切一次。

    這些 token 不是 OCR 讀錯，是行序造成的——營養表在包裝上就印在成分欄
    旁邊，PP-OCR 按列排序時把兩欄交錯了。實測 >=40 字的黏連項目裡
    11/13 含這類字樣，例如：
        `調味料(酵母抽出物，[反式脂肪 0.0公克 0.0公克] 柑橘抽出物，雞蛋…`
    刪掉之後被切斷的成分會重新接起來。

    2026-08-31 實測（門檻 20，配對 bootstrap 5000 次）：
        清單層   +0.2 點  95% CI +0.0 ~ +0.6
        添加物層 +0.6 點  95% CI +0.0 ~ +2.1
    門檻是看著全集挑的，所以另做固定半組檢查：A 半組選出 20，在沒看過的
    B 半組給 +1.3，與 B 半組自選最佳相同 —— 沒有過擬合。

    **只刪不改字**，所以輸出字元仍是輸入字元的子集，可機械檢查。
    """
    out = []
    for x in items:
        if len(S.normalize(x)) >= NUT_MINLEN and NUT_TOKEN.search(x):
            y = re.sub(r'\s{2,}', ' ', NUT_TOKEN.sub(' ', x)).strip(' 、,，')
            out.extend(split_top(y) if y else [])
        else:
            out.append(x)
    return [x for x in out if S.normalize(x)]


def p_boxsep(cid, d, gt):
    """dict 的結果，再加上「框邊界當分隔符」抽到的短項目。

    偏召回的操作點。與 p_dict 的取捨（實測）：
        p_dict    召回 62.5%｜精確 73.7%｜F1 67.6
        p_boxsep  召回 68.1%｜精確 65.6%｜F1 66.8
    F1 打平，換 5.6 點召回付 8.1 點精確。要選哪個取決於下游：
    `module_a` 若是精確比對，多出來的碎片配不到添加物、無害；
    若開了模糊或語意比對，`壞血酸鈉)` 可能誤配到「抗壞血酸鈉」而誤報。
    """
    base = p_dict(cid, d, gt)
    saved_ft, saved_sep = globals()['full_text'], globals()['SEP']
    globals()['full_text'] = lambda d_, order='raw': _text_boxsep(d_)
    globals()['SEP'] = saved_sep + BOX_SEP
    try:
        extra = p_union(cid, d, gt) or []
    finally:
        globals()['full_text'], globals()['SEP'] = saved_ft, saved_sep
    keep = [x for x in extra
            if 2 <= len(S.normalize(x)) <= 12 and _looks_like_ingredient(x)]
    return _dedupe(_denoise_nutrition(base + keep))


def p_boxsep_ds(cid, d, gt):
    """= p_boxsep，但字典切分提前到段落篩選之前。只差這一個變因。"""
    global DICT_FIRST
    saved, DICT_FIRST = DICT_FIRST, True
    try:
        return p_boxsep(cid, d, gt)
    finally:
        DICT_FIRST = saved


def _kept_segments(cid, d, gt):
    """p_union 實際採用的那些段落（同樣的評分與門檻，抽出來重用）。"""
    t = full_text(d)
    scored = []
    for seg in segments(t):
        items = split_top(seg)
        if DICT_FIRST:
            items = _dsplit_items(items)
        scored.append((_plausible(items), seg))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    thr = max(1.0, best * UNION_FRAC)
    return [seg for s, seg in scored if s >= thr]


def _kept_sections(cid, d, gt):
    """被採用的段落，**依 HEAD 位置分組**後每個位置合成一段。

    ⚠ 不能直接用 `_kept_segments`：`segments()` 對同一個 HEAD 會產生長短
    兩個候選，兩個都可能過門檻。用它當「段數」會把同一段算成兩段——
    2026-09-09 實測那樣做只換到 +11 命中卻多抽 464 項，F1 70.8 → 66.0。
    版面上的段數是 **HEAD 出現幾次**，不是候選幾個。
    """
    t = full_text(d)
    groups = {}
    for m in HEAD.finditer(t):
        rest = t[m.end():]
        e = TAIL.search(rest)
        cuts = [max(e.start(), 60) if e else 400]
        if e and 3 <= e.start() < 60:
            cuts.append(e.start())
        bestseg, bests = None, None
        for cut in cuts:
            seg = rest[:cut]
            if len(S.normalize(seg)) < 3:
                continue
            items = split_top(seg)
            if DICT_FIRST:
                items = _dsplit_items(items)
            sc = _plausible(items)
            if bests is None or sc > bests:
                bestseg, bests = seg, sc
        if bestseg is not None:
            groups[m.start()] = (bests, bestseg)
    if not groups:
        return []
    best = max(sc for sc, _ in groups.values())
    thr = max(1.0, best * UNION_FRAC)
    return [seg for sc, seg in groups.values() if sc >= thr]


def p_boxsep_dup(cid, d, gt):
    """= p_boxsep，但**跨段落的重複項不去重**。

    為什麼：多段標示（泡麵的麵條／調味包／油包）在正解裡會把同一個成分
    分別列在各段底下——「鹽」出現兩次、「芥花油」出現兩次。抽取器一路
    `_dedupe` 跨段去重，只剩一個；而 `score_one` 是一對一配對，
    配掉第一個之後第二個正解項就必然算漏。

    實測：正解自身的重複項 152 / 2888（多段案佔 11.4%、單段只有 0.2%），
    其中 **144 項被判為漏，但抽出清單裡有字串完全相同的項目**。

    重複幾次不是隨便給的，是「這一項出現在幾個**被採用的段落**裡」——
    段落數就是版面上的段數。只在段落數 ≥2 時才展開，單段商品行為不變。
    """
    base = p_boxsep(cid, d, gt)
    kept = _kept_sections(cid, d, gt)
    if len(kept) < 2:
        return base
    segn = [S.normalize(x, fold_variants=True) for x in kept]
    out = []
    for x in base:
        k = S.normalize(x, fold_variants=True)
        n = sum(1 for sg in segn if k and k in sg) if k else 1
        out.extend([x] * max(1, min(n, 3)))
    return out


# bare「料」加上否定前瞻，排除「料理包」「調理包」。
# 已實測 `HEAD.search('料理包')` 命中「料」——泡麵的「料理包／調理包」因此
# 產生大量烹調說明的假段落，且分數可與真成分段競爭（`c70`：真段落 9.0，
# 六個假段落 4.0–5.0；`c107` 假段落直接勝出）。
HEAD_NOCOOK = re.compile(
    r'(?:成\s*[分份]|原\s*料|配\s*料|[內内]\s*容\s*物|材\s*料)\s*[:：.．…·]?'
    r'|(?:(?<![香調味佐])料(?!理))\s*[:：.．…·]?'
    r'|(?:(?<![成公每份水])分)\s*[:：.．…·]')


def p_boxsep_head(cid, d, gt):
    """= p_boxsep，但 HEAD 排除「料理／調理」。只差這一個變因。"""
    g = globals()
    saved, g['HEAD'] = g['HEAD'], HEAD_NOCOOK
    try:
        return p_boxsep(cid, d, gt)
    finally:
        g['HEAD'] = saved


# 合併多個讀取器的文字再抽取（[[09-多段成分表的抽取順序]] §11）。
# 天花板實測（最大切分、零過濾的召回上限）：
#     vlcrop_hy 單獨            77.1%
#     ＋ PP-OCR v6_best         80.5%
#     ＋ Qwen3.5-2B             83.3%
# 解析層在單一讀取器上已逼近上限（現行 72.5% vs 天花板 77.1%），
# 要再往上只能增加輸入。專案在 allergy／manufacturer／name 已採同一策略。
MERGE_PRESETS = ()
FUZZY_MIN = 6      # 模糊去重的最短長度，見 _fuzzy_dedupe


def _merged_record(cid, d):
    """把其他讀取器的 images 併進同一份記錄。它們是不同張影像，
    對 `_text_boxsep` 的框邊界分隔不衝突。"""
    if not MERGE_PRESETS:
        return d
    import copy
    out = copy.deepcopy(d)
    for pre in MERGE_PRESETS:
        f = os.path.join(HERE, 'out', pre, cid + '.json')
        if not os.path.exists(f):
            continue
        j = json.load(open(f, encoding='utf-8'))
        out.setdefault('images', []).extend(j.get('images') or [])
    return out


def _fuzzy_dedupe(items):
    """跨讀取器的模糊去重：兩個讀取器把同一項讀出些微差異
    （`抗氧化劑混合濃縮生育醇` vs `…生育酵`）時，`_dedupe` 的字串鍵殺不掉，
    兩個都留著就必有一個算多抽。容差沿用 `_match`，保留較長的那個
    ——較長的通常保住了括號結構。"""
    kept = []
    for x in sorted(items, key=lambda y: -len(S.normalize(y, fold_variants=True))):
        k = S.normalize(x, fold_variants=True)
        # ⚠ **短項目只能精確比對。** `_match` 對 2 字項目的容差是 1，
        # 而成分表裡 2–4 字的項目是主力且彼此只差一個字（蔗糖／砂糖、
        # 豬肉／雞肉、大豆油／芥花油）。不設這道護欄，實測命中 2192 → 1854。
        if len(k) < FUZZY_MIN:
            if any(S.normalize(y, fold_variants=True) == k for y in kept):
                continue
            kept.append(x)
            continue
        if not any(_match(x, y) for y in kept):
            kept.append(x)
    order = {id(x): i for i, x in enumerate(items)}
    return sorted(kept, key=lambda x: order.get(id(x), 0))


def p_boxsep_merge(cid, d, gt):
    """= p_boxsep，但輸入是多個讀取器文字的聯集 ＋ 跨讀取器模糊去重。

    ⚠ 合併**原始文字**的代價很大：第二個讀取器（PP-OCR v6_best）讀的是整頁，
    地址、行銷文案、保存說明一併進來。實測召回 72.5% → 75.9%，
    但精確度 69.3% → 56.3%，F1 淨損 6 點。留著這個變體只為記錄該結果。
    """
    return _fuzzy_dedupe(p_boxsep(cid, _merged_record(cid, d), gt))


MERGE_GATE = 'strict'      # 'strict' | 'dict' | 'both'


def p_boxsep_add(cid, d, gt):
    """在 `boxsep` 的結果上，**逐項**補進其他讀取器抽到的成分。

    與 `p_boxsep_merge` 的差別：合併發生在**項目層級而非文字層級**，
    而且補進來的項目要通過閘門。理由是第二個讀取器的整頁文字含大量非成分，
    在文字層級合併等於把那些雜訊也交給段落法去猜。
    """
    base = p_boxsep(cid, d, gt)
    have = list(base)
    for pre in MERGE_PRESETS:
        f = os.path.join(HERE, 'out', pre, cid + '.json')
        if not os.path.exists(f):
            continue
        d2 = json.load(open(f, encoding='utf-8'))
        for x in (p_boxsep(cid, d2, gt) or []):
            k = S.normalize(x, fold_variants=True)
            if not k:
                continue
            ok = _strict_item(x) if MERGE_GATE in ('strict', 'both') else True
            if MERGE_GATE in ('dict', 'both'):
                ok = ok and any(w in k for w in load_dict())
            if not ok:
                continue
            if len(k) < FUZZY_MIN:
                if any(S.normalize(y, fold_variants=True) == k for y in have):
                    continue
            elif any(_match(x, y) for y in have):
                continue
            have.append(x)
    return have


def p_dbscan(cid, d, gt):
    """先用空間聚類 ＋ 詞彙投票定位成分區，再交給既有的切分器。

    出處與理由見 `region_dbscan.py`。與 `p_boxsep` 的差別**只有定位**：
    切分、去重、營養雜訊剔除全部沿用，所以兩者的差距可以歸給定位。

    定位失敗（沒有任何一團拿到分數）時退回 `p_boxsep`——寧可用整頁，
    也不要回傳空清單。退回次數會印在報表裡，那是這個方法的覆蓋率。
    """
    import region_dbscan as RD
    txt = RD.region_text(d, sep=BOX_SEP)
    if not txt:
        p_dbscan.fallback += 1
        return p_boxsep(cid, d, gt)
    # 接線必須與 p_boxsep 一致，否則比的就不是同一件事：
    # **p_dict 走正常分隔符，只有 p_union 才把框邊界當頓號。**
    # 第一版把 p_dict 也包進 BOX_SEP，每個框邊界都成了切點，
    # 抽出項數從 830 爆到 1202——那是接線錯，不是方法的結果。
    saved_ft, saved_sep = globals()['full_text'], globals()['SEP']
    try:
        globals()['full_text'] = lambda d_, order='raw': txt.replace(BOX_SEP, '')
        base = p_dict(cid, d, gt) or []
        globals()['full_text'] = lambda d_, order='raw': txt
        globals()['SEP'] = saved_sep + BOX_SEP
        extra = p_union(cid, d, gt) or []
    finally:
        globals()['full_text'], globals()['SEP'] = saved_ft, saved_sep
    keep = [x for x in extra
            if 2 <= len(S.normalize(x)) <= 12 and _looks_like_ingredient(x)]
    return _dedupe(_denoise_nutrition(base + keep))


p_dbscan.fallback = 0


def p_dbclean(cid, d, gt):
    """空間聚類的第二種用法：**剔掉營養表與廠商地址那幾團**，其餘照舊。

    與 `p_dbscan`（選最高分的一團）的差別是方向相反——那個是選，這個是排除。
    選會丟掉成分（實測定位召回 77.4% → 71.4%），排除不會。
    理由與判準見 `region_dbscan._is_junk`。
    """
    import region_dbscan as RD
    txt = RD.kept_text(d, sep=BOX_SEP)
    if not txt:
        p_dbclean.fallback += 1
        return p_boxsep(cid, d, gt)
    # 接線必須與 p_boxsep 一致，否則比的就不是同一件事：
    # **p_dict 走正常分隔符，只有 p_union 才把框邊界當頓號。**
    # 第一版把 p_dict 也包進 BOX_SEP，每個框邊界都成了切點，
    # 抽出項數從 830 爆到 1202——那是接線錯，不是方法的結果。
    saved_ft, saved_sep = globals()['full_text'], globals()['SEP']
    try:
        globals()['full_text'] = lambda d_, order='raw': txt.replace(BOX_SEP, '')
        base = p_dict(cid, d, gt) or []
        globals()['full_text'] = lambda d_, order='raw': txt
        globals()['SEP'] = saved_sep + BOX_SEP
        extra = p_union(cid, d, gt) or []
    finally:
        globals()['full_text'], globals()['SEP'] = saved_ft, saved_sep
    keep = [x for x in extra
            if 2 <= len(S.normalize(x)) <= 12 and _looks_like_ingredient(x)]
    return _dedupe(_denoise_nutrition(base + keep))


p_dbclean.fallback = 0


def p_dbmask(cid, d, gt):
    """只做篩選，**順序完全不動**——把「區域篩選」從「行序重排」隔離出來量。

    `p_dbscan` 與 `p_dbclean` 都用 `_reading_order` 重排過，兩者都輸給
    `p_boxsep`，但那個比較同時改了兩件事，歸因不了。這一版沿用
    `_text_boxsep` 原本的框順序，只把營養表／地址那幾團的框拿掉。
    """
    import region_dbscan as RD
    mask = RD.keep_mask(d)
    if mask is None:
        p_dbmask.fallback += 1
        return p_boxsep(cid, d, gt)
    flat = [l['text'] for im in d['images'] for l in im.get('lines', [])
            if l.get('text') and l.get('box')]
    txt = BOX_SEP.join(t for t, k in zip(flat, mask) if k)
    if not txt:
        p_dbmask.fallback += 1
        return p_boxsep(cid, d, gt)
    # 接線必須與 p_boxsep 一致，否則比的就不是同一件事：
    # **p_dict 走正常分隔符，只有 p_union 才把框邊界當頓號。**
    # 第一版把 p_dict 也包進 BOX_SEP，每個框邊界都成了切點，
    # 抽出項數從 830 爆到 1202——那是接線錯，不是方法的結果。
    saved_ft, saved_sep = globals()['full_text'], globals()['SEP']
    try:
        globals()['full_text'] = lambda d_, order='raw': txt.replace(BOX_SEP, '')
        base = p_dict(cid, d, gt) or []
        globals()['full_text'] = lambda d_, order='raw': txt
        globals()['SEP'] = saved_sep + BOX_SEP
        extra = p_union(cid, d, gt) or []
    finally:
        globals()['full_text'], globals()['SEP'] = saved_ft, saved_sep
    keep = [x for x in extra
            if 2 <= len(S.normalize(x)) <= 12 and _looks_like_ingredient(x)]
    return _dedupe(_denoise_nutrition(base + keep))


p_dbmask.fallback = 0


LINECLS_TH = 0.10


def _linecls_text(cid):
    """讀折外預測，只留 P(成分) >= LINECLS_TH 的行，用框邊界接起來。

    **讀預存結果而不是當場推論**：分類器要 torch，本檔 import 的
    nutrition_pipeline 走 paddle，兩者同環境會衝突（paddlex 無條件
    import modelscope → torch，cuDNN 版本相衝）。逐行分類是純文字到純文字、
    與影像無關，預存不會失去任何東西。產生方式：
        .venv_torch/Scripts/python.exe train_linecls.py cv

    **門檻 0.10 不是 argmax。** 這個任務的代價不對稱：漏掉一行成分那些字
    永遠找不回來，多留一行雜訊下游的 `_plausible` 與 `_looks_like_ingredient`
    擋得掉。實測（驗證集字元召回，相對整頁）：
        argmax(0.5) −12.3 點｜0.20 −2.5｜**0.10 +11.2**｜0.05 +10.7｜0.01 +10.0
    oracle 上限是 +11.5，門檻 0.10 拿到 97%。曲線在 0.10~0.01 之間很平，
    不是靠單一尖峰，但門檻仍是在驗證集上挑的，測試集擴充後要重挑。
    """
    p = os.path.join(HERE, 'out', 'linecls_pred', f'{cid}.json')
    if not os.path.exists(p):
        return None
    rows = json.load(open(p, encoding='utf-8'))
    keep = [r['text'] for r in rows if r['p'] >= LINECLS_TH]
    return BOX_SEP.join(keep) if keep else None


def p_linecls(cid, d, gt):
    """先用逐行分類器定位成分區，再交給既有的切分器。

    與 `p_boxsep` 的差別**只有定位**——切分、去重、營養雜訊剔除全部沿用，
    所以兩者的差距可以歸給定位。這是第三次嘗試定位：
        DBSCAN 選最高分一團   −4.5 點（CI −9.7~−0.4，不跨 0）→ 否決
        DBSCAN 排除垃圾團     更差                            → 否決
        逐行語意分類          本次
    前兩次都敗在「分隔訊號是語意的，不是空間的」，這次直接在文字上做。
    """
    txt = _linecls_text(cid)
    if not txt:
        p_linecls.fallback += 1
        return p_boxsep(cid, d, gt)
    saved_ft, saved_sep = globals()['full_text'], globals()['SEP']
    try:
        globals()['full_text'] = lambda d_, order='raw': txt.replace(BOX_SEP, '')
        base = p_dict(cid, d, gt) or []
        globals()['full_text'] = lambda d_, order='raw': txt
        globals()['SEP'] = saved_sep + BOX_SEP
        extra = p_union(cid, d, gt) or []
    finally:
        globals()['full_text'], globals()['SEP'] = saved_ft, saved_sep
    keep = [x for x in extra
            if 2 <= len(S.normalize(x)) <= 12 and _looks_like_ingredient(x)]
    return _dedupe(_denoise_nutrition(base + keep))


p_linecls.fallback = 0


def p_block(cid, d, gt):
    """以空間區塊當候選段落，不靠「成分」兩個字定位。"""
    scored = []
    for blk in blocks(d):
        items = split_top(blk)
        scored.append((_plausible(items), items))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    out = []
    for s, items in scored:
        if s >= max(1.0, best * UNION_FRAC):
            out.extend(items)
    return _dedupe(out) or _dedupe(max(scored, key=lambda x: x[0])[1])


def p_naive(cid, d, gt):
    """最直白的做法：找到「成分」兩個字，往後切到句號，用頓號分開。

    這是基準線，代表「不特別處理就這樣」。它會踩到全部三個難點，
    量出來的數字就是後面每一項改進要超過的門檻。
    """
    t = full_text(d)
    m = HEAD.search(t)
    if not m:
        return []
    seg = t[m.end():m.end() + 400]
    return [x.strip() for x in re.split(f'[{SEP}]', seg) if x.strip()]


def split_top(seg):
    """在**括號深度 0** 的分隔符切開，巢狀配方整團留成一項。

    深度計數要容忍不對稱：OCR 常把「(」或「)」讀丟，一旦深度變成負的或
    永遠回不到 0，後面整段就再也切不開，會黏成一個超長的假項目。
    所以深度夾在 0 以上，且遇到分隔符時只要深度<=0 就切。
    """
    out = []
    for it in _split_depth(seg):
        # ⚠ **搶救只作用在證實壞掉的項目上**，見 `_unbalanced` 的說明。
        out += _split_depth(it, rescue=True) if _unbalanced(it) else [it]
    return out


def _unbalanced(s):
    """這個字串的括號對不對得起來。

    對得起來 ＝ 深度計數是可信的 ＝ 上面那一刀切得對，不要動它。
    對不起來 ＝ OCR 少讀了一個括號 ＝ 深度回不到 0 ＝ 後面整段黏成一團。
    """
    d = 0
    for ch in s:
        if ch in OPEN:
            d += 1
        elif ch in CLOSE:
            d -= 1
            if d < 0:
                return True
    return d != 0


def _split_depth(seg, rescue=False):
    """在括號深度 0 的分隔符切開。`rescue` 才啟用「收尾括號被讀丟」的搶救。

    ⚠ **2026-09-10 更正**：搶救原本寫成「距離**任何**括號字元超過 OPEN_MAX
       個字就強制歸零」，但巢狀配方裡面全是括號，計數器不斷歸零、永遠到不了
       門檻——註解宣稱擋得住 `c44_酸菜白肉個人鍋`，實際上完全沒有生效
       （該案 24 項成分黏成 1 項、5 個添加物全部消失）。
       正確的量法是「**最外層**那個括號已經開了幾個字」。

    ⚠ 而且這一刀只能對**已經證實不平衡**的項目下（`split_top` 的 `rescue`）。
       整段無條件套用實測會反過來扣分：正解裡合法的巢狀複方長達 161 字
       （`c44` 的 `調味粉{…}`），被切開之後 `乳化劑(脂肪酸甘油酯)` 這種
       「功能(名稱)」結構跟著碎掉，比對器就認不得了——
       添加物層 −0.7、10 案受損。逐項修則 0 案受損。
    """
    out, buf, stack = [], [], []
    for i, ch in enumerate(seg):
        if ch in OPEN:
            stack.append(i)
        elif ch in CLOSE:
            if stack:
                stack.pop()
        if rescue and stack and i - stack[0] > OPEN_MAX:
            stack.clear()
        if ch in SEP and not stack:
            if buf:
                out.append(''.join(buf).strip())
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append(''.join(buf).strip())
    return [x for x in out if x]


def p_bracket(cid, d, gt):
    """加上括號深度：巢狀配方不切開。"""
    t = full_text(d)
    m = HEAD.search(t)
    if not m:
        return []
    return split_top(t[m.end():m.end() + 400])


def _plausible(items):
    """一組切出來的東西**像不像**成分清單。沒有 GT 時用來挑段落。

    真正的成分名有很穩定的形狀：2 到 12 個字、幾乎全是中文、沒有句號。
    地址、電話、行銷文案切出來的碎片則又長又雜。這個分數不需要很準，
    它只要能在「同一份文字的幾個候選段落」之間排出高下就夠了。
    """
    if not items:
        return -1e9
    good = 0.0
    for x in items:
        k = S.normalize(x)
        n = len(k)
        if n == 0:
            continue
        cjk = sum(1 for ch in x if '一' <= ch <= '鿿')
        ratio = cjk / max(1, len(x.replace(' ', '')))
        if 2 <= n <= 12 and ratio > 0.6:
            good += 1
        elif n > 40:                      # 整段沒切開的雜訊，扣重一點
            good -= 2
        elif ratio < 0.3:                 # 電話、網址、編號
            good -= 1
    return good


def segments(t):
    """列出所有可能的成分段。

    原本只取第一個「成分／原料」往後 400 字，兩個問題：標示上常有多處提到
    （「成分」出現在過敏原說明裡、正反面各印一次），第一個未必是對的；
    而 400 字是硬編的，短的會吃進整段地址，長的會被切斷。
    改成每個起點都產生一個候選，結尾切在最近的「營養標示／有效日期／製造商…」，
    再由 _plausible 挑。挑錯的代價只是這一案差，不會像固定規則那樣整批壞掉。
    """
    out = []
    for m in HEAD.finditer(t):
        rest = t[m.end():]
        e = TAIL.search(rest)
        # 結尾切在最近的關鍵詞，但至少留 60 字：踩過一次——c04_比菲多 是
        # `成分…水、蔗糖 品名：活性乳酸菌…`，「品名」緊接在後，
        # 一刀切下去只剩「水蔗糖」三個字，被長度門檻擋掉、整案掛零。
        # 成分段本來就常和品名／淨重交錯排版，切太急比切太鬆糟。
        cuts = [max(e.start(), 60) if e else 400]
        # 2026-08-31：TAIL 早於 60 字時，**短版也一起產生成候選**。
        # 原本硬選長版是為了救 c04，但代價是「營養標示／品名」只要出現在
        # 前 60 字內，那段雜訊必定被吃進來（實測 821 項抽出裡 88 項是
        # 營養標示／保存說明／廠商文字，佔 11%）。
        # 這支的契約本來就是「多產生、再由 _plausible 挑」，硬選等於
        # 放棄那個機制。兩個都給它挑：清單層 +0.3、添加物層 +1.7
        # （配對 bootstrap 95% CI +0.0~+4.3，95.8% 的重抽較好）。
        # 下限 3 是 normalize 後的長度門檻，比它短的候選本來就會被丟掉。
        if e and 3 <= e.start() < 60:
            cuts.append(e.start())
        for cut in cuts:
            seg = rest[:cut]
            if len(S.normalize(seg)) >= 3:
                out.append(seg)
    return out


def p_segment(cid, d, gt):
    """多候選段落 + 像不像成分表的評分。"""
    t = full_text(d)
    best, bs = [], -1e9
    for seg in segments(t):
        items = split_top(seg)
        s = _plausible(items)
        if s > bs:
            best, bs = items, s
    return best


def _dedupe(items):
    """去掉重複與被包含的項目，保留原順序。

    聯集多段之後一定會有重複——同一段成分在正反面各印一次、OCR 也會
    重複讀到。用正規化後的字串當鍵，另外丟掉「已經是別項子字串」的碎片，
    那些多半是同一項被切壞的殘骸。
    """
    seen, out = set(), []
    for x in items:
        k = S.normalize(x, fold_variants=True)
        # 門檻是 1 不是 2：單字項目在成分表裡是主力（水、糖、鹽、蒜、蔥、醋），
        # 寫成 <2 會把它們全刪掉，實測召回從 57.3% 掉到 49.9%
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def p_union(cid, d, gt):
    """**聯集**所有夠像成分表的段落，不是只挑最好的一段。

    起因是泡麵與便當：c58_拉麵道 的 43 項成分分成麵包／油包／調味粉包
    好幾段印刷，每段各自有自己的「原料」開頭。只取分數最高的一段，
    另外兩段的三十幾項全部丟掉——實測 c58 命中 1/43。

    門檻用「相對最佳段落」而不是絕對值：不同商品的成分段長度差十倍，
    絕對門檻不是把短的擋掉就是把雜訊放進來。
    """
    t = full_text(d)
    scored = []
    for seg in segments(t):
        items = split_top(seg)
        if DICT_FIRST:
            items = _dsplit_items(items)
        scored.append((_plausible(items), items))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    out = []
    for s, items in scored:
        if s >= max(1.0, best * UNION_FRAC):
            out.extend(items)
    return _dedupe(out) or _dedupe(max(scored, key=lambda x: x[0])[1])


UNION_FRAC = 0.7

# ── 字典切分要不要跑在段落篩選之前（[[09-多段成分表的抽取順序]]）──────────
# `_dict_split` 專門處理「分隔符被讀丟」的黏連字串，但它原本在 `p_dict` 裡執行，
# **位於 `_plausible` 的門檻之後**——而那道門檻正好會把它要救的東西先丟掉：
#
#   c57_來一客 唯一的段落候選其實就是成分表，但一個頓號都沒有
#      切分前   1 項、56 字   _plausible = −2.0   門檻 1.0 → 丟棄
#      切分後   6 項           _plausible = +4.0              → 保留
#
# 泡麵這類多段標示 26 案吃掉 795 項漏失中的 510 項，其中 78% 是解析層。
DICT_FIRST = False


def _dsplit_items(items):
    """對沒有括號的長項目做字典切分。括號還在就表示結構沒壞，不要動它
    ——不分青紅皂白地切會把 `辣豆瓣醬(辣椒、黃豆…)` 拆碎（實測精確度 74.0 → 62.6）。
    規則與 `p_dict` 完全相同，只是提前執行。"""
    out = []
    for x in items:
        if re.search(r'[()（）\[\]【】{}｛｝]', x):
            out.append(x)
        else:
            out.extend(_dict_split(x))
    return out
OPEN_MAX = 120   # 括號開著幾個字之後就當它的收尾被讀丟了

# 一看就不是成分的東西。這些是靠形狀分不掉的——「臺灣臺南市新市區」
# 長度與中文比例都跟成分名一樣，只能列舉。
NOT_ING = re.compile(
    r'\d{3,}|[A-Za-z]{4,}|www|http|@|'
    r'公司|企業|工廠|廠址|地址|電話|專線|信箱|網址|服務|客服|'
    r'臺灣|台灣|縣|市|區|鄉|鎮|村|里|路|街|號|樓|段|巷|弄|'
    r'日期|期限|保存|冷藏|冷凍|開封|食用|微波|加熱|請|勿|注意|警告|建議|'
    r'營養|標示|熱量|蛋白質|脂肪|碳水|每份|公克|毫克|毫升|大卡|份量|'
    r'過敏|生產線|製造|產地|品名|淨重|內容量|内容量|統一編號|條碼|'
    r'本產品|本包裝|沉澱|現象|品質|風味絕佳|美味')


def _looks_like_ingredient(x):
    """單一項目像不像成分名。

    這是「不分段、逐項分類」路線的核心。對 c58_拉麵道 這種標示，
    成分文字**散佈在整份文件**（原料兩個字在第 66 字，成分卻前後都有、
    順序被 OCR 的框序打亂），「找出一段連續區域」的模型根本不成立。

    判準只有形狀 + 黑名單，沒有用到成分字典。字典會更準，但那是下一步——
    先量出純形狀能到哪裡，才知道字典值不值得接。
    """
    k = S.normalize(x)
    n = len(k)
    if n < 1 or n > 60:
        return False
    if NOT_ING.search(x):
        return False
    cjk = sum(1 for ch in x if '一' <= ch <= '鿿')
    body = x.replace(' ', '')
    if not body:
        return False
    # 括號內容算進來：「抗氧化劑(L-抗壞血酸鈉)」的英數字是正常的
    return cjk / len(body) > 0.5 or ('(' in x or '（' in x)


def p_filter(cid, d, gt):
    """全文切開，逐項判斷是不是成分。不找區域。"""
    items = split_top(full_text(d))
    return _dedupe([x for x in items if _looks_like_ingredient(x)])


_DICT = None


def load_dict():
    """成分字典。來自 server/seed_data/reference_seed.sql 的 additives 表
    （公開來源建的參考資料庫），**不是**評估集的 GT。用 build_dict.py 產生。
    """
    global _DICT
    if _DICT is None:
        p = os.path.join(HERE, 'data', 'ingredient_dict.json')
        words = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else []
        # 依長度由長到短，最大匹配才會優先吃掉長詞
        _DICT = sorted({w for w in words if len(w) >= 3}, key=len, reverse=True)
    return _DICT


def _dict_split(x, minlen=12):
    """用字典在黏成一團的項目裡找切點。

    OCR 把「、」和括號讀丟時，`豬肉風味粉豬肉抽出物豬油麥芽糊精` 會變成一項，
    而該切的地方沒有任何標記——純規則到此為止。字典提供的是**錨點**：
    認出「麥芽糊精」在字串尾端，就等於知道它前面有個邊界，
    連帶把它的鄰居也解放出來。所以字典只收添加物也有用，
    不需要涵蓋「水」「麵粉」這些一般原料。

    只對夠長的項目動手（短的本來就不太可能是黏起來的），
    切完的碎片一律保留——寧可多給下游幾個候選，也不要漏掉添加物。
    """
    if len(S.normalize(x)) < minlen:
        return [x]
    words = load_dict()
    hits = []
    for w in words:
        i = x.find(w)
        while i >= 0:
            if not any(i < e and s < i + len(w) for s, e in hits):
                hits.append((i, i + len(w)))
            i = x.find(w, i + 1)
    if not hits:
        return [x]
    hits.sort()
    out, pos = [], 0
    for s, e in hits:
        if s > pos:
            out.append(x[pos:s])
        out.append(x[s:e])
        pos = e
    if pos < len(x):
        out.append(x[pos:])
    return [p.strip(' 、,，()（）[]【】{}') for p in out if p.strip()]


def _strict_item(x):
    """補件用的嚴格條件：短、純中文、中間沒有空白。

    空白幾乎一定代表兩個框被串接起來（`乳蛋白質含量1.5%以上· (本產品含有`），
    不是一個成分名。長度上限 8 是量出來的：放到 10 或 14 都會讓精確度掉，
    真正的頂層成分名超過 8 個字的很少（超過的多半帶括號，那些由段落法負責）。
    """
    k = S.normalize(x)
    return 1 <= len(k) <= 8 and _looks_like_ingredient(x) and ' ' not in x.strip()


def p_hybrid(cid, d, gt):
    """有像樣的成分段就用段落，抽得太少才退回全文過濾補件。

    兩條路線各有適用範圍：版面整齊的商品（飲料、乳品）成分就是一段連續文字，
    段落法的精確度高得多；泡麵便當那種多段印刷 + 框序打亂的，段落法會整段
    抓不到。用「段落法抽出幾項」當切換訊號——不能用 GT 的項數，那是作弊。

    補件只收 _strict_item。試過全部都補（不看段落法抽了幾項）：
    召回 60.5%→62.5% 但精確 73.8%→67.7%，F1 66.5→64.9。
    """
    seg = p_union(cid, d, gt)
    cand = _dedupe([x for x in split_top(full_text(d)) if _strict_item(x)])
    # 補不補的訊號要可觀察，不能用 GT 的項數。用「全文裡像成分的東西
    # 是不是遠多於段落法抽到的」——多很多就表示段落法漏了整段
    # （c58_拉麵道 的成分散在整份文件裡，段落法只蓋到十幾項）。
    if len(seg) >= HYBRID_MIN and len(seg) >= COVER_RATIO * len(cand):
        return seg
    return _dedupe(seg + cand)


HYBRID_MIN = 6
COVER_RATIO = 0.9


def p_dict(cid, d, gt):
    """在 hybrid 之上，用字典把黏成一團的項目切開。

    只切**沒有括號**的長項目。這個限制是必要的：GT 把巢狀配方
    `辣豆瓣醬(辣椒、黃豆、蠶豆…)` 保留成一項，不分青紅皂白地切會把它拆碎，
    實測精確度從 74.0% 掉到 62.6%。有括號就表示結構還在、不需要字典幫忙；
    沒括號卻很長，才是「分隔符被讀丟」的特徵。

    參數 12 是掃出來的：6/8/10/12 分別是 67.0/67.2/67.4/67.6，
    而且在三種字典詞長設定下都是 12 最好，是真訊號不是雜訊。
    """
    out = []
    for x in p_hybrid(cid, d, gt):
        if re.search(r'[()（）\[\]【】{}｛｝]', x):
            out.append(x)
        else:
            out.extend(_dict_split(x))
    return _dedupe(out)

PARSERS = {'naive': p_naive, 'bracket': p_bracket, 'segment': p_segment,
           'union': p_union, 'filter': p_filter, 'hybrid': p_hybrid,
           'dict': p_dict, 'block': p_block,
           'boxseg': p_boxseg, 'boxsep': p_boxsep,
           'boxsep_ds': p_boxsep_ds, 'boxsep_dup': p_boxsep_dup,
           'boxsep_head': p_boxsep_head, 'boxsep_merge': p_boxsep_merge, 'boxsep_add': p_boxsep_add,
           'dbscan': p_dbscan, 'dbclean': p_dbclean,
           'dbmask': p_dbmask, 'linecls': p_linecls}


def run(name, cases):
    fn = PARSERS[name]
    H = G = P = 0
    per = []
    for cid, d, gt in cases:
        got = fn(cid, d, gt) or []
        h, g, p, miss, spur = score_one(got, gt['ingredients_list'])
        H += h; G += g; P += p
        per.append((cid, h, g, p, miss, spur))
    return H, G, P, per


def report(rows):
    print(f'{"抽取器":<14}{"命中":>6}{"GT":>6}{"抽出":>6}{"召回":>8}{"精確":>8}{"F1":>8}')
    for name, (H, G, P, _) in rows:
        r = H / G if G else 0
        pr = H / P if P else 0
        f1 = 2 * r * pr / (r + pr) if r + pr else 0
        print(f'{name:<14}{H:>6}{G:>6}{P:>6}{r * 100:>7.1f}%{pr * 100:>7.1f}%{f1 * 100:>7.1f}%')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--detail', default=None)
    ap.add_argument('--worst', type=int, default=0)
    ap.add_argument('--boxes', default=None, help='out/ 底下的 OCR 輸出目錄')
    a = ap.parse_args()
    if a.boxes:
        global BOXES
        BOXES = a.boxes
    cases = load_cases()
    print(f'OCR 輸出：out/{BOXES}')
    print(f'案例 {len(cases)}｜GT 成分共 '
          f'{sum(len(g["ingredients_list"]) for _, _, g in cases)} 項\n')

    rows = []
    for name in (a.only or PARSERS):
        rows.append((name, run(name, cases)))
    # Gemini 當參考點：它直接輸出結構化清單，是這一步的現況對手
    gh = gg = gp = 0
    for cid, d, gt in cases:
        lst = gemini_list(cid)
        if lst is None:
            continue
        h, g, p, _, _ = score_one(lst, gt['ingredients_list'])
        gh += h; gg += g; gp += p
    report(rows)
    if gg:
        r, pr = gh / gg, gh / gp if gp else 0
        print(f'{"Gemini*":<14}{gh:>6}{gg:>6}{gp:>6}{r * 100:>7.1f}%{pr * 100:>7.1f}%'
              f'{2 * r * pr / (r + pr) * 100 if r + pr else 0:>7.1f}%')
        print(f'  * Gemini 只有 {sum(1 for c, _, _ in cases if gemini_list(c))}'
              f'/{len(cases)} 案有預測，GT 基數不同，不能直接跟上面比總數，看比率')

    last = rows[-1][1][3]
    if a.detail:
        for cid, h, g, p, miss, spur in last:
            if not cid.startswith(a.detail):
                continue
            print(f'\n═══ {cid}  命中 {h}/{g}｜抽出 {p} ═══')
            print(' 漏掉:', json.dumps(miss, ensure_ascii=False)[:800])
            print(' 多出:', json.dumps(spur, ensure_ascii=False)[:800])
    if a.worst:
        print(f'\n最差的 {a.worst} 案（{rows[-1][0]}）：')
        for cid, h, g, p, miss, spur in sorted(last, key=lambda x: x[1] - x[2])[:a.worst]:
            print(f'   {cid:<32}命中{h:>3}/{g:<3} 抽出{p:<3} 多{len(spur)}')


if __name__ == '__main__':
    main()
