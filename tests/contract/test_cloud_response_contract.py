"""釘住 Cloud 回應的形狀 —— 跨層契約的錨點。

為什麼是這裡：`build_response()` 是純函式（模組層只 import json 與 datetime，
不碰 DB／網路／環境變數），而且是 Cloud 所有成功回應的唯一組裝點。在這裡釘住形狀，
等於在契約的源頭釘住它。

為什麼不用 FastAPI 的 response_model：`/api/analyze` 有 11 條 return 路徑、至少 4 種
形狀（success／rejected／error／not_found），且 response_model 預設會**過濾掉未宣告的
欄位**——那會靜默丟掉 App 需要的資料，比沒有驗證更危險。

這個檔案要擋下的真實事故：
  - App 讀 `data.nutrition_facts` 做慢性病閾值判斷，該欄位只存在於巢狀的 data 底下，
    頂層沒有。任何人把它搬到頂層或改名，App 的高血壓／糖尿病警示就會靜默失效。
  - App 讀 overall_summary / additives_summary / safety_events_summary，
    但這三個在 Cloud 端是由 explanation 與 personalized_notes 間接組出來的。
"""
import numbers

import pytest

from module_d.response_builder import build_response


# ─── Cloud 回應的凍結欄位集合 ──────────────────────────────────────────────────
# 改動這個集合前請先確認 Fog 的 normalize_result() 與 App 的 AnalysisResponse
# 是否需要同步調整。這裡「刻意」用相等而非包含比對——多出欄位也要被看見。
EXPECTED_TOP_LEVEL_KEYS = {
    "status", "barcode", "health_score", "risk_level", "product_info",
    "overall_summary", "additives_summary", "safety_events_summary",
    "score_breakdown", "risk_tags", "allergen_warnings", "ingredients_detail",
    "ingredient_types", "daily_reference", "score_estimated_inputs",
    "score_fully_measured", "food_safety_events", "final_health_diagnosis",
    "explanation", "personalized_notes", "processed_at", "data",
}

# App 端 `AnalysisResponse`（APP/src/types.ts）實際讀取的頂層欄位。
# 這是跨層斷言：Cloud 少給任何一個，App 的畫面就會缺一塊。
APP_CONSUMED_TOP_LEVEL = [
    "health_score", "risk_level", "score_breakdown", "product_info",
    "allergen_warnings", "food_safety_events", "ingredients_detail",
    "overall_summary", "additives_summary", "safety_events_summary",
]

# App 的 checkNutritionThresholds() 讀取的營養欄位（每 100g/100mL 基準）
NUTRITION_FIELDS = {"calories", "protein", "fat", "sugar", "sodium"}


@pytest.fixture
def response():
    """以捏造的純資料呼叫真實的 build_response()。不需要 DB、網路或 API key。"""
    return build_response(
        product={
            "barcode": "4710018123456",
            "name": "測試飲料",
            "brand": "測試品牌",
            "manufacturer": "測試公司",
            "certifications": "[]",
            "calories": 210.0,
            "sugar": 29.2,
            "sodium": 480.0,
        },
        ai_data={
            "grade": "D",
            "score": 62,
            "overall_summary": "整體摘要",
            "additives_summary": "添加物摘要",
            "safety_events_summary": "食安摘要",
            "warnings": ["高糖"],
        },
        calc_result={"details": {"breakdown": {"sugars": 7, "energy": 3}}},
        deterministic_score=62,
        chemical=[{
            "name": "麥芽糊精",
            "isAdditive": True,
            "description": "增稠劑",
            "risk_level": "medium",
            "groupRisks": [{"group": "child", "riskLevel": 3, "reason": "測試原因"}],
        }],
        basic=["水"],
        calculated_ingredient_types={"麥芽糊精": "additive", "水": "ingredient"},
        final_safety_events=[],
        raw_allergens="牛奶,大豆",
        nutrition={
            "calories": 210.0, "protein": 0.5, "fat": 0.0,
            "sugar": 29.2, "sodium": 480.0,
        },
        daily_reference={"basis": "per_100g", "items": []},
        basic_detail=None,
        ns_estimated=[],
    )


def test_top_level_keys_are_frozen(response):
    """頂層欄位集合不得在無人察覺的情況下增減。"""
    assert set(response.keys()) == EXPECTED_TOP_LEVEL_KEYS


@pytest.mark.parametrize("field", APP_CONSUMED_TOP_LEVEL)
def test_app_consumed_field_is_present(response, field):
    """App 讀的每個頂層欄位，Cloud 都必須產出。"""
    assert field in response, f"App 的 AnalysisResponse 讀取 {field}，但 Cloud 沒有產出"
    assert response[field] is not None, f"{field} 為 None，App 會顯示空白"


def test_nutrition_facts_lives_under_data_not_top_level(response):
    """App 的慢性病閾值只看 data.nutrition_facts。搬走或改名都會讓警示靜默失效。"""
    assert "nutrition_facts" not in response, (
        "nutrition_facts 出現在頂層——若這是刻意的搬移，"
        "APP/src/utils/personalization.ts 的 checkNutritionThresholds() 必須同步修改"
    )
    assert "data" in response and "nutrition_facts" in response["data"]
    assert set(response["data"]["nutrition_facts"]) == NUTRITION_FIELDS


def test_sodium_and_sugar_are_numeric(response):
    """鈉與糖必須是數值——App 用 `typeof value === 'number'` 判斷是否比對閾值，
    型別若變成字串，警示會靜默不觸發而非報錯。"""
    nutrition = response["data"]["nutrition_facts"]
    for field in ("sodium", "sugar"):
        assert isinstance(nutrition[field], numbers.Real), f"{field} 不是數值"


def test_risk_level_is_one_of_three_values(response):
    """App 的 AnalysisResponse 把 risk_level 宣告為 'low' | 'medium' | 'high'。"""
    assert response["risk_level"] in {"low", "medium", "high"}


def test_group_risks_survive_into_ingredients_detail(response):
    """個人化添加物風險靠 ingredients_detail[].groupRisks，結構不得被攤平。"""
    additives = [i for i in response["ingredients_detail"] if i.get("isAdditive")]
    assert additives, "沒有任何添加物項目"
    risks = additives[0].get("groupRisks")
    assert risks and {"group", "riskLevel", "reason"} <= set(risks[0])


def test_score_breakdown_items_have_app_expected_shape(response):
    """App 的 getScoreBreakdownList() 讀 reason / description / points。"""
    for item in response["score_breakdown"]:
        assert {"reason", "description", "points"} <= set(item)
        assert isinstance(item["points"], numbers.Real)


def test_final_health_diagnosis_has_no_grade_key(response):
    """Cloud 不產出 grade，是 Fog 的 normalize_result() 補上的。
    若 Cloud 開始自己產出 grade，Fog 的覆寫邏輯需要重新檢視。"""
    assert "grade" not in response["final_health_diagnosis"]
