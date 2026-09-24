"""
data_sources/registry.py
========================
食品掃描建議系統 — 靜態資料來源清冊與族群規則登錄

依照《食品掃描系統_資料來源策略文件》v1.0 (2026-04-29) §2、§3、§5 建立。
本檔案為系統的「真實來源」(Single Source of Truth)，
所有 fetcher / organizer 均由此取得來源定義與規則。

版本更新流程（文件 §5.3）：
  1. 更新對應 DataSource / LiteratureRecord
  2. 更新 next_review_date
  3. 於 CHANGELOG 區段新增記錄
"""

from datetime import date
from typing import Dict, List

from .models import (
    AllergenType,
    DataSource,
    FetchMethod,
    GroupRule,
    LiteratureRecord,
    PopulationGroup,
    SourceLayer,
)


# ══════════════════════════════════════════════════════════════
# §1  資料來源（DataSource）
#     優先層級：第一層（台灣官方）> 第二層（國際）> 第三層（學術）
# ══════════════════════════════════════════════════════════════

# ── 第一層：台灣官方 ──────────────────────────────────────────

DS_FDA_DRI = DataSource(
    source_id         = "tw_fda_dri",
    institution       = "衛生福利部食品藥物管理署（食藥署）",
    document_name     = "國人膳食營養素參考攝取量（DRIs）第八版",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.GENERAL_ADULT, PopulationGroup.ELDERLY,
                         PopulationGroup.CHILDREN, PopulationGroup.PREGNANT],
    url               = "https://www.fda.gov.tw/TC/site.aspx?sid=2750",
    version           = "第八版",
    publish_date      = date(2020, 1, 1),
    description       = "每日營養素參考值，包含熱量、蛋白質、脂肪、糖、鈉等。",
    fetch_method      = FetchMethod.SCRAPE,
    notes             = "含各年齡/性別分類的每日建議攝取量表格",
)

DS_FDA_LABEL = DataSource(
    source_id         = "tw_fda_label",
    institution       = "衛生福利部食品藥物管理署（食藥署）",
    document_name     = "食品標示規範",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.GENERAL_ADULT],
    url               = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=L0040098",
    version           = "最新版（依全國法規資料庫）",
    publish_date      = date(2022, 3, 1),
    description       = "商品標示義務規範，包含營養標示格式與強制揭露項目。",
    fetch_method      = FetchMethod.API,
    api_endpoint      = "https://law.moj.gov.tw/api/v1/lawdata/L0040098",
    notes             = "全國法規資料庫提供 REST API，可直接取得法規條文 JSON",
)

DS_FDA_ALLERGEN = DataSource(
    source_id         = "tw_fda_allergen",
    institution       = "衛生福利部食品藥物管理署（食藥署）",
    document_name     = "食品過敏原標示規定",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.ALLERGY],
    url               = "https://www.foodlabel.org.tw/FdaFrontEndApp/Law/Edit?SystemId=2692d92b-ea65-4640-a17e-aa458a3f54bb&c",
    version           = "衛授食字第1071302165號公告",
    publish_date      = date(2018, 8, 21),
    description       = "台灣 11 大過敏原強制標示義務，法規層級最具強制性。",
    fetch_method      = FetchMethod.SCRAPE,
    notes             = "官方公告之過敏原標示規定全文",
)

DS_FDA_PREGNANT_BAN = DataSource(
    source_id         = "tw_fda_pregnant_ban",
    institution       = "衛生福利部食品藥物管理署（食藥署）",
    document_name     = "孕期禁忌成分公告",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.PREGNANT],
    url               = "https://www.fda.gov.tw/TC/siteContent.aspx?sid=5059",
    version           = "最新公告",
    description       = "食藥署公告孕婦應避免或限制之食品添加物與成分。",
    fetch_method      = FetchMethod.SCRAPE,
)

