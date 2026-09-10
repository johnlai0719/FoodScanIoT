#!/usr/bin/env python3
# 盤點 ground_truth 對「巢狀複合成分」的處理慣例，找出不一致的地方。
#
# 為什麼要做：`調味劑(琥珀酸二鈉、5'-次黃嘌呤核苷磷酸二鈉)` 算 1 項還是 3 項，
# GT 現在兩種寫法都有。這造成的問題不是「難看」，是**評估指標有一部分在量
# 「GT 用了哪種寫法」而不是「系統做得好不好」**：
#   模型切開括號 → 在「有展開」的案例算對、在「沒展開」的案例算錯
#   模型不切     → 反過來
# 這是尺上的刻度歪掉，加再多測試案例都不會好（那是另一個問題）。
#
# 也是教授回饋「訂完驗收標準才實作階層解析」指的那件事。
#
# **這支只盤點與報告，不修改 GT。** 修改評估集要走 repo 內既有流程
# （casetool.py / manifest_tool.py），而且會讓版本號跳主版本、
# 既有數字全部不可比——那是要你決定的事。
#
# 分類方式：對每個「名稱(內容1、內容2…)」形式的頂層項目，看它的內部成分
# 有多少個**也**以獨立項目出現在同一案的清單裡：
#   全部都有  → 明確採「展開」慣例
#   都沒有    → 明確採「整團保留」慣例
#   部分有    → 不一致，或只是巧合（水／蔗糖本來就常在別處出現）
# 「部分有」要人工看，所以會把案例列出來。
#
# 用法：
#   python audit_nesting.py
#   python audit_nesting.py --detail c44
#   python audit_nesting.py --dump out/_nesting_audit.json
import argparse
import json
import os
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OPEN = "([{（〔【[｛"
CLOSE = ")]}）〕】]｝"
PAIRS = {"(": ")", "（": "）", "{": "}", "[": "]", "〔": "〕", "【": "】"}


def split_inner(body):
    """把括號內容切成頂層成分（括號深度 0 才切）。"""
    out, depth, cur = [], 0, []
    for ch in body:
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth -= 1
        if depth == 0 and ch in "、,，;；":
            s = "".join(cur).strip()
            if s:
                out.append(s)
            cur = []
        else:
            cur.append(ch)
    s = "".join(cur).strip()
    if s:
        out.append(s)
    return out


def parse_compound(item):
    """回傳 (前綴, 內部成分清單)；不是複合項就回 None。"""
    for i, ch in enumerate(item):
        if ch in PAIRS and item.rstrip().endswith(PAIRS[ch]):
            head = item[:i].strip()
            body = item[i + 1:item.rstrip().rindex(PAIRS[ch])]
            inner = split_inner(body)
            if head and len(inner) >= 2:
                return head, inner
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", default=None)
    ap.add_argument("--dump", default=None)
    a = ap.parse_args()

    cases = json.load(open(os.path.join(S.EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    tally = Counter()
    rows = []
    for c in cases:
        cid = c["case_id"]
        gt = S.load_gt(cid, c.get("category") or "")
        if not gt or not gt.get("ingredients_list"):
            continue
        gl = gt["ingredients_list"]
        flat = {S.normalize(x, fold_variants=True) for x in gl}
        for item in gl:
            p = parse_compound(item)
            if not p:
                continue
            head, inner = p
            hit = [x for x in inner
                   if S.normalize(x, fold_variants=True) in flat]
            if len(hit) == len(inner):
                kind = "全部展開"
            elif not hit:
                kind = "整團保留"
            else:
                kind = "部分（需人工判斷）"
            tally[kind] += 1
            rows.append({"case_id": cid, "item": item, "head": head,
                         "n_inner": len(inner), "n_also_listed": len(hit),
                         "also": hit, "kind": kind})

    tot = sum(tally.values())
    print("含括號的複合項共 %d 個（%d 案）\n" % (tot, len({r["case_id"] for r in rows})))
    for k in ("整團保留", "全部展開", "部分（需人工判斷）"):
        print("   %-20s%4d 個   %4.0f%%" % (k, tally[k], 100 * tally[k] / tot))

    part = [r for r in rows if r["kind"] == "部分（需人工判斷）"]
    print("\n── 「部分」那批的內部命中比例分布 ──")
    dist = Counter("%d/%d" % (r["n_also_listed"], r["n_inner"]) for r in part)
    for k, v in sorted(dist.items(), key=lambda x: -x[1])[:10]:
        print("   %-8s%3d 個" % (k, v))
    print("\n   多數是 1/N——那通常是巧合（水／蔗糖／甘油本來就常在別處出現），")
    print("   不是刻意展開。真正的不一致要看 n/N 比較高的那些。")

    print("\n── 明確「全部展開」的（與『整團保留』直接衝突）──")
    for r in [x for x in rows if x["kind"] == "全部展開"][:12]:
        print("   %-26s%s" % (r["case_id"], r["item"][:60]))

    if a.detail:
        print("\n── %s 的所有複合項 ──" % a.detail)
        for r in rows:
            if r["case_id"].startswith(a.detail):
                print("   [%s] %d/%d  %s" % (r["kind"], r["n_also_listed"],
                                             r["n_inner"], r["item"][:90]))
    if a.dump:
        json.dump(rows, open(a.dump, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n完整清單 -> %s" % a.dump)


if __name__ == "__main__":
    main()
