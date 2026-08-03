"""
Module B — 每日參考值百分比

依據：衛生福利部食品藥物管理署《包裝食品營養標示應遵行事項》**附表一
「每日熱量及各項營養素攝取參考值」**。
法源：食品安全衛生管理法第 22 條第 3 項。

設計原則（2026-07-26 決議，教授指示）：
**只陳述「這項營養素佔一天建議量的百分之幾」，不做任何風險判斷、不給等級、
不下結論。** 分子來自產品營養標示，分母來自上述法規附表，兩者皆為既有數值，
系統本身不產生任何新的判斷。

個人化的作法也隨之改變：不再由系統判定某族群「應避免」什麼，而是**換一組
官方的參考值**重算百分比。附表一本身即依適用對象分為三欄（四歲以上／
一歲至三歲／孕乳婦），故族群化的依據同樣來自法規，非本系統自訂。

刻意不做的事：
- 不處理食品添加物。添加物的每日容許攝取量（ADI）雖有部分資料，但食品標示
  法規不要求標示添加物含量，分子永遠取不到，百分比在制度上就算不出來。
- 未訂定參考值者（如「糖」）不自行代入任何數值。法規第四點明定此種情形
  應標示「＊參考值未訂定」，本模組照辦，回傳 reference=None 供呈現端加註。
"""

# 附表一（逐字轉錄）。None 代表該欄位於附表中為「＊」（參考值未訂定）。
#
# 「糖」在附表一中沒有欄位——法規雖強制標示糖含量，卻未訂每日參考值，
# 故此處刻意不列入，不可自行採用其他來源的建議量充當官方參考值。
_REFERENCE_TABLE = {
    "general": {  # 四歲以上
        "label": "四歲以上",
        "values": {
            "calories": (2000, "大卡"),
            "protein": (60, "公克"),
            "fat": (60, "公克"),
            "saturated_fat": (18, "公克"),
            "carbohydrates": (300, "公克"),
            "sodium": (2000, "毫克"),
            "cholesterol": (300, "毫克"),
            "fiber": (25, "公克"),
        },
    },
    "toddler": {  # 一歲至三歲
        "label": "一歲至三歲",
        "values": {
            "calories": (1200, "大卡"),
            "protein": (20, "公克"),
            "fat": (None, "公克"),
            "saturated_fat": (None, "公克"),
            "carbohydrates": (None, "公克"),
            "sodium": (1200, "毫克"),
            "cholesterol": (None, "毫克"),
            "fiber": (15, "公克"),
        },
    },
    "pregnant": {  # 孕乳婦
        "label": "孕乳婦",
        "values": {
            "calories": (2200, "大卡"),
            "protein": (65, "公克"),
            "fat": (65, "公克"),
            "saturated_fat": (18, "公克"),
            "carbohydrates": (330, "公克"),
            "sodium": (2000, "毫克"),
            "cholesterol": (300, "毫克"),
            "fiber": (30, "公克"),
        },
    },
}

# 產品欄位 → 顯示名稱。順序即為呈現順序，與營養標示的慣用排列一致。
_NUTRIENT_LABELS = [
    ("calories", "熱量"),
    ("protein", "蛋白質"),
    ("fat", "脂肪"),
    ("saturated_fat", "飽和脂肪"),
    ("carbohydrates", "碳水化合物"),
    ("sugar", "糖"),
    ("fiber", "膳食纖維"),
    ("sodium", "鈉"),
]

SOURCE = (
    "衛生福利部食品藥物管理署《包裝食品營養標示應遵行事項》"
    "附表一「每日熱量及各項營養素攝取參考值」"
)

# 慢性病狀況 → 該狀況下需要留意的營養素（2026-07-26 決議）。
#
# **刻意不為這些狀況更換參考值。** 附表一只分四歲以上／一歲至三歲／孕乳婦
# 三欄，沒有三高專屬欄位；若改用臨床指引的數值，會產生三個問題：來源等級不同
# （法規附表 vs 學會建議）、多數以「佔總熱量百分比」表示而需自行假設每日總熱量、
# 不同文件之間數值未必一致。任一個都會讓畫面上的數字失去單一可追溯的出處。
#
# 故此處只做「標示哪幾項與該狀況相關」——分母、分子、百分比全部維持附表一，
# 系統做的是排序與標記，不是判斷，也沒有引入任何自訂數值。
# 相關性本身屬醫學常識層級（高血壓看鈉、高血糖看糖與碳水、高血脂看飽和脂肪
# 與膽固醇），不涉及攝取量的建議。
_CONDITION_RELEVANT_NUTRIENTS = {
    "hypertension": ("高血壓", ["sodium"]),
    "高血壓": ("高血壓", ["sodium"]),
    "diabetes": ("高血糖／糖尿病", ["sugar", "carbohydrates"]),
    "高血糖": ("高血糖／糖尿病", ["sugar", "carbohydrates"]),
    "糖尿病": ("高血糖／糖尿病", ["sugar", "carbohydrates"]),
    "hyperlipidemia": ("高血脂", ["saturated_fat", "cholesterol", "fat"]),
    "高血脂": ("高血脂", ["saturated_fat", "cholesterol", "fat"]),
    "高血脂症": ("高血脂", ["saturated_fat", "cholesterol", "fat"]),
    "kidney_disease": ("腎臟疾病", ["sodium", "protein"]),
    "慢性腎臟病患者": ("腎臟疾病", ["sodium", "protein"]),
}