DS_FDA_CHILDREN_ADDITIVE = DataSource(
    source_id         = "tw_fda_children_additive",
    institution       = "衛生福利部食品藥物管理署（食藥署）",
    document_name     = "兒童不適用添加物規範",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.CHILDREN],
    url               = "https://www.fda.gov.tw/TC/site.aspx?sid=2749",
    version           = "最新版",
    description       = "規範兒童食品中不得使用或應限量使用之添加物清單。",
    fetch_method      = FetchMethod.SCRAPE,
)

DS_HPA_ELDERLY = DataSource(
    source_id         = "tw_hpa_elderly",
    institution       = "衛生福利部國民健康署（國健署）",
    document_name     = "老年期營養參考手冊",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.ELDERLY],
    url               = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=542&pid=9821",
    version           = "第三版",
    publish_date      = date(2022, 1, 1),
    description       = "老年人營養建議，含各類營養素攝取與飲食指南。",
    fetch_method      = FetchMethod.PDF,
    notes             = "文件 §5.4 已正式登錄；PDF 第 18 頁為主要引用頁",
)

DS_HPA_CHILDREN = DataSource(
    source_id         = "tw_hpa_children",
    institution       = "衛生福利部國民健康署（國健署）",
    document_name     = "兒童飲食指南",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.CHILDREN],
    url               = "https://www.hpa.gov.tw/Pages/List.aspx?nodeid=175",
    version           = "最新版",
    description       = "兒童各年齡層的飲食建議與食物份量指引。",
    fetch_method      = FetchMethod.PDF,
)

DS_HPA_PREGNANT = DataSource(
    source_id         = "tw_hpa_pregnant",
    institution       = "衛生福利部國民健康署（國健署）",
    document_name     = "孕婦營養指引",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.PREGNANT],
    url               = "https://www.hpa.gov.tw/Pages/List.aspx?nodeid=211",
    version           = "最新版",
    description       = "孕期各階段的營養建議與禁忌食物說明。",
    fetch_method      = FetchMethod.PDF,
)

DS_HPA_CHRONIC = DataSource(
    source_id         = "tw_hpa_chronic",
    institution       = "衛生福利部國民健康署（國健署）",
    document_name     = "慢性病飲食手冊",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.CHRONIC_DISEASE],
    url               = "https://www.hpa.gov.tw/Pages/List.aspx?nodeid=215",
    version           = "最新版",
    description       = "糖尿病、腎病、心血管疾病等慢性病患者的飲食建議。",
    fetch_method      = FetchMethod.PDF,
)

DS_TDA = DataSource(
    source_id         = "tw_tda_guideline",
    institution       = "台灣糖尿病學會（TDA）",
    document_name     = "台灣糖尿病臨床照護指引",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.CHRONIC_DISEASE],
    url               = "https://www.endo-dm.org.tw/dia/direct/index.asp?BIG_KIND=4&SMALL_KIND=25",
    version           = "最新版",
    description       = "糖尿病患者的血糖管理與飲食照護建議。",
    fetch_method      = FetchMethod.PDF,
)

DS_TRS = DataSource(
    source_id         = "tw_trs_guideline",
    institution       = "台灣腎臟醫學會（TRS）",
    document_name     = "慢性腎臟病飲食指導原則",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.CHRONIC_DISEASE],
    url               = "https://www.tsn.org.tw/",
    version           = "最新版",
    description       = "CKD 患者蛋白質、鉀、磷、鈉限制的飲食指引。",
    fetch_method      = FetchMethod.PDF,
)

DS_TSOC = DataSource(
    source_id         = "tw_tsoc_guideline",
    institution       = "中華民國心臟學會（TSOC）",
    document_name     = "心血管疾病飲食與生活型態建議",
    layer             = SourceLayer.LAYER1_TAIWAN,
    applicable_groups = [PopulationGroup.CHRONIC_DISEASE],
    url               = "https://www.tsoc.org.tw/",
    version           = "最新版",
    description       = "心臟病患者飽和脂肪、膽固醇、鈉的飲食限制建議。",
    fetch_method      = FetchMethod.PDF,
)

# ── 第二層：國際權威補充依據 ─────────────────────────────────

