#!/usr/bin/env python3
"""匯出族群注意資訊的證據對照表，供評審查證用的附錄。

為什麼需要這一支
----------------
`source_quote`（來源原文引述）**不會送到 App**——那是 2026-09-22 的決定：
留在資料庫備查，評審問起再拿出來。所以需要一個「拿出來」的動作，而它必須是
腳本而不是手抄的表：抄一次就會過期，而且抄的時候會不自覺地篩掉不好看的那幾筆。

這支的立場
----------
**它列出全部，包含系統刻意不採用的那幾筆，並標明為什麼不採用。**
把被排除的條目從附錄裡藏掉，正好是這一層想避免的事——那會讓「我們只列有證據的」
變成「我們只列好看的」。被排除的原因（沒有可追溯網址）本身就是可以講的內容。

它不做的事
----------
不改資料、不補 metadata、不生成任何說明文字。表裡的每一格都是資料庫原值，
`source_title` 與 `source_year` 刻意**不輸出**：庫內同一個網址掛過 8 種不同
標題、年份格式也混，那兩欄是模型填的，不可信（見
tests/contract/test_evidence_vocabulary.py）。

用法：
    python tools/export_evidence_appendix.py                    # 印摘要到 stdout
    python tools/export_evidence_appendix.py --out 附錄         # 寫 附錄.csv 與 附錄.md

環境變數沿用 server 那一套（DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD），
在容器外跑請指向已發布的 5432。
"""
import argparse
import csv
import io
import os
import sys
from collections import Counter
from datetime import date

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("需要 psycopg2：pip install psycopg2-binary")

# 主管機關／國際評估機構網域。與 server/module_a/ingredient_matching.py 的
# _REGULATOR_DOMAINS 同一份清單；兩邊不一致時以該檔為準（它決定實際輸出）。
REGULATOR_DOMAINS = (
    "efsa.europa.eu", "who.int", "fao.org", "inchem.org",
    "fda.gov", "fsa.gov.uk", "mhlw.go.jp", "fda.gov.tw",
)

QUERY = """
SELECT a.record_id, a.name_zh, a.ins_or_e_number,
       e->>'group'             AS grp,
       e->>'concern'           AS concern,
       e->>'source_type'       AS source_type,
       e->>'source_url'        AS source_url,
       e->>'source_quote'      AS source_quote,
       e->>'ai_reasoning'      AS ai_reasoning,
       e->>'reviewed_by_human' AS reviewed
FROM additives a, LATERAL json_array_elements(a.risks) AS x(e)
WHERE a.risks IS NOT NULL AND a.risks::text NOT IN ('null', '[]')
ORDER BY a.name_zh, e->>'group'
"""

COLUMNS = [
    "record_id", "添加物", "INS/E", "族群", "concern",
    "證據層級", "來源類型", "來源網址", "系統是否採用", "未採用原因",
    "模型解讀", "來源原文引述", "人工複核",
]


def evidence_scope(url, source_type):
    """只從網域機械判定。判不出來就留空——臨床／綜述／觀察性要讀過該篇才分得出。"""
    u = (url or "").lower()
    if any(d in u for d in REGULATOR_DOMAINS):
        return "regulatory_or_authority"
    if (source_type or "").strip().lower() == "official":
        return "regulatory_or_authority"
    return ""


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "product_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "password"),
    )


def fetch_rows():
    with connect() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(QUERY)
        raw = cur.fetchall()

    rows = []
    for r in raw:
        url = (r["source_url"] or "").strip()
        # 納入門檻與服務層一致：一定要有網址。不符的**照樣列出**並標明原因，
        # 藏起來就等於把「只列有證據的」偷偷換成「只列好看的」。
        used = bool(url)
        rows.append({
            "record_id": r["record_id"] or "",
            "添加物": r["name_zh"] or "",
            "INS/E": r["ins_or_e_number"] or "",
            "族群": r["grp"] or "",
            "concern": r["concern"] or "",
            "證據層級": evidence_scope(url, r["source_type"]),
            "來源類型": (r["source_type"] or "").strip(),
            "來源網址": url,
            "系統是否採用": "是" if used else "否",
            "未採用原因": "" if used else "無可追溯的來源網址",
            "模型解讀": (r["ai_reasoning"] or "").strip(),
            "來源原文引述": (r["source_quote"] or "").strip(),
            "人工複核": "是" if str(r["reviewed"]).lower() == "true" else "否",
        })
    return rows


def summary(rows):
    used = [r for r in rows if r["系統是否採用"] == "是"]
    out = [
        "族群注意資訊證據對照表",
        f"匯出日期：{date.today().isoformat()}",
        "",
        f"條目總數：{len(rows)}（系統採用 {len(used)}、未採用 {len(rows) - len(used)}）",
        f"涵蓋添加物：{len({r['添加物'] for r in rows})} 種"
        f"（記錄列 {len({r['record_id'] for r in rows})} 筆，同名異列為不同 record_id）",
        f"涵蓋族群：{len({r['族群'] for r in rows})} 種",
        f"附有來源原文引述：{sum(1 for r in rows if r['來源原文引述'])}",
        f"經人工逐筆複核：{sum(1 for r in rows if r['人工複核'] == '是')}",
        "",
        "來源類型分佈：" + "、".join(
            f"{k or '(未填)'} {v}" for k, v in Counter(r["來源類型"] for r in rows).most_common()),
        "族群分佈：" + "、".join(
            f"{k} {v}" for k, v in Counter(r["族群"] for r in rows).most_common()),
        "",
        "納入規則",
        "  僅在能指認特定族群、具體機制，並具有可追溯來源與原文證據時，才建立風險",
        "  紀錄；否則不由系統自行推論。",
        "",
        "讀這張表要知道的三件事",
        "  1. 「模型解讀」是對來源的解讀，不是來源原文。原文在「來源原文引述」欄。",
        "  2. 「人工複核」目前全部為否，因此本表是**具來源可追溯的注意資訊**，",
        "     不是經驗證的醫療結論。",
        "  3. 未列出的添加物**不代表安全**，只代表沒有達到上述納入門檻。",
        "",
        "本表不輸出 source_title 與 source_year：庫內同一網址曾掛多種不同標題、",
        "年份格式亦不一致，該兩欄為模型填入，不可信。可追溯的是網址與原文引述。",
    ]
    return "\n".join(out)


def to_markdown(rows):
    head = ["添加物", "族群", "concern", "證據層級", "來源網址", "系統是否採用", "人工複核"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        cells = [str(r[h]).replace("|", "\\|").replace("\n", " ") for h in head]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="輸出檔名前綴，會產生 <前綴>.csv 與 <前綴>.md")
    a = ap.parse_args()

    rows = fetch_rows()
    if not rows:
        sys.exit("資料庫裡沒有任何帶 risks 的添加物——請確認連到的是正式資料庫。")

    print(summary(rows))

    if not a.out:
        print("\n（加 --out <前綴> 可寫出完整表格，含原文引述）")
        return

    csv_path = a.out + ".csv"
    # utf-8-sig：Excel 開 UTF-8 CSV 不加 BOM 會把中文顯示成亂碼。
    with io.open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    md_path = a.out + ".md"
    with io.open(md_path, "w", encoding="utf-8", newline="") as f:
        f.write(summary(rows) + "\n\n" + to_markdown(rows) + "\n")

    print(f"\n已寫出：\n  {csv_path}（完整，含原文引述）\n  {md_path}（摘要＋精簡表）")


if __name__ == "__main__":
    main()
