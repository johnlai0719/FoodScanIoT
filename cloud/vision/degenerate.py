#!/usr/bin/env python3
"""偵測自回歸重複退化，供串流解碼時提早中止。

為什麼不是用長度上限
--------------------
2026-09-14 以 177 案的既有輸出量測，長度**沒有**判別力：

    c150 百事可樂無糖   3292 字   「公克」重複 1366 次   ← 退化
    c159 義美蘇打餅乾   3563 字   最高重複 6 次          ← 正常（全集最長）

最長的那一案是正常的，退化的那一案還更短。純長度上限會截掉 c159 這種
密集雙語標示，而 c150 照樣跑滿——**擋錯人，也沒擋到該擋的**。

判準改用「尾段是不是週期性的」
------------------------------
退化的形狀是某個短單元被無限複製（「公克公克公克…」）。所以只看**尾段**，
找有沒有一個長度 ≤ MAX_PERIOD 的單元能解釋它。正常文字即使重複用詞
（營養表的「公克」欄位）也不會整段都是同一個週期，因為中間夾著數字與欄位名。

只看尾段而不是全文，是因為退化一定發生在**後面**——前半段通常是讀對的，
要保住。偵測到之後把週期段切掉、保留前面，而不是整份丟掉。
"""
import re

# 週期長度上限。「公克」是 2，中英混雜的重複片語實測到 12 以內。
# 放太大會把正常的表格列（每列結構相同但內容不同）誤判成週期。
MAX_PERIOD = 16
# 檢查視窗。要夠長才能區分「重複用詞」與「無限複製」——
# 180 字在 c159 那種密集標示上仍有 40 以上的相異字元。
WINDOW = 180
# 視窗內符合週期的比例門檻。留一成餘裕給 OCR 偶發的雜字。
PERIOD_RATIO = 0.9


def _period_of(s: str):
    """找出能解釋 s 的最短週期長度；找不到回 None。"""
    n = len(s)
    for p in range(1, MAX_PERIOD + 1):
        if n < p * 4:            # 至少要重複四次才算週期，兩次是巧合
            break
        hit = sum(1 for i in range(p, n) if s[i] == s[i - p])
        if hit / (n - p) >= PERIOD_RATIO:
            return p
    return None


def find_degenerate_tail(text: str):
    """回傳 (退化起點索引, 週期長度)；沒有退化回 (None, None)。

    起點是往前回溯到週期真正開始的位置，讓呼叫端可以只切掉那一段。
    """
    if len(text) < WINDOW:
        return None, None
    p = _period_of(text[-WINDOW:])
    if p is None:
        return None, None
    unit = text[-p:]
    # 往前找這個單元連續重複到哪裡為止
    i = len(text)
    while i - p >= 0 and text[i - p:i] == unit:
        i -= p
    return i, p


def is_degenerate(text: str) -> bool:
    return find_degenerate_tail(text)[0] is not None


def trim(text: str):
    """切掉退化的尾段，回傳 (保留的文字, 是否切過, 切掉幾個字)。

    **保留前段**而不是整份丟掉：退化發生在後面，前半通常是讀對的，
    而那正是成分表所在。整份丟掉等於把讀對的也一起丟。
    """
    i, p = find_degenerate_tail(text)
    if i is None:
        return text, False, 0
    return text[:i], True, len(text) - i


def find_runs(text: str, min_len: int = WINDOW):
    """掃描**全文**找出所有週期性長段，回傳 [(起, 迄, 週期)]。

    與 `find_degenerate_tail` 分開的理由：
      - 串流解碼時只需要看尾段——退化正在發生，看尾巴就能提早中止。
      - 事後清理既有輸出時必須掃全文：**退化不一定在結尾**。
        c150 百事可樂無糖的「公克」重複 1366 次之後，模型又接回營養表，
        乾淨的表格結尾把退化段蓋在中間，只看尾段會整案漏掉。
    """
    runs, i, n = [], 0, len(text)
    while i < n:
        found = None
        for p in range(1, MAX_PERIOD + 1):
            unit = text[i:i + p]
            if not unit or len(set(unit)) == 0:
                continue
            j = i
            while j + p <= n and text[j:j + p] == unit:
                j += p
            if j - i >= min_len:
                found = (i, j, p)
                break
        if found:
            runs.append(found)
            i = found[1]
        else:
            i += 1
    return runs


def clean(text: str, min_len: int = WINDOW):
    """把全文裡的週期性長段全部移除，回傳 (清後文字, 移除字數)。"""
    runs = find_runs(text, min_len)
    if not runs:
        return text, 0
    out, last, removed = [], 0, 0
    for a, b, _ in runs:
        out.append(text[last:a])
        removed += b - a
        last = b
    out.append(text[last:])
    return "".join(out), removed