DS_NUTRISCORE = DataSource(
    source_id         = "eu_nutriscore_v7",
    institution       = "Santé Publique France（法國公共衛生署）",
    document_name     = "NUTRI-SCORE Questions & Answers (English version)",
    layer             = SourceLayer.LAYER2_INTERNATIONAL,
    applicable_groups = list(PopulationGroup),   # 通用評分，適用所有族群
    url               = "https://www.santepubliquefrance.fr/media/files/02-determinants-de-sante/nutrition-et-activite-physique/nutri-score/qr-science-en",
    version           = "V7 (FAQ-updatedAlgo-V7)",
    publish_date      = date(2023, 12, 21),
    description       = "Nutri-Score 2017 + 2023 更新算法完整 Q&A 文件（文件 §5.4 已登錄）。",
    fetch_method      = FetchMethod.PDF,
    notes             = "實施日期：2024-01-01；歐洲多國正式採用",
)

DS_WHO_NUTRITION = DataSource(
    source_id         = "who_nutrition",
    institution       = "World Health Organization (WHO)",
    document_name     = "Healthy diet — Key facts",
    layer             = SourceLayer.LAYER2_INTERNATIONAL,
    applicable_groups = [PopulationGroup.PREGNANT, PopulationGroup.GENERAL_ADULT],
    url               = "https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
    version           = "2023",
    publish_date      = date(2023, 7, 1),
    description       = "WHO 飲食建議概要，含糖、脂肪、鹽每日上限。",
    fetch_method      = FetchMethod.SCRAPE,
    api_endpoint      = "https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
)

DS_WHO_MATERNAL = DataSource(
    source_id         = "who_maternal_nutrition",
    institution       = "World Health Organization (WHO)",
    document_name     = "Nutrition for pregnant women",
    layer             = SourceLayer.LAYER2_INTERNATIONAL,
    applicable_groups = [PopulationGroup.PREGNANT],
    url               = "https://www.who.int/health-topics/maternal-nutrition",
    version           = "最新版",
    description       = "WHO 孕婦與母嬰營養建議（文件 §2.2）。",
    fetch_method      = FetchMethod.SCRAPE,
)

DS_FDA_US = DataSource(
    source_id         = "us_fda_guidelines",
    institution       = "U.S. Food and Drug Administration (FDA)",
    document_name     = "Dietary Guidelines for Americans",
    layer             = SourceLayer.LAYER2_INTERNATIONAL,
    applicable_groups = [PopulationGroup.GENERAL_ADULT, PopulationGroup.ELDERLY],
    url               = "https://www.dietaryguidelines.gov/",
    version           = "2020-2025",
    publish_date      = date(2020, 12, 29),
    description       = "美國 FDA/NIH 飲食指引，作為國際補充依據。",
    fetch_method      = FetchMethod.API,
    api_endpoint      = "https://api.nal.usda.gov/fdc/v1/",
    notes             = "USDA FoodData Central 提供開放 API（需申請 API key）",
)

DS_EFSA = DataSource(
    source_id         = "eu_efsa",
    institution       = "European Food Safety Authority (EFSA)",
    document_name     = "EFSA Dietary Reference Values",
    layer             = SourceLayer.LAYER2_INTERNATIONAL,
    applicable_groups = [PopulationGroup.GENERAL_ADULT, PopulationGroup.ELDERLY,
                         PopulationGroup.CHILDREN],
    url               = "https://www.efsa.europa.eu/en/topics/topic/dietary-reference-values",
    version           = "最新版",
    description       = "歐洲食品安全局之每日參考攝取量。",
    fetch_method      = FetchMethod.SCRAPE,
)

# ── 第三層：學術與臨床佐證 ───────────────────────────────────

DS_COCHRANE = DataSource(
    source_id         = "cochrane_review",
    institution       = "Cochrane Collaboration",
    document_name     = "Cochrane Review（食品相關系統性回顧）",
    layer             = SourceLayer.LAYER3_ACADEMIC,
    applicable_groups = list(PopulationGroup),
    url               = "https://www.cochranelibrary.com/",
    version           = "各議題獨立版本",
    description       = "特定食品議題的系統性文獻回顧（最高等級學術證據）。",
    fetch_method      = FetchMethod.SCRAPE,
)

