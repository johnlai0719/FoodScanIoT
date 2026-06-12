import requests
import json

url = "http://localhost:3003/query"
payload = {
    "barcode": "4710126198463",
    "user_conditions": {
        "group": "adult",
        "allergens": []
    }
}

print("Testing Fog Python API /query output fields...")
try:
    response = requests.post(url, json=payload, timeout=5)
    print(f"Status Code: {response.status_code}")
    data = response.json()
    
    # Check top-level summaries
    print("\nTop-level summaries in Fog response:")
    print("overall_summary:", data.get("overall_summary"))
    print("additives_summary:", data.get("additives_summary"))
    print("safety_events_summary:", data.get("safety_events_summary"))
    
except Exception as e:
    print(f"Error: {e}")
