"""建議 8 的回歸集：擋下、說明原因、**沒寫進資料庫**——三項分開驗證。

為什麼是這裡：教授回覆適用範圍的提請確認時，同意把範圍縮到「有標示的食品包裝」，
但要求保留一個小型回歸集，並且——

    Verify rejection, explanation, and prevention of database insertions
    **without conflating them into a single misleading accuracy metric.**

所以這個檔案刻意**不算精確率**。三項各自是布林，全過才算過，失敗時列出是哪一項。
把三者混成一個百分比正是教授點名要避免的事：那個數字取決於三類案例的比例，
而比例由我們自己決定，於是數字無從解讀。

**第三項是唯一從未被驗證過的，而它是這個閘門存在的唯一理由。**
報告當初寫的是「避免無關照片被分析後寫入資料庫」——功能加了兩個月，
那句話一直沒有對應的測試。這個檔案補上它。

為什麼能純函式測：
  - `_is_valid_food_scan()` 不碰 DB／網路，可直接呼叫（第 1、2 項）
  - 第 3 項用**間諜 cursor**：把 execute() 收到的 SQL 全部記下來，
    斷言其中沒有任何 INSERT／UPDATE。不需要真的資料庫，
    符合本目錄「契約測試一律純函式、CI 每次 push 都能跑」的紀律。

照片還沒拍時，`guard_set/cases.json` 的 images 是空陣列，涉及照片的測試會 skip；
`_is_valid_food_scan()` 的行為測試則以合成的 vision_data 進行，不依賴照片。
"""
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GUARD = os.path.join(ROOT, "測試", "量化測試", "guard_set", "cases.json")

sys.path.insert(0, os.path.join(ROOT, "server"))


# ─── 待測目標 ────────────────────────────────────────────────────────────────
def _gate():
    """取出 main.py 的閘門函式。

    main.py 在模組層會讀環境變數與建立連線設定，直接 import 在 CI 上會炸，
    所以只把那一個函式的原始碼抽出來執行——它自身不依賴任何模組層狀態
    （只用到 dict 操作與內建型別），這樣做是安全的，而且測到的是**同一份程式碼**。
    """
    src = open(os.path.join(ROOT, "server", "main.py"), encoding="utf-8").read()
    m = re.search(# ⚠ 下一個頂層構造也可能是**註解**（2026-09-14：在 @app.post 那疊上面
    #   加了說明註解，這條正則就抓不到函式體而整支測試收集失敗）。
    r"\ndef _is_valid_food_scan\(.*?\n(?=\ndef |\n@|\nclass |\n#)",
                  src, re.S)
    assert m, "找不到 _is_valid_food_scan()——閘門被改名或移走了，這個測試要跟著更新"
    ns = {}
    exec(m.group(0), ns)
    return ns["_is_valid_food_scan"]


GATE = _gate()


# ─── 三類負例的合成 vision_data ───────────────────────────────────────────────
# 對應 guard_set 的三個 kind。刻意用「模型可能回傳的形狀」而不是空字典——
# 空字典太好擋，擋得住不代表擋得住真實的邊緣情況。
NEGATIVE_CASES = [
    ("food_no_label",
     {"is_food_label": False, "reject_reason": "無成分標示",
      "name": "某某餅乾", "ingredients_list": []}),
    ("nonfood_with_label",
     # 最危險的一類：版面像成分表，模型可能誤判為 True。
     # 若模型誤判，第一關擋不住，要靠後面的實質內容檢查。
     {"is_food_label": False, "reject_reason": "非食品：化妝品成分表",
      "ingredients_list": ["水", "甘油", "丁二醇"]}),
    ("unrelated",
     {"is_food_label": False, "reject_reason": "照片與食品無關"}),
    ("empty", {}),
    ("none", None),
    ("garbage", {"is_food_label": True, "ingredients_list": [],
                 "nutrition": {}, "name": None}),
]


@pytest.mark.parametrize("kind,vision", NEGATIVE_CASES)
def test_rejection(kind, vision):
    """檢查一：該擋的有沒有被擋下來。"""
    ok, _ = GATE(vision)
    assert ok is False, f"{kind}：閘門放行了不該放行的輸入"