# ── 外部商品資料 API（抓取用）────────────────────────────────

DS_OPEN_FOOD_FACTS = DataSource(
    source_id         = "open_food_facts",
    institution       = "Open Food Facts (Community Database)",
    document_name     = "Open Food Facts Product API",
    layer             = SourceLayer.LAYER2_INTERNATIONAL,
    applicable_groups = list(PopulationGroup),
    url               = "https://world.openfoodfacts.org/",
    version           = "v2",
    description       = "開放食品成分資料庫，提供條碼查詢商品成分、Nutri-Score 等資訊。",
    fetch_method      = FetchMethod.API,
    api_endpoint      = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json",
    notes             = "免費、無需 API key；台灣在地商品覆蓋率相對較低",
)


# ══════════════════════════════════════════════════════════════
# §2  文獻清冊（LiteratureRecord）
#     對應文件 §5.2「已登錄文獻清冊」
# ══════════════════════════════════════════════════════════════

LIT_NUTRISCORE_V7 = LiteratureRecord(
    record_id              = "LIT-001",
    source                 = DS_NUTRISCORE,
    version                = "V7 (FAQ-updatedAlgo-V7)",
    publish_date           = date(2023, 12, 21),
    applicable_groups      = list(PopulationGroup),
    applicable_conditions  = "食品營養評分標準（歐洲脈絡，作為國際補充參考依據）",
    cited_sections         = "p.24 (Table 5 負分閾值), p.25 (Table 6 正分閾值), p.29 (等級映射)",
    next_review_date       = date(2027, 4, 29),
    notes                  = "核心演算法依據；2024-01-01 起歐洲多國正式採用",
)

LIT_HPA_ELDERLY = LiteratureRecord(
    record_id              = "LIT-002",
    source                 = DS_HPA_ELDERLY,
    version                = "第三版",
    publish_date           = date(2022, 1, 1),
    applicable_groups      = [PopulationGroup.ELDERLY],
    applicable_conditions  = "65 歲以上老年族群，膳食纖維與各營養素建議",
    cited_sections         = "第 18 頁：每日膳食纖維建議攝取量",
    next_review_date       = date(2026, 4, 29),
    notes                  = "文件 §5.4 範例基準文獻",
)

LIT_FDA_ALLERGEN = LiteratureRecord(
    record_id              = "LIT-003",
    source                 = DS_FDA_ALLERGEN,
    version                = "2019 版",
    publish_date           = date(2019, 7, 1),
    applicable_groups      = [PopulationGroup.ALLERGY],
    applicable_conditions  = "所有含 11 大過敏原成分的食品標示",
    cited_sections         = "第 4 條：強制標示過敏原項目及標示格式",
    next_review_date       = date(2027, 4, 29),
    notes                  = "法規層級，最具強制性；過敏族群首要依據",
)

LIT_HPA_PREGNANT = LiteratureRecord(
    record_id              = "LIT-004",
    source                 = DS_HPA_PREGNANT,
    version                = "最新版",
    publish_date           = date(2021, 1, 1),
    applicable_groups      = [PopulationGroup.PREGNANT],
    applicable_conditions  = "孕期各階段（尤以第一、三孕期）",
    cited_sections         = "第三章：孕期應避免之食物與成分",
    next_review_date       = date(2027, 4, 29),
)

LIT_TDA = LiteratureRecord(
    record_id              = "LIT-005",
    source                 = DS_TDA,
    version                = "2022 版",
    publish_date           = date(2022, 1, 1),
    applicable_groups      = [PopulationGroup.CHRONIC_DISEASE],
    applicable_conditions  = "第 2 型糖尿病、第 1 型糖尿病患者",
    cited_sections         = "第五章：血糖管理與碳水化合物攝取建議",
    next_review_date       = date(2027, 4, 29),
)

LIT_HPA_CHILDREN = LiteratureRecord(
    record_id              = "LIT-006",
    source                 = DS_HPA_CHILDREN,
    version                = "最新版",
    publish_date           = date(2021, 1, 1),
    applicable_groups      = [PopulationGroup.CHILDREN],
    applicable_conditions  = "0-18 歲兒童及青少年",
    cited_sections         = "第二章：兒童不建議過量攝取之食品添加物",
    next_review_date       = date(2027, 4, 29),
)


