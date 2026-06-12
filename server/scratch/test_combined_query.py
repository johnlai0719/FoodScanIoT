import os
import sys
from urllib.parse import urlparse
from dotenv import load_dotenv
from tavily import TavilyClient

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
tavily = TavilyClient(api_key=TAVILY_API_KEY)

producer_name = "統一企業"

# Combined query
query = f"{producer_name} 食安 (不合格 OR 違規 OR 回收 OR 投訴 OR 衛生局 OR 異物 OR 客訴)"

print(f"Testing combined query: {query}")
try:
    search_params = {
        "query": query,
        "max_results": 15,
        "search_depth": "advanced"
    }
    resp = tavily.search(**search_params)
    results = resp.get("results", [])
    print(f"Total results fetched: {len(results)}")
    
    # Classification logic
    official_domains = {"fda.gov.tw", "mohw.gov.tw", "consumer.gov.tw", "gov.tw"}
    news_domains = {"udn.com", "ltn.com.tw", "cna.com.tw", "setn.com", "tvbs.com.tw", "yahoo.com", "chinatimes.com", "ettoday.net"}
    
    seen_urls = set()
    classified_results = []
    
    for r in results:
        url = r.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        
        is_official = False
        for od in official_domains:
            if domain == od or domain.endswith("." + od):
                is_official = True
                break
        
        if is_official:
            r["source_type"] = "official"
        else:
            is_news = False
            for nd in news_domains:
                if domain == nd or domain.endswith("." + nd):
                    is_news = True
                    break
            if is_news:
                r["source_type"] = "news"
            else:
                r["source_type"] = "social"
                
        classified_results.append(r)
        
    print(f"Unique classified results: {len(classified_results)}")
    for idx, r in enumerate(classified_results):
        print(f"[{idx}] Title: {r.get('title')} | Source: {r.get('source_type')} | URL: {r.get('url')}")
        print(f"Snippet: {r.get('content')[:120]}...\n")

except Exception as e:
    print(f"Error: {e}")
