"""族群注意資訊的證據紀律：納入門檻、措辭、以及原文與解讀分離。

這一層要守的主張是：

    僅在能指認特定族群、具體機制，並具有可追溯來源與原文證據時，才建立風險
    紀錄；否則不由系統自行推論。

**不是**「這些添加物已被我們證明有風險」。兩者的差別在被追問時答不答得出來。

三件事這個檔案會擋下：

1. **把 `verified` 送到呈現端。** 庫內 65 條全部 `reviewed_by_human: false`，
   沒有任何一條經過人工逐筆複核。此時輸出「已驗證」會答不出「誰驗證的」。
   欄位改名為 evidenceStatus，值是 source_backed＝附有可追溯的來源證據。

2. **輸出指不出來源的條目。** 庫內有 5 條標著 confidence "verified" 卻
   source_type "none"、網址空白——schema 上的矛盾。服務層直接不輸出。

3. **把原文與模型解讀混成一段。** sourceQuote 是來源真正寫了什麼、reason 是
   模型怎麼讀它、reviewedByHuman 是有沒有人確認過這個解讀。三層混在一起就
   分不出哪一句要負責。2026-09-22 之前 sourceQuote 根本沒被送出，畫面上
   只有模型的解讀。

另外釘住不可信的 metadata 不再外流：同一個網址（PMC4017440）在庫內掛了
8 種不同標題、26 條記錄，年份格式也混（1998 是數字、"2022" 是字串、有空字串
也有 null），21 條沒有標題。**錯誤的 metadata 比缺 metadata 更糟**——它看起來
正式，反而讓人誤信。欄位留在資料庫，只是不送出。
"""
import json
import re
from pathlib import Path

import pytest

import module_a.ingredient_matching as IM
from seed_db import RealDictLikeCursor, load_additives_db

ROOT = Path(__file__).resolve().parents[2]
MATCHING = ROOT / "server" / "module_a" / "ingredient_matching.py"
APP_TYPES = ROOT / "APP" / "src" / "types.ts"
APP_LIST = ROOT / "APP" / "src" / "components" / "IngredientsList.tsx"


@pytest.fixture(scope="module")
def cur():
    return RealDictLikeCursor(load_additives_db())


def _all_risks(cur):
    """跑過種子檔裡每一個有 risks 的添加物，收集實際送出的 groupRisks。"""
    conn = load_additives_db()
    c = conn.cursor()
    c.execute("SELECT name_zh FROM additives "
              "WHERE risks IS NOT NULL AND risks <> '[]' AND risks <> 'null'")
    names = [r[0] for r in c.fetchall()]
    assert names, "種子檔裡沒有任何帶 risks 的添加物"
    out = []
    for n in names:
        res = IM.match_ingredients([n], None, RealDictLikeCursor(conn), None)
        for row in (res["chemical"] or []) + (res["basic_detail"] or []):
            out.extend(row.get("groupRisks") or [])
    return out


def test_不輸出verified這個字(cur):
    risks = _all_risks(cur)
    assert risks, "沒有收集到任何 groupRisks，測試本身失效"
    for r in risks:
        assert "verified" not in json.dumps(r, ensure_ascii=False), \
            f"送出的條目仍含 verified：{r}"
        assert "confidence" not in r, "confidence 已更名為 evidenceStatus"


def test_證據狀態只表示有來源不表示已驗證(cur):
    for r in _all_risks(cur):
        assert r.get("evidenceStatus") == "source_backed"


def test_指不出來源的一律不輸出(cur):
    for r in _all_risks(cur):
        assert (r.get("sourceUrl") or r.get("sourceQuote")), \
            f"輸出了既無網址也無引述的條目：{r}"


def test_原文與解讀是兩個欄位(cur):
    risks = _all_risks(cur)
    # 至少要有一筆真的帶原文，否則這個分離等於沒做
    assert any(r.get("sourceQuote") for r in risks), "沒有任何條目送出 sourceQuote"
    for r in risks:
        assert "reason" in r and "sourceQuote" in r
        assert "reviewedByHuman" in r


def test_不可信的metadata不再送出(cur):
    for r in _all_risks(cur):
        assert "sourceTitle" not in r, "source_title 不可信，不應送出"
        assert "sourceYear" not in r, "source_year 不可信，不應送出"


def test_證據層級判不出來時留空(cur):
    """只從網域機械判定；臨床／綜述／觀察性需人逐篇認定，不由系統推論。"""
    for r in _all_risks(cur):
        scope = r.get("evidenceScope")
        assert scope in (None, "regulatory_or_authority"), \
            f"出現了未經人認定的證據層級：{scope}"


def _strip_comments(src: str) -> str:
    """去掉註解再檢查措辭。

    要管的是**顯示給使用者的字**，不是程式碼旁邊的說明——註解裡寫著
    「刻意不寫成風險警示」反而是好事，不該讓它把測試弄紅。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)   # 區塊註解，含 JSX 的 {/* */}
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)   # 行註解
    return src


def test_畫面措辭不得宣稱已驗證():
    ui = _strip_comments(APP_LIST.read_text(encoding="utf-8"))
    for banned in ("經驗證", "已驗證", "風險警示"):
        assert banned not in ui, f"畫面措辭不可使用「{banned}」"
    assert "未經人工複核" in ui, "reviewedByHuman 為 false 時必須在畫面標出"
    assert "未列出不代表無風險" in ui, "空白不等於安全，這句不可省略"


def test_型別文件保留空白不等於安全這條():
    types = APP_TYPES.read_text(encoding="utf-8")
    assert "不代表安全" in types