# ══════════════════════════════════════════════════════════════
# §3  族群規則（GroupRule）
#     依文件 §3.1 Top-down 架構設計
#     每條規則直接連結至文獻，輸出建議時一並附帶引用
# ══════════════════════════════════════════════════════════════

RULES: List[GroupRule] = [

    # ── 普通壯年 ────────────────────────────────────────────
    GroupRule(
        rule_id           = "GA-001",
        group             = PopulationGroup.GENERAL_ADULT,
        description       = "高鈉食品提示",
        trigger_keywords  = ["氯化鈉", "食鹽", "醬油", "味精", "麩胺酸鈉",
                             "亞硝酸鈉", "磷酸鈉", "鈉"],
        trigger_condition = "商品每 100g 含鈉量 > 600mg，或成分表含高鈉添加物",
        advice_text       = (
            "此商品含鈉量偏高。成年人每日鈉建議攝取量不超過 2,400mg（相當於食鹽 6g）。"
            "建議搭配大量蔬果，並留意當日其他飲食的鈉攝取。"
        ),
        literature        = LIT_NUTRISCORE_V7,
        can_do            = ["標示鈉成分存在", "引用每日建議上限"],
        cannot_do         = ["宣稱此商品對特定人不安全", "計算個人是否超標"],
        severity          = "info",
    ),

    GroupRule(
        rule_id           = "GA-002",
        group             = PopulationGroup.GENERAL_ADULT,
        description       = "高糖食品提示",
        trigger_keywords  = ["砂糖", "果糖", "葡萄糖", "蔗糖", "麥芽糖", "高果糖玉米糖漿",
                             "糖漿", "果糖葡萄糖液糖"],
        trigger_condition = "成分表前五項含糖類成分",
        advice_text       = (
            "此商品添加糖含量較高。WHO 建議每日游離糖攝取量應低於總熱量 10%（約 50g），"
            "最佳目標低於 5%（約 25g）。"
        ),
        literature        = LIT_NUTRISCORE_V7,
        can_do            = ["標示糖類成分存在"],
        cannot_do         = ["計算個人每日是否超標"],
        severity          = "info",
    ),

    # ── 慢性病 ───────────────────────────────────────────────
    GroupRule(
        rule_id           = "CD-001",
        group             = PopulationGroup.CHRONIC_DISEASE,
        description       = "糖尿病族群 — 高升糖成分提示",
        trigger_keywords  = ["白糖", "砂糖", "果糖", "葡萄糖", "蔗糖", "麥芽糖",
                             "高果糖玉米糖漿", "麥芽糊精", "白米", "精製澱粉"],
        trigger_condition = "成分表含快速升糖之糖類或精製澱粉",
        advice_text       = (
            "此商品含有易快速升糖之成分，糖尿病患者應注意攝取量，"
            "建議諮詢營養師評估是否適合納入個人飲食計畫。"
        ),
        literature        = LIT_TDA,
        can_do            = ["說明成分屬易升糖類別", "引用學會建議限制攝取"],
        cannot_do         = ["宣稱此商品不安全", "預測血糖變化"],
        severity          = "warning",
    ),

    GroupRule(
        rule_id           = "CD-002",
        group             = PopulationGroup.CHRONIC_DISEASE,
        description       = "腎病族群 — 高磷/高鉀成分提示",
        trigger_keywords  = ["磷酸鹽", "磷酸鈉", "焦磷酸鈉", "多聚磷酸鹽",
                             "氯化鉀", "磷酸二氫鉀", "磷酸鈉鉀"],
        trigger_condition = "成分表含磷酸鹽或鉀鹽類添加物",
        advice_text       = (
            "此商品含磷酸鹽或鉀鹽類添加物。慢性腎臟病患者（CKD）應嚴格控制磷與鉀的攝取，"
            "建議依個人腎功能狀況諮詢醫師或腎臟科營養師。"
        ),
        literature        = LIT_TDA,  # 暫用，正式版應對應 LIT_TRS
        can_do            = ["標示磷酸鹽/鉀鹽成分存在", "引用腎臟學會建議"],
        cannot_do         = ["判斷腎功能對個人是否安全"],
        severity          = "warning",
    ),

    GroupRule(
        rule_id           = "CD-003",
        group             = PopulationGroup.CHRONIC_DISEASE,
        description       = "心血管疾病族群 — 飽和脂肪提示",
        trigger_keywords  = ["棕櫚油", "椰子油", "豬油", "牛油", "奶油", "氫化植物油",
                             "部分氫化", "反式脂肪"],
        trigger_condition = "成分表含飽和脂肪或反式脂肪來源",
        advice_text       = (
            "此商品含有飽和脂肪或反式脂肪來源。心血管疾病患者建議減少飽和脂肪攝取，"
            "並完全避免人工反式脂肪，可選擇以不飽和脂肪為主的替代品。"
        ),
        literature        = LIT_NUTRISCORE_V7,
        can_do            = ["說明成分屬飽和脂肪類別"],
        cannot_do         = ["預測心血管風險"],
        severity          = "warning",
    ),

    # ── 過敏族群 ─────────────────────────────────────────────
    GroupRule(
        rule_id           = "AL-001",
        group             = PopulationGroup.ALLERGY,
        description       = "小麥/麩質過敏原警示",
        trigger_keywords  = ["小麥", "麵粉", "麩質", "大麥", "裸麥", "燕麥",
                             "gluten", "wheat", "barley", "rye", "oat"],
        trigger_condition = "成分表或過敏原警示含麩質穀物",
        advice_text       = (
            "【⚠️ 過敏原警示】此商品含有小麥/麩質成分。"
            "麩質不耐症或乳糜瀉（Celiac Disease）患者請勿食用。"
        ),
        literature        = LIT_FDA_ALLERGEN,
        can_do            = ["標示過敏原存在", "引用食藥署規定"],
        cannot_do         = ["評估個人過敏風險程度"],
        severity          = "alert",
    ),

    GroupRule(
        rule_id           = "AL-002",
        group             = PopulationGroup.ALLERGY,
        description       = "花生過敏原警示",
        trigger_keywords  = ["花生", "peanut", "花生油", "花生醬"],
        trigger_condition = "成分表或過敏原警示含花生",
        advice_text       = (
            "【⚠️ 過敏原警示】此商品含有花生成分。"
            "花生過敏者請勿食用，嚴重者可能引發過敏性休克，請隨身攜帶急救藥物。"
        ),
        literature        = LIT_FDA_ALLERGEN,
        can_do            = ["標示過敏原存在"],
        cannot_do         = ["評估個人過敏嚴重程度"],
        severity          = "alert",
    ),

    GroupRule(
        rule_id           = "AL-003",
        group             = PopulationGroup.ALLERGY,
        description       = "甲殼類過敏原警示",
        trigger_keywords  = ["蝦", "蟹", "龍蝦", "蝦仁", "蝦醬", "蟹膏",
                             "shrimp", "crab", "lobster", "prawn", "crustacean"],
        trigger_condition = "成分表或過敏原警示含甲殼類",
        advice_text       = (
            "【⚠️ 過敏原警示】此商品含有甲殼類（蝦/蟹/龍蝦）成分。"
            "甲殼類過敏者請勿食用。"
        ),
        literature        = LIT_FDA_ALLERGEN,
        severity          = "alert",
    ),

    GroupRule(
        rule_id           = "AL-004",
        group             = PopulationGroup.ALLERGY,
        description       = "牛奶/乳製品過敏原警示",
        trigger_keywords  = ["牛奶", "乳製品", "乳糖", "乳清", "奶粉", "起司",
                             "milk", "dairy", "lactose", "whey", "casein"],
        trigger_condition = "成分表或過敏原警示含牛奶/乳製品",
        advice_text       = (
            "【⚠️ 過敏原警示】此商品含有牛奶/乳製品成分。"
            "牛奶蛋白過敏或乳糖不耐症者請注意。"
        ),
        literature        = LIT_FDA_ALLERGEN,
        severity          = "alert",
    ),

    # ── 孕婦 ─────────────────────────────────────────────────
    GroupRule(
        rule_id           = "PG-001",
        group             = PopulationGroup.PREGNANT,
        description       = "孕婦 — 高咖啡因成分提示",
        trigger_keywords  = ["咖啡因", "茶鹼", "可可鹼", "咖啡", "濃茶", "能量飲",
                             "caffeine", "coffee extract", "guarana"],
        trigger_condition = "成分表含咖啡因或相關成分",
        advice_text       = (
            "此商品含有咖啡因相關成分。國健署建議孕婦每日咖啡因攝取量應低於 200mg，"
            "過量攝取可能影響胎兒發育，建議諮詢婦產科醫師。"
        ),
        literature        = LIT_HPA_PREGNANT,
        can_do            = ["標示咖啡因成分存在", "引用每日建議上限"],
        cannot_do         = ["計算個人是否超標", "預測胎兒影響"],
        severity          = "warning",
    ),

    GroupRule(
        rule_id           = "PG-002",
        group             = PopulationGroup.PREGNANT,
        description       = "孕婦 — 生魚/生肉風險提示",
        trigger_keywords  = ["生魚", "生肉", "生蠔", "未熟", "半生", "sashimi",
                             "raw oyster", "carpaccio"],
        trigger_condition = "商品為生食或半生食類",
        advice_text       = (
            "此商品屬於生食類。孕婦應避免食用生魚片、生肉、生蠔等未完全熟透食物，"
            "以降低李斯特菌、弓蟲等感染風險。請依食藥署孕婦飲食注意事項建議食用。"
        ),
        literature        = LIT_HPA_PREGNANT,
        severity          = "alert",
    ),

    GroupRule(
        rule_id           = "PG-003",
        group             = PopulationGroup.PREGNANT,
        description       = "孕婦 — 人工甜味劑提示",
        trigger_keywords  = ["阿斯巴甜", "糖精", "醋磺內酯鉀", "甜菊糖",
                             "aspartame", "saccharin", "sucralose", "acesulfame"],
        trigger_condition = "成分表含人工甜味劑",
        advice_text       = (
            "此商品含有人工甜味劑。WHO 建議孕婦應謹慎使用非營養性甜味劑（NNS），"
            "相關安全性仍持續被研究中，建議諮詢醫師或營養師。"
        ),
        literature        = LIT_HPA_PREGNANT,
        severity          = "info",
    ),

    # ── 兒童 ─────────────────────────────────────────────────
    GroupRule(
        rule_id           = "CH-001",
        group             = PopulationGroup.CHILDREN,
        description       = "兒童 — 人工色素提示",
        trigger_keywords  = ["色素", "食用色素", "人工色素", "亮藍", "日落黃",
                             "酒石黃", "誘惑紅", "Tartrazine", "Sunset Yellow",
                             "Allura Red", "Brilliant Blue", "E102", "E110", "E122",
                             "E124", "E129", "E133"],
        trigger_condition = "成分表含人工合成色素（英國 Southampton 六色素）",
        advice_text       = (
            "此商品含有人工色素。歐洲 EFSA 研究指出特定人工色素（如 Southampton 六色素）"
            "可能與兒童過動有關聯。建議兒童減少攝取含人工色素食品。"
        ),
        literature        = LIT_HPA_CHILDREN,
        can_do            = ["標示人工色素存在", "引用 EFSA 研究說明"],
        cannot_do         = ["宣稱此商品導致過動症"],
        severity          = "warning",
    ),

    GroupRule(
        rule_id           = "CH-002",
        group             = PopulationGroup.CHILDREN,
        description       = "兒童 — 高咖啡因提示",
        trigger_keywords  = ["咖啡因", "咖啡", "能量飲", "濃茶", "guarana"],
        trigger_condition = "成分表含咖啡因",
        advice_text       = (
            "此商品含有咖啡因。國健署建議 12 歲以下兒童應完全避免咖啡因攝取，"
            "青少年每日攝取量亦不宜超過 85mg。"
        ),
        literature        = LIT_HPA_CHILDREN,
        severity          = "warning",
    ),

    # ── 老人 ─────────────────────────────────────────────────
    GroupRule(
        rule_id           = "EL-001",
        group             = PopulationGroup.ELDERLY,
        description       = "老人 — 膳食纖維不足提示",
        trigger_keywords  = [],   # 此規則依營養標示數值觸發，非關鍵字
        trigger_condition = "商品每 100g 膳食纖維 < 3.0g",
        advice_text       = (
            "此商品膳食纖維含量偏低。國健署《老年期營養參考手冊》建議"
            "老年人每日應攝取足夠膳食纖維以維持腸道健康，建議搭配蔬菜或全穀類食品。"
        ),
        literature        = LIT_HPA_ELDERLY,
        can_do            = ["標示膳食纖維含量", "引用每日建議量"],
        cannot_do         = ["個人化攝取量計算"],
        severity          = "info",
    ),

    GroupRule(
        rule_id           = "EL-002",
        group             = PopulationGroup.ELDERLY,
        description       = "老人 — 高鈉提示（腎功能考量）",
        trigger_keywords  = ["氯化鈉", "食鹽", "醬油", "鈉", "亞硝酸鈉"],
        trigger_condition = "商品每 100g 含鈉量 > 400mg",
        advice_text       = (
            "此商品鈉含量較高。老年人腎臟排鈉能力下降，更需控制每日鈉攝取，"
            "建議搭配充足水分並諮詢醫師。"
        ),
        literature        = LIT_HPA_ELDERLY,
        severity          = "warning",
    ),
]


