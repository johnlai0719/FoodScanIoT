"""三層串接：Cloud → Fog → App，驗證欄位一路活到最後。

前兩份契約測試各自釘住一端的形狀，但「兩端形狀都對」不等於「串起來會通」。
中間還隔著 Fog 的 normalize_result()，它會解包、注入骨架、搬移欄位——任何一步
弄丟 App 需要的東西，前兩份測試都不會紅。

之所以能離線驗證整條鏈，是因為三層的轉換**都是純函式**：
    server/module_d/response_builder.build_response()   只 import json / datetime
    fog/transforms.normalize_result()                   只 import copy
    App 的解包判斷                                       一行三元運算式，於下方複刻
需要部署才知道的是「真實資料長什麼樣」與「網路通不通」，不是「對應關係對不對」。
"""
import pytest

from module_d.response_builder import build_response
from transforms import mask_sensitive_data, normalize_result

# APP/src/screens/HomeScreen.tsx 讀取的頂層欄位
APP_CONSUMED = [
    "health_score", "risk_level", "score_breakdown", "product_info",
    "allergen_warnings", "food_safety_events", "ingredients_detail",
    "overall_summary", "additives_summary", "safety_events_summary",
    # App 以此顯示「更新於 X」；快取命中時仍須是原始計算時間，故必須一路存活
    "processed_at",
]


def app_unwrap(payload: dict):
    """複刻 App 的解包邏輯（HomeScreen.tsx）：

        const result = json.health_score !== undefined ? json : (json.data ?? json);
        if (result?.health_score === undefined) throw new Error(result?.message ?? ...);

    回傳 (result, error_message)。error_message 非 None 代表 App 會顯示錯誤頁。
    """
    result = payload if payload.get("health_score") is not None else payload.get("data", payload)
    if not isinstance(result, dict) or result.get("health_score") is None:
        msg = (result or {}).get("message") if isinstance(result, dict) else None
        return None, msg or payload.get("message") or "無法完成分析"
    return result, None


@pytest.fixture
def cloud_success():
    """Cloud 的成功回應（真實形狀，捏造資料）。"""
    return build_response(
        product={
            "barcode": "4710018123456", "name": "測試飲料", "brand": "測試品牌",
            "manufacturer": "測試公司", "certifications": "[]",
            "calories": 210.0, "sugar": 29.2, "sodium": 480.0,
        },
        ai_data={
            "grade": "D", "score": 62,
            "overall_summary": "整體摘要", "additives_summary": "添加物摘要",
            "safety_events_summary": "食安摘要", "warnings": ["高糖"],
        },
        calc_result={"details": {"breakdown": {"sugars": 7, "energy": 3}}},
        deterministic_score=62,
        chemical=[{
            "name": "麥芽糊精", "isAdditive": True, "description": "增稠劑",
            "risk_level": "medium",
            "groupRisks": [{"group": "child", "riskLevel": 3, "reason": "測試"}],
        }],
        basic=["水"],
        calculated_ingredient_types={"麥芽糊精": "additive"},
        final_safety_events=[],
        raw_allergens="牛奶,大豆",
        nutrition={"calories": 210.0, "protein": 0.5, "fat": 0.0,
                   "sugar": 29.2, "sodium": 480.0},
        daily_reference={"basis": "per_100g", "items": []},
        basic_detail=None,
        ns_estimated=[],
    )


# ─── 成功路徑：欄位要一路活到 App ─────────────────────────────────────────────

@pytest.mark.parametrize("field", APP_CONSUMED)
def test_app_field_survives_the_whole_chain(cloud_success, field):
    """Cloud 產出 → Fog 正規化 → App 解包後，App 讀的欄位都還在。"""
    result, error = app_unwrap(normalize_result(cloud_success))
    assert error is None, f"App 會顯示錯誤頁：{error}"
    assert result.get(field) is not None, f"{field} 在傳遞過程中遺失或變成 None"


def test_nutrition_facts_survives_for_chronic_warnings(cloud_success):
    """慢性病閾值靠 data.nutrition_facts，Fog 不得把它弄丟或搬走。"""
    result, _ = app_unwrap(normalize_result(cloud_success))
    nutrition = result.get("data", {}).get("nutrition_facts")
    assert nutrition is not None, "data.nutrition_facts 遺失，高血壓／糖尿病警示會靜默失效"
    assert nutrition["sodium"] == 480.0 and nutrition["sugar"] == 29.2


def test_group_risks_survive_for_personalization(cloud_success):
    """族群添加物風險靠 ingredients_detail[].groupRisks，結構不得被攤平。"""
    result, _ = app_unwrap(normalize_result(cloud_success))
    additives = [i for i in result["ingredients_detail"] if i.get("isAdditive")]
    assert additives and additives[0]["groupRisks"][0]["group"] == "child"


