#!/usr/bin/env python3
# 合成「成分段文字 → 正確清單」的訓練資料，給切分模型用。
#
# 為什麼是這個任務：添加物層漏掉的 52 個裡，34 個是切分失敗（13 個整項沒切出來、
# 21 個切成一大坨），只有 18 個是 OCR 讀錯。切分是目前最大的單一槓桿，
# 而規則已到頂——bench_ceiling.py 量過，餵完美文字給現有抽取器也只有 F1 88.9%。
#
# 為什麼合成資料在這裡特別有效（比 rec fine-tune 那條有效）：
#   rec 合成要渲染出真實的反光／彎曲／字體，有影像域差距（上輪淨損 16 項）。
#   切分是純文字到純文字，**沒有這個 gap**。而且是「反向生成」——先造出清單
#   再序列化成字串，標籤是精確的而不是近似的。
#
# **詞彙沿用 synth_corpus.py 的 ADDITIVES / FOODS**，那兩份來自法規與添加物表、
# 與評估集無關，而且已經有 synth_corpus.py check 的污染檢查可用。
# 絕不可拿 ground_truth 的 ingredients_list 當詞庫——那是汙染紅線。
#
# **邊界位置在生成時就記錄下來（boundaries 欄位），不要事後再對齊。**
# 第一版是「劣化整串之後，用項目文字回去 input 裡找位置」，55% 的樣本對不上——
# 因為字元混淆改了 input（多磷酸鈉→多碳酸鈉），項目文字自然找不到。
# 而且正確的標籤本來就該來自劣化後的字串：模型輸出的是**輸入的片段**，
# 讀錯的字該原樣輸出，修字是 OCR 那一層的事。
#
# 劣化條件對齊實際觀察到的失敗模式，不是憑空想的：
#   丟頓號         → 造出「13 個」那一類（關華豆膠多磷酸鈉 黏成一串）
#   括號讀錯／丟失  → 造出「21 個」那一類（c44 的 316 字一大坨）
#   插入空白換行    → 跨框串接
#   字元混淆       → 用 dict_decode 實際記錄到的混淆對
#
# 用法：
#   python synth_split.py build --n=8000 -o corpus/split_train.jsonl
#   python synth_split.py stats corpus/split_train.jsonl     # 看劣化分布
#   python synth_split.py sanity corpus/split_train.jsonl    # 現有抽取器跑得多好
import argparse
import json
import os
import random
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synth_corpus as SC   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# 功能類別詞。既有結論診斷出這是上輪 rec 語料最大的缺口
# （「劑」在測試集出現 99 次、語料 0 次），切分這邊更不能漏——
# 巢狀複合成分幾乎都是「功能類別詞(物質1、物質2…)」的形狀。
FUNCTIONAL = """
抗氧化劑 複方抗氧化劑 乳化劑 複方乳化劑 粘稠劑 黏稠劑 調味劑 調味料 甜味劑
著色劑 色素 保色劑 膨脹劑 防腐劑 酸味劑 結著劑 品質改良劑 品質改良用劑
複方品質改良劑 食品用複方粉 營養添加劑 維生素 複方胡蘿蔔素 香料 複方香料
豬肉風味粉 雞肉風味粉 高湯 醬油包 調味粉包 油包 麵條
""".split()

# dict_decode.py 實際記錄到的混淆對（out/_dict_fixes.json），不是憑空列的
CONFUSE = {"磷": "碳", "鈉": "納", "己": "已", "醯": "醋", "鉀": "針",
           "藻": "澡", "胺": "假", "合": "含", "華": "垂", "白": "百"}

OPEN_CLOSE = [("(", ")"), ("（", "）"), ("{", "}"), ("[", "]"), ("〔", "〕")]
HEADS = ["成分：", "成分:", "原料：", "內容物：", "配料：", "成份：", ""]


def gen_items(rng, n_items, p_nested):
    """先造出清單——這就是結構的來源。"""
    vocab = SC.ADDITIVES + SC.FOODS
    items = []
    for _ in range(n_items):
        if rng.random() < p_nested:
            head = rng.choice(FUNCTIONAL)
            inner = rng.sample(vocab, rng.randint(2, 6))
            o, c = rng.choice(OPEN_CLOSE)
            items.append(head + o + "、".join(inner) + c)
        else:
            items.append(rng.choice(vocab))
    return items


