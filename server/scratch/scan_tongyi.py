import requests
import sys
import os
import time
from dotenv import load_dotenv

# Add parent directory and load dotenv
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from database import engine
from sqlalchemy import text

url = "http://localhost:3002/api/analyze"
payload = {
    "barcode": "4710088411164", # 統一肉燥麵
    "label_images": [],
    "user_conditions": {
        "group": "adult",
        "allergens": []
    }
}

try:
    print("Step 1: Sending scan request for 統一肉燥麵...")
    resp = requests.post(url, json=payload, timeout=60)
    print(f"Initial Scan Status Code: {resp.status_code}")
    
    print("\nStep 2: Waiting 10 seconds for safety monitor background crawler task to complete...")
    time.sleep(10)
    
    print("\nStep 3: Querying database for safety alerts inserted for 統一企業...")
    with engine.connect() as conn:
        res = conn.execute(text("SELECT id, alert_date, title, source_type, content FROM safety_alerts WHERE producer_id = 1 ORDER BY alert_date DESC"))
        rows = res.fetchall()
        print(f"Total alerts found: {len(rows)}")
        for r in rows[:5]:
            print(f"ID: {r[0]} | Date: {r[1]} | Title: {r[2]} | Source: {r[3]}")
            
    print("\nStep 4: Requesting analysis again to fetch the updated AI summaries containing the safety events...")
    resp2 = requests.post(url, json=payload, timeout=60)
    if resp2.status_code == 200:
        data = resp2.json()
        print("\n=== AI overall_summary ===")
        print(data.get("overall_summary"))
        print("\n=== AI additives_summary ===")
        print(data.get("additives_summary"))
        print("\n=== AI safety_events_summary ===")
        print(data.get("safety_events_summary"))
        print("\n=== AI Warnings ===")
        print(data.get("risk_tags"))
    else:
        print(f"Second scan failed: {resp2.text}")
        
except Exception as e:
    print(f"Execution failed: {e}")