@pytest.mark.parametrize("kind,vision", NEGATIVE_CASES)
def test_explanation(kind, vision):
    """檢查二：有沒有給出原因，而且原因要有內容。

    「有回傳字串」不夠——空字串與 None 都會讓 App 顯示空白訊息。
    這裡要求非空且至少兩個字，擋掉「」與「-」這種等於沒說的回覆。
    """
    _, reason = GATE(vision)
    assert isinstance(reason, str), f"{kind}：原因不是字串（{type(reason).__name__}）"
    assert len(reason.strip()) >= 2, f"{kind}：原因太短或空白（{reason!r}）"


class SpyCursor:
    """記下所有經過的 SQL。只負責監看，不模擬資料庫行為。"""

    def __init__(self):
        self.sql = []

    def execute(self, q, *a, **k):
        self.sql.append(q)

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass

    @property
    def writes(self):
        pat = re.compile(r"\b(INSERT|UPDATE|DELETE)\b", re.I)
        return [q for q in self.sql if pat.search(q or "")]


@pytest.mark.parametrize("kind,vision", NEGATIVE_CASES)
def test_no_db_write(kind, vision):
    """檢查三：沒有產生任何寫入。**這是這個閘門存在的唯一理由。**

    做法：走一遍 main.py 裡「閘門 → 寫入」那段的控制流，用間諜 cursor 監看。
    這裡重現的是 main.py:878-895 的結構：閘門不過 → 要嘛直接 return，
    要嘛把 vision_data 設為 None 而略過整個寫入區塊。

    ⚠ 這是**行為的複製**，不是直接執行 main.py（它模組層會連 DB）。
    所以另外有 test_gate_precedes_every_write() 釘住原始程式碼裡的順序，
    兩個一起才構成完整的保證——單靠這個，main.py 改了順序它不會知道。
    """
    cur = SpyCursor()
    scan_ok, reason = GATE(vision)
    barcode = ""                                   # 拍照上傳、沒有條碼的情境
    if not scan_ok:
        if not barcode or barcode in ("NEW", "TEST", ""):
            pass                                   # main.py 在此直接 return rejected
        else:
            vision = None
    if vision is not None and scan_ok:
        cur.execute("INSERT INTO products (barcode) VALUES (%s)", (barcode,))
    assert cur.writes == [], f"{kind}：閘門沒過卻產生了寫入 {cur.writes}"


def test_gate_precedes_every_write():
    """釘住原始碼裡的順序：閘門必須在所有 products／producers 寫入之前。

    上面那個測試複製了控制流，複製品不會知道 main.py 被改動。這裡直接檢查
    原始碼的位置關係——閘門呼叫的位置必須早於每一個 INSERT INTO products／producers。
    """
    src = open(os.path.join(ROOT, "server", "main.py"), encoding="utf-8").read()
    gate_at = src.find("scan_ok, scan_reason = _is_valid_food_scan(")
    assert gate_at > 0, "找不到閘門的呼叫點——它被移走或改名了"
    for tbl in ("INSERT INTO products", "INSERT INTO producers"):
        for m in re.finditer(re.escape(tbl), src):
            assert m.start() > gate_at, (
                f"{tbl} 出現在閘門之前（位置 {m.start()} < {gate_at}），"
                "無關照片可能在被擋下之前就寫進資料庫")


# ─── 照片就位後才跑的部分 ────────────────────────────────────────────────────
def _guard_cases():
    if not os.path.exists(GUARD):
        return []
    return json.load(open(GUARD, encoding="utf-8"))["cases"]


def test_guard_set_declared():
    """回歸集的定義檔要在，而且三類各三案。照片可以之後補。"""
    cases = _guard_cases()
    assert cases, "找不到 guard_set/cases.json"
    kinds = {}
    for c in cases:
        kinds.setdefault(c["kind"], []).append(c["case_id"])
    assert set(kinds) == {"food_no_label", "nonfood_with_label", "unrelated"}, \
        f"三類不齊：{sorted(kinds)}"
    for k, v in kinds.items():
        assert len(v) == 3, f"{k} 只有 {len(v)} 案，教授要求三類各三案"


def test_guard_set_photos_present():
    """照片到齊才算完成。未到齊時 skip 並顯示還缺幾張——不讓它假裝通過。"""
    cases = _guard_cases()
    missing = [c["case_id"] for c in cases if not c.get("images")]
    if missing:
        pytest.skip("尚未拍攝：%d/%d 案缺照片（%s）"
                    % (len(missing), len(cases), "、".join(missing[:3]) +
                       ("…" if len(missing) > 3 else "")))
    assert not missing
