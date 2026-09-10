#!/usr/bin/env python3
# 階層解析驗收的基準線：把 GT 的成分清單攤成父子邊，數出規模，
# 並把「括號語意需要人工裁決」的項目排成工作單。
#
# 為什麼需要這支：教授回饋建議 7 要求訂 hierarchy accuracy，但那不是統計學上
# 有標準定義的指標，門檻要填多少得先知道分母有多大。回饋筆記當時寫的
# 「現有正解裡沒有階層資訊、要 48 案人工重標」是**錯的**——巢狀資訊確實在，
# 只是以「字串裡的括號」而非結構化欄位的形式存在。這支就是把它變成結構。
#
# 與 audit_nesting.py 的分工：那支問「GT 的展開慣例一不一致」（同一個成分
# 有沒有同時以獨立項目出現），這支問「攤成父子邊之後有幾條」。前者是資料
# 一致性稽核，後者是指標的分母。
#
# **2026-09-01 指導教授裁定：括號不必然代表父子關係。**
#   Ingredients or components → child nodes
#   functions, purposes, categories, notes → attributes
#   Ambiguous cases → labeled separately
# 對應本檔的分類：組成／單一組成計為邊；功能類別／型態註記／過敏原提示
# 改記為屬性（見 edges() 的 attrs）；形狀不符另列待判。
#
# 這推翻了 08-24 的暫行決定（當時「全部計入」是為了在慣例未定前先固定分母，
# 且把限制攤開寫在報告裡）。v2.2 分母因此由 593 條降為 569 條、31 案降為 25 案。
# **舊數字不可與新數字並排比較。**
# `--legacy` 可切回舊算法（全部計入），供跨期對照時還原舊基準。
#
# 用法：
#   python hier_baseline.py
#   python hier_baseline.py --dump out/_hier_baseline.json
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
GT_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試", "ground_truth")
)

# GT 用三種括號表示不同層次：`高湯{…酸白菜(…)、奶精[…乳化劑(…)]}`。
# 只認小括號的話，這些項目會被誤判成「形狀不符」而排除在分母外。
# 〔〕必須跟其他括號一起列。GT 用三種括號表示層次
# （`高湯{…酸白菜(…)、奶精[…]}`），漏掉任何一種，該項目會被誤判成
# 「形狀不符」排除在分母外——2026-09-06 實測 37 項待判裡有 23 項是這樣來的。
OPEN, CLOSE, SEP = "(（{[【〔", ")）}]】〕", "、,，"

# 括號內是單一元素時的四種語意。只有第一種才該產生父子邊,其餘三種的括號
# 講的不是「成分的組成」,照括號硬拆會生出假的父子關係。
#
# 判斷順序有意義：`含` 開頭優先於功能詞,因為「大豆蛋白(含大豆卵磷脂)」
# 兩個條件都成立,但它是過敏原提示不是組成。
FORM_WORDS = ("無水", "含水", "粉末", "液體", "濃縮", "結晶")
FUNC_TAIL = ("劑", "色素", "香料")   # 抗氧化劑／乳化劑／天然食用色素…：功能類別詞

# 產地、基改標示、比例、來源——教授講的 notes，記為屬性而非子節點。
# 把 `豬肉(臺灣)` 算成父子邊等於宣稱臺灣是豬肉的成分。
NOTE_RE = re.compile(
    r"^(?:[^、,，]{1,10}(?:縣|州|省|市)"
    r"|臺灣|台灣|中國|日本|韓國|泰國|越南|美國|加拿大|澳洲|紐西蘭"
    r"|西班牙|巴拉圭|巴西|阿根廷|印尼|馬來西亞|印度|法國|德國|義大利|荷蘭"
    r"|.{0,6}基因改造.{0,4}|.{0,4}氫化|.{0,6}來源|[\d.]+ ?%|含?[\d.]+ ?公?[克毫升]"
    r")$")


def split_top(s):
    """依頂層分隔符切開,括號內的分隔符不算。"""
    d, cur, parts = 0, "", []
    for ch in s:
        if ch in OPEN:
            d += 1
        elif ch in CLOSE:
            d -= 1
        if ch in SEP and d == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def head_body(s):
    """把 `名稱(內容)` 拆成 (名稱, 內容);形狀不符回 None。"""
    m = re.match(r"^([^(（{\[【〔]+)[(（{\[【〔](.*)[)）}\]】〕]\s*$", s.strip())
    return (m.group(1).strip(), m.group(2)) if m else None


