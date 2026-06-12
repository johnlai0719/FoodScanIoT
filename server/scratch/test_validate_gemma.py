import os
import sys
import json
from dotenv import load_dotenv
from google import genai

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

producer_name = "統一企業"
snippets = [
  {
    "title": "有關統一企業股份有限公司自主通報原料輻射超出安全容許量之處理-衛生福利部",
    "content": "統一企業自主通報原料「去醣基山桑子萃取物」輻射量（Cs-137）超出安全容許量，涉及產品「預倍保明智膠囊」，已採取封存與回收措施。",
    "url": "https://www.mohw.gov.tw/cp-3568-38637-1.html",
    "published_date": "2017-11",
    "source_type": "official"
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
- Gemma must only validate and extract information. Do NOT invent or infer information not present in the snippets. You are strictly forbidden from generating safety events on your own.
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
  }}
]

Snippets to validate:
{snippets_formatted}
"""

for model in ['gemma-4-31b-it', 'gemini-2.5-flash']:
    try:
        print(f"Testing validation with model: {model}")
        response = client.models.generate_content(
            model=model,
            contents=prompt
        )
        print(f"Response from {model}:")
        print(response.text)
    except Exception as e:
        print(f"Error for {model}: {e}")
