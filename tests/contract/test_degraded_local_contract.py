"""釘住 Fog 本機降階回應的形狀。

情境：App 帶照片查詢、Cloud 連不上、Fog 也沒有這個條碼的快取。這時 Fog 以本機 OCR
辨識成分，用 Cloud 同一份 module_a 規則比對添加物，回一份**部分結果**。
產生處是 fog/transforms.py 的 build_degraded_local_response()。

這個檔案要擋下四件事：
  1. 降階結果被現行 App 當成完整結果渲染，顯示一個沒算過的分數。
     App 只要看到 health_score 就渲染結果頁（APP/src/screens/HomeScreen.tsx），
     所以降階結果絕不可帶它。~~沒有它時 App 會顯示 message，這是刻意的安全退路。~~
     **2026-09-13 更新**：App 已接上 `DegradedLocalResponse`，改為渲染第三種狀態
     （成分與添加物清單 ＋ 明確列出拿不到什麼），不再只顯示那段文字。
     「不可顯示分數」這條不變，而且現在由本檔末尾的測試直接檢查 JSX。
  2. Node 層把降階結果寫進快取，之後被當成正式結果重播。
     Node 只快取 status === 'success' 的回應（fog/queryHandler.ts）。
  3. Fog 與 App 對欄位的認知分岔。App 的 DegradedLocalResponse 型別在此逐欄比對。
  4. 降階的成分項目與 Cloud 回應不同形，App 無法共用元件。兩邊都來自
     module_a.match_ingredients()；這裡用真的種子資料跑一次，同時釘住 fog/seed_db.py 的
     cursor 行為：JSON 欄位若沒解碼，類別統稱就載不進來，「調味劑(…)」不會展開。
"""
import re
from pathlib import Path

import pytest

import module_a.ingredient_matching as IM
from module_d.response_builder import build_response
from seed_db import RealDictLikeCursor, load_additives_db
from test_layer_chain import app_unwrap
from transforms import build_degraded_local_response

APP_TYPES_FILE = Path(__file__).resolve().parents[2] / "APP" / "src" / "types.ts"

# 刻意用相等比對：多出欄位也要被看見，並同步 App 型別。
EXPECTED_KEYS = {
    "status", "degraded_mode", "message", "barcode", "ingredients_detail",
    "unavailable_fields", "engine", "processed_at", "elapsed_s",
}
# Node 層轉發時加上的欄位（fog/queryHandler.ts），App 型別要一併宣告
NODE_ADDED_KEYS = {"cached"}

# 涵蓋每一種判定：添加物、由類別統稱展開的添加物、水、未收錄、純類別統稱
INGREDIENTS = ["水", "蔗糖", "己二烯酸鉀", "調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉)", "香料"]
ENGINE = {"ocr": "test", "extractor": "test", "matcher": "test", "additive_db": "test"}


@pytest.fixture(scope="module")
def matched():
    """用真的種子資料跑 Cloud 的比對函式。module_a 的快取是模組層全域變數，
    前後都清掉，避免結果取決於測試執行順序。"""
    saved = (IM._generic_terms_cache, IM._substance_names_cache)
    IM._generic_terms_cache = IM._substance_names_cache = None
    try:
        yield IM.match_ingredients(INGREDIENTS, None, RealDictLikeCursor(load_additives_db()), None)
    finally:
        IM._generic_terms_cache, IM._substance_names_cache = saved


@pytest.fixture
def response(matched):
    return build_degraded_local_response(
        "4710018123456", matched["chemical"] + matched["basic_detail"], 1.234, ENGINE,
        processed_at="2026-09-12T10:00:00")


def test_key_set_is_frozen(response):
    assert set(response) == EXPECTED_KEYS


def test_current_app_shows_message_not_a_score(response):
    assert "health_score" not in response
    result, error = app_unwrap(response)
    assert result is None, "App 會把降階結果當成完整結果渲染，顯示一個沒算過的分數"
    assert error == response["message"]


def test_unavailable_fields_are_really_absent(response):
    assert "health_score" in response["unavailable_fields"]
    present = [f for f in response["unavailable_fields"] if f in response]
    assert not present, f"標為不提供的欄位出現在回應裡：{present}"


def test_node_layer_will_not_cache_it(response):
    # fog/queryHandler.ts：只有 status === 'success' 才寫入快取
    assert response["status"] != "success"
    assert response["degraded_mode"] == "local_ocr"


def test_ingredient_items_have_the_same_shape_as_cloud(matched, response):
    """把同一份比對結果交給 Cloud 的 build_response()，成分項目必須完全相同。"""
    cloud = build_response(
        product={"barcode": "4710018123456", "name": "x", "brand": "x",
                 "manufacturer": "x", "certifications": "[]"},
        ai_data={"grade": "C", "score": 50},
        calc_result={"details": {"breakdown": {}}},
        deterministic_score=50,
        chemical=matched["chemical"], basic=matched["basic"],
        calculated_ingredient_types=matched["calculated_ingredient_types"],
        final_safety_events=[], raw_allergens="", nutrition={},
        basic_detail=matched["basic_detail"])
    assert response["ingredients_detail"] == cloud["ingredients_detail"]


def test_seed_cursor_decodes_json_columns(matched):
    """「調味劑(…)」要靠 category 欄位衍生的類別統稱才會展開；該欄沒解碼就展不開。"""
    additives = {c["officialName"] for c in matched["chemical"]}
    assert "L-麩酸鈉" in additives
    assert any("次黃嘌呤核苷磷酸二鈉" in a for a in additives)
    assert "己二烯酸鉀" in additives
    assert [b["name"] for b in matched["basic_detail"] if b["isGenericTerm"]] == ["香料"]


