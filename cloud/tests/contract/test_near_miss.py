"""近似提示：可以提示，但**絕不可以當成判定**。

OCR 讀錯一兩個形近字時（實例：5'-次黃「膦」呤、「烏」嘌呤、L-「鈦」酸鈉），
子字串比對整串落空。近似提示附上「資料庫裡最接近的是誰」，由使用者判斷。

⚠ 這與**已被實測否決**的「字典後修正」只有一線之隔：
    修正 = 把 OCR 的字換掉，再當事實往下算 → F1 70.0 → 53.0、精確度 84.6% → 48.7%
    提示 = 原文不動，另外標一個疑似對象，不計入、不評分
差別全在「有沒有進到判定裡」。這個檔案守的就是那條線。

門檻由實測決定（2026-09-13，`PPOCR_TEST/near_miss2.py`，177 案 vlcrop 輸出）：

    長度門檻   比例    會提示   猜對   猜錯   猜對率
    （無）     0.10      6      4      2    66.7%
    （無）     0.34     48     20     28    41.7%
    ≥8 字      0.25     10      7      3    70.0%

**長度門檻是關鍵**：沒有它時猜錯的是「葡萄糖漿→葡萄糖酸」「玉米糖漿→玉米糖膠」
「乳清蛋白→乳鐵蛋白」——看起來合理但是不同物質。那些全是 4–6 字。
"""
import pytest

import module_a.ingredient_matching as IM
from seed_db import RealDictLikeCursor, load_additives_db


@pytest.fixture(scope="module")
def cur():
    return RealDictLikeCursor(load_additives_db())


def _run(cur, text):
    return IM.match_ingredients([text], None, cur, None)


def _near(result):
    return [(b["name"], (b.get("nearMiss") or {}).get("officialName"))
            for b in result["basic_detail"] if b.get("nearMiss")]


# ── 該提示的 ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("scanned,expected", [
    ("5'-次黃膦呤核苷磷酸二鈉", "5’-次黃嘌呤核苷磷酸二鈉"),   # 嘌 → 膦
    ("5'-烏嘌呤核苷磷酸二鈉", "5’-鳥嘌呤核苷磷酸二鈉"),      # 鳥 → 烏
])
def test_long_name_with_one_wrong_character_gets_a_suggestion(cur, scanned, expected):
    """魔芋爽那個真實案例。資料庫有、比對器對、`5'-` 前綴也讀對了，
    只差中間一個形近字，子字串比對就整串落空。"""
    got = dict(_near(_run(cur, scanned)))
    assert expected in got.values(), "%s 沒有得到提示（實得 %s）" % (scanned, got)


# ── 不該提示的 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("scanned", [
    "葡萄糖漿",   # 距 葡萄糖酸 1 字
    "玉米糖漿",   # 距 玉米糖膠 1 字
    "麥芽糖漿",   # 距 麥芽糖醇 1 字
    "乳清蛋白",   # 距 乳鐵蛋白 1 字
])
def test_short_common_ingredients_get_no_suggestion(cur, scanned):
    """這四個都離某個真添加物只有一個字，但它們是**不同的東西**。

    糖漿不是糖醇、乳清不是乳鐵。沒有長度門檻的話這些全會冒出提示，
    而使用者分辨不出來——這正是實測中 41.7% 猜對率的來源。
    """
    assert not _near(_run(cur, scanned)), "%s 不該有提示" % scanned


def test_water_and_generic_terms_never_get_a_suggestion(cur):
    """水與類別統稱不是「沒配到」，是**本來就不該配**。
    給它們提示等於把設計上的第三種狀態講成辨識失誤。"""
    for t in ("水", "香料", "調味劑"):
        assert not _near(_run(cur, t)), "%s 不該有提示" % t


def test_exact_matches_never_produce_a_suggestion(cur):
    """配得到的項目走的是判定路徑，不會進到提示。

    這也是為什麼「改一個字落到另一個真添加物」（己二烯酸鉀 → 己二烯酸鈉）
    那個風險在提示模式下**不存在**：提示只在比對失敗時觸發，
    而失敗就代表這串字不是任何一個真添加物。
    """
    for t in ("己二烯酸鉀", "碳酸鉀", "檸檬酸鈉"):
        r = _run(cur, t)
        assert r["chemical"], "%s 應該直接配到" % t
        assert not _near(r), "%s 配到了卻還給提示" % t


# ── 最重要的一條：不可進入判定 ───────────────────────────────────────────────
def test_a_suggestion_is_not_counted_as_an_additive(cur):
    """有提示的項目必須留在 basic_detail 且 isAdditive 為 False。

    一旦計入添加物，這個功能就從「提示」變成「字典後修正」——
    而那條路已經實測否決（F1 70.0 → 53.0）。
    """
    r = _run(cur, "5'-次黃膦呤核苷磷酸二鈉")
    assert not r["chemical"], "疑似項目不可進入 chemical（添加物清單）"
    hit = [b for b in r["basic_detail"] if b.get("nearMiss")]
    assert hit, "應該要有疑似標記"
    for b in hit:
        assert b["isAdditive"] is False
        assert b["inDatabase"] is False


def test_scanned_text_is_preserved_verbatim(cur):
    """原文不得被替換——這是第 0 條原則（輸出的每個字都要有影像來源）。
    替換掉就沒有影像來源了，那正是被否決的做法。"""
    scanned = "5'-次黃膦呤核苷磷酸二鈉"
    r = _run(cur, scanned)
    names = [b["name"] for b in r["basic_detail"]]
    assert scanned in names, "掃到的原文被改掉了：%s" % names


def test_thresholds_are_the_measured_ones():
    """門檻是量出來的，不是調出來的。改動前請重跑 near_miss2.py。"""
    assert IM.NEAR_MISS_MIN_LEN == 8
    assert IM.NEAR_MISS_MAX_RATIO == 0.25


def test_app_marks_it_as_unconfirmed():
    """畫面必須同時顯示掃到的原文與疑似對象，並講明未計入。

    只給疑似對象＝替換（被否決的做法）；只給原文＝使用者無從查證。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[3] / "APP" / "src" /
           "components" / "IngredientsList.tsx").read_text(encoding="utf-8")
    assert "nearMiss" in src
    assert "掃到：" in src, "畫面沒有顯示掃到的原文"
    assert "未將它計入添加物" in src, "畫面沒有講明這不是判定"
