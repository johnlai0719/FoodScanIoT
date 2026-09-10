#!/usr/bin/env python3
# 抓通用繁體中文語料，給 rec fine-tune 當「1:1:1」裡的通用那一份。
#
# 為什麼一定要有：上一輪與這一輪都跳過通用資料，實測後果一致——
# 輸出分布向食品詞彙 collapse。v2 新語料把成分完全命中從 583 拉到 602，
# **但營養漏欄從 65 退步到 90**：模型把算力全給了成分詞，數字與單位就讀差了。
# 官方建議 真實:合成:通用 ≈ 1:1:1，那個比例是防這件事的。
#
# 來源用維基百科隨機條目（`variant=zh-tw` 取繁體）：公開、通用領域、
# 與評估集無關，而且**刻意避開食品主題**——這份的作用是平衡，
# 不是再補一份成分詞。
#
# 行長切分沿用 synth_corpus.py 的 LEN_BUCKETS，取自 3931 條真實偵測行的分布。
# 分布不對的話，一半的算力會花在不存在的樣本型態上。
#
# 用法：
#   python fetch_general.py --n=4000 -o corpus/general_lines.txt
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synth_corpus as SC   # noqa: E402

API = "https://zh.wikipedia.org/w/api.php"
UA = "FoodScanIoT-research/1.0 (OCR training corpus)"
# 抓到食品主題就跳過——這份的用途是平衡，不是再補一份成分詞
FOOD = re.compile(r"食品|食物|料理|烹飪|飲料|添加物|營養|餐廳|小吃|菜餚|甜點")


def fetch_batch(limit=20):
    q = urllib.parse.urlencode({
        "action": "query", "generator": "random", "grnnamespace": 0,
        "grnlimit": limit, "prop": "extracts", "explaintext": 1,
        "exintro": 1, "format": "json", "variant": "zh-tw"})
    req = urllib.request.Request(API + "?" + q, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    out = []
    for p in (d.get("query", {}).get("pages") or {}).values():
        t = (p.get("extract") or "").strip()
        if len(t) < 40 or FOOD.search(t):
            continue
        out.append(re.sub(r"\s+", " ", t))
    return out


def _extend(text, k):
    """把切點往後推到拉丁字母／數字串的結尾。

    往回退（前兩版的做法）會撞到最小長度下限而放棄，切出「KR」「S999型輕」
    這種半截 token。往前延伸不會有下限問題，而且長度只多幾個字元。
    中文字之間切是可以的——真實偵測行本來就會在版面邊界斷字。
    """
    n = len(text)
    while 0 < k < n:
        a, b = text[k - 1], text[k]
        if a.isascii() and a.isalnum() and b.isascii() and b.isalnum():
            k += 1
        else:
            break
    return k


def to_lines(text, rng):
    """切成符合真實偵測行長度分布的短行。"""
    lines = []
    i = 0
    while i < len(text):
        n = SC.sample_target_len(rng)
        stop = min(len(text), i + n)
        chunk = text[i:stop]
        m = max((chunk.rfind(c) for c in "，。、；：！？）」』"), default=-1)
        if m > n * 0.5:
            stop = i + m + 1
        stop = _extend(text, stop)
        piece = text[i:stop].strip()
        if len(piece) >= 2:
            lines.append(piece)
        i = stop if stop > i else i + 2
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4000, help="目標行數")
    ap.add_argument("-o", default="corpus/general_lines.txt")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    seen, lines = set(), []
    t0 = time.time()
    fails = 0
    while len(lines) < a.n and fails < 8:
        try:
            arts = fetch_batch()
        except Exception as e:
            fails += 1
            print("  抓取失敗 %s（第 %d 次）" % (str(e)[:50], fails))
            time.sleep(2)
            continue
        for t in arts:
            for ln in to_lines(t, rng):
                if ln not in seen:
                    seen.add(ln)
                    lines.append(ln)
        print("\r  已收集 %d / %d 行" % (len(lines), a.n), end="", flush=True)
    print()

    lines = lines[:a.n]
    d = os.path.dirname(os.path.abspath(a.o))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(a.o, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    chars = set("".join(lines))
    print("寫入 %d 行、%d 種字元、%.0fs -> %s"
          % (len(lines), len(chars), time.time() - t0, a.o))
    print("\n下一步：python synth_corpus.py check %s" % a.o)


if __name__ == "__main__":
    main()