# 2026-09-01 教授回覆，改變了「括號 = 父子關係」這個前提：
#
#   Parentheses should not always indicate a parent–child relationship.
#   Ingredients or components should be treated as child nodes, while
#   functions, purposes, categories, and notes should be treated as
#   attributes. Ambiguous cases should be labeled separately.
#
# 對應到既有的 classify()：
#   組成／單一組成      → 子節點，計為父子邊
#   功能類別            → 屬性（維生素C(抗氧化劑) 的方向是反的，抗氧化劑不是成分）
#   型態註記            → 屬性（焦磷酸鈉(無水) 的「無水」是狀態不是成分）
#   來源註記            → 屬性（豬肉(臺灣)、玉米(非基因改造)：產地與標示不是成分）
#   過敏原提示          → 屬性（大豆蛋白(含大豆卵磷脂) 是警語不是配方）
#   形狀不符            → 待判，另外列出，不進分母也不當作正確
ATTRIBUTE_KINDS = ("功能類別", "型態註記", "過敏原提示", "來源註記")
AMBIGUOUS_KINDS = ("形狀不符",)


def edges(s, depth=0, out=None, attrs=None, legacy=False):
    """遞迴攤成 [(父, 子, 層)]。子項自己若也帶括號,它同時是下一層的父。

    **屬性型的括號不產生邊**（教授 2026-09-01 回覆）。它們改記入 attrs，
    因為那個資訊仍然有用——下游要顯示「維生素C（抗氧化劑）」，
    只是它不該被當成「維生素C 含有抗氧化劑」這種組成關係去評分。
    """
    if out is None:
        out = []
    if attrs is None:
        attrs = []
    hb = head_body(s)
    if not hb:
        return out
    head, body = hb
    kind = classify(s)
    if not legacy:
        if kind in ATTRIBUTE_KINDS:
            attrs.append((head, body.strip(), kind))
            return out
        if kind in AMBIGUOUS_KINDS:
            return out
    for c in split_top(body):
        inner = head_body(c)
        out.append((head, inner[0] if inner else c, depth))
        edges(c, depth + 1, out, attrs, legacy)
    return out


def balanced(s):
    """括號是否成對。不成對者攤不成樹，硬解會產生錯的配對。"""
    d = 0
    for ch in s:
        if ch in OPEN:
            d += 1
        elif ch in CLOSE:
            d -= 1
            if d < 0:
                return False
    return d == 0


def classify(item):
    """括號內為單一元素時的語意分類,決定它該不該產生父子邊。"""
    if not balanced(item):
        return "形狀不符"      # 括號沒成對，排除而非硬解
    hb = head_body(item)
    if not hb:
        return "形狀不符"
    head, body = hb
    if len(split_top(body)) > 1:
        return "組成"                       # 多元素,機械解析即可
    b = body.strip()
    if b.startswith("含"):
        return "過敏原提示"                  # 大豆蛋白(含大豆卵磷脂)
    if b in FORM_WORDS:
        return "型態註記"                    # 焦磷酸鈉(無水)
    if NOTE_RE.match(b):
        return "來源註記"                    # 豬肉(臺灣)／玉米(非基因改造)：notes
    # 功能類別詞不管出現在哪一邊都是屬性——`維生素C(抗氧化劑)` 與
    # `抗氧化劑(維生素C)` 是同一個關係寫成兩個方向，語意一樣。
    # 只判單邊的話，同一件事會因為廠商怎麼印而一個算邊、一個算屬性
    # （2026-09-06 實測影響 48 項）。
    if b.endswith(FUNC_TAIL) != head.endswith(FUNC_TAIL):
        return "功能類別"
    return "單一組成"                        # 花生醬(特選花生仁)：真的是由它製成


def versions():
    """case_id → set_version。跨期比較只能引用凍結子集，見測試集 README。"""
    p = os.path.join(os.path.dirname(GT_ROOT), "cases.json")
    return {c["case_id"]: c.get("set_version")
            for c in json.load(open(p, encoding="utf-8"))["cases"]}