def test_zero_additives_does_not_claim_absence():
    """沒比對到不等於不含（與 App 空狀態同一條原則，commit 6b57132）。"""
    resp = build_degraded_local_response(
        None, [{"name": "蔗糖", "isAdditive": False, "description": "x"}], 1.0, ENGINE)
    assert "不代表" in resp["message"]


def test_additive_count_is_by_substance_not_by_item():
    """同一添加物被切成兩項時（實測 c01：「維生素C (抗氧化劑)」與「維生素C」），訊息只算一次。"""
    items = [{"name": "a", "isAdditive": True, "officialName": "維生素C", "description": "x"},
             {"name": "b", "isAdditive": True, "officialName": "維生素C", "description": "x"},
             {"name": "c", "isAdditive": True, "officialName": "檸檬酸", "description": "x"}]
    assert "找到 2 項添加物" in build_degraded_local_response(None, items, 1.0, ENGINE)["message"]


def test_empty_result_asks_to_retake():
    resp = build_degraded_local_response(None, [], 1.0, ENGINE)
    assert resp["ingredients_detail"] == []
    assert "重新拍攝" in resp["message"]


def test_app_type_matches_fog_keys():
    source = APP_TYPES_FILE.read_text(encoding="utf-8")
    m = re.search(r"export interface DegradedLocalResponse \{(.*?)\n\}", source, re.S)
    assert m, f"在 {APP_TYPES_FILE.name} 找不到 DegradedLocalResponse"
    app_keys = set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))
    assert app_keys == EXPECTED_KEYS | NODE_ADDED_KEYS, (
        f"只有 Fog 有：{sorted(EXPECTED_KEYS - app_keys)}；"
        f"只有 App 有：{sorted(app_keys - EXPECTED_KEYS - NODE_ADDED_KEYS)}")


# ── App 端的降階畫面（2026-09-13 接上 DegradedLocalResponse 後新增）──────────
HOMESCREEN = Path(__file__).resolve().parents[2] / "APP" / "src" / "screens" / "HomeScreen.tsx"


def _screen() -> str:
    return HOMESCREEN.read_text(encoding="utf-8")


def _degraded_jsx() -> str:
    """只取降階那一段 JSX，並**去掉註解**。

    ⚠ 不去註解的話，寫「這份回應沒有 health_score」這種說明本身就會讓
    下面那條測試紅掉——第一版就是這樣（同一個錯誤在 test_timing_headers.py
    也犯過一次）。要檢查的是**畫面會渲染什麼**，不是原始碼提到什麼。
    """
    src = _screen()
    i = src.index("{!isAnalyzing && !analysisError && degradedResult && (")
    j = src.index("{/* Results */}", i)
    jsx = src[i:j]
    B = chr(92)
    jsx = re.sub(B + "{/" + B + "*.*?" + B + "*/" + B + "}", "", jsx, flags=re.S)  # JSX 註解
    jsx = re.sub("//[^" + B + "n]*", "", jsx)                                      # 行註解
    return jsx


def test_app_recognises_the_degraded_mode():
    """靠 `degraded_mode === 'local_ocr'` 而不是只看 status。

    `status: degraded` 也可能來自別的路徑，而只有本機 OCR 這一種帶著
    ingredients_detail。認錯會渲染一個空的清單。
    """
    src = _screen()
    assert "degraded_mode === 'local_ocr'" in src
    assert "setDegradedResult" in src


def test_degraded_view_never_renders_a_score():
    """這份回應沒有 health_score，畫面上出現任何分數就是編造出來的。

    `normalize_result` 憑空補 75 分那次（A1）正是這個形狀，
    差別只在那次發生在 Fog、這次會發生在畫面。
    """
    jsx = _degraded_jsx()
    for banned in ["health_score", "risk_level", "Gauge", "nutrition_facts"]:
        assert banned not in jsx, f"降階畫面不該出現 {banned}"


def test_normal_result_view_is_excluded_when_degraded():
    """兩段都會渲染的話，使用者會同時看到「離線部分結果」與一個完整結果頁。"""
    src = _screen()
    assert "!isAnalyzing && !analysisError && !degradedResult && analysisResult" in src


def test_every_unavailable_field_has_a_chinese_label():
    """`unavailable_fields` 是要給使用者看的。少一個標籤就會漏列一項
    「拿不到的東西」，而那正是這個畫面存在的理由。"""
    src = _screen()
    BS = chr(92)
    pat = "const UNAVAILABLE_LABELS[^{]*" + BS + "{(.*?)" + BS + "n};"
    m = re.search(pat, src, re.S)
    assert m, "找不到 UNAVAILABLE_LABELS"
    labelled = set(re.findall(BS + "w+(?=:)", m.group(1)))
    from transforms import DEGRADED_LOCAL_UNAVAILABLE
    missing = set(DEGRADED_LOCAL_UNAVAILABLE) - labelled
    assert not missing, f"這些欄位沒有中文標籤：{sorted(missing)}"


def test_degraded_view_keeps_the_allergen_check():
    """過敏原偵測只需要 ingredients_detail，降階跑得起來，
    而它是 Cloud 掛掉時最該保留的一塊。"""
    src = _screen()
    assert "degradedRisks" in src
    assert "matchedAllergens" in _degraded_jsx()


def test_degraded_view_does_not_claim_absence():
    """純 OCR 漏讀機率比雲端高，所以「沒偵測到」不可寫成「不含」。

    與 `NOT_IN_DB_LABEL`（"本系統未收錄"，刻意不用「無」「安全」）同一條原則。
    """
    jsx = _degraded_jsx()
    assert "不代表" in jsx, "降階畫面缺少「未列出不代表不含」這類但書"
    assert "未比對到添加物資料庫" in jsx,         "其他成分的標題必須是「未比對到添加物資料庫」，不可寫成「非添加物」"