def resolve_highlights(user_conditions) -> dict:
    """
    由使用者狀況決定要標示哪些營養素為「與你的狀況相關」。

    回傳 {營養素欄位: [狀況名稱, ...]}；多個狀況命中同一項時全部列出
    （例如同時有高血壓與腎臟疾病，鈉會標出兩者）。
    """
    if not user_conditions:
        return {}

    candidates = []
    if isinstance(user_conditions, dict):
        for v in user_conditions.values():
            if isinstance(v, (list, tuple, set)):
                candidates.extend(v)
            elif isinstance(v, str):
                candidates.append(v)
    elif isinstance(user_conditions, (list, tuple, set)):
        candidates.extend(user_conditions)
    else:
        candidates.append(user_conditions)

    highlights: dict = {}
    for c in candidates:
        entry = _CONDITION_RELEVANT_NUTRIENTS.get(str(c or "").strip())
        if not entry:
            continue
        cond_label, keys = entry
        for k in keys:
            highlights.setdefault(k, [])
            if cond_label not in highlights[k]:
                highlights[k].append(cond_label)
    return highlights

# 使用者身體狀況 → 附表一適用對象。只對應附表本身有分欄者；
# 其餘狀況（如腎臟病、糖尿病）附表一未訂專屬參考值，一律回到四歲以上那欄，
# 不自行套用其他來源的數值冒充官方參考值。
_CONDITION_TO_GROUP = {
    "pregnant": "pregnant",
    "孕婦": "pregnant",
    "哺乳": "pregnant",
    "哺乳期婦女": "pregnant",
    "lactating": "pregnant",
    "toddler": "toddler",
    "幼兒": "toddler",
    "嬰幼兒": "toddler",
    "一歲至三歲": "toddler",
}


def resolve_group(user_conditions) -> str:
    """
    由使用者狀況決定適用哪一欄參考值。無法對應者一律用「四歲以上」。

    接受三種輸入形態：主流程傳入的是 {"group": "adult", "allergens": [...]}，
    另也支援單純的字串或字串清單，避免呼叫端形態改變時靜默失效。

    注意「child」不對應到 toddler：附表一的幼兒欄位限「一歲至三歲」，
    而系統的 child 泛指兒童（含四歲以上），套用一至三歲的參考值會低估分母、
    使百分比虛高。無法確定年齡時用四歲以上那欄較保守。
    """
    if not user_conditions:
        return "general"

    candidates = []
    if isinstance(user_conditions, dict):
        candidates.append(user_conditions.get("group"))
        for v in user_conditions.values():
            if isinstance(v, (list, tuple, set)):
                candidates.extend(v)
            elif isinstance(v, str):
                candidates.append(v)
    elif isinstance(user_conditions, (list, tuple, set)):
        candidates.extend(user_conditions)
    else:
        candidates.append(user_conditions)

    for c in candidates:
        if not c:
            continue
        g = _CONDITION_TO_GROUP.get(str(c).strip())
        if g:
            return g
    return "general"


