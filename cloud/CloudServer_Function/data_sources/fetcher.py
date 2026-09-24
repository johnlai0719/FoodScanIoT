"""
data_sources/fetcher.py
=======================
食品掃描建議系統 — 資料抓取模組

實作各資料來源的 HTTP 抓取邏輯：
  1. Open Food Facts API          — 條碼查商品（立即可用，免 key）
  2. 全國法規資料庫 API           — 食藥署法規條文（免費開放）
  3. 食藥署 / 國健署 HTML 爬蟲   — 公告與指引頁面
  4. USDA FoodData Central API   — 美國 FDA 補充（需 API key）

設計原則：
  - 每個 Fetcher 繼承 BaseFetcher，統一 retry / timeout / User-Agent
  - 回傳統一格式 FetchResult（成功/失敗皆有結構）
  - 全部為同步實作；大量並行請求請用 ThreadPoolExecutor 包裝
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlencode

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

from .models import FoodProduct, PopulationGroup
from .registry import DS_OPEN_FOOD_FACTS

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# 共用資料結構
# ────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    """
    統一的抓取結果容器。
    成功：ok=True, data 含解析後資料。
    失敗：ok=False, error 描述原因。
    """
    ok:          bool
    source_id:   str
    data:        Optional[Any]  = None
    raw_text:    Optional[str]  = None
    error:       Optional[str]  = None
    status_code: Optional[int]  = None
    fetched_at:  str            = field(default_factory=lambda: date.today().isoformat())

    @classmethod
    def failure(cls, source_id: str, error: str, status_code: int = None) -> "FetchResult":
        return cls(ok=False, source_id=source_id, error=error, status_code=status_code)

    @classmethod
    def success(cls, source_id: str, data: Any, raw_text: str = None,
                status_code: int = 200) -> "FetchResult":
        return cls(ok=True, source_id=source_id, data=data,
                   raw_text=raw_text, status_code=status_code)


# ────────────────────────────────────────────────────────────
# BaseFetcher
# ────────────────────────────────────────────────────────────

class BaseFetcher:
    """
    所有 Fetcher 的基底類別。
    提供：session with retry、User-Agent、統一 GET/POST、timeout 控制。
    """

    DEFAULT_TIMEOUT   = 15        # 秒
    DEFAULT_MAX_RETRY = 3
    RETRY_BACKOFF     = 0.5       # 指數退避底數
    USER_AGENT        = (
        "FoodScanSystem/1.0 (Research; contact: admin@example.com) "
        "Python-requests/2.x"
    )

    def __init__(self, timeout: int = None, max_retry: int = None):
        if not REQUESTS_AVAILABLE:
            raise ImportError(
                "requests 套件未安裝。請執行：pip install requests beautifulsoup4"
            )
        self.timeout   = timeout   or self.DEFAULT_TIMEOUT
        self.max_retry = max_retry or self.DEFAULT_MAX_RETRY
        self.session   = self._build_session()

    def _build_session(self) -> "requests.Session":
        session = requests.Session()
        retry_strategy = Retry(
            total            = self.max_retry,
            backoff_factor   = self.RETRY_BACKOFF,
            status_forcelist = [429, 500, 502, 503, 504],
            allowed_methods  = ["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://",  adapter)
        session.headers.update({"User-Agent": self.USER_AGENT})
        return session

    def _get(self, url: str, params: Dict = None, **kwargs) -> "requests.Response":
        logger.debug("GET %s params=%s", url, params)
        return self.session.get(url, params=params, timeout=self.timeout, **kwargs)

    def _post(self, url: str, data: Dict = None, json_body: Dict = None,
              **kwargs) -> "requests.Response":
        logger.debug("POST %s", url)
        return self.session.post(url, data=data, json=json_body,
                                 timeout=self.timeout, **kwargs)


# ────────────────────────────────────────────────────────────
# 1. Open Food Facts Fetcher（條碼查商品）
# ────────────────────────────────────────────────────────────

class OpenFoodFactsFetcher(BaseFetcher):
    """
    查詢 Open Food Facts API v2，取得商品成分、過敏原、Nutri-Score。
    API 文件：https://openfoodfacts.github.io/openfoodfacts-server/api/
    免費，無需 API key，每日請求無上限（請善待公共資源）。
    """

    BASE_URL = "https://world.openfoodfacts.org/api/v2/product/"
    # 台灣商品可嘗試：https://tw.openfoodfacts.org/api/v2/product/
    TW_URL   = "https://tw.openfoodfacts.org/api/v2/product/"

    # 只取用我們需要的欄位（減少流量）
    FIELDS = (
        "product_name,brands,ingredients_text,ingredients_text_zh,"
        "allergens_tags,nutriments,nutriscore_grade,nutriscore_score,"
        "nova_group,additives_tags,labels_tags"
    )

    def fetch(self, barcode: str, prefer_tw: bool = True) -> FetchResult:
        """
        根據條碼取得商品資料。

        Args:
            barcode    : EAN-13 條碼字串，例如 "4901085612422"
            prefer_tw  : 是否優先查台灣子站（覆蓋率較低但本地化較佳）
        Returns:
            FetchResult，data 為解析後的 FoodProduct，或 None（找不到）
        """
        source_id = DS_OPEN_FOOD_FACTS.source_id
        url = (self.TW_URL if prefer_tw else self.BASE_URL) + f"{barcode}.json"
        params = {"fields": self.FIELDS, "lc": "zh"}

        try:
            resp = self._get(url, params=params)
        except Exception as exc:
            # 台灣站失敗時自動 fallback 全球站
            if prefer_tw:
                logger.warning("TW site failed (%s), falling back to global", exc)
                return self.fetch(barcode, prefer_tw=False)
            return FetchResult.failure(source_id, str(exc))

        if resp.status_code == 200:
            body = resp.json()
            if body.get("status") == 1 and "product" in body:
                product = self._parse(barcode, body["product"])
                return FetchResult.success(source_id, product, resp.text, resp.status_code)
            # 找不到商品
            if prefer_tw:
                logger.info("Barcode %s not found in TW site, trying global", barcode)
                return self.fetch(barcode, prefer_tw=False)
            return FetchResult.failure(source_id, f"商品 {barcode} 未收錄於資料庫",
                                       resp.status_code)

        return FetchResult.failure(source_id, f"HTTP {resp.status_code}", resp.status_code)

    def _parse(self, barcode: str, p: Dict) -> FoodProduct:
        """將 OFF API 回傳的 product dict 解析成 FoodProduct"""
        # 成分文字 → 清單（優先用中文版）
        ingredients_raw = (
            p.get("ingredients_text_zh") or p.get("ingredients_text") or ""
        )
        ingredients = self._split_ingredients(ingredients_raw)

        # 過敏原標籤（格式："en:gluten", "zh:花生" 等）
        allergens = [
            tag.split(":")[-1] for tag in p.get("allergens_tags", [])
        ]

        # 營養素（per 100g）
        n = p.get("nutriments", {})
        nutrition = {
            "energy_kj":  n.get("energy-kj_100g"),
            "energy_kcal":n.get("energy-kcal_100g"),
            "sugars":     n.get("sugars_100g"),
            "fat":        n.get("fat_100g"),
            "sat_fat":    n.get("saturated-fat_100g"),
            "salt":       n.get("salt_100g"),
            "sodium":     n.get("sodium_100g"),
            "protein":    n.get("proteins_100g"),
            "fiber":      n.get("fiber_100g"),
        }
        # 過濾掉 None 值
        nutrition = {k: v for k, v in nutrition.items() if v is not None}

        return FoodProduct(
            barcode           = barcode,
            product_name      = p.get("product_name", ""),
            brand             = p.get("brands", ""),
            ingredients       = ingredients,
            allergens         = allergens,
            nutrition_per100g = nutrition,
            nutri_score       = (p.get("nutriscore_grade") or "").upper() or None,
            nutri_score_val   = p.get("nutriscore_score"),
            source_api        = DS_OPEN_FOOD_FACTS.source_id,
            raw_data          = p,
        )

    @staticmethod
    def _split_ingredients(text: str) -> List[str]:
        """將成分字串拆成清單（以逗號、頓號、分號分割）"""
        import re
        if not text:
            return []
        parts = re.split(r"[,，、；;]", text)
        return [p.strip().strip("()（）[]【】") for p in parts if p.strip()]


# ────────────────────────────────────────────────────────────
# 2. 全國法規資料庫 Fetcher（食藥署法規條文）
# ────────────────────────────────────────────────────────────

class TaiwanLawFetcher(BaseFetcher):
    """
    查詢全國法規資料庫開放 API，取得台灣食品相關法規全文。
    API 說明：https://law.moj.gov.tw/api/v1/lawdata/{pcode}
    免費開放，無需 key。

    常用法規代碼（pcode）：
      L0040098  食品良好衛生規範準則
      L0040112  食品過敏原標示規定
      L0040070  食品添加物使用範圍及限量暨規格標準
      L0040001  食品安全衛生管理法（食安法）
    """

    BASE_URL = "https://law.moj.gov.tw/api/v1/lawdata/"

    KNOWN_PCODES = {
        "food_hygiene":     "L0040098",   # 食品良好衛生規範準則
        "allergen_label":   "L0040112",   # 食品過敏原標示規定
        "food_additive":    "L0040070",   # 食品添加物使用範圍及限量暨規格標準
        "food_safety_act":  "L0040001",   # 食品安全衛生管理法
        "nutrition_label":  "L0040060",   # 包裝食品營養標示應遵行事項
    }

    def fetch_by_name(self, name: str) -> FetchResult:
        """
        依預設法規名稱查詢。
        name 為 KNOWN_PCODES 的鍵名，例如 'allergen_label'。
        """
        pcode = self.KNOWN_PCODES.get(name)
        if not pcode:
            return FetchResult.failure("tw_law_api", f"未知法規名稱: {name}")
        return self.fetch_by_pcode(pcode)

    def fetch_by_pcode(self, pcode: str) -> FetchResult:
        """依 pcode 直接查詢法規全文"""
        url = self.BASE_URL + pcode
        source_id = f"tw_law_{pcode}"
        try:
            resp = self._get(url)
            if resp.status_code == 200:
                data = resp.json()
                parsed = self._parse(data)
                return FetchResult.success(source_id, parsed, resp.text, resp.status_code)
            return FetchResult.failure(source_id, f"HTTP {resp.status_code}", resp.status_code)
        except Exception as exc:
            return FetchResult.failure(source_id, str(exc))

    def fetch_allergen_rules(self) -> FetchResult:
        """快捷方法：抓取過敏原標示法規"""
        return self.fetch_by_name("allergen_label")

    def fetch_food_additive_list(self) -> FetchResult:
        """快捷方法：抓取食品添加物使用範圍及限量"""
        return self.fetch_by_name("food_additive")

    @staticmethod
    def _parse(data: Dict) -> Dict:
        """
        解析全國法規資料庫 API 回傳，整理成結構化法規資訊。
        """
        law_data = data.get("LawData", {})
        articles = data.get("LawArticles", {}).get("LawArticle", [])

        parsed_articles = []
        for art in articles:
            parsed_articles.append({
                "number":  art.get("ArticleNo", ""),
                "content": art.get("ArticleContent", "").strip(),
            })

        return {
            "pcode":        law_data.get("PCode", ""),
            "law_name":     law_data.get("LawName", ""),
            "law_date":     law_data.get("LawModifiedDate", ""),
            "law_url":      law_data.get("LawURL", ""),
            "articles":     parsed_articles,
            "article_count": len(parsed_articles),
        }

    def search_keyword_in_law(self, pcode: str, keyword: str) -> List[Dict]:
        """
        在指定法規中搜尋含特定關鍵字的條文。

        Returns:
            符合條文的清單，每項包含 number / content
        """
        result = self.fetch_by_pcode(pcode)
        if not result.ok or not result.data:
            return []
        articles = result.data.get("articles", [])
        return [
            art for art in articles
            if keyword in art.get("content", "")
        ]


# ────────────────────────────────────────────────────────────
# 3. 食藥署 / 國健署網頁爬蟲（HTML Scraper）
# ────────────────────────────────────────────────────────────

class TaiwanGovScraper(BaseFetcher):
    """
    爬取食藥署（fda.gov.tw）與國健署（hpa.gov.tw）公告頁面。
    注意：HTML 結構可能因網站改版而失效，需定期驗證。
    僅在 PDF 無法取得時作為補充手段。
    """

    FDA_BASE = "https://www.fda.gov.tw"
    HPA_BASE = "https://www.hpa.gov.tw"

    def fetch_fda_page(self, path: str) -> FetchResult:
        """
        抓取食藥署網頁並回傳純文字內容。
        path 範例：'/TC/site.aspx?sid=2750'（DRIs 頁面）
        """
        source_id = "tw_fda_scrape"
        url = urljoin(self.FDA_BASE, path)
        try:
            resp = self._get(url)
            if resp.status_code != 200:
                return FetchResult.failure(source_id, f"HTTP {resp.status_code}", resp.status_code)

            if not BS4_AVAILABLE:
                return FetchResult.success(source_id,
                                           {"raw_html": resp.text}, resp.text, resp.status_code)

            soup = BeautifulSoup(resp.content, "html.parser")
            # 移除導覽列/頁首頁尾，保留主要內容
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            main_text = soup.get_text(separator="\n", strip=True)

            return FetchResult.success(source_id,
                                       {"url": url, "text": main_text},
                                       main_text, resp.status_code)
        except Exception as exc:
            return FetchResult.failure(source_id, str(exc))

    def fetch_hpa_page(self, path: str) -> FetchResult:
        """抓取國健署網頁並回傳純文字內容"""
        source_id = "tw_hpa_scrape"
        url = urljoin(self.HPA_BASE, path)
        try:
            resp = self._get(url)
            if resp.status_code != 200:
                return FetchResult.failure(source_id, f"HTTP {resp.status_code}", resp.status_code)

            if not BS4_AVAILABLE:
                return FetchResult.success(source_id,
                                           {"raw_html": resp.text}, resp.text, resp.status_code)

            soup = BeautifulSoup(resp.content, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            main_text = soup.get_text(separator="\n", strip=True)

            return FetchResult.success(source_id,
                                       {"url": url, "text": main_text},
                                       main_text, resp.status_code)
        except Exception as exc:
            return FetchResult.failure(source_id, str(exc))

    def fetch_dri_page(self) -> FetchResult:
        """快捷方法：抓取食藥署 DRI（膳食營養素參考值）頁面"""
        return self.fetch_fda_page("/TC/site.aspx?sid=2750")

    def fetch_elderly_handbook_page(self) -> FetchResult:
        """快捷方法：抓取國健署老年期營養參考手冊頁面"""
        return self.fetch_hpa_page("/Pages/Detail.aspx?nodeid=542&pid=9821")


# ────────────────────────────────────────────────────────────
# 4. USDA FoodData Central API（美國 FDA 補充）
# ────────────────────────────────────────────────────────────

class USDAFetcher(BaseFetcher):
    """
    USDA FoodData Central API — 美國農業部食品資料庫（第二層補充依據）。
    需申請免費 API key：https://fdc.nal.usda.gov/api-guide.html

    主要用途：取得特定成分的詳細營養素組成（非條碼查詢）。
    """

    BASE_URL = "https://api.nal.usda.gov/fdc/v1/"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key
        self.session.params = {"api_key": api_key}

    def search_food(self, query: str, page_size: int = 10,
                    data_type: str = "Branded") -> FetchResult:
        """
        搜尋食品名稱。

        Args:
            query      : 搜尋詞（英文為主）
            page_size  : 每頁結果數
            data_type  : "Branded"（品牌商品）/"Foundation"（基礎食材）
        """
        source_id = "us_fda_fdc"
        url = urljoin(self.BASE_URL, "foods/search")
        params = {
            "query":     query,
            "pageSize":  page_size,
            "dataType":  data_type,
        }
        try:
            resp = self._get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                foods = [self._parse_food_summary(f) for f in data.get("foods", [])]
                return FetchResult.success(source_id, {"foods": foods,
                                                        "total": data.get("totalHits", 0)},
                                           resp.text)
            return FetchResult.failure(source_id, f"HTTP {resp.status_code}", resp.status_code)
        except Exception as exc:
            return FetchResult.failure(source_id, str(exc))

    def get_food_detail(self, fdc_id: int) -> FetchResult:
        """取得單一食品詳細資訊（含完整營養素）"""
        source_id = "us_fda_fdc"
        url = urljoin(self.BASE_URL, f"food/{fdc_id}")
        try:
            resp = self._get(url)
            if resp.status_code == 200:
                return FetchResult.success(source_id, resp.json(), resp.text)
            return FetchResult.failure(source_id, f"HTTP {resp.status_code}", resp.status_code)
        except Exception as exc:
            return FetchResult.failure(source_id, str(exc))

    @staticmethod
    def _parse_food_summary(f: Dict) -> Dict:
        return {
            "fdc_id":       f.get("fdcId"),
            "description":  f.get("description", ""),
            "brand":        f.get("brandName", "") or f.get("brandOwner", ""),
            "data_type":    f.get("dataType", ""),
            "score":        f.get("score"),
        }


# ────────────────────────────────────────────────────────────
# 5. FetcherFactory — 統一入口
# ────────────────────────────────────────────────────────────

class FetcherFactory:
    """
    提供統一的 fetcher 取得介面，避免外部程式直接依賴具體實作。
    """

    def __init__(self, usda_api_key: str = "DEMO_KEY"):
        self._off     = None   # OpenFoodFacts（延遲初始化）
        self._law     = None   # TaiwanLaw
        self._gov     = None   # TaiwanGov
        self._usda    = None
        self._usda_key = usda_api_key

    @property
    def open_food_facts(self) -> OpenFoodFactsFetcher:
        if self._off is None:
            self._off = OpenFoodFactsFetcher()
        return self._off

    @property
    def taiwan_law(self) -> TaiwanLawFetcher:
        if self._law is None:
            self._law = TaiwanLawFetcher()
        return self._law

    @property
    def taiwan_gov(self) -> TaiwanGovScraper:
        if self._gov is None:
            self._gov = TaiwanGovScraper()
        return self._gov

    @property
    def usda(self) -> USDAFetcher:
        if self._usda is None:
            self._usda = USDAFetcher(self._usda_key)
        return self._usda

    def fetch_product(self, barcode: str) -> FetchResult:
        """主要入口：依條碼取得 FoodProduct"""
        return self.open_food_facts.fetch(barcode)

    def fetch_allergen_law(self) -> FetchResult:
        """取得台灣過敏原標示法規全文"""
        return self.taiwan_law.fetch_allergen_rules()

    def fetch_food_additive_law(self) -> FetchResult:
        """取得台灣食品添加物使用範圍及限量規定"""
        return self.taiwan_law.fetch_food_additive_list()

    def batch_fetch_products(self, barcodes: List[str],
                             delay: float = 0.5) -> Dict[str, FetchResult]:
        """
        批次查詢多個條碼（含禮貌性延遲，避免對 OFF 伺服器造成壓力）。

        Args:
            barcodes : 條碼列表
            delay    : 每次請求間的等待秒數（預設 0.5 秒）
        Returns:
            {barcode: FetchResult} 字典
        """
        results = {}
        for i, barcode in enumerate(barcodes):
            results[barcode] = self.fetch_product(barcode)
            if i < len(barcodes) - 1:
                time.sleep(delay)
        return results


# ────────────────────────────────────────────────────────────
# 快速測試（直接執行此檔案時）
# ────────────────────────────────────────────────────────────

def _demo():
    """簡易 demo：查詢一個條碼並印出結果"""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    factory = FetcherFactory()

    # 測試條碼：日清雞汁拉麵（常見有收錄）
    test_barcode = "4901071182824"
    print(f"\n=== 查詢條碼：{test_barcode} ===")
    result = factory.fetch_product(test_barcode)

    if result.ok and result.data:
        p: FoodProduct = result.data
        print(f"商品名稱  : {p.product_name}")
        print(f"品牌      : {p.brand}")
        print(f"Nutri-Score: {p.nutri_score} ({p.nutri_score_val})")
        print(f"過敏原    : {p.allergens}")
        print(f"成分清單  : {p.ingredients[:5]} ...")
        print(f"營養素    : {json.dumps(p.nutrition_per100g, ensure_ascii=False, indent=2)}")
    else:
        print(f"查詢失敗：{result.error}")

    # 測試法規 API
    print("\n=== 查詢過敏原標示法規（前 3 條）===")
    law_result = factory.fetch_allergen_law()
    if law_result.ok and law_result.data:
        articles = law_result.data.get("articles", [])[:3]
        for art in articles:
            print(f"第 {art['number']} 條: {art['content'][:80]}...")
    else:
        print(f"法規查詢失敗：{law_result.error}")


if __name__ == "__main__":
    _demo()
