"""Fog 端的純轉換函式 —— 不依賴 FastAPI、資料庫或網路。

2026-08-05 從 main.py 抽出。抽出的動機是「可測性」：main.py 開頭 import fastapi，
測試要載入它就得裝整套 web 框架；而這兩個函式本身跟 FastAPI 毫無關係。抽出後
tests/contract/ 可以直接 import，也才能把 Cloud → Fog → App 三層的轉換串起來驗證。

（Cloud 端已用同樣的方式處理過，見 server/module_a ~ module_d 的抽出。）
"""
import copy


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
        else:
            target = result

    # --- 2. 確保診斷區塊存在 ---
    if "final_health_diagnosis" not in target:
        target["final_health_diagnosis"] = {
            "score": 75,
            "grade": "B",
            "summary": "AI 解析完成。",
            "score_breakdown": []
        }

    # 如果只有 product_info 但沒填入基本資訊，從平鋪層抓取
    if "product_info" not in target and "name" in result:
        target["product_info"] = {"name": result.get("name"), "brand": result.get("brand", "")}

    # --- 3. 取得並回填 Cloud 端計算之客觀 Nutri-Score 分數與等級 ---
    objective_score = result.get("health_score") or target["final_health_diagnosis"].get("score", 75)
    objective_grade = result.get("risk_level") or target["final_health_diagnosis"].get("grade") or result.get("grade") or "C"

    result["health_score"] = objective_score
    result["risk_level"] = objective_grade
    target["final_health_diagnosis"]["score"] = objective_score
    target["final_health_diagnosis"]["grade"] = objective_grade

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
