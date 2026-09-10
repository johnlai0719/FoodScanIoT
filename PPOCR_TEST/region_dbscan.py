#!/usr/bin/env python3
"""空間聚類 ＋ 詞彙投票的成分區定位。

**出處**：HalalBench（arXiv 2604.22754, 2026-02），食品包裝 OCR 的專門 benchmark。
該文在 36 張真實食品包裝上量到：

    Raw OCR          F1 0.129
    Line-based       F1 0.007   ← 幾乎歸零
    DBSCAN flat      F1 0.134
    DBSCAN + voting  F1 0.176   ← 比 raw 高 36%

原文對 line-based 崩掉的解釋：

  > food labels violate line-based layout assumptions: multi-column ingredient
  > lists, curved surfaces, and rotated text regions mean tokens sharing a scan
  > line often belong to entirely different ingredient entries.

**這正是本專案量到的「行序」問題**，而現行 `p_boxsep` 就是行拼接。

# ⚠ 實測結論（2026-09-01）：**方法不移轉，三個變體全部輸給現行的 `p_boxsep`。**
#
#     boxsep（現行）  清單層 F1 67.4   添加物層 72.3
#     dbscan（選一團）        63.0            67.9
#     dbmask（只篩選）        60.9
#     dbclean（篩選＋重排）    59.5
#
#   配對 bootstrap 5000 次：dbscan − boxsep = **−4.5 點，95% CI −9.7 ~ −0.4**，
#   不跨 0，**退步是真的**，不是雜訊。
#
#   **不移轉的原因**：台灣食品標示把品名、內容量、成分、保存期限、注意事項、
#   過敏原印在**同一塊密集文字面板**裡，空間上緊鄰，聚類分不開——分隔訊號是
#   欄位標題（語意），不是距離。HalalBench 的圖 993/1043 是合成的單一成分
#   區塊，那個面板**就是**成分表，前提不同。
#
#   量到的機制：GT 成分文字的字元召回，整頁 OCR **77.4%** → 選中的區域 **71.4%**。
#   **選區域就是在丟成分。**
#
#   本檔與三個 parser 保留，作為已驗證的否決紀錄，不進預設路徑。

**為什麼值得試**：本專案的天花板拆解是
    91.8% 成分文字有進到 OCR 全文 → 89.6% 完美定位下的切分上限 → 69.2% 實際
定位損失約 20 點、切分損失約 10 點。**定位是切分的兩倍**，而且遠在 ±8 的
雜訊底線之上。換讀取器只值 +2.3 點（CI 跨 0），這一刀砍的是大的那一半。

## 與既有 `_dict_split` 的關鍵差別

`_dict_split` 是拿字典去**切文字**，套在整份文件上會炸——字典在營養表、
廠址裡也找得到「麥芽糊精」，實測添加物精確度從 92.5 崩到 47。

**這裡的用法相反：字典是拿來「選區域」的評分器，不切任何字。**
選完之後才把該區域的文字交給既有的切分器。命中在哪裡不重要，
重要的是哪一團命中最多。這是 HalalBench 的 voting 那一步，
該文量到它單獨貢獻精確度 +0.063（相對 +72%）。

## 參數與一個必須先修的前提

依原文：`ε = 1.5 × 中位字高`、`min_samples = 3`。

⚠ **但論文的密度單位是 word 級的框**（ML Kit 回傳詞，平均每張 36 個標註），
**PP-OCR 回傳的是行級的框**。第一版直接照搬，結果整行成分（`原料：水、蔗糖、
柳丁原汁…`）只佔一兩個框，湊不滿 `min_samples=3`，被判成雜訊點丟掉——
c01 的成分文字在整頁 OCR 裡召回 1.00，在選中的區域裡卻只剩 0.11。

**修法是把行框攤成字元級的點再聚類**，讓密度單位與論文一致。
攤點只是為了聚類，文字仍以原本的框為單位重組，不會拆壞任何字。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))

EPS_MULT = 1.5      # 論文值
MIN_SAMPLES = 3     # 論文值


def _boxes(d):
    """把所有影像的框攤平成 (img, text, cx, cy, h, x0, y0)。

    **每張影像各自聚類**——兩張照片的座標系不同，混在一起聚會把
    「正面的品名」和「背面的成分」判成同一團。所以回傳時帶 image 索引。
    """
    out = []
    for i, im in enumerate(d.get('images', [])):
        for ln in im.get('lines', []):
            t = ln.get('text') or ''
            b = ln.get('box')
            if not t or not b:
                continue
            xs = [p[0] for p in b]
            ys = [p[1] for p in b]
            out.append((i, t, sum(xs) / len(xs), sum(ys) / len(ys),
                        max(ys) - min(ys), min(xs), min(ys)))
    return out


def _points(box):
    """把一個行框攤成字元級的點，讓密度單位與論文一致（見檔頭）。

    沿框的長軸等距灑 len(text) 個點。框可能是旋轉的（曲面包裝很常見），
    所以用四個角算長軸，不用 x 範圍——後者在旋轉框上會算錯。
    回傳 [(x, y), ...]，與該框的 index 一起交給聚類。
    """
    _, t, cx, cy, h, x0, y0 = box
    n = max(len(t), 1)
    if n == 1:
        return [(cx, cy)]
    # 長軸方向：用外接矩形的長邊近似，足夠了——聚類只吃距離，不吃精確位置
    w = max(1.0, abs(cx - x0) * 2)
    if w >= h:
        step = w / n
        return [(x0 + step * (k + 0.5), cy) for k in range(n)]
    step = h / n
    return [(cx, y0 + step * (k + 0.5)) for k in range(n)]


def _cluster(pts, eps):
    """DBSCAN。用 sklearn；缺套件時退回單一團（等於不做定位）。"""
    if len(pts) < MIN_SAMPLES:
        return [0] * len(pts)
    try:
        from sklearn.cluster import DBSCAN
    except ImportError:
        return [0] * len(pts)
    import numpy as np
    return DBSCAN(eps=eps, min_samples=MIN_SAMPLES).fit(
        np.array(pts, dtype=float)).labels_.tolist()


_DICT_RE = None


def _dict_re():
    """把成分字典編成一條 regex。

    字典是 `data/ingredient_dict.json`（1367 個添加物名，來自公開參考資料庫，
    **不是評估集的 GT**）。只收添加物也夠用——添加物集中出現在成分欄，
    營養表與廠商地址裡不會有。這正是投票要利用的訊號。
    """
    global _DICT_RE
    if _DICT_RE is None:
        import bench_ingredients as BI
        words = [w for w in BI.load_dict() if len(w) >= 3]
        _DICT_RE = re.compile('|'.join(re.escape(w) for w in words)) if words \
            else re.compile(r'(?!x)x')
    return _DICT_RE


# 成分欄的版面訊號。字典只收添加物，碰到「水、砂糖、鹽」這種全是天然原料的
# 標示會一個都命中不到，光靠字典會選錯團。這幾個詞是標示上的欄位標題，
# 出現在成分欄的機率遠高於其他區塊。
_HEAD = re.compile(r'成\s*分|原\s*料|配\s*料|原材料|內容物')


def _vote(texts):
    """給一團文字打分。分數 = 字典命中數 ＋ 欄位標題加權。

    用**命中數**不用命中率：命中率會讓「只有三個字剛好是添加物」的小碎團
    拿到滿分。要的是「哪一團含最多成分」，那是計數問題。
    """
    s = ''.join(texts)
    hits = len(_dict_re().findall(s))
    head = 3 if _HEAD.search(s) else 0
    return hits + head


def _reading_order(rows, med_h):
    """團內排序：先分行（y 相近者同一行），行內由左到右。

    這一步仍是行的假設，但**範圍已經縮到單一團**——團內的文字本來就屬於
    同一個版面區塊，HalalBench 說的「同一掃描線跨到別的成分欄」在這裡
    不會發生，因為別的欄已經被分到別團了。
    """
    rows = sorted(rows, key=lambda r: (r[6], r[5]))     # y0, x0
    lines, cur, base = [], [], None
    for r in rows:
        if base is None or abs(r[6] - base) <= med_h * 0.6:
            cur.append(r)
            base = r[6] if base is None else base
        else:
            lines.append(cur)
            cur, base = [r], r[6]
    if cur:
        lines.append(cur)
    return [b[1] for ln in lines for b in sorted(ln, key=lambda r: r[5])]


def region_text(d, sep='\x01', debug=False):
    """回傳「最像成分欄」的那一團文字。找不到就回傳 None。

    `sep` 沿用 `p_boxsep` 的框邊界分隔符——框的邊界是 OCR 讀丟頓號時
    唯一還在的分界訊號，聚類不該把它丟掉。
    """
    boxes = _boxes(d)
    if not boxes:
        return None
    heights = sorted(b[4] for b in boxes if b[4] > 0)
    if not heights:
        return None
    med_h = heights[len(heights) // 2]
    eps = max(med_h * EPS_MULT, 1.0)

    best, best_score, info = None, 0, []
    for img_i in {b[0] for b in boxes}:
        sub = [b for b in boxes if b[0] == img_i]
        # 字元級的點 → 聚類 → 再用「多數決」把整個框歸回某一團。
        # 一個框的字元不太可能分屬兩團（它們本來就相鄰），但真發生時
        # 以多數為準，不切開那個框。
        pts, owner = [], []
        for bi, b in enumerate(sub):
            for xy in _points(b):
                pts.append(xy); owner.append(bi)
        labels = _cluster(pts, eps)
        vote = [{} for _ in sub]
        for lab, bi in zip(labels, owner):
            vote[bi][lab] = vote[bi].get(lab, 0) + 1
        box_lab = [max(v, key=v.get) if v else -1 for v in vote]
        for lab in set(box_lab):
            if lab == -1:                      # DBSCAN 的雜訊點，不成團
                continue
            grp = [b for b, l in zip(sub, box_lab) if l == lab]
            texts = _reading_order(grp, med_h)
            sc = _vote(texts)
            info.append((img_i, lab, len(grp), sc, ''.join(texts)[:40]))
            if sc > best_score:
                best, best_score = texts, sc
    if debug:
        for row in sorted(info, key=lambda r: -r[3])[:8]:
            print('  img%d 團%-3d %3d框 分數%3d  %s' % row)
    if best is None or best_score == 0:
        return None
    return sep.join(best)


def load(cid, preset='v6_hires__boxth0.4'):
    p = os.path.join(HERE, 'out', preset, f'{cid}.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    for cid in sys.argv[1:]:
        d = load(cid)
        if d is None:
            print(cid, '缺檔')
            continue
        print('═══', cid)
        t = region_text(d, sep='｜', debug=True)
        print('  選中：', (t or '（無）')[:300])


# ─────────────────────────────────────────────────────────────────────────
# 第二種用法：不選成分區，改成**剔掉明顯不是成分的團**
#
# 為什麼改用法：第一種（選最高分的一團）實測輸給現行的 `p_boxsep`
#   boxsep 67.4 ／ dbscan 選取 53.9（抽出 830 → 1136，精確度崩到 45.7%）
# 原因是台灣標示把品名、內容量、成分、保存、注意事項印在同一塊密集文字裡，
# **空間分不開它們**——分隔訊號是欄位標題（語意），不是距離。論文的圖多為
# 合成的單一成分區塊，那個前提在這裡不成立。
#
# 但聚類確實做對了一件事：**營養表被乾淨地切成獨立一團**（c01 的 25 個框、
# c48 的兩團各 9 個框）。那正是 `_denoise_nutrition` 在項目內部手工擦的東西，
# 在這裡可以整團丟掉，而且不必碰任何字元。

_NUTRI = re.compile(r'大卡|公克|毫克|kcal|熱量|蛋白質|飽和脂肪|反式脂肪|'
                    r'碳水化合物|膳食纖維|每100|每一份量|本包裝含')
_ADDR = re.compile(r'股份有限公司|有限公司|企業社|服務專線|客服|網址|www\.|'
                   r'地址|臺灣|台灣|市.{0,6}區|鄉|鎮|號$')


def _is_junk(texts):
    """這一團是不是明顯的非成分區。

    兩個判準各自獨立，任一成立就丟：
      1. 營養表——數值與單位密集，而字典一個都沒命中
      2. 廠商地址／客服——公司行號與地址字樣，同樣沒有字典命中

    **有字典命中就一律保留**，寧可留下雜訊也不要丟掉成分。
    成分被丟掉是召回損失，救不回來；留下雜訊還有下游的 `_plausible` 與
    `_looks_like_ingredient` 可以擋。
    """
    s = ''.join(texts)
    if _dict_re().search(s):
        return False
    n = len(s) or 1
    if len(_NUTRI.findall(s)) >= 3 and sum(c.isdigit() for c in s) / n > 0.15:
        return True
    return len(_ADDR.findall(s)) >= 2


def kept_text(d, sep=''):
    """剔掉垃圾團之後，其餘全部保留。找不到任何框就回 None。"""
    boxes = _boxes(d)
    if not boxes:
        return None
    heights = sorted(b[4] for b in boxes if b[4] > 0)
    if not heights:
        return None
    med_h = heights[len(heights) // 2]
    eps = max(med_h * EPS_MULT, 1.0)

    out = []
    for img_i in sorted({b[0] for b in boxes}):
        sub = [b for b in boxes if b[0] == img_i]
        pts, owner = [], []
        for bi, b in enumerate(sub):
            for xy in _points(b):
                pts.append(xy); owner.append(bi)
        labels = _cluster(pts, eps)
        vote = [{} for _ in sub]
        for lab, bi in zip(labels, owner):
            vote[bi][lab] = vote[bi].get(lab, 0) + 1
        box_lab = [max(v, key=v.get) if v else -1 for v in vote]
        for lab in sorted(set(box_lab)):
            grp = [b for b, l in zip(sub, box_lab) if l == lab]
            texts = _reading_order(grp, med_h)
            if lab != -1 and _is_junk(texts):
                continue
            out.extend(texts)
    return sep.join(out) if out else None


def keep_mask(d):
    """回傳每個框要不要保留（順序與 `_boxes(d)` 一致）。

    **這個函式刻意不重組文字。** `kept_text` 與 `region_text` 都用
    `_reading_order` 重排過，那等於同時改了兩件事——區域篩選與行序——
    輸了也不知道是哪一件造成的。這裡只回傳遮罩，讓呼叫端沿用原本的順序，
    把「篩選」單獨隔離出來量。
    """
    boxes = _boxes(d)
    if not boxes:
        return None
    heights = sorted(b[4] for b in boxes if b[4] > 0)
    if not heights:
        return None
    med_h = heights[len(heights) // 2]
    eps = max(med_h * EPS_MULT, 1.0)

    mask = [True] * len(boxes)
    for img_i in {b[0] for b in boxes}:
        idx = [k for k, b in enumerate(boxes) if b[0] == img_i]
        sub = [boxes[k] for k in idx]
        pts, owner = [], []
        for bi, b in enumerate(sub):
            for xy in _points(b):
                pts.append(xy); owner.append(bi)
        labels = _cluster(pts, eps)
        vote = [{} for _ in sub]
        for lab, bi in zip(labels, owner):
            vote[bi][lab] = vote[bi].get(lab, 0) + 1
        box_lab = [max(v, key=v.get) if v else -1 for v in vote]
        for lab in set(box_lab):
            if lab == -1:
                continue
            grp = [k for k, l in zip(idx, box_lab) if l == lab]
            if _is_junk([boxes[k][1] for k in grp]):
                for k in grp:
                    mask[k] = False
    return mask
