"""營養標示缺漏時的行為契約。

2026-09-14 線上實測（二配鮪魚飯糰）：營養標示沒讀到，`calculate_nutriscore`
四個欄位直接 `float(product[...])` 拋 TypeError，一路傳到 main.py 的
`return {"status": "error", "message": str(e)}`，使用者在 App 上看到的是

    float() argument must be a string or a real number, not 'NoneType'

兩件事都要守住：

1. **缺值不可填 0。** 熱量、糖、鈉都是扣分項，缺值當 0 等於宣稱含量為零而
   少扣分——分數偏樂觀，方向正好是食安上最不該錯的那一邊。這與
   `test_no_fabricated_score.py` 守的是同一條原則：**把「沒算到」呈現成一個
   數字有風險。**
2. **不可把例外訊息原樣回給使用者。** 使用者無從理解也無從處理。
"""
import re
from pathlib import Path

import pytest

from module_b.scoring import InsufficientNutritionData, calculate_nutriscore

REPO = Path(__file__).resolve().parents[2]


def _product(**over):
    p = {"name": "測試商品", "calories": 379, "sugar": 5.4, "sodium": 730,
         "protein": 17.8, "fat": 18.7, "saturated_fat": 3.4}
    p.update(over)
    return p


def test_a_complete_label_still_scores():
    r = calculate_nutriscore(_product(), [])
    assert r["deterministic_grade"] in list("ABCDE")


# ── 扣分項缺值：不給分數 ────────────────────────────────────────────────────
@pytest.mark.parametrize("field,zh", [("calories", "熱量"), ("sugar", "糖"),
                                      ("sodium", "鈉")])
def test_missing_penalty_input_refuses_to_score(field, zh):
    """缺任一扣分項就不產出等級，而不是當 0 算完。"""
    with pytest.raises(InsufficientNutritionData) as ei:
        calculate_nutriscore(_product(**{field: None}), [])
    assert field in ei.value.missing


def test_missing_saturated_fat_falls_back_to_the_fat_estimate():
    """飽和脂肪缺值時以脂肪×35% 推估，並記為推估——這是既有行為，不要改掉。"""
    r = calculate_nutriscore(_product(saturated_fat=None), [])
    assert "saturated_fat" in r.get("estimated_inputs", [])
    assert r["deterministic_grade"] in list("ABCDE")


def test_missing_both_saturated_fat_and_fat_refuses_to_score():
    """推估的來源也沒有時就推不出來，同樣不填 0。"""
    with pytest.raises(InsufficientNutritionData):
        calculate_nutriscore(_product(saturated_fat=None, fat=None), [])


# ── 加分項缺值：當 0 是對的 ─────────────────────────────────────────────────
def test_missing_protein_is_treated_as_no_bonus():
    """蛋白質是加分項，拿不到就不給那份加分——與扣分項方向相反。

    若誤套扣分項的規則而拒絕計分，會讓大量只是漏讀蛋白質的商品整個沒有評分。
    """
    r = calculate_nutriscore(_product(protein=None), [])
    assert r["deterministic_grade"] in list("ABCDE")


def test_missing_fibre_and_fruit_veg_are_still_tolerated():
    """纖維與蔬果比在台灣非強制標示，缺值不加分，本來就不該擋住計分。"""
    r = calculate_nutriscore(_product(fiber=None, fruit_veg_pct=None), [])
    assert r["deterministic_grade"] in list("ABCDE")


# ── 不可把例外訊息丟給使用者 ────────────────────────────────────────────────
def _src(*parts):
    text = REPO.joinpath(*parts).read_text(encoding="utf-8")
    text = re.sub(r'"""4?.*?"""', "", text, flags=re.S)
    return re.sub(r"^\s*#.*$", "", text, flags=re.M)


def test_the_handler_does_not_return_the_raw_exception_text():
    """`message: str(e)` 會把 Python 的錯誤原文送到使用者眼前。"""
    src = _src("server", "main.py")
    assert 'message": str(e)' not in src and "message': str(e)" not in src, \
        "main.py 仍把例外訊息原樣回傳"


def test_the_missing_nutrition_message_names_the_fields_in_chinese():
    """訊息要講得出缺哪一項，使用者才知道要重拍哪裡。"""
    src = _src("server", "main.py")
    assert "InsufficientNutritionData" in src, "沒有專門處理這個例外"
    assert "重新拍攝" in src, "訊息沒有告訴使用者下一步該做什麼"


def test_no_score_is_returned_when_nutrition_is_missing():
    """回應不得帶 health_score。

    App 的判準是「有沒有 health_score」（見 CLAUDE.md）。缺營養時若仍給分，
    等於把辨識失敗呈現成一個健康評分。
    """
    src = _src("server", "main.py")
    m = re.search(r"except InsufficientNutritionData.*?(?=\n    except |\nif __name__)",
                  src, re.S)
    assert m, "找不到該例外的處理區塊"
    assert "health_score" not in m.group(0), "缺營養時的回應含 health_score"
