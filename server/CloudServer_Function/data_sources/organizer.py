"""
data_sources/organizer.py
=========================
食品掃描建議系統 — 資料整理與規則引擎

職責：
  1. 接收 FoodProduct（來自 fetcher）
  2. 依族群套用 registry 中的 GroupRule
  3. 比對成分清單、過敏原、營養素數值
  4. 組裝 ScanResult（建議 + 文獻引用 + 免責聲明）
  5. 整合 NutriScore2023（若已計算）

設計原則（對應文件 §3.2）：
  ✅ 標示成分「存在」
  ✅ 引用官方文件說明成分用途
  ✅ 針對特殊族群給予官方依據的留意提示
  ❌ 不宣稱安全或不安全
  ❌ 不用 ADI 值計算個人攝取量
  ❌ 不超出官方聲明的風險評級
"""

from __future__ import annotations

import logging
import sys
import os
from datetime import date
from typing import Dict, List, Optional, Tuple

from .models import (
    FoodProduct,
    GroupRule,
    PopulationGroup,
    ScanResult,
)
from .registry import (
    RULES,
    ALL_SOURCES,
    get_rules_by_group,
    get_overdue_literature,
)

logger = logging.getLogger(__name__)

# 嘗試匯入同專案的 NutriScore 計算器
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from Nutriscore.NutriScore import NutriScore2023
    NUTRISCORE_AVAILABLE = True
except ImportError:
    NUTRISCORE_AVAILABLE = False
    logger.warning("NutriScore2023 模組未找到，Nutri-Score 計算功能將略過。")


# ────────────────────────────────────────────────────────────
# 成分標準化工具
# ────────────────────────────────────────────────────────────

def normalize_ingredient(raw: str) -> str:
    """
    標準化單一成分字串，去除括號、修飾詞，統一小寫。
    例：「食用色素（日落黃）」→「食用色素 日落黃」
    """
    import re
    text = raw.strip()
    # 展開括號內容（保留）
    text = re.sub(r"[（(]", " ", text)
    text = re.sub(r"[）)]", " ", text)
    # 移除星號、底線、特殊符號
    text = re.sub(r"[*_†‡]", "", text)
    # 壓縮空白
    text = " ".join(text.split())
    return text.lower()


def normalize_ingredients(ingredients: List[str]) -> List[str]:
    """對成分清單批次標準化"""
    return [normalize_ingredient(i) for i in ingredients if i.strip()]


# ────────────────────────────────────────────────────────────
# 過敏原比對器
# ────────────────────────────────────────────────────────────

# OFF API 回傳的過敏原 tag → 中文顯示名稱
ALLERGEN_TAG_MAP: Dict[str, str] = {
    "gluten":       "麩質（含小麥/大麥/裸麥/燕麥）",
    "crustaceans":  "甲殼類（蝦/蟹/龍蝦）",
    "eggs":         "蛋",
    "fish":         "魚",
    "peanuts":      "花生",
    "soybeans":     "大豆",
    "milk":         "牛奶",
    "nuts":         "堅果",
    "celery":       "芹菜",
    "mustard":      "芥末",
    "sesame":       "芝麻",
    "sulphites":    "亞硫酸鹽",
    "lupin":        "羽扇豆",
    "molluscs":     "軟體動物（牡蠣/貝類/魷魚）",
}


def match_allergens(product: FoodProduct,
                    user_allergens: Optional[List[str]] = None) -> List[str]:
    """
    從商品過敏原標籤中，找出使用者關心的過敏原。

    Args:
        product        : FoodProduct 物件
        user_allergens : 使用者設定的過敏原清單（如 ["gluten", "peanuts"]）
                         若為 None 則回傳商品全部過敏原。
    Returns:
        中文過敏原名稱清單
    """
    product_allergens = set(
        tag.lower().replace("en:", "").replace("zh:", "")
        for tag in product.allergens
    )

    if user_allergens is None:
        # 回傳商品所有已知過敏原
        return [
            ALLERGEN_TAG_MAP.get(a, a)
            for a in product_allergens
            if a in ALLERGEN_TAG_MAP
        ]

    # 取交集：使用者關心 ∩ 商品實際含有
    matched = product_allergens & {a.lower() for a in user_allergens}
    return [ALLERGEN_TAG_MAP.get(a, a) for a in matched]