def load(version=None):
    vs = versions() if version else {}
    for root, _, fs in os.walk(GT_ROOT):
        if os.path.basename(root).startswith("_"):
            continue           # 底線開頭＝歸檔區,不參與評分
        for f in sorted(fs):
            if not f.endswith(".json"):
                continue
            cid = f[:-5]
            if version and vs.get(cid) != version:
                continue
            g = json.load(open(os.path.join(root, f), encoding="utf-8"))
            yield cid, (g.get("ingredients_list") or [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="把逐項結果寫成 JSON,供標註工作單使用")
    ap.add_argument("--version", help="只算某個測試集版本，如 v2.2（凍結的 48 案）")
    ap.add_argument("--legacy", action="store_true",
                    help="還原 08-24 的舊算法（全部計入，含功能類別／型態／過敏原提示），供跨期對照")
    a = ap.parse_args()

    total = bracketed = 0
    parents, all_edges, by_depth, kinds, rows = set(), 0, {}, {}, []
    cases = set()

    # 案例數要從資料算，不能寫死——2026-09-06 測試集擴到 177 案時，
    # 這行還印著「全部 58 案」，數字全對只有標題騙人。
    loaded = list(load(a.version))
    n_cases_total = len(loaded)

    for cid, items in loaded:
        for it in items:
            if not isinstance(it, str):
                continue
            total += 1
            if not re.search(r"[(（{\[【]", it):
                continue
            bracketed += 1
            kind = classify(it)
            kinds[kind] = kinds.get(kind, 0) + 1
            es = edges(it, legacy=a.legacy)
            # 「組成」與「單一組成」才計入邊的基準線;其餘三類列待裁決,
            # 慣例訂完前不計入分母。
            # 教授裁定後：只有「組成／單一組成」是父子關係。
            # --legacy 還原舊算法，讓 08-27 報告的 593 條可重現。
            counted = (kind != "形狀不符") if a.legacy else (kind in ("組成", "單一組成"))
            if counted and es:
                cases.add(cid)
                for p, c, d in es:
                    parents.add((cid, p))
                    all_edges += 1
                    by_depth[d] = by_depth.get(d, 0) + 1
            rows.append({"case_id": cid, "item": it, "kind": kind,
                         "counted": counted, "n_edges": len(es) if counted else 0})

    print("案例母體          %s" % (a.version or "全部 %d 案" % n_cases_total))
    print("成分項總數        %d" % total)
    print("含括號            %d 項（%.0f%%）" % (bracketed, 100 * bracketed / total))
    print()
    print("── 括號語意分類")
    for k in ("組成", "單一組成") + ATTRIBUTE_KINDS + ("形狀不符",):
        if k in kinds:
            if a.legacy:
                inc, note = (k != "形狀不符"), ""
            else:
                inc = k in ("組成", "單一組成")
                note = ("（屬性）" if k in ATTRIBUTE_KINDS
                        else "（待判）" if k == "形狀不符" else "")
            print("  %-10s %4d 項   %s%s" % (k, kinds[k], "計為邊" if inc else "不計為邊", note))
    print()
    print("── 基準線（%s）"
          % ("LEGACY：08-24 舊算法，全部計入" if a.legacy
             else "教授 09-01 裁定：只有組成關係計為父子邊"))
    print("  分布案數        %d" % len(cases))
    print("  父節點          %d" % len(parents))
    print("  父子邊          %d 條" % all_edges)
    for d in sorted(by_depth):
        print("    第 %d 層       %d 條" % (d + 1, by_depth[d]))
    if not a.legacy:
        attr = sum(v for k, v in kinds.items()
                   if k in ATTRIBUTE_KINDS)
        amb = kinds.get("形狀不符", 0)
        print()
        print("%d 項的括號改記為屬性（%s），不產生父子邊。"
              % (attr, "／".join(ATTRIBUTE_KINDS)))
        if amb:
            print("%d 項括號不成對，列為待判（ambiguous），既不計入分母也不算正確。" % amb)
    else:
        amb = sum(v for k, v in kinds.items()
                  if k in ATTRIBUTE_KINDS)
        print()
        print("其中 %d 條配對的括號不是組成關係（功能類別／型態／過敏原提示），" % amb)
        print("仍計入分母，此限制已寫入進度報告待指導教授回覆。")

    if a.dump:
        os.makedirs(os.path.dirname(a.dump) or ".", exist_ok=True)
        json.dump({"total_items": total, "bracketed": bracketed,
                   "kinds": kinds, "n_cases": len(cases),
                   "n_parents": len(parents), "n_edges": all_edges,
                   "by_depth": {str(k + 1): v for k, v in by_depth.items()},
                   "rows": rows},
                  open(a.dump, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("→ %s" % a.dump)


if __name__ == "__main__":
    main()
