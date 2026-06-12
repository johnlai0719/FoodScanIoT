import os
import json
import re
from google import genai
from dotenv import load_dotenv

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

producer_name = "統一企業"
snippets = [
    {
        "title": "有關統一企業股份有限公司自主通報原料輻射超出安全容許量之處理-衛生福利部",
        "content": "進階衛生福利部食品藥物管理署表示，統一企業於106年主動通報部分產品原料輻射檢測值超標，已進行全面下架回收與銷毀。",
        "url": "https://www.mohw.gov.tw/cp-3568-38637-1.html",
        "source_type": "official",
        "published_date": "2017-12"
    },
    {
        "title": "統一超食代回函，我的正式反駁聲明",
        "content": "網友在 Threads 上發文抱怨統一超食代便當有異物，對此超食代客服已聯繫處理並準備進行調解。",
        "url": "https://www.threads.com/@aurora0970/post/DPv7fhIErMt",
        "source_type": "social",
        "published_date": "2025-10"
    }
]

snippets_formatted = ""
for idx, s in enumerate(snippets):
    snippets_formatted += f"--- Snippet {idx} ---\n"
    snippets_formatted += f"Title: {s.get('title', '')}\n"
    snippets_formatted += f"Content: {s.get('content', '')}\n"
    snippets_formatted += f"URL: {s.get('url', '')}\n"
    snippets_formatted += f"Published Date: {s.get('published_date', '')}\n"
    snippets_formatted += f"Source Type: {s.get('source_type', '')}\n\n"

prompt = f"""You are a food safety data validator. You will be given a list of web search result snippets about a company named "{producer_name}". Each snippet has a "Source Type" associated with it (either official, news, or social).

Your task:
For each snippet, determine whether it describes a real food safety event (product recall, regulatory violation, contamination, lab test failure, or similar).

Rules:
- Gemini must only validate and extract information. Do NOT invent or infer information not present in the snippets. You are strictly forbidden from generating safety events on your own.
- If a snippet does not describe a food safety event, set is_food_safety_event to false and leave other fields empty.
- event_date must be in YYYY-MM format. If only year is known, use YYYY-01. If unknown, use "".
- summary must be under 100 characters/words in Traditional Chinese (繁體中文) and based only on the snippet.
- Return a JSON array containing one object for each snippet.
- Return JSON ONLY, no markdown fences.
- severity is an integer (1, 2, or 3) where:
  1 = labeling/tagging violation (標示違規)
  2 = chemical/ingredient violation (成分違規)
  3 = major safety incident (重大事件)

Required JSON Array Format:
[
  {{
    "is_food_safety_event": true or false,
    "title": "事件標題（原文語言）",
    "summary": "中文摘要（100字內）",
    "event_date": "YYYY-MM or YYYY-01 or empty string",
    "source_url": "URL of the snippet source",
    "source_type": "official / news / social",
    "severity": 1
  }},
  ...
]

Snippets to validate:
{snippets_formatted}
"""

print("Running test validation with gemini-2.5-flash-lite...")
try:
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt
    )
    print("Raw Response:")
    text = response.text.strip()
    print(text)
    
    # Parse and verify JSON
    match = re.search(r"(\[.*\])", text, re.DOTALL)
    if match:
        text = match.group(1)
    data = json.loads(text)
    print("\nParsed Data:")
    print(json.dumps(data, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error during validation: {e}")
