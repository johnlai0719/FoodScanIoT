"""
Module D — 診斷資料的組裝（**不再呼叫語言模型**）

⚠ 2026-09-14：兩段文字總結改由 `module_d/summary.py` 以規則產生，本檔的
Gemini 呼叫、複合式 prompt 與「總結持久化到 products 表」整段移除。
理由與實測（本地 2B 與 Gemini 在此任務上都會寫出無依據的句子）記在
`summary.py` 的檔頭。總結現在是**衍生值**，不快取——快取會在規則改版後
留下與現行規則不一致的舊句子，而使用者無從分辨。

本檔現在只負責：沿用資料庫既有的過敏原欄位、回填本次重算的分數與等級。

從 main.py 抽出(2026-07-24)。邏輯與原本逐字相同,僅一處必要調整:

  原本以 `'ai_data' in locals()` 判斷「本次請求是否已在快取重算階段
  (cached_result 分支)組好 ai_data」,這個隱式狀態現在改為明確的
  `ai_data` 參數(未組好時傳 None)。main.py 呼叫端已將 `ai_data` 於函式
  最前面明確初始化為 None,行為與原本完全一致。

  另有一個資料流細節需保留:原本流程中 `raw_allergens` 會在「重用資料庫
  預存 AI 總結」分支內被重新賦值(改用無預設值的 product.get("allergens"),
  取代呼叫端原本 product.get('allergens', '[]') 的預設值)。此函式回傳更新後
  的 raw_allergens,呼叫端須以此回傳值覆蓋原本的區域變數,才能與原行為一致。

本函式為此段流程中資料庫使用的最後一步——結束時會關閉傳入的 cursor/db。
"""
import json


def generate_ai_diagnosis(product, chemical, final_safety_events, user_conditions, nutrition,
                          deterministic_score, deterministic_grade, ai_data, raw_allergens,
                          cursor, db) -> dict:
    """
    回傳 dict: ai_data(更新後), raw_allergens(可能已被重用分支重新賦值)
    """
    ai_data_exists = ai_data is not None

    # 沿用資料庫既有的過敏原欄位。
    #
    # ⚠ 2026-09-14：這個分支的門檻原本是「products 表裡有沒有 overall_summary」，
    # 那是總結還由模型產生、需要「一次分析永久重用」時的判準。總結改成規則
    # 產生之後那個門檻就失去意義——而且更糟，它會讓**沒有舊總結的商品拿不到
    # 已存的過敏原**。真正的門檻是「有沒有過敏原可沿用」。
    if not ai_data_exists and product and product.get("allergens"):
        raw_allergens = product.get("allergens")
        warnings_list = []
        if isinstance(raw_allergens, list):
            warnings_list = raw_allergens
        elif isinstance(raw_allergens, str):
            # 防禦性解析：這一欄在舊資料裡有純字串與 JSON 陣列兩種寫法。
            if raw_allergens.strip().startswith("["):
                try:
                    warnings_list = json.loads(raw_allergens)
                except Exception:
                    warnings_list = [raw_allergens]
            else:
                warnings_list = [raw_allergens]
        ai_data = {
            "score": deterministic_score,
            "grade": deterministic_grade,
            "warnings": warnings_list,
        }
        ai_data_exists = True

    if not ai_data_exists:
        ai_data = {
            "score": deterministic_score,
            "grade": deterministic_grade,
            "warnings": [],
        }
    else:
        # 沿用過敏原，但分數與等級一律以本次重算的結果為準。
        ai_data["score"] = deterministic_score
        ai_data["grade"] = deterministic_grade

    cursor.close()
    db.close()

    return {"ai_data": ai_data, "raw_allergens": raw_allergens}
