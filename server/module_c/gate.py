"""
Module C — Stage 2：硬性閘門 + 網域分層

硬性閘門（兩個條件必須「同時」成立，缺一即丟棄）：
  1. 文件出現 canonical 廠商名（Stage 0 實體解析成功）
  2. 文件出現受控詞彙（下方 CONTROLLED_TERMS）

閘門是規則式的，不涉及 LLM。LLM 在本管線的唯一用途是候選文件的分群「建議」
（見 clustering.py），不參與是否收錄的判定。

網域分層（2026-07-15 決議）：
  實測 unknown 佔 55%，其中包含 BBC 中文、上下游新聞、台灣醒報、維基百科等
  明確可辨識的好來源。原「unknown 一律丟棄」會丟掉一半的好資料，故改為：
  擴充白名單 → 剩餘真正不認識的網域仍標為 unknown 並「保留」進候選佇列，
  由人工審核時決定要不要採用。不預先丟棄資料。
"""
from urllib.parse import urlparse

# --- 受控詞彙 --------------------------------------------------------------
# 沿用實測查詢模板已驗證的詞彙，並補入實測中真實出現過的事件用語
# （統一原料輻射超標、義美逾期原料、中聯油脂苯駢芘致癌油）。
CONTROLLED_TERMS = [
    # 稽查／裁罰語彙
    "不合格", "違規", "違法", "查獲", "稽查", "抽驗", "衛生局", "食藥署", "罰鍰", "裁罰",
    # 產品處置語彙
    "回收", "下架", "停售", "退運", "銷毀",
    # 危害語彙
    "異物", "超標", "逾期", "過期", "輻射", "致癌", "食物中毒", "中毒",
    # 消費者端語彙
    "投訴", "客訴", "食安",
]

# --- 網域分層 --------------------------------------------------------------
OFFICIAL_DOMAINS = {
    "fda.gov.tw", "mohw.gov.tw", "consumer.gov.tw", "gov.tw",
}

NEWS_DOMAINS = {
    # 原有白名單
    "udn.com", "ltn.com.tw", "cna.com.tw", "setn.com", "tvbs.com.tw",
    "yahoo.com", "chinatimes.com", "ettoday.net",
    # 2026-07-15 擴充：實測落在 unknown 但屬明確可辨識的可靠來源
    "bbc.com",              # BBC 中文
    "newsmarket.com.tw",    # 上下游新聞市集
    "anntw.com",            # 台灣醒報
    "news.pchome.com.tw",   # PChome 新聞
    "zh.wikipedia.org",     # 維基百科（事件條目，具編輯歷史可稽核）
    "wikipedia.org",
    "storm.mg",             # 風傳媒
    "newtalk.tw",           # 新頭殼
    "cts.com.tw", "pts.org.tw", "ftvnews.com.tw",  # 華視／公視／民視
}

# 廠商自家網域 → self_published
# 對應到既有事件者＝廠商答辯權（應予呈現）；無對應事件者＝行銷文案（丟棄）。
SELF_PUBLISHED_DOMAINS = {
    "uni-president.com.tw",   # 統一企業
    "imeifoods.com.tw",       # 義美食品
    "weichuan.com.tw",        # 味全
    "lianhwa.com.tw",         # 聯華食品
    "swirecocacola.com",      # 太古可口可樂
    "coca-cola.com.tw",
}

SOCIAL_DOMAINS = {
    "facebook.com", "instagram.com", "ptt.cc", "dcard.tw",
    "mobile01.com", "youtube.com", "threads.net", "twitter.com", "x.com",
}


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _matches(domain: str, domain_set: set[str]) -> bool:
    """精確或子網域比對（news.pchome.com.tw 需能命中 pchome 那筆完整網域）。"""
    return any(domain == d or domain.endswith("." + d) for d in domain_set)


def classify_domain(url: str) -> str:
    """
    網域分層：official / news / self_published / social / unknown

    unknown 不代表丟棄——仍會進候選佇列，由人工審核決定。
    """
    domain = _domain_of(url)
    if not domain:
        return "unknown"
    if _matches(domain, OFFICIAL_DOMAINS):
        return "official"
    if _matches(domain, SELF_PUBLISHED_DOMAINS):
        return "self_published"
    if _matches(domain, NEWS_DOMAINS):
        return "news"
    if _matches(domain, SOCIAL_DOMAINS):
        return "social"
    return "unknown"


def matched_terms(text: str) -> list[str]:
    """回傳文件中命中的受控詞彙。"""
    if not text:
        return []
    return [t for t in CONTROLLED_TERMS if t in text]


def passes_gate(text: str, manufacturer_hits: list) -> tuple[bool, list[str]]:
    """
    硬性閘門判定。

    回傳 (是否通過, 命中的受控詞彙)。

    兩個條件必須同時成立：
      1. manufacturer_hits 非空（Stage 0 已解析出 canonical 廠商）
      2. 文件出現至少一個受控詞彙

    僅提及廠商但無任何違規語彙者（如公司財報、新品發表）→ 不通過。
    """
    if not manufacturer_hits:
        return False, []
    terms = matched_terms(text)
    return (len(terms) > 0), terms
