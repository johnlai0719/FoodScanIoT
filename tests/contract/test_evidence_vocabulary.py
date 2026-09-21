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

3. **把來源原文送到 App。** source_quote 是證據基礎，但**留在資料庫備查**——
   決定是評審問起再拿出來。畫面上給網址已足以追溯（庫內「只有引述、沒有網址」
   的條目是 0 筆），逐條附上英文原文段落只會讓使用者讀不完。
   三層的分離仍在資料庫裡：source_quote 來源寫了什麼、ai_reasoning 模型怎麼讀、
   reviewed_by_human 有沒有人確認過這個解讀。

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
        assert r.get("sourceUrl"), f"輸出了沒有來源網址的條目：{r}"


def test_原文留在資料庫不送出(cur):
    """source_quote 是證據基礎，但**不送到 App**——決定是評審問起再拿出來。

    畫面上給 sourceUrl 已足以追溯（庫內「只有引述、沒有網址」的條目是 0 筆），
    逐條附上英文原文段落只會讓使用者讀不完。

    ⚠ 這是呈現上的決定，不是把證據拿掉：資料仍在 additives.risks 裡。
      要把它上架回去的話，連同這條斷言一起改。
    """
    for r in _all_risks(cur):
        assert "sourceQuote" not in r, "原文不應送到 App，它留在資料庫備查"
        # 模型的解讀與複核狀態仍要送，否則畫面無法標示「未經人工複核」
        assert "reason" in r and "reviewedByHuman" in r


def test_送出的每一條都點得開(cur):
    """門檻是「一定要有網址」，所以畫面上不會出現追溯不了的條目。"""
    risks = _all_risks(cur)
    assert risks
    for r in risks:
        assert (r.get("sourceUrl") or "").startswith("http"), \
            f"送出了沒有可用網址的條目：{r}"


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
