"""Fog 端的純轉換函式 —— 不依賴 FastAPI、資料庫或網路。

2026-08-05 從 main.py 抽出。抽出的動機是「可測性」：main.py 開頭 import fastapi，
測試要載入它就得裝整套 web 框架；而這兩個函式本身跟 FastAPI 毫無關係。抽出後
tests/contract/ 可以直接 import，也才能把 Cloud → Fog → App 三層的轉換串起來驗證。

（Cloud 端已用同樣的方式處理過，見 server/module_a ~ module_d 的抽出。）
"""
import copy
from datetime import datetime


def mask_sensitive_data(payload: dict) -> dict:
    """
    執行數據脫敏處理 (Task A)
    移除：device_id, uuid, location, user_conditions
    註：個人化已移至 App 端，正常情況下 App 不再送出 user_conditions；
        此處的移除保留為深度防禦，以防舊版 App 仍送出。
    模糊化：timestamp 截斷至小時精度
    """
    clean_payload = copy.deepcopy(payload)

    # 1. 刪除敏感欄位
    clean_payload.pop("device_id", None)
    clean_payload.pop("uuid", None)

    # 2. 刪除地理位置資訊
    clean_payload.pop("location", None)

    # 3. 刪除使用者健康背景（過敏原、慢性病等），不轉發至 Cloud
    #    個人化已移至 App 端（2026-08-04），Fog 本身不再讀取此欄位
    clean_payload.pop("user_conditions", None)

    # 4. 時間戳記截斷至小時精度 (格式假設: 2024-01-15T14:25:30 -> 2024-01-15T14:00:00)
    if "timestamp" in clean_payload:
        ts = clean_payload["timestamp"]
        if isinstance(ts, str) and len(ts) >= 13:
            clean_payload["timestamp"] = ts[:13] + ":00:00"

    return clean_payload


# 上游沒有回出一次分析結果時，能辨認出來的形狀。
# 這些鍵任何一個出現，就表示 Cloud 真的跑完了分析。
_ANALYSIS_KEYS = ("product_info", "final_health_diagnosis", "name", "ingredients",
                  "ingredients_list", "nutrition", "nutrition_facts", "health_score")
# 上游明確表示「這不是分析結果」的 status。
_NOT_ANALYSIS_STATUS = ("error", "rejected", "not_found", "degraded")


def looks_like_analysis(result) -> bool:
    """這份 payload 是不是一次**真的分析結果**。

    存在的理由：`normalize_result()` 會替沒有分數的結果補上預設 75 分
    （骨架注入 ＋ 第 3 段的 `or 75`）。那對「Cloud 算完但只缺欄位」是合理的，
    對「Cloud 根本沒算」是**把錯誤呈現成一個分數**。實測（2026-09-12）：

        FastAPI 的 500        {'detail': 'Internal Server Error'}  → health_score=75
        Cloudflare JSON 錯誤   {'errors':[{'code':1033}]}           → health_score=75
        Cloud 回 error        {'status':'error','message':'x'}     → health_score=75

    而 `CLAUDE.md` 訂的判準是「**判定結果是否有效請看有沒有 health_score**」
    ——上面這三種都會通過那個判準。

    這與 2026-08-05 把食安事件從「未查詢卻顯示為安全」改掉是同一條原則：
    **對食安 App 而言，把「沒算到」呈現成一個數字有風險。**
    """
    if not isinstance(result, dict):
        return False
    if result.get("status") in _NOT_ANALYSIS_STATUS:
        return False
    if result.get("status") == "success" and isinstance(result.get("data"), dict):
        return True
    return any(k in result for k in _ANALYSIS_KEYS)


def build_upstream_error_response(result, status_code=None) -> dict:
    """上游沒回出分析結果時，轉成 App 認得的錯誤形狀。

    **刻意不含 health_score**——App 據此進錯誤頁顯示 message，
    與降階回應（`build_degraded_local_response`）同一條原則。
    原始 payload 收在 `upstream` 供除錯，不放進頂層以免被誤當成結果欄位。
    """
    msg = None
    if isinstance(result, dict):
        for k in ("message", "detail", "error", "reason"):
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                msg = v.strip()
                break
    if not msg:
        msg = "雲端分析暫時無法完成，請稍後再試。"
    out = {
        "status": (result.get("status") if isinstance(result, dict) else None) or "error",
        "message": msg,
        "cached": False,
    }
    if status_code is not None:
        out["upstream_status"] = status_code
    if isinstance(result, dict) and result:
        out["upstream"] = result
    return out