def calculate_daily_reference(product: dict, group: str = "general",
                              highlights: dict | None = None) -> dict:
    """
    計算各營養素佔每日參考值的百分比。

    **基準優先用「每份」，其次才用「每 100 公克」**（2026-07-26 修正）：
    這個功能回答的是「我吃這一份佔一天多少」，使用者實際攝取的是一份／一包，
    不是 100 公克。洋芋片每 100g 熱量 571 大卡、佔一天 28.5%，但一包若 60g，
    實際只佔約 17%——用每 100g 會給出對使用者無意義的量級。且台灣營養標示格式
    本即「每一份量」配「每日參考值百分比」並列，用每份也更貼合標示原貌。

    每份營養值來自視覺辨識的 nutrition_per_serving（存於 other_nutrition），
    直接取自標示、不做換算。缺每份資料時退回每 100 公克，並於 basis 據實標明，
    呈現端必須顯示 basis，否則使用者會誤解量級。

    回傳每項營養素：
      amount / unit  — 產品含量與單位（依 basis 為每份或每 100g）
      reference      — 每日參考值（None 代表法規未訂定）
      percent        — 佔比（%），reference 為 None 或無含量資料時為 None
      note           — 無參考值時的說明文字
      relevant_to    — 此項與使用者哪些狀況相關（無則為空 list）

    highlights 只影響 relevant_to 標記，**不影響任何數值** ——
    分母一律為附表一對應族群欄位，不因慢性病狀況而改變。
    """
    import json as _json
    table = _REFERENCE_TABLE.get(group) or _REFERENCE_TABLE["general"]
    refs = table["values"]
    highlights = highlights or {}

    # 決定分子來源：優先每份，退回每 100 公克。
    # 每份核心值存於 other_nutrition["per_serving"]（見 main.py 組裝），
    # 也相容舊格式（other_nutrition / nutrition_per_serving 直接就是每份 dict）。
    def _as_dict(v):
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except (ValueError, TypeError):
                return None
        return v if isinstance(v, dict) else None

    other_nut = _as_dict(product.get("other_nutrition"))
    per_serving = None
    if isinstance(other_nut, dict) and isinstance(other_nut.get("per_serving"), dict):
        per_serving = other_nut["per_serving"]
    if per_serving is None:
        per_serving = _as_dict(product.get("nutrition_per_serving")) or other_nut

    use_serving = isinstance(per_serving, dict) and any(
        per_serving.get(k) is not None for k, _ in _NUTRIENT_LABELS)
    source = per_serving if use_serving else product

    serving_size = product.get("serving_size")
    if use_serving and serving_size:
        basis = f"每份（{serving_size:g} 公克／毫升）"
    elif use_serving:
        basis = "每份"
    else:
        basis = "每 100 公克（或毫升）"

    items = []
    for key, label in _NUTRIENT_LABELS:
        amount = source.get(key)
        if amount is None:
            continue  # 產品無此項標示資料，不列出，不以 0 代替
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue

        ref_entry = refs.get(key)
        ref_val, unit = (ref_entry if ref_entry else (None, None))
        if unit is None:
            unit = "毫克" if key in ("sodium", "cholesterol") else ("大卡" if key == "calories" else "公克")

        percent = None
        note = None
        if ref_val:
            percent = round(amount / ref_val * 100, 1)
        else:
            note = "參考值未訂定"

        items.append({
            "key": key,
            "label": label,
            "amount": amount,
            "unit": unit,
            "reference": ref_val,
            "percent": percent,
            "note": note,
            "relevant_to": highlights.get(key, []),
        })

    # 依百分比由高到低排序，讓佔比高的項目自然浮上來。
    #
    # 刻意用「排序」而非標示「偏高／偏低」：後者需要一個門檻，而門檻必須有出處，
    # 附表一並未訂定任何判讀標準（法規只要求標示百分比，不對數值高低作評價）。
    # 排序不引入任何新數值、不對數字作評價，仍屬純陳述。
    #
    # 未訂參考值者（percent 為 None，如「糖」）一律排在最後，不視為 0——
    # 「沒有參考值」與「佔比為零」是完全不同的兩件事，不可混為一談。
    items.sort(key=lambda i: (i["percent"] is None, -(i["percent"] or 0)))

    return {
        "group": group,
        "group_label": table["label"],
        "basis": basis,
        "sorted_by": "每日參考值百分比（高至低）；未訂參考值者列於最後",
        "source": SOURCE,
        "items": items,
        # 標示過的項目一覽，供呈現端優先排序或置頂使用
        "highlighted": sorted({k for k in highlights if any(i["key"] == k for i in items)}),
    }


def get_daily_reference_payload(product: dict, user_conditions=None) -> dict:
    """
    供主流程直接嵌入回應的完整區塊。

    個人化共兩層，且兩層都不含系統自訂的數值：
    1. 依族群換用附表一對應欄位的參考值（四歲以上／一歲至三歲／孕乳婦）
    2. 依慢性病狀況標記哪幾項與使用者相關（只標記，不改數字）
    """
    group = resolve_group(user_conditions)
    highlights = resolve_highlights(user_conditions)
    return calculate_daily_reference(product, group, highlights)
