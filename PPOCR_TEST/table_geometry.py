#!/usr/bin/env python3
# 用框的座標重建營養標示的欄位↔數值對應關係。
#
# 為什麼需要這支：純文字的 regex 解析在 57 案裡只有 13 案全對，因為它假設
# 「欄位名後面緊接著兩個數值」——而那只在 OCR 剛好按「列」線性化時成立。
# 直排表格（泡麵杯）讀出來是「熱蛋脂 | 438 | 14.8 | 20.3 | 和肥肪」這樣，
# 欄位名疊成一欄、數值散落他處，鄰接關係整個斷掉。
#
# 但 det 本來就給了每個框的四點座標，幾何關係一直都在，只是被線性化丟掉了。
# 這支不做完整的表格網格還原（那是 PP-StructureV3 沒做成功的事），只做一件
# 更小、更穩的事：**對每個欄位名，找出與它同一條帶狀區域的數值框**。
#
#   橫排表格：欄位名在左，數值在右 → 取 y 相近、x 較大的數值框，依 x 排序
#   直排表格：欄位名在上，數值在下 → 取 x 相近、y 較大的數值框，依 y 排序
#
# 方向用區域內框的長寬比投票決定，不預設。
import re
import unicodedata

FIELD_PATTERNS = [
    ('calories', r'熱量|热量'),
    ('protein', r'蛋白[質质贸]'),
    ('saturated_fat', r'[飽饱鮑鲍]和脂肪'),
    ('trans_fat', r'反式脂肪'),
    ('fat', r'(?<![飽饱鮑鲍反式和])脂肪'),
    ('carbohydrates', r'碳水化合物'),
    ('fiber', r'膳食[纖纤织][維维]'),
    ('sugar', r'^糖$|(?<![蔗葡萄麥芽乳砂果白黑紅寡多海藻糊焦])糖'),
    ('sodium', r'^[鈉钠纳]$|(?<![酸化磷碳])[鈉钠纳]'),
]
# **一個框裡常常不只一個數，甚至連欄位名一起。** 實測 c31 的 det 輸出：
#     「碳水化合物71.3公克20.2公克」「19.4公克5.5公克」「1123毫克 318毫克」
# 第一版要求「整格就是一個數」，結果幾乎每一格都被拒絕（num=None），
# 幾何配對整個失效。改成從每格抽出**數字清單**。
NUM_IN_CELL = re.compile(r'(-?\d+(?:[.,]\d+)?)\s*'
                         r'(?:大卡|公克|毫克|公絲|公升|毫升|kcal|kj|g|mg|ml)?')
# 表頭字樣。「每100公克」裡的 100 是最常見的誤抓來源；
# 「每一份量353公克」的 353 是份量不是營養值。
HEADER = re.compile(r'每\s*100|每一份量|本包裝|營養標示|参考值|參考值|淨重|內容量')


def _norm(s):
    s = unicodedata.normalize('NFKC', s or '')
    return s.replace('·', '.').strip()


def _geom(box):
    """回傳 (cx, cy, w, h)。box 是四點多邊形，可能是旋轉的。"""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return (sum(xs) / len(xs), sum(ys) / len(ys), max(xs) - min(xs), max(ys) - min(ys))


def _numbers(text):
    """抽出這一格裡的所有數值（依出現順序）。表頭類文字一律回空。"""
    t = _norm(text)
    if HEADER.search(t):
        return []
    out = []
    for m in NUM_IN_CELL.finditer(t):
        try:
            out.append(float(m.group(1).replace(',', '.')))
        except ValueError:
            pass
    return out


# ── 單位複核（2026-09-11）────────────────────────────────────────────────
# `_by_rank` 的護欄是「標籤數 == 各值欄的項目數」，但**兩邊同時各少一個時
# 這個護欄會被巧合滿足**，整排錯開一列而不報錯。
# 實測 c43_藤椒風味烤雞便當（RapidOCR v6small）：OCR 沒讀到「熱量」這個標籤
# （少 1），鈉的數值又落在欄外（少 1），7 對 7 通過護欄，七個欄位全部錯位——
# 蛋白質拿到 483（那是熱量）、碳水化合物拿到 0（那是反式脂肪）。
# 每個值都是標示上真實存在的數字，看起來完全合理，**不會有任何錯誤訊號**。
#
# ⚠ **不要改用「y 最近鄰」來複核。** 那會殺掉名次配對存在的理由：
#    c28 的表格傾斜 14 度，正確的值離標籤 107px，而錯位一列只差 50px——
#    用距離當判準會把傾斜案例的正確結果判成錯的。
# 單位則完全不受幾何影響：「大卡」只可能是熱量、「毫克」只可能是鈉。
UNIT_FIELDS = {
    'kcal': {'calories'},
    'mg': {'sodium'},
    'g': {'protein', 'fat', 'saturated_fat', 'trans_fat',
          'carbohydrates', 'sugar', 'fiber'},
}
# ⚠ 順序有意義：「毫克」「公克」都含「克」，先比長的才不會把毫克判成公克。
UNIT_RE = (
    ('kcal', re.compile(r'大卡|千卡|kcal', re.I)),
    ('mg', re.compile(r'毫克|亳克|豪克')),
    ('g', re.compile(r'公克|公剋')),
)


