"""
Module D — 整合輸出:組裝回傳給 Fog 的最終 JSON

從 main.py 抽出(2026-07-24)。邏輯逐字未改動,純函式——此段在原本的
analyze() 中位於 cursor.close()/db.close() 之後,本來就不碰資料庫,
純粹是把前面各模組(A 的成分比對、B 的 Nutri-Score、C 的食安事件、
LLM 診斷摘要)算出的結果組裝成回傳格式,故可直接抽出、不需額外做
locals() 轉參數的處理。
"""
import json

from .summary import build_additives_summary, build_overall_summary
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

    # ── 逐項得分 ───────────────────────────────────────────────────────────
    # 2026-09-13 改成**列出全部七項，含 0 分的**。原本 `if v > 0` 會把沒扣到分
    # 的項目整個省略，使用者看到的是一份殘缺的算式——而「這一項 0 分」本身就是
    # 資訊（它告訴你那一項沒有扣你分）。教授要的「點開看計算方式與占比」需要
    # 完整的分母。
    #
    # 每一項都帶 maxPoints，占比才算得出來；沒有它只知道「糖扣 7 分」，
    # 不知道 7 分是滿分還是一半。maxima 由產生分數的同一份門檻表算出
    # （見 nutriscore_v7.py），不另寫一組常數。
    maxima = calc_result['details'].get('maxima', {})

    # 每一項的實測值與單位，讓畫面能寫「含 9.0 公克糖 → 扣 7 分（滿分 10）」。
    # 值取自 product（標示實測），不是計分器內部換算後的值——使用者對得起來的
    # 是標示上的數字，不是 kJ 或鹽當量。
    value_of = {
        "energy": (product.get("calories"), "kcal"),
        "sugars": (product.get("sugar"), "g"),
        "sfa": (product.get("saturated_fat"), "g"),
        "salt": (product.get("sodium"), "mg"),
        "protein": (product.get("protein"), "g"),
        "fibre": (product.get("fiber"), "g"),
        "fruit_veg": (product.get("fruit_veg_pct"), "%"),
    }

    for k in ("energy", "sugars", "sfa", "salt", "protein", "fibre", "fruit_veg"):
        if k not in breakdown_raw:
            continue
        v = int(breakdown_raw[k])
        is_penalty = k in penalty_mapping
        name, desc_tpl = (penalty_mapping if is_penalty else bonus_mapping)[k]
        val, unit = value_of.get(k, (None, ""))

        if is_penalty:
            desc = desc_tpl.format(v)
            if k == "sugars" and product.get("sugar") is not None:
                desc = f"含有 {product['sugar']} 公克添加糖，糖分得分為 {v} 分，高糖攝取易引發慢性病與肥胖風險。"
            elif k == "energy" and product.get("calories") is not None:
                desc = f"含有 {product['calories']} kcal 熱量，熱量成分得分為 {v} 分，高熱量會增加身體代謝負荷。"
            elif k == "salt" and product.get("sodium") is not None:
                desc = f"含有 {product['sodium']} 毫克鈉，鈉分得分為 {v} 分，高鈉攝取增加高血壓與腎臟負擔。"
        else:
            desc = desc_tpl

        formatted_score_breakdown.append({
            "key": k,
            "reason": name,
            "description": desc,
            # 扣分為負、加分為正，與原本一致。
            "points": -v if is_penalty else v,
            "maxPoints": maxima.get(k),
            "kind": "penalty" if is_penalty else "bonus",
            # ⚠ None 代表**標示沒有這一項**，不是 0。畫面要寫「未標示」
            #   而不是「0 公克」——那兩件事在營養標示上意義完全不同。
            "value": val,
            "unit": unit,
        })

    # 甜味劑罰分不是查表來的（飲料固定 +4），但它會影響總分，
    # 省略它會讓明細加不回總分。
    _sw = calc_result['details'].get('sweetener_penalty') or 0
    if _sw:
        formatted_score_breakdown.append({
            "key": "sweeteners",
            "reason": "非營養性甜味劑 (Sweeteners)",
            "description": f"含非營養性甜味劑，Nutri-Score V7 對飲料固定加計 {int(_sw)} 分罰分。",
            "points": -int(_sw),
            "maxPoints": 4,
            "kind": "penalty",
            "value": None,
            "unit": "",
        })

    # 食安事件已在上方預先去重並限定近十年且最多 5 個，此處直接使用 final_safety_events

    # 建立產品基礎資訊供根目錄顯示
    product_info_root = {
        "name": product['name'] or "AI 解析產品",
        "brand": product['brand'] or "AI 解析品牌",
        "manufacturer": product['manufacturer'] or "未知製造商",
        "barcode": product['barcode']
    }

    # 取得 AI 總結欄位值。
    #
    # ~~食安歷史事件總結~~ **2026-09-13 移除**。原本的規則是「沒有事件時不得輸出
    # 食安摘要」（2026-07-26 訂），理由是 ai_data 可能來自資料庫快取、內容是先前
    # 有事件時產生的，事件清單已空卻還說「該廠商曾多次發生…」等於無佐證的負面陳述。
    # 現在整條食安管線停用中（SAFETY_EVENTS_ENABLED = False），模型拿不到任何事件
    # 資料，產出的只會是「該廠商無特定違規紀錄」這種**沒查證過的安心話**——把
    # 「未查詢」講成「沒問題」，對食安 App 是反向的風險。功能恢復時再加回來。
    # ⚠ 2026-09-14：兩段總結改為**規則產生**，不再呼叫語言模型。
    # 理由與實測見 module_d/summary.py 的檔頭——簡言之，加了約束不編造之後，
    # 模型的輸出就退化成把畫面上已有的數字念一遍；不加約束則會寫出
    # 「高膽固醇」「長期適量攝取對健康無害」這種沒有依據的句子。
    # 規則版每一句都指得回某個欄位，且回答的是「為什麼是這個等級」。
    overall_summary = build_overall_summary(
        deterministic_score, calc_result.get("grade"), formatted_score_breakdown)
    additives_summary = build_additives_summary(chemical, basic_detail)
    from .grounded_summary import summarize
    grounded = summarize(overall_summary, additives_summary, chemical)
    overall_summary, additives_summary = grounded['overall'], grounded['additives']

    # 為維持與行動 App 分割邏輯的相容性，將總結結合並加入「[AI 深度分析]：」分割符。
    # 原本是三段，現在是兩段。
    combined_summary = (f"{overall_summary}\n\n[AI 深度分析]："
                        f"\n【添加物風險總結】\n{additives_summary}")


    # 建立滿足 React Native App 與 API_SPEC_APP.md 串接要求的診斷物件
    final_health_diagnosis_obj = {
        "summary_generation": {"mode":grounded['mode'], "fallback_reason":grounded['reason'],
                               "citations":grounded['citations']},
        "score": ai_data.get("score", deterministic_score),
        "summary": combined_summary,
        "overall_summary": overall_summary,
        "additives_summary": additives_summary,
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
        # Nutri-Score 等級（A–E）。**2026-09-13 新增，而且是必要的。**
        #
        # `health_score` 是 Nutri-Score 的原始分數：**越低越好**，範圍約 −15~40。
        # App 的 Gauge 卻把它當 0–100 越高越好（A 要 ≥80），於是每一筆都判錯——
        # raw −5（最健康）顯示成 E，raw 35（最糟）反而顯示成 D。
        #
        # 等級不可由呈現端自己從分數推：飲料與純水另有一套帶（飲料 ≤2 才 B、
        # 純水才可能 A），只有 `get_grade()` 知道。所以由這裡送出去。
        "nutri_grade": (calc_result or {}).get("grade"),
        # 分數的方向。呈現端寫「越低越好」不能靠猜，也不該寫死在四個地方。
        "score_scale": "nutriscore_points_lower_is_better",
        "risk_level": risk_level,
        "product_info": product_info_root,
        "overall_summary": overall_summary,
        "additives_summary": additives_summary,
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
