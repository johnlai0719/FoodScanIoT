"""把添加物種子資料載入記憶體 SQLite，讓 Fog 離線時能直接呼叫 Cloud 的比對函式。

Fog 降階（Cloud 連不上）時要判斷哪些成分是添加物。比對規則的權威來源是
`server/module_a/ingredient_matching.py` 的 `match_ingredients()`，它只需要一個
cursor。這裡提供一個行為等同 psycopg2 `RealDictCursor` 的 cursor，資料來自
`server/seed_data/reference_seed.sql`——比對規則只有一份，Fog 不另抄。
（`PPOCR_TEST/sim_match.py` 是研究用的複刻，檔頭自己寫明已與 module_a 分岔，不可拿來用。）

只用標準函式庫：`tests/contract/` 要能在 CI 上直接載入它。

已知差異：種子檔沒有 `raw_materials` 表，`match_ingredients()` 查該表時會自行
略過。添加物判定不受影響（原料清單只在添加物沒命中時才查），但一般成分的說明
會是「本系統無收錄資料」，而 Cloud 可能寫「食藥署食品原料清單收錄之原料」。
"""
import json
import re
import sqlite3
from pathlib import Path

SEED_SQL = Path(__file__).resolve().parents[1] / "server" / "seed_data" / "reference_seed.sql"

# server/models.py 的 Additive 中宣告為 JSON 的欄位。psycopg2 會把它們解成 Python 物件，
# module_a 依賴這一點：例如 category 必須是 list，類別統稱（「調味劑」等）才載得進來，
# 否則「調味劑(L-麩酸鈉、…)」不會被展開。
JSON_COLUMNS = frozenset({"aliases", "category", "allergen_details", "risks", "description_sources"})

_ADDITIVE_INSERT = re.compile(
    r"INSERT INTO public\.additives \(([^)]*)\) VALUES \((.*?)\);\n", re.S)


def load_additives_db(path=SEED_SQL) -> sqlite3.Connection:
    """讀種子檔的 additives INSERT，建成記憶體 SQLite。筆數對不上就報錯，不靜默少載。"""
    text = Path(path).read_text(encoding="utf-8")
    stmts = _ADDITIVE_INSERT.findall(text)
    expected = text.count("INSERT INTO public.additives ")
    if not stmts or len(stmts) != expected:
        raise ValueError(f"種子檔解析到 {len(stmts)} 筆 additives，檔內有 {expected} 筆 INSERT")

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(f"CREATE TABLE additives ({stmts[0][0]})")
    for cols, values in stmts:
        # 值原樣交給 SQLite 解析：種子檔是 standard_conforming_strings=on 的 pg_dump，
        # 字串字面值的規則（'' 跳脫、反斜線不跳脫）與 SQLite 相同。
        conn.execute(f"INSERT INTO additives ({cols}) VALUES ({values})")
    conn.commit()
    return conn


class RealDictLikeCursor:
    """module_a 用到的 psycopg2 RealDictCursor 行為：每列是 dict、JSON 欄位已解碼、%s 佔位符。"""

    def __init__(self, conn: sqlite3.Connection):
        self._cur = conn.cursor()

    def execute(self, sql, params=None):
        self._cur.execute(sql.replace("%s", "?"), tuple(params or ()))

    def fetchall(self):
        cols = [d[0] for d in self._cur.description]
        rows = []
        for raw in self._cur.fetchall():
            row = {}
            for col, val in zip(cols, raw):
                if col in JSON_COLUMNS and isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except ValueError:
                        pass
                row[col] = val
            rows.append(row)
        return rows