def serialize(items, rng, cfg):
    """邊組字串邊記錄每一項的起始位置。回傳 (字串, 邊界位置, 用了哪些劣化)。

    位置在這裡就固定下來，之後只做**不改變長度**的劣化（字元混淆），
    或做會改變長度的劣化時同步平移邊界（插入空白）。
    """
    used = []
    head = rng.choice(HEADS)
    buf = [head]
    pos = len(head)
    bounds = []
    dropped = 0

    for i, it in enumerate(items):
        bounds.append(pos)
        buf.append(it)
        pos += len(it)
        if i < len(items) - 1:
            if rng.random() < cfg["p_drop_sep"]:
                dropped += 1          # 頓號被讀丟 → 下一項直接黏上來
            else:
                buf.append("、")
                pos += 1
    text = "".join(buf)
    if dropped:
        used.append("丟頓號x%d" % dropped)

    # 括號讀錯／丟失。刪一個字元會讓其後的邊界左移一格。
    if rng.random() < cfg["p_bracket"]:
        m = re.search(r"[)）}\]〕(（{\[〔]", text)
        if m:
            k = m.start()
            text = text[:k] + text[k + 1:]
            bounds = [b - 1 if b > k else b for b in bounds]
            used.append("括號缺失")

    # 跨框串接留下的空白／換行。插入 L 個字元，其後的邊界右移 L。
    if rng.random() < cfg["p_space"] and len(text) > 2:
        k = rng.randrange(1, len(text))
        ins = rng.choice([" ", "\n", "  "])
        text = text[:k] + ins + text[k:]
        bounds = [b + len(ins) if b >= k else b for b in bounds]
        used.append("插入空白")

    # 字元混淆——長度不變，邊界不動
    chars = list(text)
    n_conf = 0
    for i, ch in enumerate(chars):
        if ch in CONFUSE and rng.random() < cfg["p_confuse"]:
            chars[i] = CONFUSE[ch]
            n_conf += 1
    text = "".join(chars)
    if n_conf:
        used.append("字元混淆x%d" % n_conf)

    bounds = sorted({b for b in bounds if 0 <= b < len(text)})
    return text, bounds, used


def items_from(text, bounds):
    """依邊界把字串切回清單——這是模型該產出的東西（輸入的片段）。"""
    out = []
    for i, b in enumerate(bounds):
        e = bounds[i + 1] if i + 1 < len(bounds) else len(text)
        s = text[b:e].strip(" 、,，;；·\n")
        if s:
            out.append(s)
    return out


def build(n, out, seed, cfg):
    rng = random.Random(seed)
    d = os.path.dirname(os.path.abspath(out))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for _ in range(n):
            items = gen_items(rng, rng.randint(3, 25), cfg["p_nested"])
            text, bounds, used = serialize(items, rng, cfg)
            f.write(json.dumps({
                "input": text,
                "boundaries": bounds,
                "output": items_from(text, bounds),
                "clean": items,
                "degrade": used,
            }, ensure_ascii=False) + "\n")
    print("產生 %d 筆 -> %s" % (n, out))


def stats(path):
    from collections import Counter
    c = Counter()
    n = ni = nn = 0
    for ln in open(path, encoding="utf-8"):
        d = json.loads(ln)
        n += 1
        ni += len(d["output"])
        nn += sum(1 for x in d["output"] if re.search(r"[(（{\[〔]", x))
        for u in d["degrade"]:
            c[re.sub(r"x\d+$", "", u)] += 1
    print("%d 筆，平均 %.1f 項/筆，巢狀項佔 %.0f%%" % (n, ni / n, 100 * nn / ni))
    print("劣化出現率：")
    for k, v in c.most_common():
        print("   %-14s%5.0f%%" % (k, 100 * v / n))


def sanity(path, limit=400):
    """現有規則抽取器在合成資料上的成績。

    這是合成分布是否寫實的檢查：規則在**真實** PP-OCR 文字上是 F1 67.6%、
    在**完美文字**上是 88.9%。如果合成資料上遠高於 88.9%，表示劣化太輕、
    練出來的模型到真實資料會失效；遠低於 67.6% 則是劣化過頭。
    """
    import bench_ingredients as BI
    H = G = P = i = 0
    for i, ln in enumerate(open(path, encoding="utf-8")):
        if i >= limit:
            break
        d = json.loads(ln)
        doc = {"images": [{"lines": [{"text": d["input"],
                                      "score": None, "box": None}]}]}
        got = BI.PARSERS["dict"]("syn", doc,
                                 {"ingredients_list": d["output"]}) or []
        h, g, p, _, _ = BI.score_one(got, d["output"])
        H += h
        G += g
        P += p
    rc = H / G if G else 0
    pr = H / P if P else 0
    f1 = 2 * rc * pr / (rc + pr) if rc + pr else 0
    print("現有規則(dict)在 %d 筆合成資料上： 召回 %.1f%%  精確 %.1f%%  F1 %.1f%%"
          % (min(limit, i + 1), 100 * rc, 100 * pr, 100 * f1))
    print("   對照：真實 PP-OCR 文字 F1 67.6%｜完美文字 F1 88.9%")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--n", type=int, default=8000)
    b.add_argument("-o", default="corpus/split_train.jsonl")
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--p-drop-sep", type=float, default=0.17)
    b.add_argument("--p-bracket", type=float, default=0.33)
    b.add_argument("--p-space", type=float, default=0.30)
    b.add_argument("--p-confuse", type=float, default=0.15)
    b.add_argument("--p-nested", type=float, default=0.30)
    s = sub.add_parser("stats")
    s.add_argument("path")
    q = sub.add_parser("sanity")
    q.add_argument("path")
    q.add_argument("--limit", type=int, default=400)
    a = ap.parse_args()

    if a.cmd == "build":
        build(a.n, a.o, a.seed,
              {"p_drop_sep": a.p_drop_sep, "p_bracket": a.p_bracket,
               "p_space": a.p_space, "p_confuse": a.p_confuse,
               "p_nested": a.p_nested})
    elif a.cmd == "stats":
        stats(a.path)
    else:
        sanity(a.path, a.limit)


if __name__ == "__main__":
    main()
