import os
import sys
import json
import re
from dotenv import load_dotenv
from google import genai

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

# Read safety alerts for 統一企業 from DB
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text("SELECT title, content, alert_date, source_url, source_type, severity FROM safety_alerts WHERE producer_id = 1 LIMIT 5"))
    safety_alerts = [dict(r._mapping) for r in res.fetchall()]


nutrition = {
    "calories": 470.0,
    "protein": 9.2,
    "fat": 22.1,
    "sugar": 2.5,
    "sodium": 1890.0
}

chemical = [
  {
    "name": "Monosodium L-Glutamate (味精)",
    "risk_level": "medium"
  },
  {
    "name": "Sorbic Acid (己二烯酸鉀)",
    "risk_level": "medium"
  }
]

user_conditions = {
    "group": "adult",
    "allergens": []
}

deterministic_score = 32
deterministic_grade = "E"

composite_prompt = f"""
你是一個專業食品健康診斷與安全分析AI。請依據以下提供的產品客觀數據，進行全面分析並提供繁體中文的健康與安全報告：

【基本商品資訊】
商品名稱: 統一肉燥麵
製造商/品牌: 統一企業
營養成分: {json.dumps(nutrition, ensure_ascii=False)}

【權威計分依據 (Nutri-Score V7 2024 最新版)】
依據歐盟 2024 演算法算出的確切總分: {deterministic_score}
確定之健康分級: {deterministic_grade} 級 (A為最優，E為最差)

【廠商食安歷史 (Module C)】
{json.dumps(safety_alerts, ensure_ascii=False)}

【權威資料庫已提供之食品添加物資訊 (Module B)】
{json.dumps(chemical, ensure_ascii=False)}

【使用者健康背景】
{json.dumps(user_conditions, ensure_ascii=False)}

【任務】
請使用 Google Gemma 模型進行深度分析，並提供以下三個 AI 總結：
1. 「總體商品健康診斷總結」(overall_summary)：參考「確切總分」與「健康分級」，產出 100 字內之個人化核心診斷與長期過量攝取的累積慢性健康風險（例如：吃了沒事，但吃久了會有事）。
2. 「添加物風險總結」(additives_summary)：分析本產品所含的食品添加物、人工化學成分（如防腐劑、防凝劑、甘味劑等）的組合風險，特別是針對該使用者背景（如糖尿病、孕婦、高血壓等）的危害程度，產出 100 字內的分析總結。若無添加物，請說明「本產品無添加化學食品添加物」。
3. 「食安歷史事件總結」(safety_events_summary)：分析本產品製造商（廠商）以往的食安歷史違規與歷史事件，對消費者信任度與產品安全的影響，產出 100 字內的分析總結。若無歷史食安事件，請說明「該廠商無特定歷史食安違規紀錄」。
4. 產出具體「個人化警示」(warnings) 清單。

請以繁體中文回答。回傳格式必須為純 JSON，不可有任何 Markdown 標記，結構如下：
{{
  "score": {deterministic_score},
  "grade": "{deterministic_grade}",
  "overall_summary": "總體商品健康診斷總結文字",
  "additives_summary": "添加物風險總結文字",
  "safety_events_summary": "食安歷史事件總結文字",
  "warnings": ["警告1", "警告2"]
}}
"""

print("Prompt length:", len(composite_prompt))
for model in ['gemma-4-31b-it', 'gemini-2.5-flash']:
    try:
        print(f"\nCalling model: {model}")
        response = client.models.generate_content(
            model=model,
            contents=composite_prompt
        )
        print(f"--- Response from {model} ---")
        print(response.text)
    except Exception as e:
        print(f"Error for {model}: {e}")
