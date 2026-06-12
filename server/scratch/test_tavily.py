import os
import sys
from dotenv import load_dotenv
from tavily import TavilyClient

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
tavily = TavilyClient(api_key=TAVILY_API_KEY)

producer_name = "統一企業"
SEARCH_TIERS = [
    {
        "source_type": "official",
        "queries": [f"{producer_name} 食安 不合格 回收"],
        "include_domains": ["fda.gov.tw", "mohw.gov.tw", "consumer.gov.tw"],
    },
    {
        "source_type": "news",
        "queries": [f"{producer_name} 食安 違規", f"{producer_name} food safety recall"],
        "include_domains": ["udn.com", "ltn.com.tw", "cna.com.tw", "setn.com", "tvbs.com.tw"],
    },
    {
        "source_type": "social",
        "queries": [f"{producer_name} 食安 投訴 衛生局", f"{producer_name} 異物 客訴"],
    },
]

seen_urls = set()
results = []
for tier in SEARCH_TIERS:
    source_type = tier["source_type"]
    include_domains = tier.get("include_domains")
    for q in tier["queries"]:
        try:
            search_params = {
                "query": q,
                "max_results": 5,
                "search_depth": "advanced"
            }
            if include_domains:
                search_params["include_domains"] = include_domains
            print(f"Searching: {q}")
            resp = tavily.search(**search_params)
            for r in resp.get("results", []):
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    r["source_type"] = source_type
                    results.append(r)
        except Exception as e:
            print(f"Error: {e}")

print(f"Total results: {len(results)}")
for idx, r in enumerate(results[:5]):
    print(f"[{idx}] Title: {r.get('title')} | Source: {r.get('source_type')}")
    print(f"Content: {r.get('content')[:100]}...")