# ────────────────────────────────────────────────────────────
# 規則引擎
# ────────────────────────────────────────────────────────────

class RuleEngine:
    """
    族群規則引擎（文件 §3.1 Top-down 架構）。
    輸入：商品成分 + 族群
    輸出：觸發的規則建議清單
    """

    def __init__(self, rules: List[GroupRule] = None):
        self.rules = rules or RULES

    def evaluate(
        self,
        group:              PopulationGroup,
        ingredients:        List[str],
        nutrition:          Dict[str, float],
        allergen_tags:      List[str] = None,
    ) -> List[Dict]:
        """
        對指定族群評估所有規則。

        Args:
            group           : 目標族群
            ingredients     : 標準化後的成分清單
            nutrition       : 每 100g 營養素 dict
            allergen_tags   : OFF 過敏原標籤列表
        Returns:
            觸發建議清單（每項為 GroupRule.build_advice() 回傳的 dict）
        """
        group_rules = [r for r in self.rules if r.group == group]
        triggered   = []

        for rule in group_rules:
            if self._check_rule(rule, ingredients, nutrition, allergen_tags or []):
                advice = rule.build_advice()
                triggered.append(advice)
                logger.debug("規則觸發：%s (%s)", rule.rule_id, rule.description)

        # 依嚴重度排序：alert > warning > info
        severity_order = {"alert": 0, "warning": 1, "info": 2}
        triggered.sort(key=lambda x: severity_order.get(x.get("severity", "info"), 3))

        return triggered

    def _check_rule(
        self,
        rule:          GroupRule,
        ingredients:   List[str],
        nutrition:     Dict[str, float],
        allergen_tags: List[str],
    ) -> bool:
        """
        判斷單一規則是否應觸發。
        使用雙層比對：關鍵字比對 + 營養素閾值比對。
        """
        # 1. 關鍵字比對（成分清單 + 過敏原標籤）
        if rule.trigger_keywords:
            all_text = " ".join(ingredients + allergen_tags).lower()
            if any(kw.lower() in all_text for kw in rule.trigger_keywords):
                return True

        # 2. 基於營養素的規則（關鍵字清單為空，依數值觸發）
        if not rule.trigger_keywords:
            return self._check_nutrition_rule(rule.rule_id, nutrition)

        return False

    @staticmethod
    def _check_nutrition_rule(rule_id: str, nutrition: Dict[str, float]) -> bool:
        """
        數值型規則判斷（供無關鍵字的規則使用）。
        規則定義直接寫在此，未來可抽成設定檔。
        """
        thresholds = {
            # rule_id:  (欄位名, 比較方式, 閾值)
            "EL-001": ("fiber",  "<",  3.0),    # 老人：低纖維
            "EL-002": ("sodium", ">",  400.0),   # 老人：高鈉（mg）
            "GA-001": ("sodium", ">",  600.0),   # 壯年：高鈉（mg）
        }
        if rule_id not in thresholds:
            return False

        field, op, threshold = thresholds[rule_id]
        value = nutrition.get(field)
        if value is None:
            return False   # 無數據，不觸發（避免誤判）

        # 鈉單位換算：OFF 回傳的是 g/100g，需換算為 mg/100g
        if field == "sodium":
            value = value * 1000  # g → mg

        if op == "<":
            return value < threshold
        if op == ">":
            return value > threshold
        return False


# ────────────────────────────────────────────────────────────
# 主整合器（DataOrganizer）
# ────────────────────────────────────────────────────────────