def test_fog_derives_missing_summaries():
    """三個 summary 缺席時，Fog 必須從 explanation / personalized_notes 補出來。

    build_response() 一律會產出非空的 summary（缺值時填入 fallback 字串），所以這條
    回填路徑只在「快取命中、而快取內容是舊格式」時才會走到——那正是 Fog 保留這段
    映射的唯一理由。用 build_response 的輸出測不到它，必須手工構造舊形狀。

    這條的前身是 fog/scratch/test_summary_mapping.py（需要跑起伺服器才能執行）。
    """
    legacy_cached = {
        "status": "success",
        "health_score": 70,
        "risk_level": "medium",
        "explanation": {"overall": "整體說明", "additives": "添加物說明"},
        "personalized_notes": ["食安事件說明"],
        "data": {"product_info": {"name": "舊格式產品"}, "ingredients_detail": []},
    }
    out = normalize_result(legacy_cached)

    assert out["overall_summary"] == "整體說明"
    assert out["additives_summary"] == "添加物說明"
    assert out["safety_events_summary"] == "食安事件說明"

    # 補出來之後，App 才不會在 EXPECTED_FIELDS 檢查時抱怨缺欄位
    result, error = app_unwrap(out)
    assert error is None
    for field in ("overall_summary", "additives_summary", "safety_events_summary"):
        assert result.get(field), f"{field} 未被回填，App 會顯示空白摘要"


def test_normalize_is_idempotent(cloud_success):
    """快取命中時 Fog 會對已正規化的物件再跑一次，兩次結果必須一致。

    這條在遷移前是會失敗的——舊版每跑一次就往 summary 疊一層前綴、
    往 allergen_warnings 追加一次比對結果，正是快取被使用者汙染的成因。
    """
    once = normalize_result(cloud_success)
    twice = normalize_result(dict(once))
    for field in APP_CONSUMED:
        assert once.get(field) == twice.get(field), f"{field} 在第二次正規化後改變了"


# ─── 失敗路徑：App 必須認得出來，而不是顯示假的 100 分 ────────────────────────

FAILURE_SHAPES = [
    # Cloud：照片沒通過品質閘門
    ({"status": "rejected",
      "message": "無法從照片辨識出食品成分標示。請對準產品的成分表與營養標示，重新拍攝清晰照片。"},
     "rejected"),
    # Cloud：查無條碼
    ({"status": "not_found", "barcode": "4710000000000",
      "message": "資料庫查無此條碼紀錄，請點擊下方「上傳照片」按鈕。"},
     "not_found"),
    # Cloud：例外
    ({"status": "error", "message": "分析失敗"}, "error"),
    # Fog：Cloud 不可用且無快取
    ({"status": "degraded", "message": "目前無法取得最新資料且無快取，請稍後再試",
      "cached": False, "cached_at": None},
     "degraded"),
]


@pytest.mark.parametrize("payload,label", FAILURE_SHAPES)
def test_failure_shapes_are_recognised_as_errors(payload, label):
    """這四種形狀都是 HTTP 200 且沒有 health_score。

    App 若只檢查 response.ok 就會把它們當成有效結果，結果頁的
    `health_score ?? 100` 會顯示一個假的 100 分，而後端寫好的引導訊息
    永遠不會被看到。2026-08-05 修正。
    """
    result, error = app_unwrap(payload)
    assert result is None, f"{label} 被誤判為有效結果，會顯示假的健康分數"
    assert error == payload["message"], f"{label} 的引導訊息沒有傳達給使用者"


def test_success_is_not_mistaken_for_failure(cloud_success):
    """反向確認：正常結果不得被錯誤判定攔下。"""
    result, error = app_unwrap(normalize_result(cloud_success))
    assert error is None and result["health_score"] == 62


# ─── 隱私：健康背景不得離開裝置 ───────────────────────────────────────────────

def test_masking_strips_everything_it_promises():
    """脫敏是「健康／裝置／位置資料不流向 Cloud」的唯一保證，且失敗是無聲的。

    對應 Obsidian 實驗與效能量化 T6（資料脫敏欄位驗證）。
    """
    payload = {
        "barcode": "4710018123456",
        "label_images": ["base64..."],
        "device_id": "device-abc",
        "uuid": "uuid-xyz",
        "location": "25.03,121.56",
        "user_conditions": {"group": "pregnant", "allergens": ["牛奶"]},
        "timestamp": "2026-08-05T14:25:30",
    }
    masked = mask_sensitive_data(payload)

    for leaked in ("device_id", "uuid", "location", "user_conditions"):
        assert leaked not in masked, f"{leaked} 沒有被剝除，會外洩到 Cloud"
    assert masked["timestamp"] == "2026-08-05T14:00:00", "時間戳未截斷至小時"
    # 分析必需的欄位不得被誤刪
    assert masked["barcode"] == "4710018123456"
    assert masked["label_images"] == ["base64..."]
    # 原始 payload 不可被就地修改（呼叫端還要用）
    assert "user_conditions" in payload