def _unit_of(text):
    """這一格帶的單位。判不出來回 None——沒有單位就不做複核，不猜。"""
    for u, pat in UNIT_RE:
        if pat.search(text):
            return u
    return None


def _unit_conflicts(srcs):
    """名次配對的結果裡，有幾個欄位拿到了單位不相容的值。

    只在**讀得到單位**時才判定：OCR 常把數字與單位切成兩框（實測 c13
    讀成 '0公' + '克'），那種情況 unit 是 None，一律視為無從判斷而放過。
    """
    bad = 0
    for key, items in srcs.items():
        for it in items:
            if not it:
                continue
            u = it.get('unit')
            if u and key not in UNIT_FIELDS[u]:
                bad += 1
                break
    return bad


def _cluster_1d(vals, gap):
    """把一維座標分群。gap 是「超過這個距離就算不同群」。"""
    if not vals:
        return []
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    groups, cur = [], [order[0]]
    for prev, i in zip(order, order[1:]):
        if vals[i] - vals[prev] > gap:
            groups.append(cur)
            cur = []
        cur.append(i)
    groups.append(cur)
    return groups


def _by_rank(labels, nums, vertical):
    """按名次配對：標籤欄第 n 個 ↔ 各值欄第 n 個。

    只用相對順序，所以表格傾斜、透視變形、印在曲面上都不影響——
    這是水平／垂直帶狀比對做不到的。
    前提是標籤與各值欄的項目數一致；不一致就放棄，交回帶狀比對處理。
    """
    if len(labels) < 4 or not nums:
        return {}, 0, {}
    # 主軸：橫排表格的「欄」沿 x 分布，直排表格沿 y
    axis = (lambda it: it['cy']) if vertical else (lambda it: it['cx'])
    cross = (lambda it: it['cx']) if vertical else (lambda it: it['cy'])

    lab_axis = sorted(axis(it) for _, it in labels)
    span = lab_axis[-1] - lab_axis[0]
    # 分欄間隙取標籤自身跨度的一半，避免把同一欄拆開或把兩欄併起來
    gap = max(span * 0.5, 40)
    vals = [n for n in nums if axis(n) > max(axis(it) for _, it in labels) * 0.0]
    groups = _cluster_1d([axis(n) for n in vals], gap)
    # 只保留在標籤之後（右側／下方）且項目數與標籤數相同的欄
    lab_max = max(axis(it) for _, it in labels)
    cols = []
    for g in groups:
        members = sorted((vals[i] for i in g), key=cross)
        if sum(1 for m in members if axis(m) > lab_max) < len(members) * 0.6:
            continue
        # 欄裡的成員可能比標籤多：表頭的數值會被併進來。實測 c46 的
        # 「410 公克」（每一份量的值，自成一框所以躲過 HEADER 過濾）
        # 落在第二欄左緣 47px 內，害整欄變 10 個成員而被整欄丟棄，該案 0/16。
        # 表頭永遠在資料列之前，所以多出來的從**前面**截掉。
        if len(members) > len(labels):
            members = members[len(members) - len(labels):]
        if len(members) != len(labels):
            continue
        cols.append(members)
    if not cols:
        return {}, 0, {}
    cols.sort(key=lambda c: sum(axis(m) for m in c) / len(c))
    ordered = sorted(labels, key=lambda kv: cross(kv[1]))
    out, srcs = {}, {}
    for i, (key, _) in enumerate(ordered):
        picked = [c[i] for c in cols[:2] if c[i]['nums']]
        if picked:
            v = [p['nums'][0] for p in picked]
            out[key] = (v[0], v[1] if len(v) > 1 else None)
            srcs[key] = picked          # 供單位複核用，見 _unit_conflicts
    return out, len(cols), srcs