# ══════════════════════════════════════════════════════════════
# §4  查詢輔助函式
# ══════════════════════════════════════════════════════════════

# 所有資料來源（依 source_id 索引）
ALL_SOURCES: Dict[str, DataSource] = {
    ds.source_id: ds
    for ds in [
        DS_FDA_DRI, DS_FDA_LABEL, DS_FDA_ALLERGEN, DS_FDA_PREGNANT_BAN,
        DS_FDA_CHILDREN_ADDITIVE, DS_HPA_ELDERLY, DS_HPA_CHILDREN,
        DS_HPA_PREGNANT, DS_HPA_CHRONIC, DS_TDA, DS_TRS, DS_TSOC,
        DS_NUTRISCORE, DS_WHO_NUTRITION, DS_WHO_MATERNAL,
        DS_FDA_US, DS_EFSA, DS_COCHRANE, DS_OPEN_FOOD_FACTS,
    ]
}

# 文獻清冊（依 record_id 索引）
ALL_LITERATURE: Dict[str, LiteratureRecord] = {
    lit.record_id: lit
    for lit in [
        LIT_NUTRISCORE_V7, LIT_HPA_ELDERLY, LIT_FDA_ALLERGEN,
        LIT_HPA_PREGNANT, LIT_TDA, LIT_HPA_CHILDREN,
    ]
}


def get_sources_by_layer(layer: SourceLayer) -> List[DataSource]:
    """取得指定優先層級的所有資料來源"""
    return [s for s in ALL_SOURCES.values() if s.layer == layer]


def get_sources_by_group(group: PopulationGroup) -> List[DataSource]:
    """取得適用指定族群的所有資料來源"""
    return [s for s in ALL_SOURCES.values() if group in s.applicable_groups]


def get_rules_by_group(group: PopulationGroup) -> List[GroupRule]:
    """取得適用指定族群的所有規則"""
    return [r for r in RULES if r.group == group]


def get_overdue_literature(as_of=None) -> List[LiteratureRecord]:
    """回傳所有已超過複查日期的文獻"""
    return [lit for lit in ALL_LITERATURE.values() if lit.is_review_due(as_of)]


# ══════════════════════════════════════════════════════════════
# CHANGELOG
# ══════════════════════════════════════════════════════════════
# 2026-04-29  v1.0  依策略文件 v1.0 初始建立，涵蓋三層來源、六族群、13 條規則
