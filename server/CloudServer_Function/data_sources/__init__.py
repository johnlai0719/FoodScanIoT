"""
data_sources
============
食品掃描建議系統 — 資料來源模組

快速使用：
    from data_sources import DataOrganizer, FetcherFactory, PopulationGroup

    factory   = FetcherFactory()
    organizer = DataOrganizer()

    result    = factory.fetch_product("4901085612422")
    if result.ok:
        scan = organizer.organize(result.data, PopulationGroup.ELDERLY)
        print(scan)
"""

from .models import (
    AllergenType,
    DataSource,
    FetchMethod,
    FoodProduct,
    GroupRule,
    LiteratureRecord,
    PopulationGroup,
    ScanResult,
    SourceLayer,
)

from .registry import (
    ALL_LITERATURE,
    ALL_SOURCES,
    RULES,
    get_overdue_literature,
    get_rules_by_group,
    get_sources_by_group,
    get_sources_by_layer,
)

from .fetcher import (
    FetchResult,
    FetcherFactory,
    OpenFoodFactsFetcher,
    TaiwanLawFetcher,
    TaiwanGovScraper,
)

from .organizer import (
    DataOrganizer,
    RuleEngine,
    check_literature_overdue,
    export_scan_result_json,
    format_scan_result,
    match_allergens,
    normalize_ingredients,
)

__version__ = "1.0.0"
__all__ = [
    # models
    "AllergenType", "DataSource", "FetchMethod", "FoodProduct",
    "GroupRule", "LiteratureRecord", "PopulationGroup", "ScanResult", "SourceLayer",
    # registry
    "ALL_LITERATURE", "ALL_SOURCES", "RULES",
    "get_overdue_literature", "get_rules_by_group",
    "get_sources_by_group", "get_sources_by_layer",
    # fetcher
    "FetchResult", "FetcherFactory",
    "OpenFoodFactsFetcher", "TaiwanLawFetcher", "TaiwanGovScraper",
    # organizer
    "DataOrganizer", "RuleEngine",
    "check_literature_overdue", "export_scan_result_json",
    "format_scan_result", "match_allergens", "normalize_ingredients",
]