ANCHOR = re.compile(r'營養標示|营养标示|每一份量|本包裝含|本包装含')
# 欄位名之後只允許數值與單位——用來區分「營養表的一列」與「成分表裡剛好
# 含到同一個字的長串文字」
TAIL_OK = re.compile(r'^[\s\d.,:：()（）%/大卡公克毫克絲升亳克gGmMlL─\-—]*$')


def _label_kind(pat, text):
    """判斷這一格是不是營養欄位名。回傳 'plain' / 'with_values' / None。

    只用 re.search 會誤配：泡麵杯的直排成分文字裡有「小麥蛋白質水解物焦糖
    色素…」（命中 蛋白質）、「鈉5鳥嘌呤核苷磷酸二鈉琥珀酸二鈉」（命中 鈉）、
    「香料D木糖白芝」（命中 糖）。實測 c58/c59 找到的標籤**全部**是這類，
    真正的營養表欄位一個都沒配到。

    真正的欄位格只有兩種長相：
      整格就是欄位名（「熱量」「碳水化合物」）
      欄位名開頭、後面只跟數值與單位（「碳水化合物71.3公克20.2公克」）
    「欄位名埋在一長串中文中間」一律不是。
    """
    m = re.match(f'(?:{pat})', text)
    if m:
        tail = text[m.end():]
        if not tail.strip():
            return 'plain'
        if TAIL_OK.match(tail) and re.search(r'\d', tail):
            return 'with_values'
        return None
    # 欄位名前面容許極少量雜訊（表格線被讀成字元之類）
    m = re.search(f'(?:{pat})', text)
    if m and m.start() <= 1 and len(text) <= 8:
        return 'plain'
    return None


def _nutrition_scope(items):
    """用「營養標示／每一份量」當錨點，圈出營養表所在的區域。

    回傳該區域內的框；找不到錨點就回 None，由呼叫端退回整張圖。

    窗口大小以錨點框自身尺寸為基準（不是絕對像素），因為同一支程式要處理
    254x400 到 4284x5712 的照片。倍率取得寬鬆——寧可多包一些無關的框，
    也不要把表格的右半邊或下半邊切掉；後續的欄位比對本來就會過濾。
    """
    anchors = [it for it in items if ANCHOR.search(it['t'])]
    if not anchors:
        return None
    best = None
    for a in anchors:
        r = max(a['w'], a['h'])
        x0, x1 = a['cx'] - r * 6, a['cx'] + r * 8
        y0, y1 = a['cy'] - r * 4, a['cy'] + r * 10
        got = [it for it in items if x0 <= it['cx'] <= x1 and y0 <= it['cy'] <= y1]
        # 以「區域內含幾個營養欄位名」評分，挑最像營養表的那個錨點
        n = sum(1 for it in got
                for _, pat in FIELD_PATTERNS if re.search(pat, it['t']))
        if best is None or n > best[0]:
            best = (n, got)
    return best[1] if best and best[0] >= 3 else None


