"""規則總結的契約：只能講手上有的東西。

2026-09-14 兩段文字總結由模型改為規則產生。這個檔案守的是**改回去很容易、
但改回去就錯了**的那幾條界線：

  - 不得宣稱安全或有害（本系統做標示判讀，不做安全性評估）
  - 添加物為 0 時不得講成「不含添加物」
  - 缺值不得講成 0
  - 不得出現未傳入的營養素名稱

最後一條特別重要：實測本地 2B 會寫出「高膽固醇」，而營養資料裡根本沒有
膽固醇欄位。規則版做不到這種事，但**有人把模型接回來就會**，所以釘住它。
"""
import re
from pathlib import Path

import pytest

import module_a.ingredient_matching as IM
from module_d.summary import (GROUP_LABEL_ZH, build_additives_summary,
                              build_overall_summary)
from seed_db import RealDictLikeCursor, load_additives_db

REPO = Path(__file__).resolve().parents[3]

# 不可出現在總結裡的字。安全性宣稱兩個方向都擋——「無害」會讓人放心地
# 多吃，「有害」則是本系統沒有做過的判定。
FORBIDDEN = ("無害", "安全無虞", "對健康無害", "無風險", "有害", "致癌",
             "經評估安全", "可安心")
# 沒有傳進來的營養素。模型最愛補這幾個。
NOT_IN_DATA = ("膽固醇", "反式脂肪", "維生素", "礦物質")


def _breakdown(salt=-9, protein=5, fibre_value=None):
    return [
        {"key": "energy", "reason": "熱量 (Energy)", "points": -3, "maxPoints": 10,
         "value": 379, "unit": "kcal"},
        {"key": "sugars", "reason": "糖分 (Sugars)", "points": -1, "maxPoints": 15,
         "value": 5.4, "unit": "g"},
        {"key": "salt", "reason": "鈉/鹽分 (Sodium/Salt)", "points": salt,
         "maxPoints": 20, "value": 1.8, "unit": "g"},
        {"key": "protein", "reason": "蛋白質 (Protein)", "points": protein,
         "maxPoints": 7, "value": 17.8, "unit": "g"},
        {"key": "fibre", "reason": "膳食纖維 (Fibre)", "points": 0, "maxPoints": 5,
         "value": fibre_value, "unit": "g"},
    ]


@pytest.fixture(scope="module")
def cur():
    return RealDictLikeCursor(load_additives_db())


# ── 不得宣稱安全或有害 ───────────────────────────────────────────────────────
def test_overall_summary_never_claims_safety_or_harm():
    s = build_overall_summary(11, "D", _breakdown())
    for w in FORBIDDEN:
        assert w not in s, "總結出現安全性宣稱：%s" % w


def test_additives_summary_never_claims_safety_or_harm(cur):
    r = IM.match_ingredients(["己二烯酸鉀", "檸檬酸鈉", "蔗糖"], None, cur, None)
    s = build_additives_summary(r["chemical"], r["basic_detail"])
    for w in FORBIDDEN:
        assert w not in s, "添加物總結出現安全性宣稱：%s" % w


def test_summary_mentions_no_nutrient_that_was_not_given():
    """只能講傳進來的營養素。

    ⚠ 這正是本地 2B 失敗的地方：它寫「高膽固醇」，而膽固醇不在資料裡。
    """
    s = build_overall_summary(11, "D", _breakdown())
    for w in NOT_IN_DATA:
        assert w not in s, "總結提到未傳入的營養素：%s" % w


# ── 沒查到不可講成沒有 ───────────────────────────────────────────────────────
def test_zero_additives_is_not_stated_as_containing_none():
    """添加物 0 的 42 案中有 11 案是漏讀的，系統分不出來。"""
    s = build_additives_summary([], [])
    assert "沒有辨識成功" in s or "未辨識" in s or "辨識成功" in s, \
        "未說明可能是辨識失敗"
    assert "以包裝上的成分欄為準" in s
    assert "不含" not in s, "把「沒比對到」講成「不含」"