class DataOrganizer:
    """
    資料整理主類別。
    整合 RuleEngine + NutriScore + 過敏原比對，組裝完整的 ScanResult。

    使用範例：
        organizer = DataOrganizer()
        product   = fetcher.fetch_product("4901085612422").data
        result    = organizer.organize(product, PopulationGroup.ELDERLY)
        print(result)
    """

    DISCLAIMER = (
        "本建議依據政府公開衛生指引，透過 AI 整合呈現，"
        "僅供參考，不構成醫療行為或診斷。"
        "個人健康狀況請諮詢醫師或營養師。"
    )

    def __init__(self, rules: List[GroupRule] = None):
        self.engine = RuleEngine(rules)

    def organize(
        self,
        product:        FoodProduct,
        group:          PopulationGroup,
        user_allergens: Optional[List[str]] = None,
        product_category: str = "food",
    ) -> ScanResult:
        """
        主要整合方法：輸入商品+族群，輸出完整掃描結果。

        Args:
            product          : 由 fetcher 取得的 FoodProduct
            group            : 使用者所屬族群
            user_allergens   : 使用者設定的過敏原偏好（None 表示顯示全部）
            product_category : Nutri-Score 商品分類（food/cheese/beverage 等）
        """
        # Step 1: 標準化成分清單
        normalized = normalize_ingredients(product.ingredients)

        # Step 2: 計算或補充 Nutri-Score
        product = self._ensure_nutriscore(product, product_category)

        # Step 3: 過敏原比對
        allergen_alerts = match_allergens(product, user_allergens)

        # Step 4: 族群規則評估
        advices = self.engine.evaluate(
            group         = group,
            ingredients   = normalized,
            nutrition     = product.nutrition_per100g,
            allergen_tags = product.allergens,
        )

        # Step 5: 若有過敏原命中，插入最高優先提示
        if allergen_alerts:
            allergen_advice = self._build_allergen_advice(allergen_alerts)
            advices.insert(0, allergen_advice)

        return ScanResult(
            product        = product,
            group          = group,
            advices        = advices,
            allergen_alerts= allergen_alerts,
            disclaimer     = self.DISCLAIMER,
            scan_timestamp = date.today(),
        )

    def organize_batch(
        self,
        products:         List[FoodProduct],
        group:            PopulationGroup,
        user_allergens:   Optional[List[str]] = None,
        product_category: str = "food",
    ) -> List[ScanResult]:
        """批次整理多個商品"""
        return [
            self.organize(p, group, user_allergens, product_category)
            for p in products
        ]

    def _ensure_nutriscore(self, product: FoodProduct,
                            category: str) -> FoodProduct:
        """
        若 OFF API 未回傳 Nutri-Score，使用本地 NutriScore2023 計算。
        """
        if product.nutri_score or not NUTRISCORE_AVAILABLE:
            return product

        n = product.nutrition_per100g
        required = ["energy_kj", "sugars", "sat_fat", "salt", "protein", "fiber"]
        if not all(n.get(k) is not None for k in required):
            logger.debug("商品 %s 營養素不完整，略過 Nutri-Score 計算", product.barcode)
            return product

        try:
            calc  = NutriScore2023(category=category)
            score, grade = calc.calculate({
                "energy_kj": n["energy_kj"],
                "sugars":    n["sugars"],
                "sat_fat":   n["sat_fat"],
                "salt":      n["salt"],
                "protein":   n["protein"],
                "fiber":     n["fiber"],
                "fvl_pct":   n.get("fvl_pct", 0),
            })
            product.nutri_score     = grade
            product.nutri_score_val = score
            logger.info("本地計算 Nutri-Score：%s (%d)", grade, score)
        except Exception as exc:
            logger.warning("Nutri-Score 計算失敗：%s", exc)

        return product

    @staticmethod
    def _build_allergen_advice(allergens: List[str]) -> Dict:
        """組裝過敏原警示建議物件"""
        allergen_list = "、".join(allergens)
        return {
            "rule_id":  "ALLERGEN-MATCH",
            "group":    "過敏族群",
            "severity": "alert",
            "advice":   (
                f"【⚠️ 過敏原警示】此商品含有以下過敏原：{allergen_list}。"
                "如您對上述成分過敏，請勿食用。"
            ),
            "citation": (
                "依據衛生福利部食品藥物管理署《食品過敏原標示規定》(2019)"
                "第 4 條：強制標示 11 大過敏原項目。"
            ),
            "disclaimer": (
                "本建議依據政府公開衛生指引，透過 AI 整合呈現，"
                "僅供參考，不構成醫療行為或診斷。"
            ),
        }


# ────────────────────────────────────────────────────────────
# 報表輸出工具
# ────────────────────────────────────────────────────────────

