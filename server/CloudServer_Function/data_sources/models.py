"""
data_sources/models.py
======================
食品掃描建議系統 — 核心資料結構定義

依照《食品掃描系統_資料來源策略文件》v1.0 (2026-04-29) 設計。
所有欄位說明均對應文件對應章節。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional


# ────────────────────────────────────────────────────────────
# 列舉類型
# ────────────────────────────────────────────────────────────

class SourceLayer(Enum):
    """
    資料來源優先層級（文件 §2.1）。
    數字越小優先度越高，第一層具最高強制力。
    """
    LAYER1_TAIWAN        = 1   # 優先：衛福部、國民健康署、食藥署（台灣本地法規）
    LAYER2_INTERNATIONAL = 2   # 補充：WHO、FAO、美國 FDA/NIH、EFSA（國際標準）
    LAYER3_ACADEMIC      = 3   # 佐證：Cochrane Review、各大醫學會臨床指引

    @property
    def label_zh(self) -> str:
        labels = {
            1: "優先（第一層）— 台灣官方法規",
            2: "補充（第二層）— 國際權威標準",
            3: "佐證（第三層）— 學術與臨床支持",
        }
        return labels[self.value]


class PopulationGroup(Enum):
    """
    系統支援之目標族群（文件 §2.2、§3.1）。
    每個族群對應獨立的規則集與官方文件。
    """
    GENERAL_ADULT    = "general_adult"    # 普通壯年
    CHRONIC_DISEASE  = "chronic_disease"  # 慢性病（糖尿病、腎病、心血管疾病）
    ALLERGY          = "allergy"          # 11 大過敏原追蹤
    PREGNANT         = "pregnant"         # 孕婦
    CHILDREN         = "children"         # 兒童
    ELDERLY          = "elderly"          # 老人

    @property
    def label_zh(self) -> str:
        labels = {
            "general_adult":   "普通壯年",
            "chronic_disease": "慢性病族群",
            "allergy":         "過敏族群（11 大過敏原）",
            "pregnant":        "孕婦",
            "children":        "兒童",
            "elderly":         "老人",
        }
        return labels[self.value]


class FetchMethod(Enum):
    """資料抓取方式"""
    MANUAL  = "manual"   # 人工下載/維護
    API     = "api"      # 官方 REST API
    SCRAPE  = "scrape"   # HTML 爬蟲
    PDF     = "pdf"      # PDF 解析


class AllergenType(Enum):
    """
    台灣食藥署《食品過敏原標示規定》11 大過敏原（文件 §2.2）。
    依據食品安全衛生管理法第 22 條授權訂定。
    """
    GLUTEN          = "gluten"          # 含麩質穀物（小麥、大麥、裸麥、燕麥等）
    CRUSTACEAN      = "crustacean"      # 甲殼類（蝦、蟹、龍蝦等）
    EGG             = "egg"             # 蛋
    FISH            = "fish"            # 魚
    PEANUT          = "peanut"          # 花生
    SOYBEAN         = "soybean"         # 大豆
    MILK            = "milk"            # 牛奶（含乳糖）
    SESAME          = "sesame"          # 芝麻
    TREE_NUT        = "tree_nut"        # 堅果（核桃、腰果、杏仁等）
    SULFITE         = "sulfite"         # 亞硫酸鹽（SO₂ ≥ 10 mg/kg）
    MOLLUSCS        = "molluscs"        # 軟體動物（牡蠣、貝類、魷魚等）


# ────────────────────────────────────────────────────────────
# 核心資料模型
# ────────────────────────────────────────────────────────────

@dataclass
class DataSource:
    """
    單一資料來源定義（文件 §2.1、§5.2）。

    Attributes:
        source_id       : 唯一識別碼（snake_case）
        institution     : 發布機構全名
        document_name   : 文件/資料集名稱
        layer           : 優先層級
        applicable_groups : 適用族群列表
        url             : 線上取得網址（None 表示僅紙本）
        version         : 版本號（如 "V7"、"第三版"）
        publish_date    : 發布日期
        description     : 文件用途說明
        fetch_method    : 資料抓取方式
        api_endpoint    : API 端點（fetch_method == API 時使用）
        notes           : 備註
    """
    source_id:         str
    institution:       str
    document_name:     str
    layer:             SourceLayer
    applicable_groups: List[PopulationGroup]
    url:               Optional[str]        = None
    version:           Optional[str]        = None
    publish_date:      Optional[date]       = None
    description:       str                  = ""
    fetch_method:      FetchMethod          = FetchMethod.MANUAL
    api_endpoint:      Optional[str]        = None
    notes:             str                  = ""

    def __str__(self) -> str:
        return f"[{self.layer.value}層] {self.institution}《{self.document_name}》"


@dataclass
class LiteratureRecord:
    """
    文獻清冊記錄（文件 §5.2）。
    每條規則須對應一筆文獻記錄，引用需精確至版本與章節（文件 §5.1）。

    Attributes:
        record_id           : 文獻編號
        source              : 對應 DataSource
        version             : 文件版本號/日期
        publish_date        : 正式發布日期
        applicable_groups   : 適用族群
        applicable_conditions: 適用條件說明
        cited_sections      : 引用具體條文或頁碼
        next_review_date    : 下次檢視日期（至少每年一次，文件 §5.3）
        notes               : 備註
    """
    record_id:             str
    source:                DataSource
    version:               str
    publish_date:          date
    applicable_groups:     List[PopulationGroup]
    applicable_conditions: str
    cited_sections:        str
    next_review_date:      date
    notes:                 str = ""

    def citation_text(self) -> str:
        """
        產生標準引用文字（符合文件 §5.1 精確度要求）。
        格式：「依據 {機構}《{文件}》{版本}（{年份}）{頁碼/條文}」
        """
        year = self.publish_date.year
        return (
            f"依據 {self.source.institution}"
            f"《{self.source.document_name}》"
            f"{self.version}（{year}）"
            f"{self.cited_sections}"
        )

    def is_review_due(self, as_of: Optional[date] = None) -> bool:
        """檢查是否已到文獻複查期限"""
        check_date = as_of or date.today()
        return check_date >= self.next_review_date


@dataclass
class GroupRule:
    """
    族群規則定義（文件 §3.1）。
    每個族群有一套來自官方文件的篩選規則，符合條件時系統觸發提示。

    Attributes:
        rule_id             : 規則唯一識別碼
        group               : 適用族群
        description         : 規則說明
        trigger_keywords    : 觸發此規則的成分關鍵字列表
        trigger_condition   : 觸發條件說明（自然語言）
        advice_text         : 提示訊息（直接引用官方說明）
        literature          : 對應文獻記錄
        can_do              : 本規則可做的判斷（文件 §3.2 OK欄）
        cannot_do           : 本規則不適合做的判斷（文件 §3.2 X欄）
        severity            : 提示嚴重度（info / warning / alert）
    """
    rule_id:           str
    group:             PopulationGroup
    description:       str
    trigger_keywords:  List[str]
    trigger_condition: str
    advice_text:       str
    literature:        LiteratureRecord
    can_do:            List[str]         = field(default_factory=list)
    cannot_do:         List[str]         = field(default_factory=list)
    severity:          str               = "info"  # info | warning | alert

    def matches(self, ingredient_list: List[str]) -> bool:
        """
        判斷成分清單是否觸發此規則。
        比對方式：關鍵字子字串比對（大小寫不敏感）。
        """
        ingredients_lower = [i.lower() for i in ingredient_list]
        return any(
            kw.lower() in ingredient
            for kw in self.trigger_keywords
            for ingredient in ingredients_lower
        )

    def build_advice(self) -> Dict:
        """
        組裝完整的建議物件（含文獻引用，符合文件 §5.1）。
        """
        return {
            "rule_id":    self.rule_id,
            "group":      self.group.label_zh,
            "severity":   self.severity,
            "advice":     self.advice_text,
            "citation":   self.literature.citation_text(),
            "disclaimer": (
                "本建議依據政府公開衛生指引，透過 AI 整合呈現，"
                "僅供參考，不構成醫療行為或診斷。"
                "個人健康狀況請諮詢醫師或營養師。"
            ),
        }


@dataclass
class FoodProduct:
    """
    食品商品資料模型（掃描後的商品資訊）。

    Attributes:
        barcode         : 條碼（EAN-13 / QR Code）
        product_name    : 商品名稱
        brand           : 品牌
        ingredients     : 成分清單（從標示解析）
        allergens       : 標示過敏原列表
        nutrition_per100g: 每 100g 營養素 dict
        nutri_score     : Nutri-Score 等級（A-E）
        nutri_score_val : Nutri-Score 積分
        source_api      : 資料來源 API 名稱
        raw_data        : 原始 API 回傳資料
    """
    barcode:            str
    product_name:       str             = ""
    brand:              str             = ""
    ingredients:        List[str]       = field(default_factory=list)
    allergens:          List[str]       = field(default_factory=list)
    nutrition_per100g:  Dict[str, float] = field(default_factory=dict)
    nutri_score:        Optional[str]   = None   # "A" ~ "E"
    nutri_score_val:    Optional[int]   = None
    source_api:         str             = ""
    raw_data:           Optional[Dict]  = None


@dataclass
class ScanResult:
    """
    最終掃描輸出結果：商品資訊 + 族群建議 + 文獻來源。
    """
    product:    FoodProduct
    group:      PopulationGroup
    advices:    List[Dict]              = field(default_factory=list)
    allergen_alerts: List[str]         = field(default_factory=list)
    disclaimer: str                    = (
        "本建議依據政府公開衛生指引，透過 AI 整合呈現，"
        "僅供參考，不構成醫療行為或診斷。"
        "個人健康狀況請諮詢醫師或營養師。"
    )
    scan_timestamp: Optional[date]     = None