def test_unmatched_ingredients_are_not_called_non_additives(cur):
    """實測 5.8% 的真添加物會落在未比對到那一欄，那是缺口不是判定。"""
    r = IM.match_ingredients(["己二烯酸鉀", "蔗糖", "麵粉"], None, cur, None)
    s = build_additives_summary(r["chemical"], r["basic_detail"])
    assert "非添加物" not in s
    assert "不代表確定不是添加物" in s


# ── 缺值不可當成 0 ──────────────────────────────────────────────────────────
def test_missing_value_is_reported_as_unlabelled_not_zero():
    s = build_overall_summary(11, "D", _breakdown(fibre_value=None))
    assert "未標示" in s
    assert "非含量為零" in s, "沒有講明計 0 分是保守原則而不是量到 0"


def test_present_value_is_not_reported_as_missing():
    s = build_overall_summary(11, "D", _breakdown(fibre_value=3.2))
    assert "未標示" not in s


# ── 講得出「為什麼是這個等級」 ───────────────────────────────────────────────
def test_overall_summary_names_the_largest_penalty_with_its_maximum():
    """分母一定要寫——只有「鈉 9 分」看不出是滿分還是一半。"""
    s = build_overall_summary(11, "D", _breakdown(salt=-9))
    assert "鈉/鹽分 9/20" in s, "沒有寫出扣最多的那一項與其上限：%s" % s


def test_overall_summary_states_the_direction_of_the_score():
    """不寫方向的話「11 分」會被讀成 0–100 裡的 11 分。"""
    assert "越低越健康" in build_overall_summary(11, "D", _breakdown())


def test_overall_summary_reports_how_much_bonus_offsets():
    s = build_overall_summary(11, "D", _breakdown(salt=-9, protein=5))
    assert "抵銷約" in s and "%" in s


def test_no_penalties_is_stated_plainly():
    items = [dict(i, points=0) for i in _breakdown()]
    s = build_overall_summary(-2, "A", items)
    assert "未達扣分門檻" in s


# ── 族群詞彙不可另立一份 ────────────────────────────────────────────────────
def test_group_labels_stay_within_the_single_source_of_truth():
    """中文說法的鍵必須落在 module_a 的值域內。

    族群詞彙曾經散在四處各自維護，導致中英文比對方向相反、六種族群警告
    從未觸發。這裡只補「英文碼 → 給人看的中文」，值域仍以 module_a 為準。
    """
    allowed = set(IM.GROUP_ZH_TO_EN.values())
    assert set(GROUP_LABEL_ZH) == allowed, \
        "多了 %s／少了 %s" % (set(GROUP_LABEL_ZH) - allowed, allowed - set(GROUP_LABEL_ZH))


# ── 真的不再呼叫模型 ────────────────────────────────────────────────────────
def _strip_comments(text):
    text = re.sub(r'"""4?.*?"""', "", text, flags=re.S)
    return re.sub(r"^\s*#.*$", "", text, flags=re.M)


def test_diagnosis_no_longer_calls_a_language_model():
    src = _strip_comments((REPO / "cloud" / "module_d" / "diagnosis.py")
                          .read_text(encoding="utf-8"))
    for token in ("generate_content", "genai", "gemini", "composite_prompt"):
        assert token not in src, "diagnosis.py 仍含 %s" % token


def test_summary_module_calls_nothing_external():
    """純函式：不連網、不讀資料庫、不讀檔。"""
    src = _strip_comments((REPO / "cloud" / "module_d" / "summary.py")
                          .read_text(encoding="utf-8"))
    for token in ("requests", "urllib", "cursor", "execute", "open(", "import os"):
        assert token not in src, "summary.py 不該出現 %s" % token


def test_app_fallback_has_no_hardcoded_fabricated_copy():
    """備援文案不得再寫死條碼專屬的假內容。

    原本寫死了「檢出去水醋酸，法規禁止加在茶葉飲料」與針對具名企業的
    合規背書。那些會真的顯示給使用者。
    """
    src = (REPO / "APP" / "src" / "utils" / "aiSummaries.ts").read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    assert "去水醋酸" not in src
    assert "統一企業" not in src
    assert "4710018123456" not in src, "仍有寫死條碼的分支"
