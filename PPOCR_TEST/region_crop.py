#!/usr/bin/env python3
# 用便宜的 OCR 框先把「營養表」與「成分區」切出來，之後才決定各自送給誰讀。
#
# 為什麼要有這一步：整張照片餵給任何讀取器都有三個問題——輸出長度沒有上限
# （PaddleOCR-VL 三個杯麵案就是這樣爆的）、送出去的資料量大（正式管線平均
# 每案 1.9MB、p50 11.3s）、而且營養表與成分段其實該用不同方法讀
# （數值可用算術驗證，成分不行）。
#
# 這一步是整條管線的單點故障：錨點找錯，後面全錯而且會安靜地錯。所以它必須
# 有自己的指標，不能只看端到端 F1——這支的下半部就是在量它。
#
# 用法：
#   python region_crop.py                    # 全測試集，印區域命中報告
#   python region_crop.py --detail c58       # 單案：切出哪些塊、各自判成什麼
#   python region_crop.py --dump out/_regions.json
import argparse
import json
import os
import re
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# 取 det 框的來源目錄。⚠ 寫死會讓測試集擴充後靜默只處理舊案例——
# 2026-09-07 同一個坑在 bench_ingredients／score_items／boot_compare／emit_json
# 各出現一次。用環境變數 PPOCR_BOXES 覆寫。
BOXES = os.environ.get("PPOCR_BOXES") or "v6_best"

# 錨點用「印得比較大、通常不會讀錯」的標題字，不用成分名本身。
# 成分名是開放詞彙，拿它當錨點等於要先解決原問題。
ING_ANCHORS = ["成分", "成份", "原料", "內容物", "配料"]

# 營養表的種子詞只收「不會出現在成分表裡」的。「糖」「鈉」「公克」不能收——
# 成分表的「蔗糖」「多磷酸鈉」會讓整個成分區被誤判成營養表（實測第一版就是
# 這樣：48/55 行併成一塊、整塊判成營養表，成分區 57 案只切出 9 案）。
NUT_SEEDS = ["熱量", "蛋白質", "飽和脂肪", "反式脂肪", "碳水化合物",
             "每一份量", "本包裝含", "營養標示"]
# ── 四個可調的旋鈕（2026-09-07 掃過，結論：維持預設）────────────────────
# 全網格 108 組。**代理指標（PP-OCR 文字有沒有落在框裡）說三個有效，
# 端到端重跑之後全部不成立。**
#
#   代理指標           NUT_SEED_MIN 3→2  營養區 159→167，成分區與面積不變
#                     LONG_RATIO .45→.6 面積 59%→51% 且營養留存 93.4%→95.5%
#                     CLUSTER_K 1.5→3.0 成分留存 98.4%→99.4%
#                     A/B 過擬合檢查兩半邊同向改善，看起來很穩
#
#   端到端（重跑 Hunyuan，177 案）
#                     三個一起改  添加物 −0.3（CI 跨 0）、營養格 2121→2113
#                     只改 seed   添加物 ±0.0、營養格 2121→**2116**
#
# **為什麼 seed=2 會變差**——它多找到 8 個營養區，但其中有找錯的：
#
#   c107  真的是營養表         5 → 9 格   ✔
#   c83   找到「574.2大卡/3份」那一小塊，injects 錯的候選   16 → 6 格
#   c77   **找到的其實是成分表**（「檸檬酸蝦青精、麥芽糊精…」重複三次）
#                                                        10 → 6 格
#
# `NUT_SEED_MIN = 3` 不是隨手訂的數字，**它就是「成分表被誤判成營養表」的護欄**
# ——那正是第一版踩過的坑（48/55 行併成一塊、57 案只切出 9 案）。放寬它等於
# 拿「多找到幾張營養表」去換「偶爾把成分表當成營養表」，實測是淨損。
#
# ⚠ **最重要的一課：`found` 這個指標會把「找錯區域」算成成功。**
#    c77 的 found=True，但框裡是成分。要抓這種錯需要另外兩條指標
#    （框內錨點、裁切純度），見 [[資料流與驗收總表#區域裁切]]。
#
# 旋鈕保留為環境變數是為了讓上面這些結果可以重現，**不是建議去調它**。
NUT_SEED_MIN = int(os.environ.get("RC_SEED_MIN") or 3)