def format_scan_result(result: ScanResult) -> str:
    """
    將 ScanResult 格式化為易讀的文字報告（適合 CLI 輸出或 log）。
    """
    p = result.product
    lines = [
        "=" * 60,
        f"🔍 食品掃描報告",
        f"商品：{p.product_name or '(未知商品)'} | 品牌：{p.brand}",
        f"條碼：{p.barcode}",
        f"族群：{result.group.label_zh}",
        f"掃描日期：{result.scan_timestamp}",
        "-" * 60,
    ]

    if p.nutri_score:
        lines.append(f"Nutri-Score：{p.nutri_score}  (積分 {p.nutri_score_val})")

    if result.allergen_alerts:
        lines.append(f"⚠️  過敏原：{', '.join(result.allergen_alerts)}")

    lines.append("")

    if result.advices:
        lines.append("📋 個人化建議：")
        for i, adv in enumerate(result.advices, 1):
            severity_icon = {"alert": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(
                adv.get("severity", "info"), "ℹ️"
            )
            lines.append(f"  {i}. {severity_icon} {adv['advice']}")
            lines.append(f"     📚 {adv['citation']}")
            lines.append("")
    else:
        lines.append("✅ 此商品對您的族群無特別注意事項。")

    lines += [
        "-" * 60,
        f"⚖️  免責聲明：{result.disclaimer}",
        "=" * 60,
    ]

    return "\n".join(lines)


def export_scan_result_json(result: ScanResult) -> Dict:
    """
    將 ScanResult 轉成 JSON 可序列化的 dict（供 API 回傳使用）。
    """
    p = result.product
    return {
        "product": {
            "barcode":           p.barcode,
            "name":              p.product_name,
            "brand":             p.brand,
            "nutri_score":       p.nutri_score,
            "nutri_score_value": p.nutri_score_val,
            "ingredients":       p.ingredients[:20],   # 限制長度
            "nutrition_per100g": p.nutrition_per100g,
            "source_api":        p.source_api,
        },
        "scan": {
            "group":          result.group.value,
            "group_label":    result.group.label_zh,
            "scan_date":      result.scan_timestamp.isoformat() if result.scan_timestamp else None,
            "allergen_alerts": result.allergen_alerts,
            "advices":        result.advices,
            "advice_count":   len(result.advices),
            "disclaimer":     result.disclaimer,
        },
    }


# ────────────────────────────────────────────────────────────
# 系統狀態檢查
# ────────────────────────────────────────────────────────────

def check_literature_overdue() -> List[str]:
    """
    檢查是否有文獻已超過複查期限（文件 §5.3）。
    回傳警告訊息清單（空清單表示全部正常）。
    """
    overdue = get_overdue_literature()
    if not overdue:
        return []
    warnings = []
    for lit in overdue:
        warnings.append(
            f"⚠️  文獻 {lit.record_id}《{lit.source.document_name}》"
            f"已超過複查日期（{lit.next_review_date}），請更新。"
        )
    return warnings


# ────────────────────────────────────────────────────────────
# 快速測試
# ────────────────────────────────────────────────────────────

def _demo():
    """
    端對端模擬：直接建立一個假商品，跑過完整 organize 流程。
    （不需要網路連線）
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 模擬一個超商微波炒飯的商品資料
    mock_product = FoodProduct(
        barcode      = "4710088116389",
        product_name = "XX牌黃金蛋炒飯（微波）",
        brand        = "XX食品",
        ingredients  = [
            "白米飯", "雞蛋", "玉米", "青豆", "棕櫚油",
            "食鹽", "醬油", "砂糖", "味精（麩胺酸鈉）",
            "磷酸鹽", "焦磷酸鈉", "色素（日落黃）", "花生油",
        ],
        allergens    = ["eggs", "soybeans", "gluten", "peanuts"],
        nutrition_per100g = {
            "energy_kj":  820,
            "energy_kcal": 196,
            "sugars":     2.5,
            "fat":        5.2,
            "sat_fat":    2.1,
            "salt":       0.9,
            "sodium":     0.36,   # g/100g
            "protein":    5.8,
            "fiber":      1.2,    # 低纖維，應觸發 EL-001
        },
        source_api   = "mock",
    )

    organizer = DataOrganizer()

    for group in [PopulationGroup.ELDERLY, PopulationGroup.ALLERGY,
                  PopulationGroup.CHRONIC_DISEASE, PopulationGroup.CHILDREN]:
        result = organizer.organize(
            product          = mock_product,
            group            = group,
            user_allergens   = None,
            product_category = "food",
        )
        print(format_scan_result(result))
        print()

    # 文獻複查狀態
    overdue_warnings = check_literature_overdue()
    if overdue_warnings:
        print("\n" + "\n".join(overdue_warnings))
    else:
        print("\n✅ 所有文獻均在有效複查期內。")


if __name__ == "__main__":
    _demo()