def normalize_result(result: dict):
    """將 Cloud 回應正規化為 App 期望的格式（與使用者無關）。

    個人化比對（過敏原、族群添加物風險、慢性病營養閾值）已於 2026-08-04 移至 App 端，
    使用者健康背景不再離開裝置。本函式只保留與使用者無關的格式處理：
      1. 格式解包（Cloud 的 {status, data:{...}} 或扁平格式 → 統一結構）
      2. final_health_diagnosis 骨架注入
      3. 客觀 Nutri-Score 分數/等級提升至頂層
      4. overall_summary / additives_summary / safety_events_summary 雙向映射（App 直接讀這三個）
    """
    print(f"[DEBUG] Processing result from Cloud. Keys: {list(result.keys())}")

    # --- 0. 上游沒回出分析結果就原樣轉成錯誤，**不要補分數** ---
    #     見 looks_like_analysis 的說明：往下走會讓任何錯誤長出 75 分。
    if not looks_like_analysis(result):
        print("[WARN] 上游未回出分析結果，不注入分數")
        return build_upstream_error_response(result)

    # --- 1. 格式標準化 (Unwrapping) ---
    target = {}
    if result.get("status") == "success" and "data" in result:
        target = result["data"]
    elif "product_info" in result or "final_health_diagnosis" in result:
        target = result
    else:
        # 如果是扁平化格式 (直接有 name 或 ingredients)
        if any(k in result for k in ["name", "ingredients", "ingredients_list", "nutrition"]):
            print("[INFO] Flat format detected, wrapping into product_info")
            target = {
                "product_info": {
                    "name": result.get("name") or result.get("product_name", "未知產品"),
                    "brand": result.get("brand", ""),
                    "ingredients": ",".join(result.get("ingredients_list", [])) if isinstance(result.get("ingredients_list"), list) else result.get("ingredients", ""),
                    "allergens": result.get("allergens", "")
                },
                "nutrition_facts": result.get("nutrition") or result.get("nutrition_facts", {}),
                "ingredients_detail": result.get("ingredients_detail", [])
            }
            # ⚠ **包好要放回 result，否則整段白做。** 本函式結尾是 `return result`，
            #    而這裡的 `target` 是**新建的 dict**——不合併回去的話，
            #    `product_info`／`nutrition_facts`／`ingredients_detail` 全部遺失，
            #    呼叫端只會拿到原本的扁平鍵。
            #    （2026-09-12 實測：扁平輸入只回 name／ingredients_list／health_score／
            #    risk_level，包裝結果從未離開這個函式。真實 Cloud 不走這條路，
            #    所以一直沒人發現。）
            result.update(target)
        else:
            target = result

    # --- 2. 確保診斷區塊存在 ---
    # ⚠ **score 留 None，不要放 75。** 這個骨架的用途是讓 App 拿得到結構，
    #    不是提供一個分數。真實的 Cloud 一定會回 health_score
    #    （Nutri-Score 必然算得出數字），所以走到這裡就表示上游沒給——
    #    那時候填 75 就是憑空造一個健康分數出來。
    #    grade 同理：沒有分數就沒有等級。
    if "final_health_diagnosis" not in target:
        target["final_health_diagnosis"] = {
            "score": None,
            "grade": None,
            "summary": "AI 解析完成。",
            "score_breakdown": []
        }

    # 如果只有 product_info 但沒填入基本資訊，從平鋪層抓取
    if "product_info" not in target and "name" in result:
        target["product_info"] = {"name": result.get("name"), "brand": result.get("brand", "")}

    # --- 3. 取得並回填 Cloud 端計算之客觀 Nutri-Score 分數與等級 ---
    # ⚠ `target` 也要找。Cloud 的 `{status, data:{...}}` 形狀會把分數放在 data 裡，
    #    只看 `result` 頂層會找不到，於是落到骨架的預設 75——**真的算出來的 82
    #    會被換成捏造的 75**（2026-09-12 實測）。骨架是在第 2 段才注入的，
    #    所以它的 score 一定是 75，不能當成上游給的值。
    objective_score = (result.get("health_score") or target.get("health_score")
                       or target["final_health_diagnosis"].get("score"))
    objective_grade = (result.get("risk_level") or target.get("risk_level")
                       or target["final_health_diagnosis"].get("grade")
                       or result.get("grade"))

    # ⚠ **只有真的拿到分數才寫回去。** 上游沒給就讓 health_score 保持不存在，
    #    App 據此把它當成「不是有效結果」（CLAUDE.md 的判準），
    #    而不是看到一個沒人算過的數字。已辨識出來的成分與品名照樣留在 payload，
    #    之後 App 若要呈現「有內容但無評分」的部分結果，資料是齊的。
    if objective_score is not None:
        result["health_score"] = objective_score
        result["risk_level"] = objective_grade or "C"
        target["final_health_diagnosis"]["score"] = objective_score
        target["final_health_diagnosis"]["grade"] = objective_grade or "C"
    else:
        print("[WARN] 上游未提供 health_score，不補預設值")

    # --- 4. 雙向相容映射：確保三個 summary 在 result 最外層 ---
    if "overall_summary" not in result or not result["overall_summary"]:
        explanation = result.get("explanation")
        if isinstance(explanation, dict):
            result["overall_summary"] = explanation.get("overall") or explanation.get("overall_summary")

    if "additives_summary" not in result or not result["additives_summary"]:
        explanation = result.get("explanation")
        if isinstance(explanation, dict):
            result["additives_summary"] = explanation.get("additives") or explanation.get("additives_summary")

    if "safety_events_summary" not in result or not result["safety_events_summary"]:
        notes = result.get("personalized_notes")
        if isinstance(notes, list) and len(notes) > 0:
            result["safety_events_summary"] = "\n".join(notes)
        elif isinstance(notes, str):
            result["safety_events_summary"] = notes
        else:
            explanation = result.get("explanation")
            if isinstance(explanation, dict):
                result["safety_events_summary"] = explanation.get("safety") or explanation.get("safety_events_summary")

    # 同步複製到 target / final_health_diagnosis 以免其他位置需要
    if "overall_summary" in result and isinstance(target.get("final_health_diagnosis"), dict):
        target["final_health_diagnosis"]["overall_summary"] = result["overall_summary"]
    if "additives_summary" in result and isinstance(target.get("final_health_diagnosis"), dict):
        target["final_health_diagnosis"]["additives_summary"] = result["additives_summary"]
    if "safety_events_summary" in result and isinstance(target.get("final_health_diagnosis"), dict):
        target["final_health_diagnosis"]["safety_events_summary"] = result["safety_events_summary"]

    return result