# 分成分區與營養表的主訊號是「行有多長」：成分是橫貫整個標示面的長行，
# 營養表是一格一格的短行。實測中位數 c38 853 vs 136、c09 1782 vs 274、
# c44 2260 vs 335。c58 是例外（營養表整列被框成一長條），所以長度只當
# 第一道，營養表仍然先用種子詞抓出來扣掉。
LONG_RATIO = float(os.environ.get("RC_LONG_RATIO") or 0.45)  # × 該圖行長的 90 百分位
CLUSTER_K = float(os.environ.get("RC_K") or 1.5)             # 成分塊的分群間距
GROW_ROUNDS = int(os.environ.get("RC_GROW_ROUNDS") or 2)     # 營養區往外吸收的輪數


def bbox(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def thickness(box):
    x0, y0, x1, y1 = bbox(box)
    return min(x1 - x0, y1 - y0)


def gap(a, b):
    """兩個 axis-aligned bbox 的間距（重疊時為 0）。取兩軸較大者。"""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(0, max(bx0 - ax1, ax0 - bx1))
    dy = max(0, max(by0 - ay1, ay0 - by1))
    return max(dx, dy)


def cluster(lines, k=1.5):
    """把文字行依鄰近度分群。距離門檻用行厚度的中位數當尺——
    這樣不必知道圖多大、字多大，直排橫排也一體適用。"""
    if k is None:
        k = CLUSTER_K
    idx = [i for i, l in enumerate(lines) if l.get("box")]
    if not idx:
        return []
    unit = statistics.median(thickness(lines[i]["box"]) for i in idx) or 1
    thr = k * unit
    bbs = {i: bbox(lines[i]["box"]) for i in idx}

    parent = {i: i for i in idx}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            i, j = idx[a], idx[b]
            if gap(bbs[i], bbs[j]) <= thr:
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[ra] = rb

    groups = {}
    for i in idx:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def block_info(lines, members):
    txt = "".join(lines[i]["text"] for i in members)
    norm = S.normalize(txt, fold_variants=True)
    bbs = [bbox(lines[i]["box"]) for i in members]
    return {
        "members": members,
        "n_lines": len(members),
        "text": txt,
        "norm": norm,
        "bbox": (min(b[0] for b in bbs), min(b[1] for b in bbs),
                 max(b[2] for b in bbs), max(b[3] for b in bbs)),
    }


def length(box):
    x0, y0, x1, y1 = bbox(box)
    return max(x1 - x0, y1 - y0)


def union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


CELL = re.compile(r"\d|公克|毫克|大卡|公毫|克")

# 頓號訊號。SEP_MIN=0 即關閉，行為與 2026-09-10 之前完全相同。
SEP_RE = re.compile(r"[、，,；;]")
SEP_MIN = int(os.environ.get("RC_SEP_MIN") or 2)


def grow(seed_box, lines, idx_pool, unit, long_thr, rounds=None):
    """從種子框往外吸收營養表的數值格。

    只吸收「短行且看起來像格子」的——含數字或單位。不設這個條件的話成長會
    雪崩：c40 實測整片成分行被營養表吞掉，成分區只剩 3 行（40 項掉到 9 項）。
    營養表的數值欄與標籤欄是分開的框，所以還是得成長，不能只取種子。
    """
    if rounds is None:
        rounds = GROW_ROUNDS
    box = seed_box
    taken = set()
    for _ in range(rounds):
        pad = (box[0] - 3 * unit, box[1] - 3 * unit,
               box[2] + 3 * unit, box[3] + 3 * unit)
        for i in idx_pool:
            if i in taken:
                continue
            if length(lines[i]["box"]) >= long_thr:
                continue
            if not CELL.search(lines[i]["text"]):
                continue
            b = bbox(lines[i]["box"])
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if pad[0] <= cx <= pad[2] and pad[1] <= cy <= pad[3]:
                taken.add(i)
        if taken:
            box = union([seed_box] + [bbox(lines[i]["box"]) for i in taken])
    return box, taken


def find_regions(lines, k=None):
    """先抓營養表（結構強、種子詞乾淨），扣掉之後，剩下的長行才是成分區。

    順序很重要。反過來做——先分群再分類——會失敗，因為包裝上營養表就緊貼著
    成分段，鄰近度分不開它們（實測整張標示 48/55 行併成一塊）。
    """
    if k is None:
        k = CLUSTER_K
    idx = [i for i, l in enumerate(lines) if l.get("box")]
    out = {"ingredients": None, "nutrition": None, "blocks": [], "why": {}}
    if not idx:
        return out
    unit = statistics.median(thickness(lines[i]["box"]) for i in idx) or 1
    lens = sorted(length(lines[i]["box"]) for i in idx)
    p90 = lens[int(0.9 * (len(lens) - 1))]
    thr = LONG_RATIO * p90

    # ── 營養表：種子詞 → 只往「短的格子」成長 ──
    seeds, seen_words = [], set()
    for i in idx:
        n = S.normalize(lines[i]["text"], fold_variants=True)
        w = [x for x in NUT_SEEDS if S.normalize(x, True) in n]
        if w:
            seeds.append(i)
            seen_words.update(w)
    nut_taken = set()
    if len(seen_words) >= NUT_SEED_MIN:
        nb, nut_taken = grow(union([bbox(lines[i]["box"]) for i in seeds]),
                             lines, idx, unit, thr)
        out["nutrition"] = nb
        out["why"]["nutrition"] = f"種子詞 {len(seen_words)} 種：{'、'.join(sorted(seen_words))}"
        nut_taken |= set(seeds)

    # ── 成分區：扣掉營養表之後的長行 ──
    rest = [i for i in idx if i not in nut_taken]
    if not rest:
        return out
    # ── 成分區的候選池：行長 **或** 頓號（[[15-以頓號密度補強成分區定位]]）──
    # 行長量的是「這一行橫貫整個標示面」，頓號量的是「這一行在列舉東西」，
    # 兩者的失效模式互補：
    #   行長失效   短的續行（c99「、玫瑰鹽。」約 0.1×p90，必然被排除）
    #   頓號失效   OCR 把整段頓號都讀丟（c57_來一客 的成分表一個頓號都沒有）
    #
    # 逐行分辨力實測（成分行 1003、其他行 7923）：
    #   行長÷p90   成分 0.998 vs 其他 0.408   單一門檻最佳 F1 0.48
    #   頓號個數    成分 3.00  vs 其他 **0.00**  單一門檻最佳 F1 **0.70**
    # 其他行的頓號個數連 p75 都是 0。
    #
    # ⚠ 不收「·」——實測它在台灣包裝上多作項目符號用
    #    （「·原產地：台灣」「·負責廠商」「·天天好運來」）。
    # ⚠ 這解決不了「框錯位置」：c99 的行銷文案本身就有 2–3 個頓號。
    long_idx = [i for i in rest
                if length(lines[i]["box"]) >= thr
                or (SEP_MIN and len(SEP_RE.findall(lines[i]["text"])) >= SEP_MIN)]
    if not long_idx:
        return out

    sub = [lines[i] for i in long_idx]
    groups = cluster(sub, k)
    blocks = []
    for g in groups:
        members = [long_idx[j] for j in g]
        bi = block_info(lines, members)
        anc = [w for w in ING_ANCHORS if S.normalize(w, True) in bi["norm"]]
        bi["anchor"] = anc[0] if anc else None
        blocks.append(bi)
    out["blocks"] = blocks

    # 有錨點就取有錨點的那些塊（成分常分成麵包／油包／粉包好幾團，全收）；
    # 完全沒錨點時退而取字最多的一塊，並記下來是用退路判的。
    anchored = [b for b in blocks if b["anchor"]]
    if anchored:
        out["ingredients"] = union([b["bbox"] for b in blocks
                                    if b["anchor"] or b["n_lines"] >= 2])
        out["why"]["ingredients"] = f"錨點「{anchored[0]['anchor']}」＋長行"
    else:
        best = max(blocks, key=lambda b: len(b["norm"]))
        out["ingredients"] = best["bbox"]
        out["why"]["ingredients"] = "無錨點，取字最多的長行塊（退路）"
    return out


def text_in(lines, box):
    if box is None:
        return ""
    x0, y0, x1, y1 = box
    keep = []
    for l in lines:
        if not l.get("box"):
            continue
        bx0, by0, bx1, by1 = bbox(l["box"])
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            keep.append(l["text"])
    return "\n".join(keep)


def area_frac(box, lines):
    """裁切框佔「所有文字外接框」的比例。用文字範圍當分母而不是整張圖，
    因為圖上大半是商品外觀，用整張圖當分母會讓所有區域看起來都很小。"""
    if box is None:
        return None
    bbs = [bbox(l["box"]) for l in lines if l.get("box")]
    if not bbs:
        return None
    ax = (max(b[2] for b in bbs) - min(b[0] for b in bbs)) * \
         (max(b[3] for b in bbs) - min(b[1] for b in bbs))
    a = (box[2] - box[0]) * (box[3] - box[1])
    return a / ax if ax else None


def evaluate(k=None, dump=None):
    cases = json.load(open(os.path.join(S.EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    rows, dumped = [], {}
    for c in cases:
        cid = c["case_id"]
        p = os.path.join(HERE, "out", BOXES, f"{cid}.json")
        gt = S.load_gt(cid, c.get("category") or "")
        if not os.path.exists(p) or not gt or not gt.get("is_food_label", True):
            continue
        rec = json.load(open(p, encoding="utf-8"))

        full_ing, crop_ing, ing_box_found = [], [], False
        full_txt, nut_txt = [], []
        nut_box_found = False
        areas, nareas = [], []
        for img in rec["images"]:
            lines = img.get("lines") or []
            r = find_regions(lines, k)
            dumped.setdefault(cid, []).append(
                {"path": img["path"],
                 "ingredients": r["ingredients"], "nutrition": r["nutrition"]})
            if r["ingredients"]:
                ing_box_found = True
                areas.append(area_frac(r["ingredients"], lines))
            if r["nutrition"]:
                nut_box_found = True
                nareas.append(area_frac(r["nutrition"], lines))
            full_ing.append("\n".join(l["text"] for l in lines))
            crop_ing.append(text_in(lines, r["ingredients"]))
            full_txt.append("\n".join(l["text"] for l in lines))
            nut_txt.append(text_in(lines, r["nutrition"]))

        # 營養數值：整張讀得到幾格 → 裁進營養區之後還剩幾格
        fn = cn = 0
        ftxt, ntxt = "\n".join(full_txt), "\n".join(nut_txt)
        for grp in ("nutrition", "nutrition_per_serving"):
            for kk, vv in (gt.get(grp) or {}).items():
                if vv is None:
                    continue
                if S.number_present(vv, ftxt):
                    fn += 1
                    if S.number_present(vv, ntxt):
                        cn += 1

        gl = gt.get("ingredients_list") or []
        if gl:
            fa = S.normalize("\n".join(full_ing), fold_variants=True)
            ca = S.normalize("\n".join(crop_ing), fold_variants=True)
            fe, ff, _ = S.ingredient_hits(gl, fa)
            ce, cf, _ = S.ingredient_hits(gl, ca)
            rows.append({
                "case_id": cid, "category": c.get("category"),
                "gt_n": len(gl),
                "full_hit": len(fe) + len(ff), "crop_hit": len(ce) + len(cf),
                "ing_found": ing_box_found, "nut_found": nut_box_found,
                "area": statistics.mean([a for a in areas if a]) if areas else None,
                "narea": statistics.mean([a for a in nareas if a]) if nareas else None,
                "nut_full": fn, "nut_crop": cn,
            })
    if dump:
        json.dump(dumped, open(dump, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    return rows


def report(rows, k):
    n = len(rows)
    ing_ok = sum(1 for r in rows if r["ing_found"])
    nut_ok = sum(1 for r in rows if r["nut_found"])
    tf = sum(r["full_hit"] for r in rows)
    tc = sum(r["crop_hit"] for r in rows)
    tg = sum(r["gt_n"] for r in rows)
    areas = [r["area"] for r in rows if r["area"]]
    print(f"\n{'='*72}\n分群門檻 k={k}   case 數 = {n}\n{'='*72}")
    print(f"\n── 有沒有切出區域 ──")
    print(f"   成分區找到 {ing_ok}/{n}    營養區找到 {nut_ok}/{n}")
    print(f"\n── 切完之後成分還在不在（GT {tg} 項）──")
    print(f"   整張圖讀得到 {tf} 項 → 只看成分區裁切內 {tc} 項，"
          f"因裁切而掉了 {tf - tc} 項")
    if areas:
        print(f"   成分區平均佔文字範圍 {statistics.mean(areas):.0%}"
              f"（送出去的資料量大致按這個比例降）")

    nf = sum(r["nut_full"] for r in rows)
    nc = sum(r["nut_crop"] for r in rows)
    nareas = [r["narea"] for r in rows if r["narea"]]
    print(f"\n── 切完之後營養數值還在不在 ──")
    print(f"   整張圖讀得到 {nf} 格 → 只看營養區裁切內 {nc} 格，"
          f"因裁切而掉了 {nf - nc} 格")
    if nareas:
        print(f"   營養區平均佔文字範圍 {statistics.mean(nareas):.0%}")

    nbad = [r for r in rows if r["nut_full"] - r["nut_crop"] > 0 or not r["nut_found"]]
    if nbad:
        print(f"\n── 營養區要修的 {len(nbad)} 案 ──")
        for r in sorted(nbad, key=lambda r: -(r["nut_full"] - r["nut_crop"]))[:12]:
            tag = "沒切出營養區" if not r["nut_found"] else \
                  f"裁切掉了 {r['nut_full'] - r['nut_crop']} 格"
            print(f"   {r['case_id']:<28}{r['category'] or '':<16}"
                  f"整張 {r['nut_full']:>3} → 裁後 {r['nut_crop']:>3}   {tag}")

    bad = [r for r in rows if r["full_hit"] - r["crop_hit"] > 0 or not r["ing_found"]]
    if bad:
        print(f"\n── 要修的 {len(bad)} 案 ──")
        for r in sorted(bad, key=lambda r: -(r["full_hit"] - r["crop_hit"])):
            tag = "沒切出成分區" if not r["ing_found"] else \
                  f"裁切掉了 {r['full_hit'] - r['crop_hit']} 項"
            print(f"   {r['case_id']:<28}{r['category'] or '':<16}"
                  f"整張 {r['full_hit']:>3}/{r['gt_n']:<3} → 裁後 {r['crop_hit']:>3}   {tag}")


def detail(case_id, k):
    cases = json.load(open(os.path.join(S.EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    c = next((x for x in cases if x["case_id"].startswith(case_id)), None)
    if not c:
        sys.exit(f"找不到 {case_id}")
    rec = json.load(open(os.path.join(HERE, "out", BOXES,
                                      f"{c['case_id']}.json"), encoding="utf-8"))
    for img in rec["images"]:
        lines = img.get("lines") or []
        r = find_regions(lines, k)
        print(f"\n{img['path']}   {len(lines)} 行 → 長行塊 {len(r['blocks'])} 個")
        for bi in sorted(r["blocks"], key=lambda b: -b["n_lines"])[:10]:
            print(f"   {'錨點' + bi['anchor'] if bi['anchor'] else '-':<10}"
                  f"{bi['n_lines']:>3}行  {str(tuple(int(v) for v in bi['bbox'])):<30}"
                  f"{bi['text'][:40]}")
        print(f"   => 成分區 {r['ingredients']}   {r['why'].get('ingredients','')}")
        print(f"   => 營養區 {r['nutrition']}   {r['why'].get('nutrition','')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=float, default=1.5, help="分群距離門檻（行厚度的倍數）")
    ap.add_argument("--detail", default=None)
    ap.add_argument("--dump", default=None)
    a = ap.parse_args()
    if a.detail:
        detail(a.detail, a.k)
    else:
        report(evaluate(a.k, a.dump), a.k)
