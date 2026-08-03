"""
Module D — LLM 生成自然語言摘要(以知識庫約束)+ 持久化

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
import re
import time


def generate_ai_diagnosis(product, chemical, final_safety_events, user_conditions, nutrition,
                          deterministic_score, deterministic_grade, ai_data, raw_allergens,
                          cursor, db, genai_client) -> dict:
    """
    回傳 dict: ai_data(更新後), raw_allergens(可能已被重用分支重新賦值)
    """
    # 強化後的複合式 Prompt，使用 Google Gemma 同時生成總體、添加物與歷史事件三個 AI 總結
    composite_prompt = f"""
    你是 FoodAware Pro 專家系統。請執行「臨床風險診斷」並針對商品進行「添加物風險總結」、「食安歷史事件總結」與「總體商品健康診斷總結」。

    【產品資訊】
    產品: {product['name']} | 品牌: {product['brand']} | 廠商: {product['manufacturer']}
    營養成分: {json.dumps(nutrition, ensure_ascii=False)}

    【權威計分依據 (Nutri-Score V7 2024 最新版)】
    依據歐盟 2024 演算法算出的確切總分: {deterministic_score}
    確定之健康分級: {deterministic_grade} 級 (A為最優，E為最差)

    【廠商食安歷史 (Module C)】
    {json.dumps(final_safety_events, ensure_ascii=False)}

    【權威資料庫已提供之食品添加物資訊 (Module B)】
    {json.dumps(chemical, ensure_ascii=False)}

    【使用者健康背景】
    {json.dumps(user_conditions, ensure_ascii=False)}

    【任務】
    請進行深度分析，並提供以下三個 AI 總結：
    1. 「總體商品健康診斷總結」(overall_summary)：參考「確切總分」與「健康分級」，產出 50 字內之個人化核心診斷與長期過量攝取的累積慢性健康風險（例如：吃了沒事，但吃久了會有事）。
    2. 「添加物風險總結」(additives_summary)：分析本產品所含的食品添加物、人工化學成分（如防腐劑、防凝劑、甘味劑等）的組合風險，特別是針對該使用者背景（如糖尿病、孕婦、高血壓等）的危害程度，產出 100 字內的分析總結。若無添加物，請說明「本產品無添加化學食品添加物」。
    3. 「食安歷史事件總結」(safety_events_summary)：分析本產品製造商（廠商）以往的食安歷史違規與歷史事件，對消費者信任度與產品安全的影響，產出 100 字內的分析總結。若無歷史食安事件，請說明「該廠商無特定歷史食安違規紀錄」。
    4. 產出具體「個人化警示」(warnings) 清單。

    請以繁體中文回答。回傳格式必須為純 JSON，不可有任何 Markdown 標記，結構如下：
    {{
      "score": {deterministic_score},
      "grade": "{deterministic_grade}",
      "overall_summary": "總體商品健康診斷總結文字",
      "additives_summary": "添加物風險總結文字",
      "safety_events_summary": "食安歷史事件總結文字",
      "warnings": ["警告1", "警告2"]
    }}
    """

    # --- 🚀 檢查資料庫中是否有預存的 AI 總結，若有則直接重用 ---
    ai_data_exists = ai_data is not None
    is_reused_from_db = False
    if not ai_data_exists and product and product.get("overall_summary") and product.get("overall_summary") != "診斷引擎暫時降級運作。":
        print(f"[INFO] [Analyze] Reusing pre-analyzed AI summaries from PostgreSQL for barcode: {product['barcode']}")

        # 防禦性解析過敏原字串
        raw_allergens = product.get("allergens")
        warnings_list = []
        if raw_allergens:
            if isinstance(raw_allergens, list):
                warnings_list = raw_allergens
            elif isinstance(raw_allergens, str):
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
            "overall_summary": product.get("overall_summary"),
            "additives_summary": product.get("additives_summary"),
            "safety_events_summary": product.get("safety_events_summary"),
            "warnings": warnings_list
        }
        ai_data_exists = True
        is_reused_from_db = True

    # --- 🚀 只有在無快取 AI 資料且無資料庫預存時才發送大模型請求 ---
    if not ai_data_exists:
        try:
            try:
                print(f"[DEBUG] [Analyze] Sending prompt to Gemma. Length: {len(composite_prompt)}")
                _t_gemma_start = time.time()
                ai_resp = genai_client.models.generate_content(model="gemini-2.5-flash-lite", contents=composite_prompt)
                ai_data = json.loads(re.search(r'(\{.*\})', ai_resp.text, re.DOTALL).group(1))
                print(f"[PERF] Gemini-2.5-flash-lite Diagnosis: {(time.time() - _t_gemma_start)*1000:.0f}ms")
            except Exception as first_e:
                print(f"[WARN] [Analyze] gemini-2.5-flash-lite failed, falling back to gemini-2.5-flash... Error: {first_e}")
                _t_gemma_start = time.time()
                ai_resp = genai_client.models.generate_content(model="gemini-2.5-flash", contents=composite_prompt)
                ai_data = json.loads(re.search(r'(\{.*\})', ai_resp.text, re.DOTALL).group(1))
                print(f"[PERF] Gemini-2.5-flash Diagnosis (fallback): {(time.time() - _t_gemma_start)*1000:.0f}ms")
        except Exception as ai_e:
            print(f"[WARN] [Analyze] Both primary and fallback models failed: {ai_e}")
            ai_data = {
                "score": deterministic_score,
                "grade": deterministic_grade,
                "overall_summary": "診斷引擎暫時降級運作。",
                "additives_summary": "無法分析添加物風險。",
                "safety_events_summary": "無法分析廠商食安歷史。",
                "warnings": []
            }
    else:
        if not is_reused_from_db:
            print("[INFO] [Query] Reusing pre-cached AI summaries (Skipping LLM Generation for Fog/Cache mode)")
        # 即使是使用快取，健康評分與等級仍然依據本次重算的結果更新，以維持個人化計算的準確性
        ai_data["score"] = deterministic_score
        ai_data["grade"] = deterministic_grade

    # 將新生成的 AI 總結持久化儲存到資料庫中，以實現「一次分析，永久重用」
    if not is_reused_from_db and ai_data and ai_data.get("overall_summary") != "診斷引擎暫時降級運作。" and product and product.get("barcode"):
        try:
            update_sql = """
                UPDATE products
                SET overall_summary = %s, additives_summary = %s, safety_events_summary = %s
                WHERE barcode = %s
            """
            cursor.execute(update_sql, (
                ai_data.get("overall_summary"),
                ai_data.get("additives_summary"),
                ai_data.get("safety_events_summary"),
                product['barcode']
            ))
            db.commit()
            print(f"[SUCCESS] [DB] 持久化預存 AI 總結於資料庫: {product['barcode']}")
        except Exception as update_e:
            print(f"[WARN] [DB] Failed to save AI summaries to DB: {update_e}")
            db.rollback()

    cursor.close()
    db.close()

    return {"ai_data": ai_data, "raw_allergens": raw_allergens}