# ─── Fog 本機降階：Cloud 連不上時，以本機 OCR 產出的部分結果 ──────────────────────
# 契約由 tests/contract/test_degraded_local_contract.py 強制，App 端型別是
# APP/src/types.ts 的 DegradedLocalResponse。
DEGRADED_LOCAL_MODE = "local_ocr"

# 降階結果刻意不提供的欄位。App 據此把對應區塊顯示成「離線時無法提供」，
# 而不是把缺值當成 0、當成「沒有」。
DEGRADED_LOCAL_UNAVAILABLE = (
    "health_score", "risk_level", "score_breakdown", "nutrition_facts",
    "daily_reference", "product_info", "allergen_warnings",
    "food_safety_events", "overall_summary", "additives_summary",
    "safety_events_summary",
)


def build_degraded_local_response(barcode, ingredients_detail, elapsed_s, engine,
                                  processed_at=None) -> dict:
    """降階回應的唯一組裝點。

    **刻意不含 health_score**：現行 App 只看 health_score 決定是否渲染結果頁，沒有它就
    進錯誤頁並顯示 message。所以還沒支援降階的 App 會顯示這段說明，不會把沒算過的
    分數呈現給使用者。也因此這份結果**不可再經過 normalize_result()**——它會替缺分數
    的結果補上預設 75 分。

    ingredients_detail 的每一項直接取自 module_a.match_ingredients() 的 chemical ＋
    basic_detail，與 Cloud 回應的同名欄位同形，App 可共用同一套元件。
    """
    # 依正式名去重：抽取器常把同一項切出兩個版本（「維生素C (抗氧化劑)」與「維生素C」），
    # 各自配到同一筆添加物。訊息裡的數字要算物質數，不是項目數。
    n_additives = len({it.get("officialName") or it.get("name")
                       for it in ingredients_detail if it.get("isAdditive") is True})
    if not ingredients_detail:
        message = ("雲端分析暫時無法連線，本機離線辨識也未能從照片讀出成分。"
                   "請對準成分表重新拍攝，或稍後再試。")
    elif n_additives == 0:
        # 與 App 的空狀態同一條原則（commit 6b57132）：沒比對到 ≠ 不含。
        message = ("雲端分析暫時無法連線，已改用本機離線辨識，但沒有比對到添加物。"
                   "離線辨識可能漏讀，這不代表本產品不含添加物，請以包裝標示為準，"
                   "並稍後重試以取得完整分析。")
    else:
        message = (f"雲端分析暫時無法連線，已改用本機離線辨識，找到 {n_additives} 項添加物。"
                   "離線辨識可能有遺漏，且不含健康評分、營養與食安資訊，"
                   "請稍後重試以取得完整分析。")
    return {
        "status": "degraded",
        "degraded_mode": DEGRADED_LOCAL_MODE,
        "message": message,
        "barcode": barcode,
        "ingredients_detail": ingredients_detail,
        "unavailable_fields": list(DEGRADED_LOCAL_UNAVAILABLE),
        "engine": engine,
        "processed_at": processed_at or datetime.now().isoformat(),
        "elapsed_s": round(float(elapsed_s), 2),
    }
