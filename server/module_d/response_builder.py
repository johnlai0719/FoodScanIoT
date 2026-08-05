"""
Module D — 整合輸出:組裝回傳給 Fog 的最終 JSON

從 main.py 抽出(2026-07-24)。邏輯逐字未改動,純函式——此段在原本的
analyze() 中位於 cursor.close()/db.close() 之後,本來就不碰資料庫,
純粹是把前面各模組(A 的成分比對、B 的 Nutri-Score、C 的食安事件、
LLM 診斷摘要)算出的結果組裝成回傳格式,故可直接抽出、不需額外做
locals() 轉參數的處理。
"""
import json
from datetime import datetime


def build_response(product, ai_data, calc_result, deterministic_score,
                   chemical, basic, calculated_ingredient_types,
                   final_safety_events, raw_allergens, nutrition,
                   daily_reference=None, basic_detail=None,
                   ns_estimated=None) -> dict:
    # 獲取標章資訊 (從資料庫)
    raw_certs = product.get('certifications', '[]')
    if not raw_certs: raw_certs = '[]'
    if isinstance(raw_certs, str): cert_marks = json.loads(raw_certs)
    else: cert_marks = raw_certs

    # --- 6. 建構回傳格式 ---
    # 本函式即為回應形狀的權威來源，由 tests/contract/test_cloud_response_contract.py
    # 凍結欄位集合。原註解指向的 shared/types.ts 已於 2026-08-05 移除。
    # 映射等級到風險程度
    grade_to_risk = {"A": "low", "B": "low", "C": "medium", "D": "high", "E": "high"}
    risk_level = grade_to_risk.get(ai_data.get("grade", "C"), "medium")

    # 整理過敏原，兼容 JSON 字串與普通逗號分隔字串
    final_allergens = []
    if raw_allergens:
        raw_allergens_str = str(raw_allergens).strip()
        if raw_allergens_str.startswith('[') or raw_allergens_str.startswith('"'):
            try:
                parsed = json.loads(raw_allergens_str)
                if isinstance(parsed, list):
                    final_allergens = [str(x) for x in parsed if x]
                elif isinstance(parsed, str):
                    if parsed and parsed != '[]':
                        final_allergens = [parsed]
            except Exception:
                final_allergens = [a.strip() for a in raw_allergens_str.split(",") if a.strip()]
        else:
            if raw_allergens_str and raw_allergens_str != '[]':
                final_allergens = [a.strip() for a in raw_allergens_str.split(",") if a.strip()]

    # 整合添加物（chemical）與天然成分（basic），建構完整的 ingredients_detail
    full_ingredients_detail = []
    for chem_item in chemical:
        full_ingredients_detail.append(chem_item)
    # 一般成分的說明改由 Module A 依實際比對依據產生（basic_detail）。
    # 原本此處對每一項一律寫「天然成分，提供基礎營養」，那是對所有未命中項目
    # 的無依據宣稱——系統並不知道它是不是天然的，也不知道它提供什麼營養。
    if basic_detail:
        full_ingredients_detail.extend(basic_detail)
    else:
        for basic_item in basic:
            full_ingredients_detail.append({
                "name": basic_item,
                "isAdditive": False,
                "description": "此項未收錄於本系統資料庫，無法提供說明。"
            })

    # 建立符合 App 格式之個人化分數細項（score_breakdown）
    formatted_score_breakdown = []
    breakdown_raw = calc_result['details']['breakdown']

    # 扣分指標映射（penalties，值轉為負數）
    penalty_mapping = {
        "energy": ("熱量 (Energy)", "熱量成分得分為 {} 分，含有較高熱量會增加身體代謝負荷。"),
        "sugars": ("糖分 (Sugars)", "糖分得分為 {} 分，高糖攝取易引發慢性病與肥胖風險。"),
        "sfa": ("飽和脂肪 (Saturated Fatty Acids)", "飽和脂肪得分為 {} 分，過量可能影響心血管健康。"),
        "salt": ("鈉/鹽分 (Sodium/Salt)", "鈉分得分為 {} 分，高鈉攝取增加高血壓與腎臟負擔。")
    }
    # 加分指標映射（bonuses，值維持正數）
    bonus_mapping = {
        "protein": ("蛋白質 (Protein)", "含有豐富蛋白質，有助於身體組織與肌肉修復。"),
        "fibre": ("膳食纖維 (Fibre)", "含有膳食纖維，有益於腸胃蠕動與消化健康。"),
        "fruit_veg": ("天然蔬果比例 (Fruit & Vegetables)", "富含天然蔬果成分，提供多種維生素與抗氧化物。")
    }

    for k, v in breakdown_raw.items():
        if v > 0:
            if k in penalty_mapping:
                name, desc_tpl = penalty_mapping[k]
                # 嘗試使用資料庫中的具體數據補充描述
                actual_val_desc = desc_tpl.format(int(v))
                if k == "sugars" and product.get("sugar") is not None:
                    actual_val_desc = f"含有 {product['sugar']} 公克添加糖，糖分得分為 {int(v)} 分，高糖攝取易引發慢性病與肥胖風險。"
                elif k == "energy" and product.get("calories") is not None:
                    actual_val_desc = f"含有 {product['calories']} kcal 熱量，熱量成分得分為 {int(v)} 分，高熱量會增加身體代謝負荷。"
                elif k == "salt" and product.get("sodium") is not None:
                    actual_val_desc = f"含有 {product['sodium']} 毫克鈉，鈉分得分為 {int(v)} 分，高鈉攝取增加高血壓與腎臟負擔。"

                formatted_score_breakdown.append({
                    "reason": name,
                    "description": actual_val_desc,
                    "points": -int(v)
                })
            elif k in bonus_mapping:
                  name, desc = bonus_mapping[k]
                  formatted_score_breakdown.append({
                      "reason": name,
                      "description": desc,
                      "points": int(v)
                  })

    # 食安事件已在上方預先去重並限定近十年且最多 5 個，此處直接使用 final_safety_events

    # 建立產品基礎資訊供根目錄顯示
    product_info_root = {
        "name": product['name'] or "AI 解析產品",
        "brand": product['brand'] or "AI 解析品牌",
        "manufacturer": product['manufacturer'] or "未知製造商",
        "barcode": product['barcode']
    }

    # 取得三個 AI 總結欄位值
    overall_summary = ai_data.get("overall_summary") or ai_data.get("summary") or "診斷完成。"
    additives_summary = ai_data.get("additives_summary") or "無法獲取添加物風險總結。"
    # 沒有食安事件時不得輸出食安摘要（2026-07-26）。
    # ai_data 可能來自資料庫的快取欄位，內容是先前有事件時產生的；若事件清單已空
    # 卻仍輸出「該廠商曾多次發生…」這類敘述，等於在沒有任何佐證的情況下對廠商
    # 做出負面陳述——比顯示不精確的事件更糟，因為連來源連結都沒有。
    if final_safety_events:
        safety_events_summary = ai_data.get("safety_events_summary") or "無法獲取廠商食安歷史總結。"
    else:
        safety_events_summary = "本系統目前未提供廠商食安事件資訊。"

    # 為維持與行動 App 分割邏輯的相容性，將三個總結結合並加入「[AI 深度分析]：」分割符
    combined_summary = f"{overall_summary}\n\n[AI 深度分析]：\n【添加物風險總結】\n{additives_summary}"
    if final_safety_events:
        combined_summary += f"\n\n【食安歷史事件總結】\n{safety_events_summary}"

    # 建立滿足 React Native App 與 API_SPEC_APP.md 串接要求的診斷物件
    final_health_diagnosis_obj = {
        "score": ai_data.get("score", deterministic_score),
        "summary": combined_summary,
        "overall_summary": overall_summary,
        "additives_summary": additives_summary,
        "safety_events_summary": safety_events_summary,
        "warnings": ai_data.get("warnings", []),
        "score_breakdown": formatted_score_breakdown,
        "groupRiskSummary": [],
        "evidence_chain": []
    }

    # 建構 FogQueryResult 與 API_SPEC_APP 回應格式
    return {
        "status": "success",
        "barcode": product['barcode'],
        "health_score": ai_data.get("score", deterministic_score),
        "risk_level": risk_level,
        "product_info": product_info_root,
        "overall_summary": overall_summary,
        "additives_summary": additives_summary,
        "safety_events_summary": safety_events_summary,
        "score_breakdown": formatted_score_breakdown,
        "risk_tags": ai_data.get("warnings", []),
        "allergen_warnings": final_allergens,
        "ingredients_detail": full_ingredients_detail,
        "ingredient_types": calculated_ingredient_types,
        # 每日參考值百分比(2026-07-26)。純數值陳述,不含任何風險判斷;
        # 呈現端務必一併顯示 basis(計算基準為每 100 公克)與 source。
        "daily_reference": daily_reference,
        # Nutri-Score 計分中屬推估（非標示實測）的輸入項目。
        # 空 list 代表全部輸入皆來自標示；非空時呈現端應於等級旁加註，
        # 例如「部分營養素標示未提供，等級含推估成分」。
        "score_estimated_inputs": ns_estimated or [],
        "score_fully_measured": not (ns_estimated or []),
        "food_safety_events": final_safety_events,
        "final_health_diagnosis": final_health_diagnosis_obj,
        "explanation": {
            "triggers": [ch['name'] for ch in chemical if ch.get('risk_level') == 'high'],
            "sources": ["歐盟 Nutri-Score V7 (2024)", "FoodScanIoT 食品添加物知識庫"]
        },
        "personalized_notes": [combined_summary],
        "processed_at": datetime.now().isoformat(),
        # 原始詳細資料保留於 data 欄位供前端舊版適配與擴充顯示
        "data": {
            "product_info": product_info_root,
            "ingredients_detail": chemical,
            "nutrition_facts": nutrition,
            "certification_marks": cert_marks,
            "ingredient_types": calculated_ingredient_types
        }
    }