def parse_boxes(lines, debug=False):
    """lines: [(text, box)]，回傳 {欄位: (每份, 每100g)}。

    只在營養表附近作業：先用欄位名與表頭錨點框出範圍，再在範圍內配對。
    """
    items = []
    for text, box in lines:
        if not box or len(box) < 4:
            continue
        cx, cy, w, h = _geom(box)
        items.append({'t': _norm(text), 'cx': cx, 'cy': cy, 'w': w, 'h': h,
                      'nums': _numbers(text), 'unit': _unit_of(_norm(text))})
    if not items:
        return {}

    # **先把搜尋範圍限制在營養表附近。**
    # 不做這一步的話，在整張圖上找欄位名必然誤配：泡麵杯的直排成分文字裡有
    # 「木糖」「磷酸二鈉」「脂肪酸甘油酯」，`糖`/`鈉`/`脂肪` 全都會命中。
    # 實測 c58 只找到 1 個標籤且是「香料D木糖白芝」（成分區的直排框），
    # c59 找到的 2 個也全是成分文字——真正的營養表標籤一個都沒找到。
    # 連帶讓「整組標籤投票決定橫排/直排」也被污染：那兩案的成分是直排、
    # 營養表是橫排，投票結果卻是直排。
    scope = _nutrition_scope(items)
    search = scope if scope else items

    # 欄位名可能單獨成框（「脂肪」），也可能與數值同框
    # （「碳水化合物71.3公克20.2公克」）。先找出所有含欄位名的框，
    # 同框就帶數值的直接定案——那是最可靠的來源，不需要幾何推論。
    direct, labels = {}, []
    for key, pat in FIELD_PATTERNS:
        for it in search:
            kind = _label_kind(pat, it['t'])
            if kind is None:
                continue
            if kind == 'with_values':
                direct[key] = (it['nums'][0],
                               it['nums'][1] if len(it['nums']) > 1 else None)
            labels.append((key, it))
            break
    if not labels:
        return {}

    # 表格範圍：欄位名的外接矩形，四周放寬。放寬量用欄位名自身的尺寸推估，
    # 不用絕對像素——不同解析度的照片才能共用同一套邏輯。
    lxs = [it['cx'] for _, it in labels]
    lys = [it['cy'] for _, it in labels]
    mw = sum(it['w'] for _, it in labels) / len(labels)
    mh = sum(it['h'] for _, it in labels) / len(labels)

    # 方向投票：欄位名普遍高瘦 → 直排表格
    vertical = sum(1 for _, it in labels if it['h'] > it['w'] * 1.5) > len(labels) / 2

    region = [it for it in items
              if min(lxs) - mw * 8 <= it['cx'] <= max(lxs) + mw * 12
              and min(lys) - mh * 8 <= it['cy'] <= max(lys) + mh * 12]
    nums = [it for it in region if it['nums']]

    # ── 先試「按名次配對」──────────────────────────────────────────────
    # 這是對傾斜／透視／曲面唯一穩健的做法。實測 c28 的表格整體傾斜約 14 度
    # （x 每右移 430px，y 上移 107px），用水平帶配對必然全錯：「熱量」在
    # y=656，它的兩個值在 y=549 與 y=447，差了 107 和 209 像素。
    # 但各欄由上往下的**順序**是對的——標籤欄第 n 個，就對應值欄第 n 個。
    # 只用相對名次、不用絕對座標，所以拍歪了也不影響。
    ranked, ncols, ranked_srcs = _by_rank(labels, nums, vertical)
    parse_boxes.last_cols = ncols      # 供呼叫端當信心訊號用
    # 單位複核：不相容就整組丟掉，改走下面的帶狀比對。理由見 UNIT_FIELDS。
    # **整組丟掉而不是只丟衝突的那個欄位**——錯位是整排一起發生的，
    # 留下沒被單位抓到的那幾個等於留下一半的錯值，比全丟更糟。
    nbad = _unit_conflicts(ranked_srcs)
    parse_boxes.last_unit_conflicts = nbad
    if nbad:
        ranked = {}
    if ranked:
        for k, v in ranked.items():
            direct.setdefault(k, v)
        if len(ranked) >= 4:      # 至少對上四個欄位才採信這條路徑
            return {**ranked, **{k: v for k, v in direct.items() if k not in ranked}}

    out = dict(direct)
    for key, lab in labels:
        if key in out and out[key][1] is not None:
            continue                      # 同框已經拿到兩欄，不必再推
        # **方向逐標籤判定，不用全圖投票。**
        # DBNet 本來就同圖混合輸出直排與橫排（實測 c58 有 83 直排 + 5 橫排框），
        # 用一次投票決定整張圖的方向沒有道理。加上 _nutrition_scope 與
        # _label_kind 之後，實測 57 案裡只剩 c41 一案標籤方向混合——
        # 影響雖小，但逐標籤判斷才是正確寫法，成本也只有一行。
        lab_vertical = lab['h'] > lab['w'] * 1.5
        if lab_vertical:
            # 直排：同一「列」是 x 相近的一群，數值在欄位名下方（y 較大）
            band = [n for n in nums
                    if n is not lab
                    and abs(n['cx'] - lab['cx']) <= max(lab['w'], mw) * 0.9
                    and n['cy'] > lab['cy']]
            band.sort(key=lambda n: n['cy'])
        else:
            band = [n for n in nums
                    if n is not lab
                    and abs(n['cy'] - lab['cy']) <= max(lab['h'], mh) * 0.7
                    and n['cx'] > lab['cx']]
            band.sort(key=lambda n: n['cx'])
        if not band:
            continue
        # 帶內把各框的數字依序攤平：一個框可能就含兩欄（「4.6公克 1.3公克」），
        # 也可能一框一欄，攤平後統一取前兩個
        flat = [v for n in band for v in n['nums']]
        if not flat:
            continue
        if key in out:
            # 同框已拿到第一欄，缺第二欄 → 用帶內第一個數補
            out[key] = (out[key][0], flat[0])
        else:
            out[key] = (flat[0], flat[1] if len(flat) > 1 else None)
        if debug:
            print(f"   {key:<16}{'直排' if vertical else '橫排'} "
                  f"帶內 {len(band)} 框 / {len(flat)} 數 → {v1}, {v2}")
    return out
