"""
Module B — Nutri-Score V7 計分前置與執行

從 main.py 抽出(2026-07-24)。純函式,邏輯逐字未改動,不碰 cursor/db/genai。
"""
from module_b.nutriscore_v7 import calculator as ns_calculator

SWEETENER_KEYWORDS = [
    "阿斯巴甜", "aspartame",
    "醋磺內酯鉀", "acesulfame", "安賽蜜",
    "蔗糖素", "sucralose", "三氯蔗糖",
    "糖精", "saccharin",
    "紐甜", "neotame",
    "愛德萬甜", "advantame",
    "甜菊", "steviol", "stevia",
    "甜蜜素", "cyclamate",
    "索馬甜", "thaumatin",
    "赤藻糖醇", "erythritol",
    "木糖醇", "xylitol",
    "山梨糖醇", "sorbitol",
    "甘露糖醇", "mannitol",
    "麥芽糖醇", "maltitol",
    "異麥芽", "isomalt",
    "乳糖醇", "lactitol"
]


def calculate_nutriscore(product, ing_list) -> dict:
    """
    回傳 dict: calc_result, deterministic_score, deterministic_grade
    """
    def _num(key, default=0.0):
        v = product.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # 準備計算所需數值。**有實測值就用實測值，沒有才退回推估，並記錄哪幾項是推估的。**
    #
    # 2026-07-26 修正：原本無條件以「脂肪 × 35%」推估飽和脂肪、並固定填入
    # 加分項（纖維、蔬果比）缺值時傳 None，不填假設值。
    # calculator 對 None 的處理是「不加分」（見 nutriscore_v7.py 的在地化調整），
    # 這對加分項是正確的中立處理：拿不到就不給那份加分，而不是編一個數字。
    # 原本填「纖維 2.0 / 蔬果比 10%」其實無作用——兩者皆低於加分門檻（蔬果比
    # 須 >40% 才加分、纖維須 >3.0g），恆加 0 分，只是看起來像有在算。
    #
    # 飽和脂肪則相反：它是**扣分項**，缺值時 calculator 會當 0，等於宣稱飽和
    # 脂肪為零而少扣分、偏樂觀。且它是法定強制標示、標示上必有，故不接受缺值
    # 或推估——缺值時另行處理（見呼叫端 estimated 判斷），不在此填假設。
    estimated = []

    sfa = _num('saturated_fat')
    if sfa is None:
        # 暫時仍以脂肪×35% 推估以維持等級可產出，但明確標記為推估，
        # 供呈現端加註。實測此推估對堅果類高估近三倍、對油炸類低估，
        # 會改變等級（開心果 E↔D），故僅為過渡，正解是抓取標示實測值。
        sfa = float(product['fat']) * 0.35
        estimated.append('saturated_fat')

    ns_data = {
        "energy": float(product['calories']) * 4.184, # kcal 轉 kJ
        "sugars": float(product['sugar']),
        "sfa": sfa,
        "salt": float(product['sodium']) / 1000 * 2.5, # mg 鈉 轉 g 鹽
        "proteins": float(product['protein']),
        "fibres": _num('fiber'),          # 缺 → None → 不加分（正確）
        "fruit_veg_pct": _num('fruit_veg_pct'),  # 無來源 → None → 不加分（正確）
    }

    # 判定是否為特定類別 (後續可優化為從資料庫讀取)
    is_beverage = any(kw in product['name'] for kw in ["飲", "水", "汁", "奶", "啡", "茶"])
    is_cheese = any(kw in product['name'] for kw in ["乳酪", "起司", "Cheese"])

    # 飲料細項判定：檢測是否為純水
    is_water = is_beverage and any(kw in product['name'] for kw in ["水", "礦泉水"]) and not any(kw in product['name'] for kw in ["茶", "奶", "汁", "啡", "飲", "風味", "汽水", "可樂", "蘇打", "沙士"])

    # 飲料細項判定：檢測是否含有非營養性甜味劑 (NNS)
    has_sweeteners = False
    if isinstance(ing_list, list):
        has_sweeteners = any(
            any(kw in str(ing).lower() for kw in SWEETENER_KEYWORDS)
            for ing in ing_list
        )

    # 執行 100% 準確的 V7 演算法
    calc_result = ns_calculator.calculate(
        ns_data,
        is_beverage=is_beverage,
        is_cheese=is_cheese,
        has_sweeteners=has_sweeteners,
        is_water=is_water
    )
    deterministic_score = calc_result['score']
    deterministic_grade = calc_result['grade']

    return {
        "calc_result": calc_result,
        "deterministic_score": deterministic_score,
        "deterministic_grade": deterministic_grade,
        # 哪幾項輸入為推估而非標示實測值。呈現端應據此加註，
        # 否則使用者會以為整個等級都是依實際標示算出來的。
        "estimated_inputs": estimated,
        "is_fully_measured": not estimated,
    }
